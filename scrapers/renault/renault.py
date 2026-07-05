"""Renault Group job scraper — France, all-tech (IT & Systems + R&D), CDI only.

Discovery note (why Workday CXS and not the website's own feed):
  renaultgroup.com/en/careers/our-international-vacancies/ is a Next.js/WordPress
  page. Its "offers" widget calls a WPGraphQL resolver named `workdayJobs`
  (GetWorkdayJobsList) via a Faust.js proxy at www.renaultgroup.com/api/fetch.
  That proxy uses Apollo persisted queries and 500s for headless callers, and the
  WordPress GraphQL backend (back.renaultgroup.com) is Cognito-gated. The resolver
  name gives the game away: the real ATS is Workday. The public Workday board is
  the Renault–Nissan–Mitsubishi Alliance tenant:

    https://alliancewd.wd3.myworkdayjobs.com/renault-group-careers

  so we hit its standard CXS JSON API directly (same shape as Air Liquide /
  Rothschild) and skip the fragile WordPress proxy entirely:

    POST /wday/cxs/alliancewd/renault-group-careers/jobs               (listing)
    GET  /wday/cxs/alliancewd/renault-group-careers<externalPath>      (detail)

Filter mapping (this scope):
  locationCountry = "France"                          (server-side facet)
  workerSubType   = "a - Regular (no fixed end date)" (server-side facet, = CDI)
  jobFamilyGroup  = IT & Systems + R&D, looped        (server-side facet, per-loop
                    so each row is tagged with its family as `category`)

Scope decision (see filters.md): France + CDI, widened from Data/AI to *all tech*
by keeping the "IT & Systems" and "R&D" job families wholesale (so cybersecurity,
software, devops and data-platform roles all come through). Yield is small — the
board is ~270 reqs group-wide, France/permanent is ~36, and only the IT & Systems
family carries tech (4 rows at build time); R&D-permanent-France was empty. That
low yield is expected, not a bug (cf. Salesforce / Mirakl / N26).

  Automaker caveat: "R&D" at Renault is mostly automotive hardware engineering
  (chassis/powertrain/materials) — the physical-product junk _relevance.py drops
  elsewhere (Safran/Thales/Airbus). It was kept wholesale here per the explicit
  scope choice, and is empty for now. If mechanical-eng rows start flooding in,
  gate the R&D family with `scrapers._relevance.is_tech_role` (import it and filter
  in scrape()) rather than dropping the family.

Workday quirks (shared with the Air Liquide scraper — same wd3 infra):
  - The endpoint is fronted by Cloudflare and rate-limits bursts of POSTs from one
    IP with empty-body HTTP 400s (~60s recovery). We pace requests and clear
    cookies + back off long on a non-200.
  - Renault's listing rows put the *location* in bulletFields[0] and the job
    family in bulletFields[-1] — NOT the requisition id (Air Liquide puts the id
    there). The requisition id lives in the externalPath tail as `_JOBREQ_<n>`,
    so that's what we parse for native_job_id (cross-checked against the detail's
    jobReqId).
  - The detail's `startDate` is the canonical ISO posted date; the listing only
    gives a relative "Posted N Days Ago".

The steady-state request count is tiny (a couple of listing pages + one detail per
matched job, all spaced >= REQUEST_DELAY_SECONDS), so this runs fine from GitHub
Actions — no WAF IP-block like Safran/BNP.
"""
from __future__ import annotations

import html
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

TENANT = "alliancewd"
SITE = "renault-group-careers"
HOST = f"https://{TENANT}.wd3.myworkdayjobs.com"
LIST_URL = f"{HOST}/wday/cxs/{TENANT}/{SITE}/jobs"
DETAIL_URL_TEMPLATE = f"{HOST}/wday/cxs/{TENANT}/{SITE}{{external_path}}"

# Workday country facet ids are global across tenants (this France id is the exact
# same string Air Liquide uses).
FILTER_COUNTRY_FRANCE = "54c5b6971ffb4bf0b116fe7651ec789a"
# workerSubType ids ARE tenant-specific — this one is Renault's "a - Regular
# (no fixed end date)", i.e. CDI. Do not copy Air Liquide's id here.
FILTER_WORKERSUBTYPE_CDI = "62e55b3e447c01871e63baa4ca0f9391"

# jobFamilyGroup facet → clean category label. Looped one family at a time so each
# row can be tagged with its family and so we only detail-fetch the tech families
# (the France/CDI board is mostly manufacturing/finance/HR we don't want).
FAMILIES: dict[str, str] = {
    "62e55b3e447c01d4bec98a7cc60fd170": "Information Technologies & Systems",
    "62e55b3e447c01ffac909e7cc60fdd70": "Research & Development",
}

# Polite, project-naming User-Agent (playbook hard rule). The CXS JSON API is
# UA-agnostic (probing accepted this style); the 400s are request-frequency
# rate-limiting, not UA fingerprinting.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
    "Origin": HOST,
    "Referer": f"{HOST}/{SITE}",
}

