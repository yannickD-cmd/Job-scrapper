"""Pernod Ricard job scraper — France, Tech & IT families, CDI only.

Pernod Ricard's single global board is hosted on Workday at
pernodricard.wd3.myworkdayjobs.com/pernod-ricard. Workday exposes the usual
public JSON CXS API:

  GET  /wday/cxs/pernodricard/pernod-ricard            (seeds session cookies)
  POST /wday/cxs/pernodricard/pernod-ricard/jobs       (listing, faceted)
  GET  /wday/cxs/pernodricard/pernod-ricard<externalPath>   (detail)

Scope (locked with the user):
  - Country     : France only      (no country facet exists — filtered
                                     client-side off the listing bulletFields,
                                     which carry the country name)
  - Job families: "Tech" + "Information Technology"   (jobFamilyGroup facet)
  - Job type    : CDI only          (workerSubType = "Regular")
  - Title filter: none — keep every in-family role

Workday quirks worth knowing (this tenant):
  - Faceted POSTs return an empty-body HTTP 400 (JSON `errorCode: HTTP_400`)
    UNLESS the caller first does a GET on the CXS root to seed the
    PLAY_SESSION / wd-browser-id / __cf_bm cookies. An *empty*-facet POST works
    cookie-free, but any `appliedFacets` payload needs the session. So we seed
    a Session once up front and reuse its cookies on every request.
  - The *faceted* POST endpoint is metered by a slow-refilling token bucket
    (the empty-facet POST and the detail GET are NOT — they stay 200 even while
    faceted POSTs 400). Once the bucket is drained by a burst, a single faceted
    POST sips the last token, then everything 400s again until it refills (tens
    of minutes after heavy abuse, escalating with repeat offence). To stay well
    clear of it we make exactly ONE faceted POST per run: both job families are
    queried together in a single `jobFamilyGroup: [Tech, IT]` payload. On a 400
    we re-seed the session and back off — same shape as Rothschild.
  - The listing rows don't carry the job family. The two in-scope families are
    disjoint (Tech ~34 + IT ~4 == 38 combined, no overlap), so the combined
    query can't mis-count; we just can't tell Tech from IT per row, hence the
    coarse `category = "Tech / IT"` for every returned role (both are Pernod
    Ricard's tech families — the distinction is low-value, IT is ~4 globally).
  - native_job_id: the detail's `jobReqId` is the clean public id ("JR-053956").
    The listing's bulletFields also carry it (last "JR-" element); we dedup on
    that before fetching details, then trust the detail's jobReqId.
  - Country is reliable on the detail (`country.descriptor` == "France"); the
    France country id is the Workday-global 54c5b6971ffb4bf0b116fe7651ec789a.
  - `startDate` is already ISO YYYY-MM-DD and matches the "Posted N Days Ago"
    surfaced by the listing — used directly as posted_date.
"""
from __future__ import annotations

import html
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

TENANT = "pernodricard"
SITE = "pernod-ricard"
HOST = f"https://{TENANT}.wd3.myworkdayjobs.com"
SESSION_URL = f"{HOST}/wday/cxs/{TENANT}/{SITE}"
LIST_URL = f"{HOST}/wday/cxs/{TENANT}/{SITE}/jobs"
DETAIL_URL_TEMPLATE = f"{HOST}/wday/cxs/{TENANT}/{SITE}{{external_path}}"

# jobFamilyGroup facet ids in scope — both of Pernod Ricard's tech families.
# Queried TOGETHER in one faceted POST (see the token-bucket note above); the
# two are disjoint so the union can't double-count. We can't tell which family
# a given row came from, so every in-scope role gets the coarse CATEGORY label.
FAMILY_IDS: list[str] = [
    "5c4276c36b5a1001e317a08d36940000",  # Tech (~34 global)
    "371688745b57014fe9c19df9ef17a12f",  # Information Technology (~4 global)
]
CATEGORY = "Tech / IT"

# workerSubType facet — "Regular" == permanent (CDI). The Regular facet is the
# only employment type in scope, so every returned row is a CDI by construction.
WORKERSUBTYPE_REGULAR = "371688745b5701d8d14db11fa6174024"

