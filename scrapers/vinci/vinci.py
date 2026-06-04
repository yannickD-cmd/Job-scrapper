"""VINCI job scraper — France, Data & AI + Software/IT family, CDI only.

VINCI's careers site (jobs.vinci.com) runs on Radancy / TalentBrew. The page
URL carries facets in the *path* (geo radius + location hierarchy + categories);
the AJAX endpoint `/en/search-jobs/results` ignores every filtering query param
and always returns the full global list. So we can't filter server-side — we
crawl the whole board and filter client-side from the listing cards.

Three-pass scrape:

1. LISTING. Walk every page of the AJAX endpoint with `IsPagination=True`
   (drops the ~1.9 MB filters block from each response). The JSON wraps a
   `results` HTML string; each card carries native_job_id, title, location,
   category and contract type ("Permanent" = CDI) — enough to filter before
   any detail-page fetch.

2. CLIENT-SIDE FILTER. Keep cards whose category is the IT / IT SYSTEMS family
   (VINCI has no dedicated Data category; this single family covers data, AI,
   BI, software, infra, security and SAP roles), whose contract is Permanent,
   and whose location region is in metropolitan France or a DROM. The card
   region ("Île-de-France Region", "Bretagne", …) is the country gate: VINCI's
   detail-page JSON-LD frequently leaves addressCountry empty for FR jobs, so
   the listing region is the reliable French signal.

3. ENRICHMENT. For each survivor, fetch the detail page and read the
   schema.org/JobPosting JSON-LD (description, datePosted, VINCI req id,
   employmentType). If the JSON-LD's addressCountry explicitly names a foreign
   country, drop the row (guards against a mislabelled card).

To change scope, edit CATEGORIES_IN_SCOPE / CONTRACTS_IN_SCOPE.
"""
from __future__ import annotations

import json
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

HOST = "https://jobs.vinci.com"
RESULTS_URL = f"{HOST}/en/search-jobs/results"

RECORDS_PER_PAGE = 100
MAX_PAGES = 200  # defensive cap; the board is ~60 pages at 100/page

# VINCI's job-category taxonomy is construction-centric and has no "Data"
# bucket — data/AI/BI/software/infra/security/SAP roles all live under this
# single family. Scope chosen with the user: Data & AI + Software/IT.
CATEGORIES_IN_SCOPE: set[str] = {"IT / IT SYSTEMS"}

# Contract types to keep. The card shows the English Radancy label; "Permanent"
# is the CDI marker (some rows read "Permanent - full time").
CONTRACTS_IN_SCOPE: tuple[str, ...] = ("Permanent",)

