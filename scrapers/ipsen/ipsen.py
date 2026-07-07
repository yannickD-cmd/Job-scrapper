"""Ipsen job scraper — France, IT / Digital / Data family, all contract types.

Ipsen's single global board is hosted on Workday at
ipsen.wd103.myworkdayjobs.com/en-EN/Ipsen_Careers. Workday exposes the usual
public JSON CXS API:

  POST /wday/cxs/ipsen/Ipsen_Careers/jobs        (listing, faceted)
  GET  /wday/cxs/ipsen/Ipsen_Careers<externalPath>   (detail)

Scope (house default — pharma corporate board):
  - Country     : France only   (locationCountry facet — Ipsen has a real one,
                                  unlike Pernod Ricard)
  - Job family  : "IT / Digital / Data" (jobFamilyGroup facet). This single
                  family is a clean tech bucket — Software/IT + Data + AI +
                  Digital + Security — so NO title/relevance gate is needed
                  (all 11 France rows today are genuine tech: Lead Data
                  Engineer, Data & AI Platforms Solution Architect, M365
                  Platform Engineer, CISO, IT Enterprise Architect, …).
  - Job type    : ALL kept raw. Employment-type filtering is dashboard-side
                  (see feedback_noise_filters_dashboard_only); every current
                  role happens to be "Open ended" (CDI) anyway. We still TAG
                  each row's contract as employment_type when we can.

Why this tenant is EASY (contrast with Pernod Ricard / Rothschild):
  - It accepts a COMBINED faceted POST (locationCountry + jobFamilyGroup) and
    returns 200 — no need to loop one facet id per request.
  - It has a real `locationCountry` facet, so France is filtered server-side
    (no bulletFields country-name sniffing).
  - No token-bucket throttle was observed; plain requests with a browser UA
    return 200 repeatedly. We still run cookie-free with a couple of retries
    (the standard Workday-behind-Cloudflare defence) so it stays CI-safe.

Employment-type tagging (best-effort, never a recall risk):
  1. AUTHORITATIVE enumeration = France + IT/Digital/Data, NO workerSubType
     filter. This guarantees we see every in-scope row regardless of contract
     (a per-workerSubType loop could silently drop a row of a type we forgot
     to map — that would false-close it in the DB). This query is the only
     recall-critical one; if it fails we ABORT (run.py closes nothing).
  2. The authoritative response echoes a `workerSubType` facet scoped to the
     current filter, telling us exactly which contract types are present. For
     each present, known type we fire ONE extra tag query (France + IT/Data +
     that type) and map its req ids -> a French label. Rows we can't tag keep
     employment_type=None. These enrichment queries are best-effort: a failure
     there is caught and logged, never aborts the run.

Workday quirks worth knowing (this tenant):
  - The listing `bulletFields[0]` and the detail `jobReqId` are the clean
    public id, e.g. "R-21232" — used as native_job_id and for dedup.
  - The listing `externalPath` carries a posting-revision suffix the id drops
    (e.g. id "R-21232" -> path ".../Lead-Data-Engineer_R-21232-1"). The detail
    fetch MUST use the FULL externalPath (dropping the suffix 404s).
  - `startDate` is already ISO YYYY-MM-DD and matches the listing's
    "Posted N Days Ago" — used directly as posted_date.
  - The detail omits workerSubType entirely (its `timeType` is Full/Part time,
    not CDI/CDD) — hence the tag-query pass above.
  - Country is reliable on the detail (`country.descriptor` == "France"); the
    France country id is the Workday-global 54c5b6971ffb4bf0b116fe7651ec789a.
"""
from __future__ import annotations

import html
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

TENANT = "ipsen"
SITE = "Ipsen_Careers"
HOST = f"https://{TENANT}.wd103.myworkdayjobs.com"
LIST_URL = f"{HOST}/wday/cxs/{TENANT}/{SITE}/jobs"
DETAIL_URL_TEMPLATE = f"{HOST}/wday/cxs/{TENANT}/{SITE}{{external_path}}"