# Country lives in the listing's bulletFields as a plain name, never as a facet.
FRANCE_COUNTRY_NAME = "France"

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
}

PAGE_SIZE = 50
MAX_PAGES = 10  # defensive cap (the combined Tech+IT result is tiny)
REQUEST_TIMEOUT = 30
# Workday/Cloudflare here is rate-sensitive: a burst of faceted POSTs drains a
# token bucket and returns empty-body 400s. Go slow; back off long when blocked.
REQUEST_DELAY_SECONDS = 1.5      # between detail GETs (not bucket-metered)
FACET_DELAY_SECONDS = 5.0        # between the (few) faceted listing POSTs
RETRY_BACKOFF_SECONDS = 45.0
MAX_RETRIES = 3

JR_RE = re.compile(r"\bJR[-\d]+\b")


@dataclass
class Job:
    native_job_id: str          # jobReqId, e.g. "JR-053956"
    title: str
    location: str               # detail's location (clean city)
    category: str | None        # job family ("Tech" / "Information Technology")
    apply_url: str              # detail's externalUrl
    employment_type: str        # always "CDI" (Regular facet)
    description: str | None = None
    posted_date: str | None = None   # YYYY-MM-DD from detail's startDate
    identifier: str | None = None    # detail's jobPostingInfo.id (internal hash)
    raw_payload: dict | None = None


def _clean_description(content: str | None) -> str | None:
    if not content:
        return None
    text = BeautifulSoup(html.unescape(content), "html.parser").get_text(" ", strip=True)
    return text or None


def _native_job_id_from_listing(row: dict) -> str | None:
    """The JR id is the last "JR-…" token in bulletFields, e.g. "JR-053956"."""
    for field in reversed(row.get("bulletFields") or []):
        if isinstance(field, str) and JR_RE.fullmatch(field.strip()):
            return field.strip()
    # Fallback: tail of externalPath, e.g. "..._JR-053956" or "..._JR-034242-1".
    path = row.get("externalPath") or ""
    m = re.search(r"_(JR-\d+)", path)
    return m.group(1) if m else None


def _row_is_france(row: dict) -> bool:
    return any(
        isinstance(f, str) and f.strip() == FRANCE_COUNTRY_NAME
        for f in (row.get("bulletFields") or [])
    )


def _seed_session() -> requests.Session:
    """A faceted POST 400s unless the caller already holds the CXS session
    cookies. A GET on the CXS root sets them; reuse the same Session after."""
    session = requests.Session()
    session.headers.update(HEADERS)
    session.get(SESSION_URL, timeout=REQUEST_TIMEOUT)
    return session


def _is_error_payload(data: object) -> bool:
    return isinstance(data, dict) and "errorCode" in data


def _post_listing(session: requests.Session, offset: int) -> dict:
    body = {
        "appliedFacets": {
            "jobFamilyGroup": FAMILY_IDS,            # Tech + IT, one query
            "workerSubType": [WORKERSUBTYPE_REGULAR],
        },
        "limit": PAGE_SIZE,
        "offset": offset,
        "searchText": "",
    }
    last_err: str | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        response = session.post(LIST_URL, json=body, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            if not _is_error_payload(data):
                return data
            last_err = f"error payload {data.get('errorCode')}"
        else:
            last_err = f"HTTP {response.status_code}: {response.text[:120]}"
        # Empty-body 400 == rate limit. Re-seed the session (fresh cookies) and
        # wait out the recovery window before retrying.
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS)
            session.cookies.clear()
            session.get(SESSION_URL, timeout=REQUEST_TIMEOUT)
    raise requests.HTTPError(f"listing failed: {last_err}")


def _get_detail(session: requests.Session, external_path: str) -> dict:
    url = DETAIL_URL_TEMPLATE.format(external_path=external_path)
    last_err: str | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            if not _is_error_payload(data):
                return data
            last_err = f"error payload {data.get('errorCode')}"
        elif response.status_code == 404:
            # Terminal, not transient: the job was removed between the listing
            # snapshot and this fetch. Don't waste retries — surface the 404 so
            # the caller can drop it (closing it is correct; it's genuinely gone).
            raise requests.HTTPError(f"detail 404 for {external_path}", response=response)
        else:
            last_err = f"HTTP {response.status_code}: {response.text[:120]}"
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS)
            session.cookies.clear()
            session.get(SESSION_URL, timeout=REQUEST_TIMEOUT)
    raise requests.HTTPError(f"detail failed for {external_path}: {last_err}")


