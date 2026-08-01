"""Kering Group job scraper — France, Tech & Digital, Regular only.

Kering publishes the same requisitions on TWO independent surfaces, and
neither is a superset of the other, so this scraper reads BOTH and unions
them on the requisition number:

  A. Corporate feed — https://www.kering.com/en/talent/job-offers/
     A Next.js front-end that ships the full listing inside a
     `__NEXT_DATA__` <script> on every paginated page. No public JSON API,
     and the filter UI (country, brand, job family) is purely client-side —
     query-string filters are silently ignored by the server, so we walk
     every page and filter in Python. Carries `jobFamilyId` /
     `workerSubType` / the full description inline.

  B. ATS of record — https://careers.kering.com (Eightfold "PCSX")
     `GET /api/pcsx/search` is open (no key, no browser fingerprinting) and
     pages 10 at a time; `GET /api/pcsx/position_details` returns the
     description plus the Workday-side custom fields
     (`efcustomTextJobFamily`, `efcustomTextWorkerSubtype`, house).
     NB: the older `/api/apply/v2/jobs` endpoint on the same host is
     closed ("Not authorized for PCSX") — do not go back to it.

Why both: the corporate feed lags the ATS by hours (a req published on
careers.kering.com only reaches www.kering.com on the next feed refresh),
while the ATS index carries fewer rows overall (~1030 vs ~1377 — it drops
some houses/regions the corporate feed still lists). Measured 2026-07-31:
976 reqs on both, 389 corporate-only, 54 ATS-only — and the two freshest
France/Tech reqs of the day (R169288 AI Engineer, R169232 Data Engineer)
existed ONLY on the ATS. Reading a single surface loses jobs either way.

Identity: `native_job_id` is the requisition number (R-prefixed, e.g.
R169288), the only id both surfaces share. It is also more stable than the
corporate feed's internal `jobId`, which is re-issued when a req is edited
— that churn used to close the row and re-insert it as a false NEW.

Scope is applied per surface and unioned (a job kept by either surface is
kept), so a taxonomy disagreement can only ever include, never drop:
  corporate : locationCountry / jobFamilyId / workerSubType
  ATS       : locations + department (listing) and
              efcustomTextWorkerSubtype (detail)
Both taxonomies agree 1:1 on France rows today
(`Information_&_Digital_Technologies` <-> `Tech & Digital`).

To change scope, edit COUNTRIES_IN_SCOPE / JOB_FAMILY_IDS_IN_SCOPE /
PCSX_DEPARTMENTS_IN_SCOPE / WORKER_SUBTYPES_IN_SCOPE.
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup

# --- surface A: corporate Next.js feed ------------------------------------
LISTING_URL = "https://www.kering.com/en/talent/job-offers/?page={page}"

# --- surface B: Eightfold PCSX (the ATS of record) ------------------------
PCSX_SEARCH_URL = "https://careers.kering.com/api/pcsx/search"
PCSX_DETAIL_URL = "https://careers.kering.com/api/pcsx/position_details"
PCSX_DOMAIN = "kering.com"
# Page size is fixed server-side at 10 — `num` is accepted and ignored.
PCSX_PAGE_SIZE = 10
# `timestamp` (newest first) is the only stable ordering; the default `hot`
# re-ranks between calls, which is not safe to page through.
PCSX_SORT_BY = "timestamp"

# Both surfaces publish the requisition number; the apply URL is the same
# shape for either (the corporate feed's `url` already points at the ATS).
APPLY_URL_TEMPLATE = "https://careers.kering.com/careers/{req_id}"

COUNTRIES_IN_SCOPE: set[str] = {"France"}
# Corporate feed: filter on the stable filter id, not the human-facing label.
JOB_FAMILY_IDS_IN_SCOPE: set[str] = {"Information_&_Digital_Technologies"}
# ATS: same family under Eightfold's own (coarser) taxonomy. The detail
# payload's `efcustomTextJobFamily` is a SUB-family ("Data & Analytics",
# "Infrastructure", …), so it is kept for display but never gated on.
PCSX_DEPARTMENTS_IN_SCOPE: set[str] = {"Tech & Digital"}
# "Regular" is Kering's tag for permanent salaried roles (CDI).
# Excludes Agency, Fixed Term, Trainee, Student (Fixed Term), Apprenticeship.
WORKER_SUBTYPES_IN_SCOPE: set[str] = {"Regular"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_DELAY_SECONDS = 2.0        # corporate feed: HTML pages
PCSX_REQUEST_DELAY_SECONDS = 1.0   # ATS: JSON API
REQUEST_TIMEOUT = 30
MAX_PAGES = 200                    # corporate feed, 12 jobs/page
MAX_PCSX_PAGES = 400               # ATS, 10 jobs/page

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2.0
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>', re.DOTALL
)
REQ_ID_RE = re.compile(r"^R\d+$")


@dataclass
class Job:
    native_job_id: str          # requisition number, e.g. "R169288"
    title: str
    apply_url: str
    description: str | None = None
    location: str | None = None
    category: str | None = None
    posted_date: str | None = None
    employment_type: str | None = None
    identifier: str | None = None
    raw_payload: dict | None = None


def _request(session: requests.Session, url: str, **kwargs) -> requests.Response:
    """GET with linear-backoff retry on transient timeouts / 5xx.

    Raises the last error once retries are exhausted, so a genuine outage on
    either surface aborts the run instead of returning a partial list — a
    non-empty partial return would slip past db.persist_run_results' empty
    guard and retire every row the failed surface would have carried.
    """
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, **kwargs)
        except (requests.Timeout, requests.ConnectionError,
                requests.exceptions.ChunkedEncodingError) as exc:
            last_exc = exc
            print(f"  [retry {attempt}/{MAX_RETRIES}] {type(exc).__name__} on {url}",
                  flush=True)
        else:
            if response.status_code not in RETRYABLE_STATUS:
                return response
            last_exc = requests.HTTPError(
                f"{response.status_code} on attempt {attempt}: {response.text[:120]}",
                response=response,
            )
            print(f"  [retry {attempt}/{MAX_RETRIES}] HTTP {response.status_code} "
                  f"on {url}", flush=True)
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    assert last_exc is not None
    raise last_exc


# --------------------------------------------------------------------------
# surface A — corporate Next.js feed
# --------------------------------------------------------------------------
def _extract_next_data(html: str) -> dict | None:
    m = NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _job_search_section(next_data: dict) -> dict | None:
    sections = (
        next_data.get("props", {})
        .get("pageProps", {})
        .get("props", {})
        .get("sections", [])
    )
    for s in sections:
        if s.get("type") == "job-search":
            return s.get("props") or {}
    return None


def _req_id_from_url(url: str | None) -> str | None:
    """Pull the requisition id (e.g. R169288) off the apply URL tail."""
    if not url:
        return None
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return tail if REQ_ID_RE.match(tail or "") else None


def _crawl_corporate(session: requests.Session) -> dict[str, dict]:
    """Walk every page of the corporate feed. Returns {req_id: raw job}."""
    by_req: dict[str, dict] = {}
    skipped_no_req = 0
    page = 1
    total_pages = 1
    advertised: int | None = None

    print("[corporate] www.kering.com feed (filters are client-side, "
          "walking all pages)...", flush=True)

    while page <= total_pages and page <= MAX_PAGES:
        response = _request(session, LISTING_URL.format(page=page))
        response.raise_for_status()

        next_data = _extract_next_data(response.text)
        if not next_data:
            raise RuntimeError(f"corporate feed page {page}: no __NEXT_DATA__")

        section = _job_search_section(next_data)
        if section is None:
            raise RuntimeError(f"corporate feed page {page}: no job-search section")

        page_jobs = section.get("jobList") or []
        for raw in page_jobs:
            req_id = _req_id_from_url(raw.get("url"))
            if not req_id:
                skipped_no_req += 1
                continue
            # A re-issued req keeps its number but gets a fresh internal
            # jobId; keep whichever copy the feed published most recently.
            kept = by_req.get(req_id)
            if kept is None or (raw.get("publishedAt") or "") > (kept.get("publishedAt") or ""):
                by_req[req_id] = raw

        if page == 1:
            total_pages = int(section.get("totalPages") or 1)
            advertised = section.get("totalJobNumber")
            print(f"  advertised: {advertised} jobs / {total_pages} pages", flush=True)

        page += 1
        if page <= total_pages and page <= MAX_PAGES:
            time.sleep(REQUEST_DELAY_SECONDS)

    if skipped_no_req:
        print(f"  {skipped_no_req} rows had no R-number in their URL (skipped)",
              flush=True)
    # `totalJobNumber` reads a search index that runs ahead of the cached
    # listing pages, so a small shortfall here is the publish backlog, not a
    # pagination bug — those rows show up on the ATS surface below.
    print(f"  → {len(by_req)} requisitions\n", flush=True)
    return by_req


# --------------------------------------------------------------------------
# surface B — Eightfold PCSX
# --------------------------------------------------------------------------
def _crawl_pcsx(session: requests.Session) -> dict[str, dict]:
    """Page the ATS search API. Returns {req_id: position}."""
    by_req: dict[str, dict] = {}
    start = 0
    pages = 0
    advertised: int | None = None

    print("[ats] careers.kering.com PCSX search...", flush=True)

    while pages < MAX_PCSX_PAGES:
        response = _request(session, PCSX_SEARCH_URL, params={
            "domain": PCSX_DOMAIN,
            "query": "",
            "location": "",
            "start": start,
            "sort_by": PCSX_SORT_BY,
        })
        response.raise_for_status()
        data = (response.json() or {}).get("data") or {}

        positions = data.get("positions") or []
        if advertised is None:
            advertised = data.get("count")
            print(f"  advertised: {advertised} positions", flush=True)

        before = len(by_req)
        for pos in positions:
            req_id = str(pos.get("displayJobId") or pos.get("atsJobId") or "")
            if REQ_ID_RE.match(req_id):
                by_req.setdefault(req_id, pos)

        pages += 1
        start += len(positions)
        # Stop on an empty page, on a page that added nothing new (the API
        # repeating itself), or once the advertised total is covered.
        if not positions or len(by_req) == before or start >= (advertised or 0):
            break
        time.sleep(PCSX_REQUEST_DELAY_SECONDS)

    if advertised and len(by_req) < advertised:
        print(f"  WARNING: collected {len(by_req)} of {advertised} advertised "
              f"positions (ordering shifted mid-crawl?)", flush=True)
    print(f"  → {len(by_req)} requisitions\n", flush=True)
    return by_req


def _pcsx_is_france(pos: dict[str, Any]) -> bool:
    """France by the listing's own location strings.

    `locations` is free text ("Paris, France"); `standardizedLocations` is
    normalised with an ISO country suffix ("Paris, IDF, FR").
    """
    for loc in pos.get("locations") or []:
        if isinstance(loc, str) and loc.strip().endswith("France"):
            return True
    for loc in pos.get("standardizedLocations") or []:
        if isinstance(loc, str) and loc.strip().endswith(", FR"):
            return True
    return False


def _pcsx_candidate(pos: dict[str, Any]) -> bool:
    """Cheap listing-level gate deciding which positions get a detail fetch."""
    return _pcsx_is_france(pos) and pos.get("department") in PCSX_DEPARTMENTS_IN_SCOPE


def _fetch_pcsx_detail(session: requests.Session, position_id: Any) -> dict | None:
    """Detail payload: description + the Workday custom fields.

    A single missing detail (404 / malformed) drops just that position —
    the contract type it carries is what the scope gate needs, so keeping
    the row would mean guessing at it.
    """
    response = _request(session, PCSX_DETAIL_URL, params={
        "position_id": str(position_id),
        "domain": PCSX_DOMAIN,
        "hl": "en",
    })
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return (response.json() or {}).get("data") or None


def _first(value: Any) -> Any:
    """Eightfold custom text fields come back as single-element lists."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