# Workday-global France country id (same value across every tenant).
FILTER_COUNTRY_FRANCE = "54c5b6971ffb4bf0b116fe7651ec789a"
# jobFamilyGroup facet — the one clean tech bucket on this board.
FILTER_JOBFAMILY_IT_DIGITAL_DATA = "c49c43aaf7921001f933985a9ed30000"
CATEGORY_LABEL = "IT / Digital / Data"

# workerSubType facet id -> French contract label. Used only to TAG rows; it is
# never a filter (all contract types are kept). Ids observed 2026-07 on this
# tenant; an unmapped/new type simply leaves employment_type=None (the row is
# still enumerated and kept — recall is guaranteed by the unfiltered query).
WORKERSUBTYPE_LABELS: dict[str, str] = {
    "d7f2a483cc9c0100d461132daf507c1a": "CDI",          # Open ended
    "d7f2a483cc9c0176b7bff32caf50781a": "CDD",          # Temporary - fixed term
    "d7f2a483cc9c01f98aca082daf50791a": "Alternance",   # Temporary - apprentice
    "d7f2a483cc9c0102d7290d2daf507a1a": "Stage",        # Temporary - trainee
    "97d5434774e71001eff91eec6b7e0000": "VIE",          # VIE
    "d7f2a483cc9c01205325202daf50801a": "Contractor",   # External / Contractor
    "d7f2a483cc9c01aaaaf5a687af50ea1a": "Contractor",   # Contractor
    "d7f2a483cc9c0145b769162daf507d1a": "Expatriate",   # Expatriate
}

BASE_FACETS: dict[str, list[str]] = {
    "locationCountry": [FILTER_COUNTRY_FRANCE],
    "jobFamilyGroup": [FILTER_JOBFAMILY_IT_DIGITAL_DATA],
}

# Public req id shape, e.g. "R-21232". Validated (not trusted verbatim) so a
# listing-schema change can't hand back a shared value that would collapse every
# row onto one dedup key — see _listing_req_id.
REQ_ID_RE = re.compile(r"R-\d+")

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
    "Referer": f"{HOST}/en-EN/{SITE}",
}

PAGE_SIZE = 20
MAX_PAGES = 20  # defensive cap (the France IT/Data set is ~11 today)
REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.5   # between requests (JSON API, ≥ 1.0s per playbook)
RETRY_BACKOFF_SECONDS = 30.0
MAX_RETRIES = 3


@dataclass
class Job:
    native_job_id: str          # jobReqId, e.g. "R-21232"
    title: str
    location: str               # detail's location (clean city)
    category: str | None        # always "IT / Digital / Data" (the family gate)
    apply_url: str              # detail's externalUrl
    employment_type: str | None = None  # "CDI"/"CDD"/… from workerSubType, or None
    description: str | None = None
    posted_date: str | None = None      # YYYY-MM-DD from detail's startDate
    identifier: str | None = None       # detail's jobPostingInfo.id (internal hash)
    raw_payload: dict | None = None


def _clean_description(content: str | None) -> str | None:
    if not content:
        return None
    text = BeautifulSoup(html.unescape(content), "html.parser").get_text(" ", strip=True)
    return text or None


def _build_location(info: dict, listing_row: dict) -> str:
    """Join the primary posting city with every additionalLocations entry.

    The listing is filtered locationCountry=France, so each req IS France-tagged
    — but on a multi-location req Workday's `jobPostingInfo.location` is only the
    PRIMARY city, which may be the non-France one (e.g. primary "London (UK)",
    additional "Paris"). Storing just the primary would hide the France city from
    the dashboard's is_idf / France filter. Emitting all cities as one "A; B"
    string lets is_idf (which passes a multi-location string if ANY listed city
    qualifies) surface the role correctly. (Same rationale as the Cisco scraper.)
    """
    cities: list[str] = []
    primary = (
        (info.get("location") or "").strip()
        or (info.get("jobRequisitionLocation") or {}).get("descriptor")
        or ""
    ).strip()
    if primary:
        cities.append(primary)
    for extra in info.get("additionalLocations") or []:
        if isinstance(extra, str) and extra.strip():
            cities.append(extra.strip())
    # Dedup, preserve order.
    location = "; ".join(dict.fromkeys(cities))
    return location or (listing_row.get("locationsText") or "").strip()