def _collect_listing(session: requests.Session) -> list[dict]:
    """Fetch the full France-filtered Tech+IT/CDI listing in one combined query
    (paginating only if it ever exceeds PAGE_SIZE, which it won't today)."""
    offset = 0
    page = 0
    payload = _post_listing(session, offset)
    total = int(payload.get("total") or 0)
    raw: list[dict] = list(payload.get("jobPostings") or [])

    while len(raw) < total and page < MAX_PAGES:
        page += 1
        offset += PAGE_SIZE
        time.sleep(FACET_DELAY_SECONDS)  # paging is another faceted POST
        payload = _post_listing(session, offset)
        new = payload.get("jobPostings") or []
        if not new:
            break
        raw.extend(new)

    france = [r for r in raw if _row_is_france(r)]
    print(f"  {len(raw)}/{total} Tech+IT CDI rows, {len(france)} in France", flush=True)
    return france


def _row_to_job(listing_row: dict, detail: dict) -> Job:
    info = detail.get("jobPostingInfo") or {}

    apply_url = (info.get("externalUrl") or "").strip()
    if not apply_url:
        ext = listing_row.get("externalPath") or ""
        apply_url = f"{HOST}/{SITE}{ext}" if ext else ""
    if not apply_url:
        raise RuntimeError(f"Pernod Ricard detail missing externalUrl: {info!r}")

    native_job_id = (
        (info.get("jobReqId") or "").strip()
        or _native_job_id_from_listing(listing_row)
        or ""
    )
    if not native_job_id:
        raise RuntimeError(f"Pernod Ricard row missing JR id: {listing_row!r}")

    location = (
        (info.get("location") or "").strip()
        or (info.get("jobRequisitionLocation") or {}).get("descriptor")
        or listing_row.get("locationsText")
        or ""
    ).strip()

    posted = info.get("startDate")
    posted_date = posted[:10] if isinstance(posted, str) and len(posted) >= 10 else None

    title = (info.get("title") or listing_row.get("title") or "").strip()

    return Job(
        native_job_id=native_job_id,
        title=title,
        location=location,
        category=CATEGORY,
        apply_url=apply_url,
        employment_type="CDI",
        description=_clean_description(info.get("jobDescription")),
        posted_date=posted_date,
        identifier=info.get("id"),
        raw_payload={"listing": listing_row, "detail": info},
    )


def scrape() -> list[dict]:
    session = _seed_session()

    started = time.time()
    print("Listing phase (combined Tech+IT, France-filtered)...", flush=True)

    # One faceted POST. Deliberately NOT wrapped in try/except: if it fails after
    # retries we must NOT return a partial/empty-by-error list — a non-empty
    # partial would make db.persist_run_results close every row not in it, and an
    # error-empty would only be saved by the empty-return guard. Letting the
    # HTTPError propagate makes run.py log a failed run and close nothing; the
    # next scheduled run (4×/day) recovers.
    rows = _collect_listing(session)
    by_jr: dict[str, dict] = {}
    for r in rows:
        jr = _native_job_id_from_listing(r)
        if jr:
            by_jr.setdefault(jr, r)  # dedup defensively (the union has no dups)

    print(f"  -> {len(by_jr)} unique France Tech/IT CDI roles", flush=True)

    print("\nDetail phase...", flush=True)
    jobs: list[Job] = []
    for jr, row in by_jr.items():
        ext = row.get("externalPath") or ""
        if not ext:
            print(f"  skip {jr}: missing externalPath", flush=True)
            continue
        try:
            detail = _get_detail(session, ext)
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            if status == 404:
                # Removed since the listing snapshot — drop it. It will close,
                # which is correct: it's genuinely gone.
                print(f"  {jr}: detail 404, dropping (job removed)", flush=True)
                continue
            # Any other detail failure means the result would be incomplete;
            # don't return a partial list (it would false-close the dropped
            # rows). Abort so run.py logs a failed run and closes nothing.
            raise
        jobs.append(_row_to_job(row, detail))
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
