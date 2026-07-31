"""Michelin job scraper — France, Data/AI + Software/IT + Cyber/Cloud, all contract types.

Michelin's global board is hosted on Workday, tenant ``michelinhr``, site ``Michelin``:

    https://michelinhr.wd3.myworkdayjobs.com/en-US/Michelin

so we hit the standard public CXS JSON API (same shape as Ipsen / Cisco / Renault):

    POST /wday/cxs/michelinhr/Michelin/jobs            (listing, faceted)
    GET  /wday/cxs/michelinhr/Michelin<externalPath>   (detail: desc + startDate)

Two Michelin-specific Workday quirks drive the whole design (both verified live,
fixtures in material/):

  1. COMBINED facets 400. A POST that applies ``Location_Country`` AND
     ``jobFamilyGroup`` together returns HTTP_400 (empty error payload). Each facet
     works fine ALONE. So we cannot ask the server for "France AND IS&Digital" in
     one call — we crawl each axis separately and intersect client-side (same
     tactic Pernod Ricard uses for its unfaceted crawl).
  2. ``total`` is only reported on the offset=0 page; every later page echoes
     ``total: 0`` while still returning its 20 rows. A naive ``len(rows) >= total``
     loop therefore stops after page 2. We capture the total from the FIRST page
     only and page until a page comes back empty (or we hit the first-page total).
     (limit is hard-capped at 20 — 100 returns HTTP_400.)

Scope decision — why is_tech_role(title) is the PRIMARY gate, not the family facet
(Schneider-style, contrast Ipsen/Cisco where we filter on the category facet):
  Michelin France is ~202 open roles, but ~95% are Euromaster tyre-fitters, plant
  operators, maintenance, chemists and sales — the board is a giant industrial
  feed. Crucially the jobFamilyGroup facet is NOT a reliable tech selector here:
  the flagship "Alternance – Data Scientist & IA Industrielle" is filed under the
  *Personnel* (HR) family, and the only France IS&Digital row is a lone Software
  Architect. Selecting on the family facet alone would MISS the marquee AI role —
  exactly the failure `feedback_include_data_adjacent_ai_roles` warns against
  (missing a real AI role costs more than a little noise). So we:
    - crawl the whole France set (Location_Country = France), and
    - KEEP a row iff `scrapers._relevance.is_tech_role(title)` fires  OR  the row
      is in the IS&Digital family (taken wholesale — it is the one clean tech
      bucket: IT / Digital / Data / Software / Cyber. Its France yield is tiny but
      any bland-titled IS&Digital role is rescued this way).
  This drops factory/production/mechanical/tyre-shop/sales while catching data/AI
  roles wherever Michelin chose to file them. Yield today ≈ 2 genuine tech roles
  (Data Scientist alternance + Senior Software Architect); low is expected and
  correct for this board (cf. Salesforce / N26 / Snowflake).

  IS&Digital is taken wholesale; "Services & Solutions" is NOT — its France content
  is Euromaster "Conseiller Technique" field-advisor (GTM), not software — so it is
  left to the is_tech_role gate like every other non-IS&Digital family.

Contract type: Michelin exposes it only as a listing facet
(``jobPostingEmployeeContractType``), never on the detail (``timeType`` is blank).
Per `feedback_noise_filters_dashboard_only` employment-type filtering is dashboard-
side and CDI-inclusive here (we keep permanent AND AI/data-adjacent
alternance/stage), so we do NOT spend a request budget faceting it; we tag
employment_type from the French title prefix (Alternance/Stage/Apprentissage/CDD)
as a best-effort hint and leave it None otherwise. Never a recall risk.

Detail payload (verified): jobReqId (== "R-<digits>", the clean public id),
title, jobDescription (HTML), location + jobRequisitionLocation.country.descriptor
("France"), startDate (already ISO YYYY-MM-DD), externalUrl, id (internal hash).
The FULL externalPath must be used for the detail GET (it carries a posting-
revision suffix on some rows, e.g. ``…_R-2026021816-1``).

CI: Michelin's wd3 CXS is open to plain requests (UA-agnostic, no WAF); we still run
cookie-free with retries (standard Workday-behind-Cloudflare defence) so it stays
CI-safe.
"""
from __future__ import annotations

import html
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

from scrapers._relevance import is_tech_role

TENANT = "michelinhr"
SITE = "Michelin"
HOST = f"https://{TENANT}.wd3.myworkdayjobs.com"
LIST_URL = f"{HOST}/wday/cxs/{TENANT}/{SITE}/jobs"
DETAIL_URL_TEMPLATE = f"{HOST}/wday/cxs/{TENANT}/{SITE}{{external_path}}"