def _is_error_payload(data: object) -> bool:
    return isinstance(data, dict) and "errorCode" in data


def _listing_req_id(row: dict) -> str | None:
    """Public req id ("R-21232"), VALIDATED to the R-<digits> shape.

    We do not trust bulletFields[0] verbatim: if Ipsen ever reconfigures the
    listing so slot 0 holds a shared value (a time-type label, a category, …),
    every row would map to the same string, dedup would collapse them to one,
    and the non-empty result would sail past db.persist_run_results' empty-return
    guard and false-close every other open Ipsen row. Requiring the R-<digits>
    shape means a wrong-shaped value yields None (row skipped) — and if the whole
    board changed shape, the result goes fully empty and the guard protects it.
    (Mirrors the Pernod template's JR_RE.fullmatch check.)"""
    bullets = row.get("bulletFields") or []
    if bullets and isinstance(bullets[0], str):
        candidate = bullets[0].strip()
        if REQ_ID_RE.fullmatch(candidate):
            return candidate
    # Fallback: parse the req id from the externalPath tail. The `R-\d+` match is
    # greedy over digits only, so it stops before any posting-revision suffix
    # (…_R-21232-1 -> "R-21232").
    m = re.search(r"_(R-\d+)", row.get("externalPath") or "")
    return m.group(1) if m else None


def _new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def _post_listing(session: requests.Session, applied_facets: dict, offset: int) -> dict:
    body = {
        "appliedFacets": applied_facets,
        "limit": PAGE_SIZE,
        "offset": offset,
        "searchText": "",
    }
    last_err: str | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        # Cookie-free: Workday fronts CXS with Cloudflare, which tags the first
        # response with __cf_bm / wd-browser-id cookies. On a flagged fingerprint
        # (datacenter ASNs like GitHub Actions) those cookies make follow-up POSTs
        # keep 400-ing; bare cookie-free requests are scored fresh. (Rothschild /
        # Pernod lesson — applies to every Workday tenant.)
        session.cookies.clear()
        response = session.post(LIST_URL, json=body, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            if not _is_error_payload(data):
                return data
            last_err = f"error payload {data.get('errorCode')}"
        else:
            last_err = f"HTTP {response.status_code}: {response.text[:160]}"
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS)
    raise requests.HTTPError(f"listing failed (offset {offset}): {last_err}")


def _get_detail(session: requests.Session, external_path: str) -> dict:
    url = DETAIL_URL_TEMPLATE.format(external_path=external_path)
    last_err: str | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        session.cookies.clear()
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            if not _is_error_payload(data):
                return data
            last_err = f"error payload {data.get('errorCode')}"
        elif response.status_code == 404:
            # Terminal, not transient: the job was removed between the listing
            # snapshot and this fetch. Surface it so the caller drops it (closing
            # it is correct — it's genuinely gone).
            raise requests.HTTPError(f"detail 404 for {external_path}", response=response)
        else:
            last_err = f"HTTP {response.status_code}: {response.text[:160]}"
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS)
    raise requests.HTTPError(f"detail failed for {external_path}: {last_err}")


def _collect_listing(session: requests.Session, applied_facets: dict) -> tuple[list[dict], dict]:
    """Fetch every row for `applied_facets`, paginating. Returns
    (rows, first_page_payload); the payload carries the facet counts we use for
    employment-type tagging."""
    first = _post_listing(session, applied_facets, offset=0)
    total = int(first.get("total") or 0)
    rows: list[dict] = list(first.get("jobPostings") or [])

    page = 0
    while len(rows) < total and page < MAX_PAGES:
        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)
        payload = _post_listing(session, applied_facets, offset=page * PAGE_SIZE)
        new = payload.get("jobPostings") or []
        if not new:
            break
        rows.extend(new)
    return rows, first


def _present_subtypes(listing_payload: dict) -> list[str]:
    """workerSubType facet ids with count>0 in the current scope that we know
    how to label."""
    present: list[str] = []
    for facet in listing_payload.get("facets") or []:
        if facet.get("facetParameter") != "workerSubType":
            continue
        for value in facet.get("values") or []:
            fid = value.get("id")
            if fid in WORKERSUBTYPE_LABELS and (value.get("count") or 0) > 0:
                present.append(fid)
    return present


