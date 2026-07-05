"""Richemont job scraper — France, Technology + Data families, CDI only.

Richemont (luxury holding: Cartier, Van Cleef & Arpels, Montblanc, IWC, …) fronts
its careers site at careers.richemont.com, but that is only a marketing wrapper —
the real ATS is Workday, tenant `richemont`, site `richemont`, exposing the
standard CXS JSON API (same shape as the Air Liquide / Rothschild scrapers):

  POST /wday/cxs/richemont/richemont/jobs              (listing)
  GET  /wday/cxs/richemont/richemont<externalPath>     (detail)

Filter mapping (this scope):
  locationCountry = "France"                (server-side facet; global Workday
                    country WID, shared with Air Liquide)
  workerSubType   = "Permanent" (= CDI)     (server-side facet)
  jobFamilyGroup  = Technology + Data, looped one at a time so each row can be
                    tagged with its family as `category` (the listing does not
                    echo the family per row).

Scope decision (see filters.md): France, Data & AI + Software/Tech, CDI only.
Richemont has a clean JOB FUNCTION facet, so we filter on the `jobFamilyGroup`
facet directly and do NOT apply the is_tech_role title gate — the "Technology"
and "Data" families are already pure tech (Product Owner, Data Scientist, …),
per the prefer-platform-category-over-is_tech_role rule.

Yield note: this is a low-yield board and that is expected, not a bug. Richemont's
engineering/IT is concentrated at its Geneva HQ; France is overwhelmingly retail
("Commercial" = 523 of 1156 group-wide). France + Technology + Permanent is
currently 0 (the handful of France Technology roles are all fixed-term / assignee)
and France + Data + Permanent is ~2. To widen later: add worker sub-types to
FILTER_WORKERSUBTYPE (e.g. the Fixed-Term id) or append more family ids to
FAMILIES.

Workday quirks worth knowing (shared with Air Liquide / Rothschild):
  - The endpoint is fronted by Cloudflare and can rate-limit aggressively: a burst
    of POSTs from one IP starts returning empty-body HTTP 400. Recovery window is
    ~60s. So we go slow (REQUEST_DELAY_SECONDS) and clear cookies + back off on 400.
  - Combining facets is fine *when paced*; the 400s are purely rate-limiting.
  - The listing's `bulletFields[0]` is the public requisition id ("JR130855") —
    used as native_job_id. The detail's `jobPostingInfo.id` is an internal hash
    kept as `identifier` only.
  - The detail's `startDate` is the canonical posted date (ISO) — used as
    posted_date; the listing only gives a relative "Posted N Days Ago".

The steady-state request count is tiny (a couple of listing pages + one detail per
matched job, all spaced >= REQUEST_DELAY_SECONDS), so this runs fine from GitHub
Actions.
"""
from __future__ import annotations

import html
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

TENANT = "richemont"
SITE = "richemont"
HOST = f"https://{TENANT}.wd3.myworkdayjobs.com"
LIST_URL = f"{HOST}/wday/cxs/{TENANT}/{SITE}/jobs"
DETAIL_URL_TEMPLATE = f"{HOST}/wday/cxs/{TENANT}/{SITE}{{external_path}}"

FILTER_COUNTRY_FRANCE = "54c5b6971ffb4bf0b116fe7651ec789a"
# "Permanent" worker sub-type == CDI. This is the only place employment type is
# available: the detail JSON has no worker-type field, so the CDI gate MUST stay
# server-side on this facet.
FILTER_WORKERSUBTYPE_PERMANENT = "e16f4ac9730a1000ae2714a5e4d60000"

# jobFamilyGroup facet → category label. Looped one at a time so each row can be
# tagged with its family (the listing doesn't echo the family per row).
FAMILIES: dict[str, str] = {
    "c13cc4af92c81001108c30efd7310000": "Technology",
    "c13cc4af92c81001108bc6ad6f0e0000": "Data",
}

