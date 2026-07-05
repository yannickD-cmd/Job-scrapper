"""Veolia job scraper — France, Data & AI titles, CDI only.

Veolia's careers board (jobs.veolia.com) runs on Radancy / TalentBrew — the same
ATS as VINCI, but a different skin. Two things shape this scraper:

1. The AJAX listing endpoint `/en/search-jobs/results` ignores every filtering
   query param (Keyword, Location, facets) and always returns the full global
   list (~3000 jobs). So, like VINCI, we crawl the whole board and filter
   client-side. Cards must be requested with `IsPagination=True&SearchType=5`
   or the JSON comes back with an empty `results` string.

2. Veolia's listing card is minimal: it carries only the title (<h2>) and a
   location ("City, Country"). There is NO category and NO contract type on the
   card. The contract type lives only on the detail page — and Veolia encodes it
   in the JSON-LD `employmentType` using its own label vocabulary:
       "Standard"                -> CDI (the visible page literally shows "CDI")
       "Temporary Work / Casual" -> CDD
       "Apprenticeship"          -> alternance / apprentissage
       "Internship"              -> stage
   So CDI cannot be inferred from the title (a "Chargé de Mission Data" with no
   title marker turned out to be a CDD) — it must be read from the detail page.

Three-pass scrape:

1. LISTING. Walk every page of the AJAX endpoint at 100 records/page. Each card
   yields native_job_id (Radancy numeric id, the dedup key), title and location.

2. CLIENT-SIDE FILTER (cheap, from card). Keep cards whose country segment is
   France (incl. the DROM, e.g. Réunion), and whose TITLE matches the Data & AI
   keyword filter. Title is the only category signal Veolia exposes — this is an
   unusable-category board, so we gate on title like Schneider, not on a facet.

3. ENRICHMENT + CONTRACT GATE. For each survivor, fetch the detail page and read
   the schema.org/JobPosting JSON-LD (description, datePosted, req identifier,
   employmentType). Keep only employmentType == "Standard" (CDI). A detail fetch
   that fails for a non-404 reason ABORTS the run rather than returning a partial
   set — a partial return would let db.persist_run_results false-close the CDI
   rows we couldn't confirm this time (see feedback_partial_scrape_false_close).

To change scope, edit DATA_AI_TITLE / CONTRACTS_IN_SCOPE.
"""
from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

HOST = "https://jobs.veolia.com"
RESULTS_URL = f"{HOST}/en/search-jobs/results"

RECORDS_PER_PAGE = 100
MAX_PAGES = 60  # defensive cap; the board is ~30 pages at 100/page

# Veolia's card exposes no category, so Data & AI scope is a TITLE filter. Kept
# deliberately inclusive (French + English, data-adjacent AI) per
# feedback_include_data_adjacent_ai_roles — a bit of noise is cheaper than
# missing a real AI role, and the CDI gate downstream is what actually thins it.
DATA_AI_TITLE = re.compile(
    r"(\bdata\b|donn[ée]es?|datavi|datalake|datawarehouse|big\s*data|dataiku|"
    r"\bIA\b|intelligence\s+artificielle|\bA\.?I\.?\b|"
    r"machine\s*learning|\bML\b|\bMLOps\b|deep\s*learning|\bLLM\b|\bNLP\b|"
    r"g[ée]n[ée]rative|genai|analytics|analytique|d[ée]cisionnel|"
    r"\bBI\b|business\s+intelligence|"
    r"data\s*scien|data\s*engineer|data\s*analy|data\s*architect|"
    r"data\s*govern|data\s*ops|data\s*steward)",
    re.I,
)

# Contract gate. Veolia's JSON-LD employmentType is its own contract label, not
# schema.org's FULL_TIME/PART_TIME. "Standard" is the CDI marker (the visible
# detail page renders "CDI" for it). CDD / Apprenticeship / Internship are out.
CONTRACTS_IN_SCOPE: frozenset[str] = frozenset({"Standard"})

# Country gate: a job is French if its card's country segment (the text after
# the last comma) is France or a French overseas territory (DROM-COM).
_FRENCH_COUNTRY_SEGMENTS = (
    "france",
    "reunion", "la reunion",
    "guadeloupe", "martinique", "guyane", "mayotte",
    "nouvelle-caledonie", "polynesie francaise", "polynesie",
    "saint-martin", "saint-barthelemy", "saint-pierre-et-miquelon",
    "wallis-et-futuna",
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}
AJAX_HEADERS = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}

REQUEST_DELAY_SECONDS = 1.0       # JSON listing endpoint
DETAIL_DELAY_SECONDS = 2.0        # HTML detail pages
REQUEST_TIMEOUT = 30


@dataclass
class Job:
    native_job_id: str
    title: str
    location: str
    apply_url: str
    category: str | None = None
    employment_type: str | None = None
    # Filled by detail-page enrichment:
    description: str | None = None
    posted_date: str | None = None
    identifier: str | None = None
    raw_payload: dict | None = None


def _norm(text: str) -> str:
    """Lower-case, strip accents — for accent-insensitive matching."""
    decomposed = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").lower()


def _is_french(location: str) -> bool:
    """France (or a DROM-COM) is the text after the card's last comma."""
    country_segment = location.rsplit(",", 1)[-1].strip()
    return _norm(country_segment) in _FRENCH_COUNTRY_SEGMENTS


def _listing_params(page: int) -> str:
    return urlencode({
        "ActiveFacetID": 0,
        "CurrentPage": page,
        "RecordsPerPage": RECORDS_PER_PAGE,
        "Distance": 50,
        "RadiusUnitType": 0,
        "ShowRadius": "False",
        "IsPagination": "True",     # required, or `results` comes back empty
        "FacetType": 0,
        "SearchResultsModuleName": "Search Results",
        "SortCriteria": 0,
        "SortDirection": 1,
        "SearchType": 5,            # required, or `results` comes back empty
        "Keyword": "",
        "Location": "",
    })