# --------------------------------------------------------------------------
# scope gates
# --------------------------------------------------------------------------
def _corporate_in_scope(raw: dict[str, Any]) -> bool:
    return (
        raw.get("locationCountry") in COUNTRIES_IN_SCOPE
        and raw.get("jobFamilyId") in JOB_FAMILY_IDS_IN_SCOPE
        and raw.get("workerSubType") in WORKER_SUBTYPES_IN_SCOPE
    )


def _pcsx_in_scope(pos: dict[str, Any], detail: dict | None) -> bool:
    if not _pcsx_candidate(pos):
        return False
    if detail is None:
        return False
    return _first(detail.get("efcustomTextWorkerSubtype")) in WORKER_SUBTYPES_IN_SCOPE


# --------------------------------------------------------------------------
# merge
# --------------------------------------------------------------------------
def _html_to_text(html: str | None) -> str | None:
    if not html:
        return None
    return BeautifulSoup(html, "html.parser").get_text("\n", strip=True)


def _format_location(city: str | None, country: str | None) -> str | None:
    parts = []
    if city:
        # Kering yells city names in ALL CAPS; tidy for display.
        parts.append(city.title())
    if country:
        parts.append(country)
    return ", ".join(parts) if parts else None


def _epoch_to_date(ts: Any) -> str | None:
    if not isinstance(ts, (int, float)) or ts <= 0:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).date().isoformat()


