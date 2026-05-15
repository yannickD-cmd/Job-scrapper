"""Rothschild & Co job scraper — France, Data & AI roles (experienced board).

Rothschild & Co's experienced-professionals board is hosted on Workday at
rothschildandco.wd3.myworkdayjobs.com/RothschildAndCo_Lateral. Internships,
apprenticeships and graduate roles live on a separate Workday board and are
intentionally excluded here (the WorkerSubType filter below drops Intern
(Trainee) and Apprentice (Fixed Term)).

Workday exposes a public JSON API:
  POST /wday/cxs/rothschildandco/RothschildAndCo_Lateral/jobs    (listing)
  GET  /wday/cxs/rothschildandco/RothschildAndCo_Lateral/job<externalPath>
                                                                 (detail)

Filter mapping (this scope):
  locationCountry  = "France"
  WorkerSubType    = "Permanent" + "Fixed Term"   (CDI + CDD, no interns)
  CF_LRV_Division_For_Job_Posting_Anchor_Extended = one per Division, looped

Workday quirks worth knowing:
  - `WorkerSubType` is PascalCase in the request payload even though the
    facets section of the response echoes it as camelCase `workerSubType`.
    The lowercase form returns HTTP 400 silently.
  - `X-Calypso-Selected-Locale: en-US` plus a `/en-US/RothschildAndCo_Lateral`
    Referer are required for filtered queries; without them the server
    returns HTTP 400 even on otherwise valid bodies.
  - The Division field is NOT returned by either the listing or the detail
    endpoint. We recover it by querying once per Division and tagging
    every returned row with that Division's name.
  - The listing's `bulletFields[0]` is the public JR id (e.g. "JR015746")
    — used as native_job_id. The detail endpoint's `jobPostingInfo.id` is
    an internal hash kept as `identifier` only.
  - The detail's `startDate` matches the "Posted N Days Ago" surfaced by
    the listing (verified: AI Solution Architect, startDate 2026-05-05,
    listing "Posted 10 Days Ago", scraped on 2026-05-15). So we use it
    as posted_date.

Data scope: we run the per-Division loop across ALL divisions (data roles
land in Digital, Finance, Wealth & Asset Management, Group Change &
Operations, etc. — there's no single "Data" division) and then filter
titles client-side via DATA_TITLE_RE. Edit that regex to widen / narrow.
"""
from __future__ import annotations

import html
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

TENANT = "rothschildandco"
SITE = "RothschildAndCo_Lateral"
HOST = f"https://{TENANT}.wd3.myworkdayjobs.com"
LIST_URL = f"{HOST}/wday/cxs/{TENANT}/{SITE}/jobs"
DETAIL_URL_TEMPLATE = f"{HOST}/wday/cxs/{TENANT}/{SITE}{{external_path}}"

FILTER_COUNTRY_FRANCE = "54c5b6971ffb4bf0b116fe7651ec789a"
FILTER_WORKERSUBTYPE_PERMANENT = "3e51e8850431019e18f1760c9c70c00e"
FILTER_WORKERSUBTYPE_FIXED_TERM = "3e51e8850431010bfa3c7c0c9c70c10e"

# Division facet (CF_LRV_Division_For_Job_Posting_Anchor_Extended) → category.
# Looped one at a time so we can tag each job with its Division name.
DIVISIONS: dict[str, str] = {
    "717bbd35acd60144bc1859cca373a01f": "Wealth Management",
    "9e3a5eceea5b1000f386f26a9b860000": "Wealth and Asset Management",
    "717bbd35acd601a0c21b47cca3739a1f": "Global Advisory",
    "717bbd35acd6012dc2d704cca373861f": "Finance",
    "c65962ac34ca01c327d3d4e09e01316e": "Digital",
    "717bbd35acd601c724711dcca3738e1f": "Legal and Compliance",
    "9e3a5eceea5b1000ba2acb42b5e80000": "Five Arrows",
    "704e961bdb001001de796ac82fcd0000": "Group Change & Operations Management",
    "717bbd35acd601d2391c34cca373941f": "Risk",
    "428726bb1de701977cb406e3f80009bd": "R&Co4Generations",
    "717bbd35acd601544a7d17cca3738c1f": "Internal Audit",
    "717bbd35acd601ce3ea50bcca373881f": "Human Resources",
    "428726bb1de7012be64ac1cff800a8bc": "Corporate Communications",
}