# Polite, project-naming User-Agent (playbook hard rule). The CXS JSON API is
# UA-agnostic — the 400s are request-frequency rate-limiting, not UA fingerprinting.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    "Content-Type": "application/json",
    "Origin": HOST,
    "Referer": f"{HOST}/en-US/{SITE}",
}

# Sized for the steady-state volume (a few France Tech/Data CDI rows = one page).
# The cap of 20 pages is a defensive backstop against a pagination bug.
PAGE_SIZE = 20
MAX_PAGES = 20  # per family — defensive cap
REQUEST_TIMEOUT = 30
# Cloudflare in front of Workday can rate-limit: rapid POSTs from one IP start
# returning empty-body HTTP 400. Go slow, back off long when blocked.
REQUEST_DELAY_SECONDS = 2.0
RETRY_BACKOFF_SECONDS = 30.0
MAX_RETRIES = 3


@dataclass
class Job:
    native_job_id: str          # requisition id, e.g. "JR130855"
    title: str
    location: str               # detail's jobRequisitionLocation / listing locationsText
    category: str | None        # job family label ("Technology" / "Data")
    apply_url: str              # detail's externalUrl
    employment_type: str        # always "CDI" for this scope
    description: str | None = None
    posted_date: str | None = None    # YYYY-MM-DD from detail's startDate
    identifier: str | None = None     # detail's jobPostingInfo.id (internal hash)
    raw_payload: dict | None = None


def _clean_description(content: str | None) -> str | None:
    if not content:
        return None
    text = BeautifulSoup(html.unescape(content), "html.parser").get_text(" ", strip=True)
    return text or None


def _native_job_id_from_listing(row: dict) -> str:
    """Requisition id lives in bulletFields[0] (e.g. 'JR130855')."""
    bullets = row.get("bulletFields") or []
    if bullets and isinstance(bullets[0], str) and bullets[0].strip():
        return bullets[0].strip()
    # Fallback: the "_JR12345" segment of externalPath (which may carry a trailing
    # "-1" duplicate-suffix, so we don't anchor to the end of the string).
    path = row.get("externalPath") or ""
    m = re.search(r"_(JR\d{4,})", path)
    if m:
        return m.group(1)
    raise RuntimeError(f"Richemont listing row missing requisition id: {row!r}")


def _fetch_listing(session: requests.Session, family_id: str, page: int) -> dict:
    body = {
        "appliedFacets": {
            "locationCountry": [FILTER_COUNTRY_FRANCE],
            "workerSubType": [FILTER_WORKERSUBTYPE_PERMANENT],
            "jobFamilyGroup": [family_id],
        },
        "limit": PAGE_SIZE,
        "offset": (page - 1) * PAGE_SIZE,
        "searchText": "",
    }
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        # Clear cookies before each attempt: Cloudflare's __cf_bm and Workday's
        # session cookies get tagged "suspicious" after a few rapid POSTs and
        # then keep 400-ing. Cookie-free callers recover after the rate window.
        session.cookies.clear()
        response = session.post(LIST_URL, json=body, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            return response.json()
        last_exc = requests.HTTPError(
            f"{response.status_code} on attempt {attempt}: {response.text[:120]}",
            response=response,
        )
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS)
    assert last_exc is not None
    raise last_exc


def _fetch_detail(session: requests.Session, external_path: str) -> dict:
    url = DETAIL_URL_TEMPLATE.format(external_path=external_path)
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        session.cookies.clear()
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            return response.json()
        last_exc = requests.HTTPError(
            f"{response.status_code} on attempt {attempt}: {response.text[:120]}",
            response=response,
        )
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS)
    assert last_exc is not None
    raise last_exc


