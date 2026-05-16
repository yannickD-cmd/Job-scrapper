"""Airbus job scraper — France, Regular (CDI), Data / AI / Software / Digital.

Airbus's careers board is Workday at ag.wd3.myworkdayjobs.com/Airbus.

Endpoints:
  POST /wday/cxs/ag/Airbus/jobs           (listing, JSON)
  GET  /wday/cxs/ag/Airbus/job<externalPath>  (detail, JSON)

Filter mapping (this scope):
  locationCountry = "France"
  workerSubType   = "Regular"       (CDI; Apprentice/Trainee deliberately dropped)
  jobFamily       = looped one-by-one across:
                      - Digital               <JF-IM-DI>   ~48 jobs
                      - Software Engineering  <JF-EN-EK>   ~15 jobs
                      - Computing & Comm/Info & Data Proc. <JF-EN-EB>  ~4 jobs

We loop over jobFamily (rather than passing all three IDs at once) so we
can tag each row with its Family name as `category` — the detail endpoint
omits jobFamily entirely.

Workday quirks worth knowing:
  - Airbus accepts lowercase facet keys (locationCountry, workerSubType,
    jobFamily). The Rothschild tenant only accepts PascalCase WorkerSubType
    — these casing rules are per-tenant Workday config.
  - `X-Calypso-Selected-Locale: en-US` plus a /en-US/Airbus Referer are
    required; without them filtered POSTs can return HTTP 400 with no body.
  - jobFamily / jobFamilyGroup are NULL on the detail endpoint. We tag
    from the per-family listing loop.
  - The listing's `bulletFields[0]` is the public JR id (e.g. "JR10413396")
    and matches the detail's `jobReqId` — used as native_job_id.
  - The detail's `startDate` is the canonical posted_date (ISO YYYY-MM-DD,
    matches "Posted N Days Ago" surfaced on the listing).
  - The detail's `jobRequisitionLocation.descriptor` is more specific than
    the listing's `locationsText` (e.g. "Blagnac (Airbus Protect)" vs
    "Toulouse Area") — we prefer it.

Cookies: Workday-on-Cloudflare can rate-limit a session after a burst of
filtered POSTs. We clear cookies on each request as defensive insurance
(same pattern as the Rothschild scraper).
"""
from __future__ import annotations

import html
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

TENANT = "ag"
SITE = "Airbus"
HOST = f"https://{TENANT}.wd3.myworkdayjobs.com"
LIST_URL = f"{HOST}/wday/cxs/{TENANT}/{SITE}/jobs"
DETAIL_URL_TEMPLATE = f"{HOST}/wday/cxs/{TENANT}/{SITE}{{external_path}}"

FILTER_COUNTRY_FRANCE = "54c5b6971ffb4bf0b116fe7651ec789a"
FILTER_WORKERSUBTYPE_REGULAR = "f5811cef9cb501a69768a71d470a6d15"

# jobFamily facet → category label. Looped one at a time so we can tag
# each posting with its family (the detail endpoint returns jobFamily=None).
JOB_FAMILIES: dict[str, str] = {
    "f5811cef9cb501602cf214e9540adaec": "Digital",
    "f5811cef9cb5018f6f641ee9540a16ed": "Software Engineering",
    "f5811cef9cb501caed5212e9540ac4ec": "Computing & Information & Data Processing",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": HOST,
    "Referer": f"{HOST}/en-US/{SITE}",
    "X-Calypso-Selected-Locale": "en-US",
}

PAGE_SIZE = 20  # Workday's default; larger pages occasionally 400 on this tenant
MAX_PAGES = 20  # per jobFamily — defensive cap
REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.5
RETRY_BACKOFF_SECONDS = 30.0
MAX_RETRIES = 3


@dataclass
class Job:
    native_job_id: str         # JR id, e.g. "JR10413396"
    title: str
    location: str
    category: str              # jobFamily descriptor
    apply_url: str             # detail's externalUrl
    employment_type: str       # always "Regular" (CDI) in this scope
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
    """JR id lives in bulletFields[0] on the listing rows."""
    bullets = row.get("bulletFields") or []
    if bullets and isinstance(bullets[0], str) and bullets[0].strip():
        return bullets[0].strip()
    # Fallback: tail segment of externalPath, e.g. ".._JR10413396".
    path = row.get("externalPath") or ""
    if "_JR" in path:
        return "JR" + path.rsplit("_JR", 1)[1].split("-")[0]
    raise RuntimeError(f"Airbus listing row missing JR id: {row!r}")


def _fetch_listing(
    session: requests.Session,
    job_family_id: str,
    page: int,
) -> dict:
    body = {
        "appliedFacets": {
            "locationCountry": [FILTER_COUNTRY_FRANCE],
            "workerSubType": [FILTER_WORKERSUBTYPE_REGULAR],
            "jobFamily": [job_family_id],
        },
        "limit": PAGE_SIZE,
        "offset": (page - 1) * PAGE_SIZE,
        "searchText": "",
    }
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
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

    apply_url = (info.get("externalUrl") or "").strip()
    if not apply_url:
        ext = listing_row.get("externalPath") or ""
        if ext:
            apply_url = f"{HOST}/{SITE}{ext}" if ext.startswith("/") else f"{HOST}/{SITE}/{ext}"
    if not apply_url:
        raise RuntimeError(f"Airbus detail missing externalUrl: {info!r}")

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
        employment_type="Regular",
        description=_clean_description(info.get("jobDescription")),
        posted_date=posted_date,
        identifier=info.get("id"),
        raw_payload={"listing": listing_row, "detail": info},
    )


def _collect_family_rows(
    session: requests.Session,
    family_id: str,
    family_name: str,
) -> list[dict]:
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
    print("Fetch phase (per-jobFamily listing)...", flush=True)

    all_rows: list[dict] = []
    for family_id, family_name in JOB_FAMILIES.items():
        try:
            rows = _collect_family_rows(session, family_id, family_name)
            all_rows.extend(rows)
        except requests.HTTPError as exc:
            print(f"  {family_name}: HTTP error {exc}; skipping", flush=True)
        time.sleep(REQUEST_DELAY_SECONDS)

    # A job tagged with two families would appear twice; dedup by JR id and
    # keep the first-seen tagging (= first family it surfaced under).
    by_jr: dict[str, dict] = {}
    for r in all_rows:
        try:
            jr = _native_job_id_from_listing(r)
        except RuntimeError as exc:
            print(f"  skip: {exc}", flush=True)
            continue
        by_jr.setdefault(jr, r)

    print(f"  -> {len(by_jr)} unique France/Regular jobs across families", flush=True)

    print("\nDetail phase...", flush=True)
    jobs: list[Job] = []
    for r in by_jr.values():
        ext = r.get("externalPath") or ""
        if not ext:
            print(f"  skip: missing externalPath ({r.get('title')!r})", flush=True)
            continue
        try:
            detail = _fetch_detail(session, ext)
        except requests.HTTPError as exc:
            print(f"  detail failed for {ext}: {exc}", flush=True)
            continue
        jobs.append(_row_to_job(r, detail, r.get("_family_name") or ""))
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