def _build_job(
    req_id: str,
    raw: dict[str, Any] | None,
    pos: dict[str, Any] | None,
    detail: dict[str, Any] | None,
) -> Job | None:
    """Merge one requisition's two views into a single row.

    The corporate feed wins on every shared field: it is what previous runs
    stored, so preferring it keeps titles/dates stable for rows already in
    the DB (a posted_date that moved would be logged as a re-date).
    """
    title = (raw or {}).get("jobPosting") or (pos or {}).get("name") or ""
    title = title.strip()
    if not title:
        return None

    if raw:
        location = _format_location(raw.get("locationCity"), raw.get("locationCountry"))
    else:
        location = ", ".join((pos or {}).get("locations") or []) or None

    category = (raw or {}).get("jobFamily")
    if not category and detail:
        category = _first(detail.get("efcustomTextJobFamily"))
    if not category:
        category = (pos or {}).get("department")

    posted = (raw or {}).get("publishedAt")
    posted_date = (
        posted[:10] if isinstance(posted, str) and len(posted) >= 10
        else _epoch_to_date((pos or {}).get("postedTs"))
    )

    if raw:
        worker_subtype = raw.get("workerSubType")
        job_time = raw.get("jobTimeType")
    else:
        worker_subtype = _first((detail or {}).get("efcustomTextWorkerSubtype"))
        job_time = (pos or {}).get("workLocationOption")
    employment_type = (
        f"{worker_subtype} ({job_time})"
        if worker_subtype and job_time
        else worker_subtype or job_time
    )

    description = _html_to_text((raw or {}).get("description"))
    if not description and detail:
        description = _html_to_text(detail.get("jobDescription"))

    sources = [s for s, present in (("corporate", raw), ("ats", pos)) if present]
    house = (raw or {}).get("houseName") or _first((detail or {}).get("efcustomTextHouse"))

    return Job(
        native_job_id=req_id,
        title=title,
        apply_url=(raw or {}).get("url") or APPLY_URL_TEMPLATE.format(req_id=req_id),
        description=description,
        location=location,
        category=category,
        posted_date=posted_date,
        employment_type=employment_type,
        identifier=req_id,
        raw_payload={
            "req_id": req_id,
            "sources": sources,
            "house": house,
            "corporate": raw,
            "ats": pos,
            "ats_detail": detail,
        },
    )


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)
    started = time.time()

    corporate = _crawl_corporate(session)
    pcsx = _crawl_pcsx(session)

    # Detail fetch only for ATS rows that clear the listing-level gate; the
    # contract type lives in the detail payload and nowhere else.
    candidates = {rid: pos for rid, pos in pcsx.items() if _pcsx_candidate(pos)}
    print(f"[ats] {len(candidates)} France/{'/'.join(sorted(PCSX_DEPARTMENTS_IN_SCOPE))} "
          f"candidates → fetching details", flush=True)
    details: dict[str, dict] = {}
    for i, (req_id, pos) in enumerate(sorted(candidates.items()), start=1):
        detail = _fetch_pcsx_detail(session, pos.get("id"))
        if detail is None:
            print(f"  {req_id}: detail 404 — dropped", flush=True)
        else:
            details[req_id] = detail
        if i < len(candidates):
            time.sleep(PCSX_REQUEST_DELAY_SECONDS)
    print(f"  → {len(details)} details fetched\n", flush=True)

    kept: list[Job] = []
    kept_by_source = {"both": 0, "corporate-only": 0, "ats-only": 0}
    only_reason: dict[str, list[str]] = {"corporate": [], "ats": []}

    for req_id in sorted(set(corporate) | set(pcsx)):
        raw = corporate.get(req_id)
        pos = pcsx.get(req_id)
        detail = details.get(req_id)

        in_corporate = bool(raw) and _corporate_in_scope(raw)
        in_ats = bool(pos) and _pcsx_in_scope(pos, detail)
        if not (in_corporate or in_ats):
            continue

        job = _build_job(req_id, raw, pos, detail)
        if job is None:
            continue
        kept.append(job)

        if raw and pos:
            kept_by_source["both"] += 1
        elif raw:
            kept_by_source["corporate-only"] += 1
        else:
            kept_by_source["ats-only"] += 1
        # Scope disagreements between the two taxonomies, for the smoke test.
        if in_corporate and pos and not in_ats:
            only_reason["corporate"].append(req_id)
        if in_ats and raw and not in_corporate:
            only_reason["ats"].append(req_id)

    print("Filters:", flush=True)
    print(f"  countries         : {sorted(COUNTRIES_IN_SCOPE)}", flush=True)
    print(f"  job families      : {sorted(JOB_FAMILY_IDS_IN_SCOPE)} "
          f"| ATS: {sorted(PCSX_DEPARTMENTS_IN_SCOPE)}", flush=True)
    print(f"  worker subtypes   : {sorted(WORKER_SUBTYPES_IN_SCOPE)}", flush=True)
    print(f"  requisitions seen : {len(set(corporate) | set(pcsx))} "
          f"(corporate {len(corporate)}, ATS {len(pcsx)}, "
          f"both {len(set(corporate) & set(pcsx))})", flush=True)
    print(f"  kept              : {len(kept)} "
          f"(on both surfaces {kept_by_source['both']}, "
          f"corporate-only {kept_by_source['corporate-only']}, "
          f"ATS-only {kept_by_source['ats-only']})", flush=True)
    if only_reason["corporate"]:
        print(f"  kept via corporate scope, ATS disagreed: {only_reason['corporate']}",
              flush=True)
    if only_reason["ats"]:
        print(f"  kept via ATS scope, corporate disagreed: {only_reason['ats']}",
              flush=True)
    print(f"  elapsed           : {time.time() - started:.1f}s\n", flush=True)

    return [asdict(j) for j in kept]


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
        desc = (j.get("description") or "").strip()
        desc_preview = desc[:200] + ("…" if len(desc) > 200 else "")
        payload = j.get("raw_payload") or {}

        print(f"[{j['native_job_id']}] {j['title']}")
        print(f"  Sources    : {', '.join(payload.get('sources') or [])}")
        print(f"  Brand      : {payload.get('house')}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc_preview}")
        print()