# Country gate: a job is French if its listing region matches one of these
# (accent-insensitive substring) or the location text contains "france".
_FRENCH_REGIONS = (
    "ile-de-france", "auvergne-rhone-alpes", "bourgogne-franche-comte",
    "bretagne", "centre-val de loire", "corse", "grand est",
    "hauts-de-france", "normandie", "nouvelle-aquitaine", "occitanie",
    "pays de la loire", "provence-alpes-cote d'azur",
    # Overseas (DROM)
    "guadeloupe", "martinique", "guyane", "la reunion", "reunion", "mayotte",
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

REQUEST_DELAY_SECONDS = 1.0       # JSON listing endpoint
DETAIL_DELAY_SECONDS = 2.0        # HTML detail pages
REQUEST_TIMEOUT = 30


@dataclass
class Job:
    native_job_id: str
    title: str
    location: str
    category: str
    employment_type: str
    apply_url: str
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
    n = _norm(location)
    if "france" in n:
        return True
    return any(region in n for region in _FRENCH_REGIONS)


def _listing_params(page: int) -> str:
    return urlencode({
        "CurrentPage": page,
        "RecordsPerPage": RECORDS_PER_PAGE,
        "ShowRadius": "False",
        "IsPagination": "True",
        "SearchResultsModuleName": "Search Results",
        "SortCriteria": 0,
        "SortDirection": 0,
        "Keyword": "",
        "Location": "",
    })


def _text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def _parse_listing_page(results_html: str) -> tuple[list[Job], int]:
    soup = BeautifulSoup(results_html, "html.parser")

    jobs: list[Job] = []
    for anchor in soup.select("a.search-results--link[data-job-id]"):
        job_id = (anchor.get("data-job-id") or "").strip()
        if not job_id:
            continue

        title = _text(anchor.select_one(".search-results--link-jobtitle"))
        location = _text(anchor.select_one(".job-location"))
        category = _text(anchor.select_one(".job-categories"))
        contract = _text(anchor.select_one(".search-results--link-job-type"))

        href = anchor.get("href") or ""
        apply_url = HOST + href if href.startswith("/") else href

        jobs.append(Job(
            native_job_id=job_id,
            title=title,
            location=location,
            category=category,
            employment_type=contract,
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
    """VINCI emits unpadded dates like '2026-5-19'. Pad to ISO YYYY-MM-DD."""
    if not raw:
        return None
    parts = raw.split("-")
    if len(parts) == 3:
        try:
            return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
        except ValueError:
            pass
    return raw


def _payload_country_text(payload: dict) -> str:
    """Concatenate everything country-ish from jobLocation for the FR check."""
    loc = payload.get("jobLocation")
    places = loc if isinstance(loc, list) else [loc]
    bits: list[str] = []
    for place in places:
        if not isinstance(place, dict):
            continue
        addr = place.get("address") or {}
        if isinstance(addr, dict):
            for key in ("addressCountry", "addressRegion",
                        "addressLocality", "streetAddress"):
                val = addr.get(key)
                if isinstance(val, str):
                    bits.append(val)
    return " ".join(bits)


def _is_foreign_per_detail(payload: dict) -> bool:
    """True only if the detail page *explicitly* places the job outside France.

    addressCountry is often blank on FR rows, so blank/France never drops a row;
    we only drop when an addressCountry names a non-French country and no part of
    the address mentions France.
    """
    loc = payload.get("jobLocation")
    places = loc if isinstance(loc, list) else [loc]
    countries = []
    for place in places:
        if isinstance(place, dict):
            addr = place.get("address") or {}
            if isinstance(addr, dict):
                c = addr.get("addressCountry")
                if isinstance(c, str) and c.strip():
                    countries.append(_norm(c))
    if not countries:
        return False
    full = _norm(_payload_country_text(payload))
    if "france" in full:
        return False
    return all(c not in ("france", "fr") for c in countries)


def _enrich(session: requests.Session, job: Job) -> None:
    """Fetch detail page, fill enrichment fields. Silent best-effort."""
    response = session.get(job.apply_url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    payload = _parse_detail_payload(response.text)
    if not payload:
        return

    job.description = payload.get("description")
    job.posted_date = _normalize_date(payload.get("datePosted"))
    job.identifier = payload.get("identifier")
    if payload.get("employmentType"):
        job.employment_type = payload["employmentType"]
    job.raw_payload = payload


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    # --- Phase 1: listing -------------------------------------------------
    print("Listing phase...", flush=True)
    ajax_headers = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}
    all_listings: dict[str, Job] = {}  # dedup by native_job_id
    page = 1
    total_pages = 1
    started = time.time()

    while page <= total_pages and page <= MAX_PAGES:
        url = f"{RESULTS_URL}?{_listing_params(page)}"
        response = session.get(url, headers=ajax_headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        data = response.json()
        results_html = data.get("results") or ""
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

    # --- Phase 2: client-side filter (family + contract + country) --------
    in_scope = [
        j for j in all_listings.values()
        if j.category in CATEGORIES_IN_SCOPE
        and j.employment_type.startswith(CONTRACTS_IN_SCOPE)
        and _is_french(j.location)
    ]
    print(
        f"Filter [category={sorted(CATEGORIES_IN_SCOPE)}, "
        f"contract={list(CONTRACTS_IN_SCOPE)}, country=France]: "
        f"{len(in_scope)}/{len(all_listings)} kept\n",
        flush=True,
    )

    # --- Phase 3: detail-page enrichment ----------------------------------
    print(
        f"Enrichment phase: fetching {len(in_scope)} detail pages "
        f"(~{int(len(in_scope) * DETAIL_DELAY_SECONDS)}s)...",
        flush=True,
    )

    kept: list[Job] = []
    dropped_foreign = 0
    failed = 0

    for i, job in enumerate(in_scope, 1):
        time.sleep(DETAIL_DELAY_SECONDS)
        try:
            _enrich(session, job)
        except Exception as exc:
            print(f"  [{i}/{len(in_scope)}] {job.native_job_id} enrich FAILED: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            failed += 1
            # Keep it — card data already satisfies the contract.
            kept.append(job)
            continue

        if job.raw_payload and _is_foreign_per_detail(job.raw_payload):
            dropped_foreign += 1
            print(f"  [{i}/{len(in_scope)}] {job.native_job_id} {job.title!r} "
                  f"→ drop (detail country not France)", flush=True)
            continue

        kept.append(job)
        print(f"  [{i}/{len(in_scope)}] {job.identifier or job.native_job_id} "
              f"{job.title!r} → keep", flush=True)

    print(flush=True)
    print(f"Enrichment: kept {len(kept)}, dropped(foreign) {dropped_foreign}, "
          f"failed {failed}", flush=True)

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
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