def _row_to_job(listing_row: dict, detail: dict, family_name: str) -> Job:
    info = detail.get("jobPostingInfo") or {}
    if not info:
        raise RuntimeError(f"Richemont detail missing jobPostingInfo: {detail!r}")

    apply_url = (info.get("externalUrl") or "").strip()
    if not apply_url:
        # Fallback only — externalUrl is present in practice and locale-less
        # (verified: .../richemont/job/...), so we synthesise to match it.
        ext = listing_row.get("externalPath") or ""
        if ext:
            apply_url = f"{HOST}/{SITE}{ext}" if ext.startswith("/") else f"{HOST}/{SITE}/{ext}"
    if not apply_url:
        raise RuntimeError(f"Richemont detail missing externalUrl: {info!r}")

    location = (
        (info.get("jobRequisitionLocation") or {}).get("descriptor")
        or info.get("location")
        or listing_row.get("locationsText")
        or ""
    ).strip()

    posted = info.get("startDate")
    posted_date = posted[:10] if isinstance(posted, str) and len(posted) >= 10 else None

    title = (info.get("title") or listing_row.get("title") or "").strip()

    return Job(
        native_job_id=_native_job_id_from_listing(listing_row),
        title=title,
        location=location,
        category=family_name,
        apply_url=apply_url,
        employment_type="CDI",
        description=_clean_description(info.get("jobDescription")),
        posted_date=posted_date,
        identifier=info.get("id"),
        raw_payload={"listing": listing_row, "detail": info},
    )


def _collect_family_rows(session: requests.Session, family_id: str, family_name: str) -> list[dict]:
    """Fetch the full France + CDI listing for one job family, with paging.
    Stamps each row with `_family_name` for downstream category tagging."""
    page = 1
    payload = _fetch_listing(session, family_id, page)
    total = int(payload.get("total") or 0)
    rows: list[dict] = list(payload.get("jobPostings") or [])

    while len(rows) < total and page < MAX_PAGES:
        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)
        payload = _fetch_listing(session, family_id, page)
        new = payload.get("jobPostings") or []
        if not new:
            break
        rows.extend(new)

    for r in rows:
        r["_family_name"] = family_name

    print(f"  {family_name}: {len(rows)}/{total} rows", flush=True)
    return rows


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Fetch phase (per-family listing)...", flush=True)

    all_rows: list[dict] = []
    for family_id, family_name in FAMILIES.items():
        # NB: no catch-and-continue here. A partial return (some families dropped)
        # slips past db.persist_run_results' empty-guard and would false-close the
        # missing slice. If a family 400s past its retries, let it propagate and
        # fail the whole run rather than retire live rows.
        rows = _collect_family_rows(session, family_id, family_name)
        all_rows.extend(rows)
        time.sleep(REQUEST_DELAY_SECONDS)

    # Dedup by requisition id; keep first-seen (= first family it surfaced under).
    by_id: dict[str, dict] = {}
    for r in all_rows:
        rid = _native_job_id_from_listing(r)
        by_id.setdefault(rid, r)
    print(f"  -> {len(by_id)} unique France/CDI jobs across families", flush=True)

    print("\nDetail phase...", flush=True)
    jobs: list[Job] = []
    for r in by_id.values():
        ext = r.get("externalPath") or ""
        if not ext:
            print(f"  skip: missing externalPath ({r.get('title')!r})", flush=True)
            continue
        try:
            detail = _fetch_detail(session, ext)
        except requests.HTTPError as exc:
            # A single detail 404 is fine to skip (job pulled between list+detail);
            # anything else raised here is a genuine failure worth surfacing.
            print(f"  detail failed for {ext}: {exc}", flush=True)
            continue
        try:
            jobs.append(_row_to_job(r, detail, r.get("_family_name") or ""))
        except RuntimeError as exc:
            print(f"  parse failed for {ext}: {exc}", flush=True)
        time.sleep(REQUEST_DELAY_SECONDS)

    elapsed = time.time() - started
    print(f"  -> {len(jobs)} jobs in {elapsed:.1f}s\n", flush=True)
    return [asdict(j) for j in jobs]


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    started = time.time()
    try:
        jobs = scrape()
    except Exception as exc:
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise

    elapsed = time.time() - started
    print(f"=== {len(jobs)} jobs final (total runtime {elapsed:.1f}s) ===\n")

    for j in jobs:
        desc = j["description"] or ""
        desc = desc[:200] + ("..." if len(desc) > 200 else "")
        print(f"[{j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
