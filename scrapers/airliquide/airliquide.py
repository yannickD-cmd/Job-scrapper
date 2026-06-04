"""Air Liquide job scraper — France, Data/AI/Digital & IT roles, CDI only.

Air Liquide's global external careers board is hosted on Workday at
airliquidehr.wd3.myworkdayjobs.com/fr-FR/AirLiquideExternalCareer and exposes
the standard Workday CXS JSON API (same shape as the Rothschild scraper):

  POST /wday/cxs/airliquidehr/AirLiquideExternalCareer/jobs   (listing)
  GET  /wday/cxs/airliquidehr/AirLiquideExternalCareer<externalPath>  (detail)

Filter mapping (this scope):
  locationCountry = "France"                       (server-side facet)
  workerSubType   = "Regular - Open ended" (= CDI) (server-side facet)
  jobFamilyGroup  = one or more families, looped    (server-side facet, per-loop
                    so each row can be tagged with its family as `category`)

Scope decision (see filters.md): locked scope is the "Digital & IT" job family,
France, CDI, no title gate (category facet alone). Air Liquide is an industrial-
gas group, so "Digital & IT" is mostly IT/infra/SAP/cybersecurity; some genuine
Data/AI roles may instead sit under "Research - Engineering - Technology" (a much
larger, mostly-non-data engineering family). Widening to that family was
explicitly declined — to add it later, append its id to FAMILIES (and consider a
title gate so the non-data engineering roles don't flood in).

Workday quirks worth knowing (shared with Rothschild):
  - The endpoint is fronted by Cloudflare and rate-limits aggressively: a burst
    of POSTs from one IP starts returning empty-body HTTP 400 (errorCode
    "HTTP_400", empty message). Recovery window is ~60s. So we go slow
    (REQUEST_DELAY_SECONDS) and clear cookies + back off long on a 400.
  - Combining facets is fine *when paced*; the 400s are purely rate-limiting,
    not a structural rejection of multi-facet queries.
  - The listing's `bulletFields[0]` is the public requisition id (e.g.
    "R10092364") — used as native_job_id. The detail's `jobPostingInfo.id` is
    an internal hash kept as `identifier` only.
  - The listing does NOT echo the job family per row, so we recover `category`
    by looping one family at a time and tagging every returned row.
  - The detail's `startDate` is the canonical posted date (ISO) — used as
    posted_date; the listing only gives a relative "Posted N Days Ago".

The steady-state request count is small (a few listing pages + one detail per
matched job, all spaced >= REQUEST_DELAY_SECONDS), so unlike Safran/BNP this
runs fine from GitHub Actions.
"""
from __future__ import annotations

import html
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

TENANT = "airliquidehr"
SITE = "AirLiquideExternalCareer"
HOST = f"https://{TENANT}.wd3.myworkdayjobs.com"
LIST_URL = f"{HOST}/wday/cxs/{TENANT}/{SITE}/jobs"
DETAIL_URL_TEMPLATE = f"{HOST}/wday/cxs/{TENANT}/{SITE}{{external_path}}"

FILTER_COUNTRY_FRANCE = "54c5b6971ffb4bf0b116fe7651ec789a"
FILTER_WORKERSUBTYPE_CDI = "431d4efb2c9c01cabd63a83fa900d90c"  # "Regular - Open ended"

# jobFamilyGroup facet → category label. Looped one at a time so each row can be
# tagged with its family (the listing doesn't echo the family per row).
FAMILIES: dict[str, str] = {
    "93b630c7b5ff018c34308ce510054f46": "Digital & IT",
    # "93b630c7b5ff0105853483e510054346": "Research - Engineering - Technology",
}

# Polite, project-naming User-Agent (playbook hard rule). The CXS JSON API is
# UA-agnostic — probing accepted both this style and a browser UA; the 400s were
# purely request-frequency rate-limiting, not UA fingerprinting. So unlike the
# Cloudflare-fronted HTML scrapers, we don't need to masquerade as a browser here.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
    "Origin": HOST,
    "Referer": f"{HOST}/fr-FR/{SITE}",
}

# PAGE_SIZE/MAX_PAGES are sized for the steady-state volume (~6 France Digital&IT
# CDI rows = a single page). The cap of 20 pages × PAGE_SIZE is a defensive
# backstop; if a future scope change pushes this past a few pages, revisit the
# detail-phase budget (each detail call costs REQUEST_DELAY_SECONDS).
PAGE_SIZE = 20
MAX_PAGES = 20  # per family — defensive cap
REQUEST_TIMEOUT = 30
# Cloudflare in front of Workday is aggressive: rapid POSTs from one IP start
# returning empty-body HTTP 400. Go slow, and back off long when blocked.
# 30s backoff × 2 retries = 60s, matching the observed ~60s recovery window.
REQUEST_DELAY_SECONDS = 2.0
RETRY_BACKOFF_SECONDS = 30.0
MAX_RETRIES = 3


@dataclass
class Job:
    native_job_id: str          # requisition id, e.g. "R10092364"
    title: str
    location: str               # detail's jobRequisitionLocation / listing locationsText
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
    """Requisition id lives in bulletFields[0] (e.g. 'R10092364')."""
    bullets = row.get("bulletFields") or []
    if bullets and isinstance(bullets[0], str) and bullets[0].strip():
        return bullets[0].strip()
    # Fallback: tail segment of externalPath, e.g. "..._R10092364".
    path = row.get("externalPath") or ""
    m = re.search(r"_(R\d{4,})$", path)
    if m:
        return m.group(1)
    raise RuntimeError(f"Air Liquide listing row missing requisition id: {row!r}")


def _fetch_listing(session: requests.Session, family_id: str, page: int) -> dict:
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
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        # Clear cookies before each attempt: Cloudflare's __cf_bm and Workday's
        # session cookies get tagged "suspicious" after a few rapid POSTs and
        # then every follow-up on the same Session keeps 400-ing. Cookie-free
        # callers recover after the rate-limit window.
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
        # The detail shape (jobPostingInfo.*) is the one thing not verified live
        # against this tenant (probing got rate-limited first). If it's absent,
        # fail loudly here so the caller can skip + log rather than silently
        # emitting an empty-title row.
        raise RuntimeError(f"Air Liquide detail missing jobPostingInfo: {detail!r}")

    apply_url = (info.get("externalUrl") or "").strip()
    if not apply_url:
        # Fallback only — externalUrl is present in practice. The canonical
        # Workday externalUrl is locale-less (verified: .../AirLiquideExternalCareer
        # /job/...), so we synthesise without a /fr-FR/ segment to match it.
        ext = listing_row.get("externalPath") or ""
        if ext:
            apply_url = f"{HOST}/{SITE}{ext}" if ext.startswith("/") else f"{HOST}/{SITE}/{ext}"
    if not apply_url:
        raise RuntimeError(f"Air Liquide detail missing externalUrl: {info!r}")

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
        try:
            rows = _collect_family_rows(session, family_id, family_name)
            all_rows.extend(rows)
        except requests.HTTPError as exc:
            # One family failing shouldn't kill the run — log and continue.
            print(f"  {family_name}: HTTP error {exc}; skipping", flush=True)
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
            print(f"  detail failed for {ext}: {exc}", flush=True)
            continue
        try:
            jobs.append(_row_to_job(r, detail, r.get("_family_name") or ""))
        except RuntimeError as exc:
            # Structural mismatch (e.g. missing jobPostingInfo/externalUrl) —
            # skip this one row and keep going rather than failing the run.
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
