"""SAP job scraper — France, tech Work Areas only, Regular employment only.

SAP's global board (jobs.sap.com) runs on **SuccessFactors Career Site Builder**
(the jobs2web / Recruiting-Marketing stack). Plain `requests` works — no
Cloudflare/Akamai challenge from CI IPs — so this stays in the CI matrix.

Scope is driven by SAP's own **Work Area** taxonomy (the `department` facet),
NOT by a title keyword filter. SAP has a clean, usable category facet, so we use
it directly — `is_tech_role` (the title predicate for boards whose ATS category
is unusable, e.g. Schneider) is reserved here for the ONE Work Area that is
genuinely mixed. France's Work Area distribution (19 open roles today):

    Sales ................................ 10   commercial      -> drop
    Consulting and Professional Services .  4   MIXED           -> is_tech_role
    Presales .............................  2   sales-eng       -> drop
    Information Technology ...............   1   tech            -> keep
    Software-Development Operations ......   1   tech            -> keep
    Administration .......................   1   back-office     -> drop

"Consulting and Professional Services" holds both technical solution/enterprise
architects (incl. the Data Management & Landscape Transformation architect) and
functional module consultants (e.g. Costing & Profitability / PaPM). That bucket
"has too much noise" for the category alone, so — and only there — we refine it
with the shared title predicate `is_tech_role` (scrapers/_relevance.py). Pure
engineering/IT Work Areas ("Information Technology", any "Software-*") are kept
wholesale; every other Work Area is dropped.

Two-pass scrape, same shape as the Sanofi scraper:

1. LISTING. `/search/` with the country facet `optionsFacetsDD_country=FR`
   returns France-only postings, 25 per page, paginated by `startrow`. Each
   `<tr class="data-row">` gives native_job_id (the numeric segment of the
   `/job/<slug>/<id>/` href), title, location and apply_url. Work Area is NOT in
   the listing, so scope can only be decided after the detail fetch.

2. ENRICHMENT. SAP detail pages carry no JSON-LD JobPosting; every field is a
   labelled token whose value span has a stable `data-careersite-propertyid`:
       facility     -> Requisition ID   (SAP internal req id -> identifier)
       date         -> Posted Date       ("Jun 10, 2026" -> ISO)
       department   -> Work Area         (-> category, the scope axis)
       customfield3 -> Career Status     (Professional / Graduate / Student)
       shifttype    -> Employment Type   ("Regular Full Time" / "Limited ...")
       location     -> "City, FR, 12345"
   We keep in-scope Work Areas, then drop anything whose Employment Type isn't
   Regular (scope = permanent / CDI-equivalent only; excludes apprenticeship,
   internship, limited-term).

To change scope, edit TECH_WORK_AREAS / MIXED_WORK_AREAS / EMPLOYMENT_TYPE_*.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

from scrapers._relevance import is_tech_role

HOST = "https://jobs.sap.com"
SEARCH_URL = f"{HOST}/search/"

# France. SF CSB country facet uses the ISO code, not the display name.
COUNTRY_FACET = "FR"

# --- Scope: SAP Work Area (the `department` token) ---------------------------
# Pure engineering / IT Work Areas, kept wholesale. Matched as: exact membership
# here OR any Work Area whose name starts with "Software" (Software-Design and
# Development, Software-Development Operations, Software Engineering, ...).
TECH_WORK_AREAS: set[str] = {"Information Technology"}
_TECH_WORK_AREA_PREFIX = "Software"

# Genuinely mixed Work Areas: technical architects share the bucket with
# functional/business consultants, so we refine on the title via is_tech_role.
MIXED_WORK_AREAS: set[str] = {"Consulting and Professional Services"}

# Permanent / CDI-equivalent only. SAP emits "Regular Full Time" / "Regular Part
# Time" for permanent roles and "Limited ..." for fixed-term (apprenticeships,
# internships, short temporary contracts show as Limited / Student career status).
EMPLOYMENT_TYPE_IN_SCOPE_PREFIX = "regular"

PAGE_SIZE = 25          # SF CSB serves 25 rows per search page
MAX_PAGES = 20          # defensive cap: 500 rows; France is ~19 today

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_DELAY_SECONDS = 2.0
REQUEST_TIMEOUT = 30

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_JOB_ID_RE = re.compile(r"/job/[^/]+/(\d+)/?$")
_TOTAL_RE = re.compile(r"Results\s+\d+\s+to\s+\d+\s+of\s+([\d,]+)")
_DATE_RE = re.compile(r"([A-Za-z]{3,9})\s+(\d{1,2}),\s*(\d{4})")


@dataclass
class Job:
    native_job_id: str
    title: str
    apply_url: str
    location: str = ""
    # Filled by detail-page enrichment:
    category: str | None = None            # Work Area
    posted_date: str | None = None
    employment_type: str | None = None
    identifier: str | None = None          # SAP Requisition ID
    description: str | None = None
    raw_payload: dict | None = None


def _parse_listing_page(html: str) -> tuple[list[Job], int]:
    """Return (jobs on this page, total result count)."""
    soup = BeautifulSoup(html, "html.parser")

    jobs: list[Job] = []
    for row in soup.select("tr.data-row"):
        anchor = row.select_one('a.jobTitle-link[href^="/job/"]') \
            or row.select_one('a[href^="/job/"]')
        if not anchor:
            continue
        href = anchor.get("href") or ""
        m = _JOB_ID_RE.search(href)
        if not m:
            continue
        native_id = m.group(1)

        title = anchor.get_text(" ", strip=True)
        loc_el = row.select_one("td.colLocation .jobLocation") \
            or row.select_one(".jobLocation")
        location = loc_el.get_text(" ", strip=True) if loc_el else ""

        jobs.append(Job(
            native_job_id=native_id,
            title=title,
            apply_url=HOST + href if href.startswith("/") else href,
            location=location,
        ))

    total = 0
    m = _TOTAL_RE.search(html)
    if m:
        total = int(m.group(1).replace(",", ""))
    return jobs, total


def _normalize_date(raw: str | None) -> str | None:
    """'Jun 10, 2026' -> '2026-06-10'. Locale-independent month parse."""
    if not raw:
        return None
    m = _DATE_RE.search(raw)
    if not m:
        return None
    month = _MONTHS.get(m.group(1)[:3].lower())
    if not month:
        return None
    return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(2)):02d}"


def _token(soup: BeautifulSoup, property_id: str) -> str | None:
    """Read a labelled detail field by its stable data-careersite-propertyid."""
    el = soup.select_one(f'span[data-careersite-propertyid="{property_id}"]')
    if not el:
        return None
    # Collapse the doubled whitespace SAP emits ("Consulting  and Professional").
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() or None


def _enrich(session: requests.Session, job: Job) -> bool:
    """Fetch detail page, fill enrichment fields. Returns True on success."""
    response = session.get(job.apply_url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    job.identifier = _token(soup, "facility")
    job.posted_date = _normalize_date(_token(soup, "date"))
    job.category = _token(soup, "department")
    job.employment_type = _token(soup, "shifttype")
    detail_location = _token(soup, "location")
    if detail_location:
        job.location = detail_location
    career_status = _token(soup, "customfield3")

    body = soup.select_one("div.jobDisplay") or soup.select_one("div.job")
    if body:
        job.description = body.get_text("\n", strip=True)

    job.raw_payload = {
        "requisition_id": job.identifier,
        "posted_date_raw": _token(soup, "date"),
        "work_area": job.category,
        "career_status": career_status,
        "employment_type": job.employment_type,
        "expected_travel": _token(soup, "travel"),
        "location": job.location,
    }
    # A page that parsed no tokens at all means the layout changed — treat as fail.
    return bool(job.identifier or job.employment_type or job.category)


def _work_area_in_scope(work_area: str | None, title: str) -> bool:
    """Decide scope on SAP's Work Area; refine only the mixed bucket by title."""
    if not work_area:
        return False
    wa = work_area.strip()
    if wa in TECH_WORK_AREAS or wa.startswith(_TECH_WORK_AREA_PREFIX):
        return True
    if wa in MIXED_WORK_AREAS:
        return is_tech_role(title)
    return False