WORKERSUBTYPE_TO_LABEL: dict[str, str] = {
    FILTER_WORKERSUBTYPE_PERMANENT: "CDI",
    FILTER_WORKERSUBTYPE_FIXED_TERM: "CDD",
}

# Title patterns that mark a role as data-relevant.
# - Multi-letter tokens use case-insensitive matching with word boundaries.
# - Two-letter acronyms (AI / IA / ML / BI) are case-sensitive on purpose
#   to avoid false positives like "Aix", "Bid", "Bilan", etc.
DATA_TITLE_RE = re.compile(
    r"\b("
    r"data|"
    r"analytics?|analyst|analyste|"
    r"scientist|"
    r"machine\s+learning|"
    r"intelligence\s+artificielle|"
    r"artificial\s+intelligence"
    r")\b",
    re.IGNORECASE,
)
DATA_TITLE_ACRONYM_RE = re.compile(r"\b(AI|IA|ML|BI)\b")  # case-sensitive

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": HOST,
    "Referer": f"{HOST}/en-US/{SITE}",
    "X-Calypso-Selected-Locale": "en-US",
    "From": "yannickarieldossa@gmail.com",
}

PAGE_SIZE = 50
MAX_PAGES = 20  # per (division × employment-type) — defensive cap
REQUEST_TIMEOUT = 30
# Workday is fronted by Cloudflare and is aggressive: ~10 POSTs within 2s
# from the same IP starts returning empty-body HTTP 400. Observed recovery
# window is ~60s. So we go slow and we back off long when blocked.
REQUEST_DELAY_SECONDS = 2.0
RETRY_BACKOFF_SECONDS = 30.0
MAX_RETRIES = 3


@dataclass
class Job:
    native_job_id: str         # JR id, e.g. "JR015746"
    title: str
    location: str              # detail's jobRequisitionLocation.descriptor
    category: str | None       # Division descriptor
    apply_url: str             # detail's externalUrl
    employment_type: str       # "CDI" or "CDD"
    description: str | None = None
    posted_date: str | None = None   # YYYY-MM-DD from detail's startDate
    identifier: str | None = None    # detail's jobPostingInfo.id (internal hash)
    raw_payload: dict | None = None


def _title_is_data_role(title: str) -> bool:
    return bool(DATA_TITLE_RE.search(title) or DATA_TITLE_ACRONYM_RE.search(title))


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
    # Fallback: tail segment of externalPath, e.g. "..._JR015746".
    path = row.get("externalPath") or ""
    if "_JR" in path:
        return "JR" + path.rsplit("_JR", 1)[1]
    raise RuntimeError(f"Rothschild listing row missing JR id: {row!r}")


def _fetch_listing(
    session: requests.Session,
    division_id: str,
    page: int,
) -> dict:
    body = {
        "appliedFacets": {
            "locationCountry": [FILTER_COUNTRY_FRANCE],
            "WorkerSubType": [
                FILTER_WORKERSUBTYPE_PERMANENT,
                FILTER_WORKERSUBTYPE_FIXED_TERM,
            ],
            "CF_LRV_Division_For_Job_Posting_Anchor_Extended": [division_id],
        },
        "limit": PAGE_SIZE,
        "offset": (page - 1) * PAGE_SIZE,
        "searchText": "",
    }
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        response = session.post(LIST_URL, json=body, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            return response.json()
        # Workday's CF front sometimes returns 400 with empty body on bursts
        # but the same request succeeds after a backoff. Retry these.
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


def _infer_employment_type(detail_info: dict, listing_row: dict) -> str:
    """Derive CDI / CDD from the detail's `timeType` is unreliable (that's
    Full/Part time). The detail endpoint omits WorkerSubType outright, so
    we rely on which listing filter the row came through.

    Caller stamps `_workersubtype_label` onto the listing dict before calling.
    """
    label = listing_row.get("_workersubtype_label")
    if label:
        return label
    # Defensive fallback: time-type as a last resort.
    return (detail_info.get("timeType") or "").strip() or "Permanent"


def _row_to_job(
    listing_row: dict,
    detail: dict,
    division_name: str,
) -> Job:
    info = (detail.get("jobPostingInfo") or {})

    apply_url = (info.get("externalUrl") or "").strip()
    if not apply_url:
        # Synthesise from externalPath as a fallback.
        ext = listing_row.get("externalPath") or info.get("jobPostingId") or ""
        if ext:
            apply_url = f"{HOST}/{SITE}{ext}" if ext.startswith("/") else f"{HOST}/{SITE}/{ext}"
    if not apply_url:
        raise RuntimeError(f"Rothschild detail missing externalUrl: {info!r}")

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
        category=division_name,
        apply_url=apply_url,
        employment_type=_infer_employment_type(info, listing_row),
        description=_clean_description(info.get("jobDescription")),
        posted_date=posted_date,
        identifier=info.get("id"),
        raw_payload={"listing": listing_row, "detail": info},
    )