# Workday-global France country id (same value across every tenant; verified on
# this tenant's Location_Country facet).
FILTER_COUNTRY_FRANCE = "54c5b6971ffb4bf0b116fe7651ec789a"
# jobFamilyGroup facet id for "IS&Digital" — the one clean tech bucket, taken
# wholesale as a union with the is_tech_role gate. WID observed 2026-07; if it
# rotates the family-wholesale rescue silently no-ops (0 ids) but the is_tech_role
# gate — the primary selector — is unaffected, so recall of real tech roles holds.
FILTER_FAMILY_IS_DIGITAL = "cae294f2796f012797e45b33f7173967"
FAMILY_IS_DIGITAL_LABEL = "IS&Digital"
CATEGORY_TITLE_MATCH = "Data/AI/Software (title match)"

# Public req id shape, e.g. "R-2026028627". Validated (not trusted verbatim) so a
# listing-schema change can't collapse every row onto one dedup key.
REQ_ID_RE = re.compile(r"R-\d+")

# Best-effort employment_type tag from the French title prefix (contract type is
# not on the detail; see module docstring). Order matters — most specific first.
_EMP_TYPE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\balternance\b|\balternant", "Alternance"),
    (r"\bapprenti", "Apprentissage"),
    (r"\bstage\b|\bstagiaire\b|\binternship\b|\bintern\b", "Stage"),
    (r"\bcdd\b|fixed[- ]term|dur[eé]e d[eé]termin", "CDD"),
    (r"\bv\.?i\.?e\.?\b", "VIE"),
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
    "Origin": HOST,
    "Referer": f"{HOST}/en-US/{SITE}",
}

PAGE_SIZE = 20          # Workday hard cap on this tenant (100 -> HTTP_400)
MAX_PAGES = 40          # defensive cap (France ~202 rows = ~11 pages)
REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.2   # between requests (JSON API, >= 1.0s per playbook)
RETRY_BACKOFF_SECONDS = 20.0
MAX_RETRIES = 3


@dataclass
class Job:
    native_job_id: str          # jobReqId, e.g. "R-2026028627"
    title: str
    location: str
    category: str | None        # "IS&Digital" (family) or "Data/AI/Software (title match)"
    apply_url: str              # detail's externalUrl
    employment_type: str | None = None   # best-effort from title prefix, else None
    description: str | None = None
    posted_date: str | None = None       # YYYY-MM-DD from detail's startDate
    identifier: str | None = None        # detail's jobPostingInfo.id (internal hash)
    raw_payload: dict | None = None


def _clean_description(content: str | None) -> str | None:
    if not content:
        return None
    text = BeautifulSoup(html.unescape(content), "html.parser").get_text(" ", strip=True)
    return text or None


def _employment_type_from_title(title: str) -> str | None:
    t = title.lower()
    for pattern, label in _EMP_TYPE_PATTERNS:
        if re.search(pattern, t):
            return label
    return None


def _is_error_payload(data: object) -> bool:
    return isinstance(data, dict) and "errorCode" in data


def _listing_req_id(row: dict) -> str | None:
    """Public req id ("R-<digits>"), VALIDATED to shape. bulletFields[0] holds it
    on this tenant; fall back to parsing the externalPath tail. A wrong-shaped
    value yields None (row skipped) rather than a shared dedup key that would
    collapse rows and slip past db.persist_run_results' empty-return guard."""
    bullets = row.get("bulletFields") or []
    if bullets and isinstance(bullets[0], str):
        candidate = bullets[0].strip()
        if REQ_ID_RE.fullmatch(candidate):
            return candidate
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
        # Cookie-free: Workday fronts CXS with Cloudflare; a flagged __cf_bm cookie
        # makes follow-up POSTs keep 400-ing on datacenter ASNs (GitHub Actions).
        # Bare cookie-free requests are scored fresh (Rothschild/Ipsen lesson).
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
            # snapshot and this fetch. Surface it so the caller drops just this row.
            raise requests.HTTPError(f"detail 404 for {external_path}", response=response)
        else:
            last_err = f"HTTP {response.status_code}: {response.text[:160]}"
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS)
    raise requests.HTTPError(f"detail failed for {external_path}: {last_err}")


def _collect_listing(session: requests.Session, applied_facets: dict) -> list[dict]:
    """Fetch every row for `applied_facets`, paginating. Handles this tenant's
    quirk where `total` is reported only on the offset=0 page (0 thereafter): we
    capture the total once, then page until an empty page or the total is reached."""
    first = _post_listing(session, applied_facets, offset=0)
    total = int(first.get("total") or 0)
    rows: list[dict] = list(first.get("jobPostings") or [])

    page = 0
    while rows and len(rows) < total and page < MAX_PAGES:
        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)
        payload = _post_listing(session, applied_facets, offset=page * PAGE_SIZE)
        new = payload.get("jobPostings") or []
        if not new:
            break
        rows.extend(new)
    return rows