def _is_regular(employment_type: str | None) -> bool:
    return bool(employment_type) and \
        employment_type.strip().lower().startswith(EMPLOYMENT_TYPE_IN_SCOPE_PREFIX)


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    # ---- Phase 1: listing (France facet, paginated) --------------------------
    print("Listing phase (France)...", flush=True)
    all_listings: dict[str, Job] = {}  # dedup by native_job_id
    total = 0
    started = time.time()

    for page in range(MAX_PAGES):
        params = {
            "q": "",
            "sortColumn": "referencedate",
            "sortDirection": "desc",
            "startrow": page * PAGE_SIZE,
            "optionsFacetsDD_country": COUNTRY_FACET,
        }
        response = session.get(SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        page_jobs, page_total = _parse_listing_page(response.text)
        if page_total:
            total = page_total
        for j in page_jobs:
            all_listings.setdefault(j.native_job_id, j)

        print(
            f"  page {page + 1} (startrow {page * PAGE_SIZE}): "
            f"{len(page_jobs)} rows ({len(all_listings)}/{total or '?'} unique)",
            flush=True,
        )

        if not page_jobs or len(all_listings) >= total:
            break
        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"  -> {len(all_listings)} France postings in "
          f"{time.time() - started:.1f}s\n", flush=True)

    # ---- Phase 2: enrich every posting (Work Area lives on the detail page) --
    print(f"Enrichment phase: {len(all_listings)} detail pages "
          f"(~{int(len(all_listings) * REQUEST_DELAY_SECONDS)}s)...", flush=True)

    enriched: list[Job] = []
    failed = 0
    for i, job in enumerate(all_listings.values(), 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            ok = _enrich(session, job)
        except Exception as exc:
            print(f"  [{i}/{len(all_listings)}] {job.native_job_id} FAILED: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            failed += 1
            continue
        if not ok:
            print(f"  [{i}/{len(all_listings)}] {job.native_job_id} "
                  f"no tokens parsed", flush=True)
            failed += 1
            continue
        enriched.append(job)

    # A total detail-page wipeout (layout change / block) must not look like an
    # empty scrape that retires every open row. Abort loudly instead.
    if all_listings and not enriched:
        raise RuntimeError(
            f"SAP: enriched 0 of {len(all_listings)} detail pages "
            f"({failed} failed) — aborting to avoid false-closing DB rows."
        )

    # ---- Phase 3: Work Area scope + employment-type filter -------------------
    kept: list[Job] = []
    dropped_area: dict[str | None, int] = {}
    dropped_type: dict[str | None, int] = {}

    for job in enriched:
        if not _work_area_in_scope(job.category, job.title):
            dropped_area[job.category] = dropped_area.get(job.category, 0) + 1
            marker = f"drop (area: {job.category})"
        elif not _is_regular(job.employment_type):
            dropped_type[job.employment_type] = \
                dropped_type.get(job.employment_type, 0) + 1
            marker = f"drop (type: {job.employment_type})"
        else:
            kept.append(job)
            marker = "KEEP"
        print(f"  [{job.category}] {job.title!r} -> {marker}", flush=True)

    print(flush=True)
    print(f"Work-area + employment filter:", flush=True)
    print(f"  kept          : {len(kept)}", flush=True)
    print(f"  off-scope area: {sum(dropped_area.values())} {dict(dropped_area)}",
          flush=True)
    print(f"  non-regular   : {sum(dropped_type.values())} {dict(dropped_type)}",
          flush=True)
    print(f"  failed        : {failed}", flush=True)

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

    print(f"\n=== {len(jobs)} jobs final "
          f"(total runtime {time.time() - started:.1f}s) ===\n")

    for j in jobs:
        desc = (j["description"] or "").strip().replace("\n", " ")
        desc = desc[:200] + ("…" if len(desc) > 200 else "")
        print(f"[{j['identifier'] or j['native_job_id']}] {j['title']}")
        print(f"  Work Area  : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