def _collect_division_rows(
    session: requests.Session,
    division_id: str,
    division_name: str,
) -> list[dict]:
    """Fetch the full France+(CDI|CDD) listing for one Division, with
    paging. Stamps each row with `_division_name` and `_workersubtype_label`
    for downstream tagging (the listing doesn't echo these back)."""
    page = 1
    payload = _fetch_listing(session, division_id, page)
    total = int(payload.get("total") or 0)
    rows: list[dict] = list(payload.get("jobPostings") or [])

    while len(rows) < total and page < MAX_PAGES:
        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)
        payload = _fetch_listing(session, division_id, page)
        new = payload.get("jobPostings") or []
        if not new:
            break
        rows.extend(new)

    for r in rows:
        r["_division_name"] = division_name
        # We can't tell from the listing alone whether a row is CDI vs CDD
        # (the filter passed both). Best-effort: title heuristic, else CDI.
        # Detail endpoint doesn't help either, so this stays approximate.
        t = (r.get("title") or "").lower()
        if " cdd" in f" {t} " or "fixed term" in t or "fixed-term" in t:
            r["_workersubtype_label"] = "CDD"
        else:
            r["_workersubtype_label"] = "CDI"

    print(
        f"  {division_name}: {len(rows)}/{total} rows",
        flush=True,
    )
    return rows


def _warmup(session: requests.Session) -> None:
    """Workday's CF front blocks fresh sessions that go straight to the JSON
    API. Hit the careers HTML once to bank cookies (__cf_bm, PLAY_SESSION,
    wd-browser-id) before the first POST."""
    try:
        session.get(
            f"{HOST}/en-US/{SITE}",
            headers={"Accept": "text/html,application/xhtml+xml"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        print(f"  warmup GET failed (non-fatal): {exc}", flush=True)


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)
    _warmup(session)
    time.sleep(REQUEST_DELAY_SECONDS)

    started = time.time()
    print("Fetch phase (per-Division listing)...", flush=True)

    all_rows: list[dict] = []
    for division_id, division_name in DIVISIONS.items():
        try:
            rows = _collect_division_rows(session, division_id, division_name)
            all_rows.extend(rows)
        except requests.HTTPError as exc:
            # A single division failing shouldn't kill the run — log and continue.
            print(f"  {division_name}: HTTP error {exc}; skipping", flush=True)
        # Sleep on both success and failure so a 400-storm can't fire 13
        # requests in <2s and trip Cloudflare further.
        time.sleep(REQUEST_DELAY_SECONDS)

    # A job belonging to multiple divisions would in theory appear twice; dedup
    # by JR id, keep the first-seen (= the first division it surfaced under).
    by_jr: dict[str, dict] = {}
    for r in all_rows:
        jr = _native_job_id_from_listing(r)
        by_jr.setdefault(jr, r)

    # Title filter for data roles.
    data_rows = [r for r in by_jr.values() if _title_is_data_role(r.get("title") or "")]
    print(
        f"  -> {len(by_jr)} France perm/CDD jobs, "
        f"{len(data_rows)} match data title filter",
        flush=True,
    )

    print("\nDetail phase...", flush=True)
    jobs: list[Job] = []
    for r in data_rows:
        ext = r.get("externalPath") or ""
        if not ext:
            print(f"  skip: missing externalPath ({r.get('title')!r})", flush=True)
            continue
        try:
            detail = _fetch_detail(session, ext)
        except requests.HTTPError as exc:
            print(f"  detail failed for {ext}: {exc}", flush=True)
            continue
        jobs.append(_row_to_job(r, detail, r.get("_division_name") or ""))
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