def _parse_listing_page(results_html: str) -> tuple[list[Job], int]:
    soup = BeautifulSoup(results_html, "html.parser")

    jobs: list[Job] = []
    for anchor in soup.select("a[data-job-id]"):
        job_id = (anchor.get("data-job-id") or "").strip()
        heading = anchor.find("h2")
        if not job_id or heading is None:
            # The "chevron / View job offer" anchor also carries data-job-id but
            # has no <h2>; skip it so each job is counted once.
            continue

        title = heading.get_text(" ", strip=True)
        loc_el = anchor.select_one(".job-location")
        location = loc_el.get_text(" ", strip=True) if loc_el else ""

        href = anchor.get("href") or ""
        apply_url = HOST + href if href.startswith("/") else href

        jobs.append(Job(
            native_job_id=job_id,
            title=title,
            location=location,
            apply_url=apply_url,
        ))

    total_pages = 1
    section = soup.select_one("[data-total-pages]")
    if section:
        try:
            total_pages = int(section["data-total-pages"])
        except (TypeError, ValueError, KeyError):
            pass

    return jobs, total_pages


def _parse_detail_payload(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    return None


def _normalize_date(raw: str | None) -> str | None:
    """Veolia emits unpadded dates like '2026-3-30'. Pad to ISO YYYY-MM-DD."""
    if not raw:
        return None
    parts = raw.split("-")
    if len(parts) == 3:
        try:
            return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
        except ValueError:
            pass
    return raw


def _crawl_listing(session: requests.Session) -> dict[str, Job]:
    print("Listing phase...", flush=True)
    all_listings: dict[str, Job] = {}  # dedup by native_job_id
    page = 1
    total_pages = 1
    started = time.time()

    while page <= total_pages and page <= MAX_PAGES:
        url = f"{RESULTS_URL}?{_listing_params(page)}"
        response = session.get(url, headers=AJAX_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        results_html = (response.json() or {}).get("results") or ""
        page_jobs, total_pages = _parse_listing_page(results_html)
        for j in page_jobs:
            all_listings.setdefault(j.native_job_id, j)

        print(
            f"  page {page}/{total_pages}: {len(page_jobs)} jobs "
            f"({len(all_listings)} unique so far)",
            flush=True,
        )

        page += 1
        if page <= total_pages and page <= MAX_PAGES:
            time.sleep(REQUEST_DELAY_SECONDS)

    print(f"  → {len(all_listings)} unique jobs in {time.time() - started:.1f}s\n",
          flush=True)
    return all_listings


def _fetch_detail(session: requests.Session, job: Job) -> str | None:
    """GET the detail HTML. Returns None only on a genuine 404 (job removed);
    re-raises any other error after one retry so the caller can abort."""
    for attempt in (1, 2):
        try:
            response = session.get(job.apply_url, timeout=REQUEST_TIMEOUT)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.text
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(DETAIL_DELAY_SECONDS)
    return None


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    # --- Phase 1: listing -------------------------------------------------
    all_listings = _crawl_listing(session)

    # --- Phase 2: client-side filter (country + Data&AI title) ------------
    candidates = [
        j for j in all_listings.values()
        if _is_french(j.location) and DATA_AI_TITLE.search(j.title)
    ]
    print(
        f"Filter [country=France, title=Data&AI]: "
        f"{len(candidates)}/{len(all_listings)} candidates\n",
        flush=True,
    )

    # --- Phase 3: enrichment + CDI gate -----------------------------------
    print(
        f"Enrichment phase: fetching {len(candidates)} detail pages "
        f"(~{int(len(candidates) * DETAIL_DELAY_SECONDS)}s)...",
        flush=True,
    )

    kept: list[Job] = []
    dropped_contract = 0
    dropped_gone = 0

    for i, job in enumerate(candidates, 1):
        time.sleep(DETAIL_DELAY_SECONDS)
        html = _fetch_detail(session, job)  # non-404 errors propagate -> abort
        if html is None:
            dropped_gone += 1
            print(f"  [{i}/{len(candidates)}] {job.native_job_id} → 404, drop",
                  flush=True)
            continue

        payload = _parse_detail_payload(html)
        if payload:
            job.description = payload.get("description")
            job.posted_date = _normalize_date(payload.get("datePosted"))
            job.identifier = payload.get("identifier")
            job.employment_type = payload.get("employmentType")
            job.category = payload.get("industry")
            job.raw_payload = payload

        if job.employment_type not in CONTRACTS_IN_SCOPE:
            dropped_contract += 1
            print(f"  [{i}/{len(candidates)}] {job.identifier or job.native_job_id} "
                  f"{job.title!r} → drop ({job.employment_type})", flush=True)
            continue

        kept.append(job)
        print(f"  [{i}/{len(candidates)}] {job.identifier or job.native_job_id} "
              f"{job.title!r} → keep (CDI)", flush=True)

    print(flush=True)
    print(f"Enrichment: kept {len(kept)} CDI, dropped(contract) {dropped_contract}, "
          f"dropped(404) {dropped_gone}", flush=True)

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
    print(f"\n=== {len(jobs)} jobs final (total runtime {elapsed:.1f}s) ===\n")

    for j in jobs:
        desc = BeautifulSoup(j["description"] or "", "html.parser").get_text(" ", strip=True)
        desc = desc[:200] + ("…" if len(desc) > 200 else "")
        print(f"[{j['identifier'] or j['native_job_id']}] {j['title']}")
        print(f"  Industry   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