def _row_to_job(listing_row: dict, detail: dict, category: str | None) -> Job:
    info = detail.get("jobPostingInfo") or {}
    if not info:
        raise RuntimeError(f"Michelin detail missing jobPostingInfo: {detail!r}")

    native_job_id = (
        (info.get("jobReqId") or "").strip()
        or _listing_req_id(listing_row)
        or ""
    )
    if not native_job_id:
        raise RuntimeError(f"Michelin row missing req id: {listing_row!r}")

    apply_url = (info.get("externalUrl") or "").strip()
    if not apply_url:
        ext = listing_row.get("externalPath") or ""
        apply_url = f"{HOST}/{SITE}{ext}" if ext else ""
    if not apply_url:
        raise RuntimeError(f"Michelin detail missing externalUrl: {info!r}")

    location = (
        (info.get("location") or "").strip()
        or (info.get("jobRequisitionLocation") or {}).get("descriptor")
        or (listing_row.get("locationsText") or "").strip()
    )

    posted = info.get("startDate")
    posted_date = posted[:10] if isinstance(posted, str) and len(posted) >= 10 else None

    title = (info.get("title") or listing_row.get("title") or "").strip()

    return Job(
        native_job_id=native_job_id,
        title=title,
        location=location,
        category=category,
        apply_url=apply_url,
        employment_type=_employment_type_from_title(title),
        description=_clean_description(info.get("jobDescription")),
        posted_date=posted_date,
        identifier=info.get("id"),
        raw_payload={"listing": listing_row, "detail": info},
    )


def scrape() -> list[dict]:
    session = _new_session()
    started = time.time()

    # 1) Recall-critical: enumerate the WHOLE France set. Any failure aborts (a
    #    partial return would false-close the missing rows in the DB).
    print("Listing phase — France (all families)...", flush=True)
    france_rows = _collect_listing(session, {"Location_Country": [FILTER_COUNTRY_FRANCE]})
    france_by_id: dict[str, dict] = {}
    for r in france_rows:
        rid = _listing_req_id(r)
        if rid:
            france_by_id.setdefault(rid, r)
    print(f"  -> {len(france_by_id)} France roles (all categories)", flush=True)

    # 2) IS&Digital family (one clean tech bucket), crawled globally then intersected
    #    with France for a wholesale-keep set + category labelling. Recall-critical
    #    for the wholesale slice -> abort on failure (do NOT swallow-and-continue).
    print("Listing phase — IS&Digital family (tech bucket)...", flush=True)
    time.sleep(REQUEST_DELAY_SECONDS)
    isd_rows = _collect_listing(session, {"jobFamilyGroup": [FILTER_FAMILY_IS_DIGITAL]})
    isd_ids = {rid for r in isd_rows if (rid := _listing_req_id(r))}
    isd_france_ids = isd_ids & set(france_by_id)
    print(f"  -> {len(isd_ids)} IS&Digital global, {len(isd_france_ids)} in France", flush=True)

    # 3) Keep a France row iff it is IS&Digital (wholesale) OR is_tech_role(title).
    kept: list[tuple[str, dict, str]] = []  # (rid, listing_row, category)
    for rid, row in france_by_id.items():
        title = row.get("title") or ""
        if rid in isd_france_ids:
            kept.append((rid, row, FAMILY_IS_DIGITAL_LABEL))
        elif is_tech_role(title):
            kept.append((rid, row, CATEGORY_TITLE_MATCH))
    print(f"  -> {len(kept)} in-scope tech roles (IS&Digital wholesale + is_tech_role)",
          flush=True)

    # 4) Detail phase.
    print("\nDetail phase...", flush=True)
    jobs: list[Job] = []
    for rid, row, category in kept:
        ext = row.get("externalPath") or ""
        if not ext:
            print(f"  skip {rid}: missing externalPath", flush=True)
            continue
        try:
            detail = _get_detail(session, ext)
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            if status == 404:
                # Removed since the listing snapshot — drop just this row (closing
                # it is correct: it's genuinely gone).
                print(f"  {rid}: detail 404, dropping (job removed)", flush=True)
                continue
            # Any other detail failure means the result would be incomplete;
            # returning a partial list would false-close the dropped rows. Abort.
            raise
        jobs.append(_row_to_job(row, detail, category))
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