PAGE_SIZE = 20
MAX_PAGES = 20  # per family — defensive cap
REQUEST_TIMEOUT = 30
# Cloudflare in front of Workday rate-limits rapid POSTs with empty-body HTTP 400.
# Go slow, and back off long when blocked (30s × 2 retries ≈ the ~60s window).
REQUEST_DELAY_SECONDS = 2.0
RETRY_BACKOFF_SECONDS = 30.0
MAX_RETRIES = 3

_JOBREQ_RE = re.compile(r"(JOBREQ_\d+)")


@dataclass
class Job:
    native_job_id: str          # requisition id, e.g. "JOBREQ_50264195"
    title: str
    location: str               # detail's location / jobRequisitionLocation
    category: str | None        # job family label
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
    """Requisition id lives in the externalPath tail, e.g.
    '/job/Paris/Lead---Data-Platform_JOBREQ_50264195-1' -> 'JOBREQ_50264195'.
    (Renault's bulletFields[0] is the location, not the id.)"""
    path = row.get("externalPath") or ""
    m = _JOBREQ_RE.search(path)
    if m:
        return m.group(1)
    raise RuntimeError(f"Renault listing row missing JOBREQ id: {row!r}")


def _post_listing(session: requests.Session, family_id: str, page: int) -> dict:
    body = {
        "appliedFacets": {
            "locationCountry": [FILTER_COUNTRY_FRANCE],
            "workerSubType": [FILTER_WORKERSUBTYPE_CDI],
            "jobFamilyGroup": [family_id],
        },
        "limit": PAGE_SIZE,
        "offset": (page - 1) * PAGE_SIZE,
        "searchText": "",
    }
    return _request_with_retry(session, "POST", LIST_URL, json_body=body)


def _fetch_detail(session: requests.Session, external_path: str) -> dict:
    url = DETAIL_URL_TEMPLATE.format(external_path=external_path)
    return _request_with_retry(session, "GET", url)


def _request_with_retry(
    session: requests.Session, method: str, url: str, json_body: dict | None = None
) -> dict:
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        # Clear cookies before each attempt: Cloudflare's __cf_bm gets tagged
        # "suspicious" after a few rapid POSTs and then every follow-up on the same
        # Session keeps 400-ing. Cookie-free callers recover after the window.
        session.cookies.clear()
        if method == "POST":
            response = session.post(url, json=json_body, timeout=REQUEST_TIMEOUT)
        else:
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
        raise RuntimeError(f"Renault detail missing jobPostingInfo: {detail!r}")

    apply_url = (info.get("externalUrl") or "").strip()
    if not apply_url:
        ext = listing_row.get("externalPath") or ""
        if ext:
            apply_url = f"{HOST}/{SITE}{ext}" if ext.startswith("/") else f"{HOST}/{SITE}/{ext}"
    if not apply_url:
        raise RuntimeError(f"Renault detail missing externalUrl: {info!r}")

    location = (
        (info.get("jobRequisitionLocation") or {}).get("descriptor")
        or info.get("location")
        or (listing_row.get("bulletFields") or [""])[0]
        or listing_row.get("locationsText")
        or ""
    ).strip()

    posted = info.get("startDate")
    posted_date = posted[:10] if isinstance(posted, str) and len(posted) >= 10 else None

    title = (info.get("title") or listing_row.get("title") or "").strip()

    # Prefer the detail's authoritative jobReqId; fall back to the path parse.
    native_id = (info.get("jobReqId") or "").strip() or _native_job_id_from_listing(listing_row)

    return Job(
        native_job_id=native_id,
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
    payload = _post_listing(session, family_id, page)
    total = int(payload.get("total") or 0)
    rows: list[dict] = list(payload.get("jobPostings") or [])

    while len(rows) < total and page < MAX_PAGES:
        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)
        payload = _post_listing(session, family_id, page)
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
        # NOTE: unlike Air Liquide we do NOT swallow a per-family HTTP error and
        # continue — a partial return would let the DB false-close the missing
        # family's still-open rows (see feedback_partial_scrape_false_close). The
        # only tolerated per-row drop is a detail failure below.
        rows = _collect_family_rows(session, family_id, family_name)
        all_rows.extend(rows)
        time.sleep(REQUEST_DELAY_SECONDS)

    # Dedup by requisition id; keep first-seen (= first family it surfaced under).
    by_id: dict[str, dict] = {}
    for r in all_rows:
        rid = _native_job_id_from_listing(r)
        by_id.setdefault(rid, r)
    print(f"  -> {len(by_id)} unique France/CDI tech jobs across families", flush=True)

    print("\nDetail phase...", flush=True)
    jobs: list[Job] = []
    for r in by_id.values():
        ext = r.get("externalPath") or ""
        if not ext:
            print(f"  skip: missing externalPath ({r.get('title')!r})", flush=True)
            continue
        try:
            detail = _fetch_detail(session, ext)
            jobs.append(_row_to_job(r, detail, r.get("_family_name") or ""))
        except requests.HTTPError as exc:
            # A detail 404/failure is the one tolerated per-row drop (the posting
            # likely just closed between listing and detail).
            print(f"  detail failed for {ext}: {exc}", flush=True)
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