def _build_employment_labels(session: requests.Session, subtype_ids: list[str]) -> dict[str, str]:
    """For each in-scope contract type, tag which req ids belong to it. Purely
    enrichment: any failure is swallowed (recall is already guaranteed by the
    unfiltered authoritative listing), so a hiccup here never aborts the run."""
    labels: dict[str, str] = {}
    for fid in subtype_ids:
        label = WORKERSUBTYPE_LABELS[fid]
        facets = {**BASE_FACETS, "workerSubType": [fid]}
        try:
            time.sleep(REQUEST_DELAY_SECONDS)
            rows, _ = _collect_listing(session, facets)
        except Exception as exc:
            # Best-effort ONLY. Recall is already guaranteed by the unfiltered
            # authoritative listing, so ANY failure here — an HTTP error, a read
            # timeout / connection reset, or a non-JSON Cloudflare interstitial
            # (none of which are HTTPError subclasses) — must be swallowed rather
            # than abort an otherwise-complete run over cosmetic contract tags.
            print(
                f"  employment tag '{label}' failed "
                f"({type(exc).__name__}: {exc}); leaving those None",
                flush=True,
            )
            continue
        for r in rows:
            rid = _listing_req_id(r)
            if rid:
                labels[rid] = label
    return labels


def _row_to_job(listing_row: dict, detail: dict, employment_type: str | None) -> Job:
    info = detail.get("jobPostingInfo") or {}

    native_job_id = (
        (info.get("jobReqId") or "").strip()
        or _listing_req_id(listing_row)
        or ""
    )
    if not native_job_id:
        raise RuntimeError(f"Ipsen row missing req id: {listing_row!r}")

    apply_url = (info.get("externalUrl") or "").strip()
    if not apply_url:
        ext = listing_row.get("externalPath") or ""
        apply_url = f"{HOST}/{SITE}{ext}" if ext else ""
    if not apply_url:
        raise RuntimeError(f"Ipsen detail missing externalUrl: {info!r}")

    location = _build_location(info, listing_row)

    posted = info.get("startDate")
    posted_date = posted[:10] if isinstance(posted, str) and len(posted) >= 10 else None

    title = (info.get("title") or listing_row.get("title") or "").strip()

    return Job(
        native_job_id=native_job_id,
        title=title,
        location=location,
        category=CATEGORY_LABEL,
        apply_url=apply_url,
        employment_type=employment_type,
        description=_clean_description(info.get("jobDescription")),
        posted_date=posted_date,
        identifier=info.get("id"),
        raw_payload={"listing": listing_row, "detail": info},
    )


def scrape() -> list[dict]:
    session = _new_session()
    started = time.time()

    print("Listing phase (France + IT/Digital/Data, all contract types)...", flush=True)
    # Recall-critical: enumerate the WHOLE scope with no contract filter so we
    # never miss (and thus false-close) a row of a contract type we didn't map.
    rows, first_payload = _collect_listing(session, BASE_FACETS)

    by_id: dict[str, dict] = {}
    for r in rows:
        rid = _listing_req_id(r)
        if rid:
            by_id.setdefault(rid, r)  # dedup on req id (multi-location -> one row)
    print(f"  -> {len(by_id)} unique France IT/Digital/Data roles", flush=True)

    # Best-effort employment-type tagging (never a recall risk — see docstring).
    labels = _build_employment_labels(session, _present_subtypes(first_payload))

    print("\nDetail phase...", flush=True)
    jobs: list[Job] = []
    for rid, row in by_id.items():
        ext = row.get("externalPath") or ""
        if not ext:
            print(f"  skip {rid}: missing externalPath", flush=True)
            continue
        try:
            detail = _get_detail(session, ext)
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            if status == 404:
                # Removed since the listing snapshot — drop it (it will close,
                # which is correct: it's genuinely gone).
                print(f"  {rid}: detail 404, dropping (job removed)", flush=True)
                continue
            # Any other detail failure means the result would be incomplete;
            # returning a partial list would false-close the dropped rows. Abort
            # so run.py logs a failed run and closes nothing.
            raise
        jobs.append(_row_to_job(row, detail, labels.get(rid)))
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
