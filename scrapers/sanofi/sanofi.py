"""Sanofi job scraper — France, Digital Data & Technology, Regular only.

Two-pass scrape:

1. LISTING. Walks all pages of the France-filtered list, parsing one row
   per <a data-job-id>. Each row has: native_job_id, title, location,
   category, apply_url. The category is in the listing HTML — no detail
   page needed — so we filter to CATEGORIES_IN_SCOPE here, before any
   detail-page fetches.

2. ENRICHMENT. For each surviving listing, fetches the detail page and
   reads the embedded schema.org/JobPosting JSON-LD block (always present
   on Sanofi detail pages). That gives us description, posted_date (the
   real Sanofi datePosted, not when we saw it), Sanofi req id, and
   employmentType. We then drop anything whose employmentType isn't in
   JOB_TYPES_IN_SCOPE.

URL pattern (robots-allowed, sitemap-indexed):
    https://jobs.sanofi.com/en/location/france-jobs/2649/3017382/2/{page}
    2649=Sanofi org, 3017382=France facet, /2/=depth marker, {page}=1..N.

To change scope, edit CATEGORIES_IN_SCOPE / JOB_TYPES_IN_SCOPE.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

LISTING_URL = "https://jobs.sanofi.com/en/location/france-jobs/2649/3017382/2/{page}"
HOST = "https://jobs.sanofi.com"

CATEGORIES_IN_SCOPE: set[str] = {"Digital Data & Technology"}
JOB_TYPES_IN_SCOPE: set[str] = {"Regular"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_DELAY_SECONDS = 2.0
REQUEST_TIMEOUT = 30


@dataclass
class Job:
    native_job_id: str
    title: str
    location: str
    category: str
    apply_url: str
    # Filled by detail-page enrichment:
    description: str | None = None
    posted_date: str | None = None
    employment_type: str | None = None
    identifier: str | None = None
    raw_payload: dict | None = None


def _parse_listing_page(html: str) -> tuple[list[Job], int]:
    soup = BeautifulSoup(html, "html.parser")

    jobs: list[Job] = []
    for anchor in soup.select("a[data-job-id]"):
        job_id = (anchor.get("data-job-id") or "").strip()
        if not job_id:
            continue

        title_el = anchor.find("h2")
        loc_el = anchor.select_one(".job-location")
        cat_el = anchor.select_one(".job-category")

        title = title_el.get_text(strip=True) if title_el else ""
        location = (
            loc_el.get_text(" ", strip=True).removeprefix("Location:").strip()
            if loc_el else ""
        )
        category = (
            cat_el.get_text(" ", strip=True).removeprefix("Category:").strip()
            if cat_el else ""
        )

        href = anchor.get("href") or ""
        apply_url = HOST + href if href.startswith("/") else href

        jobs.append(Job(
            native_job_id=job_id,
            title=title,
            location=location,
            category=category,
            apply_url=apply_url,
        ))

    total_pages = 1
    pag_input = soup.select_one("input.pagination-current")
    if pag_input and pag_input.get("max"):
        try:
            total_pages = int(pag_input["max"])
        except (TypeError, ValueError):
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
    """Sanofi sometimes emits unpadded dates like '2026-5-14'. Pad to ISO."""
    if not raw:
        return None
    parts = raw.split("-")
    if len(parts) == 3:
        try:
            return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
        except ValueError:
            pass
    return raw


def _enrich(session: requests.Session, job: Job) -> bool:
    """Fetch detail page, fill enrichment fields. Returns True on success."""
    response = session.get(job.apply_url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    payload = _parse_detail_payload(response.text)
    if not payload:
        return False

    job.description = payload.get("description")
    job.posted_date = _normalize_date(payload.get("datePosted"))
    job.employment_type = payload.get("employmentType")
    job.identifier = payload.get("identifier")
    job.raw_payload = payload
    return True


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    print("Listing phase...", flush=True)
    all_listings: dict[str, Job] = {}  # dedup by native_job_id
    page = 1
    total_pages = 1
    started = time.time()

    while page <= total_pages:
        url = LISTING_URL.format(page=page)
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        page_jobs, total_pages = _parse_listing_page(response.text)
        for j in page_jobs:
            all_listings.setdefault(j.native_job_id, j)

        print(
            f"  page {page}/{total_pages}: "
            f"{len(page_jobs)} jobs ({len(all_listings)} unique so far)",
            flush=True,
        )

        page += 1
        if page <= total_pages:
            time.sleep(REQUEST_DELAY_SECONDS)

    listing_elapsed = time.time() - started
    print(f"  → {len(all_listings)} unique jobs in {listing_elapsed:.1f}s\n", flush=True)

    # Phase 2: category filter
    in_scope = [j for j in all_listings.values() if j.category in CATEGORIES_IN_SCOPE]
    print(
        f"Category filter {sorted(CATEGORIES_IN_SCOPE)}: "
        f"{len(in_scope)}/{len(all_listings)} kept\n",
        flush=True,
    )

    # Phase 3: detail-page enrichment + job-type filter
    print(
        f"Enrichment phase: fetching {len(in_scope)} detail pages "
        f"(~{int(len(in_scope) * REQUEST_DELAY_SECONDS)}s)...",
        flush=True,
    )

    kept: list[Job] = []
    dropped_by_type: dict[str | None, int] = {}
    failed = 0

    for i, job in enumerate(in_scope, 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            ok = _enrich(session, job)
        except Exception as exc:
            print(f"  [{i}/{len(in_scope)}] {job.native_job_id} FAILED: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            failed += 1
            continue

        if not ok:
            print(f"  [{i}/{len(in_scope)}] {job.native_job_id} no JSON-LD found",
                  flush=True)
            failed += 1
            continue

        if job.employment_type in JOB_TYPES_IN_SCOPE:
            kept.append(job)
            marker = "KEEP"
        else:
            dropped_by_type[job.employment_type] = \
                dropped_by_type.get(job.employment_type, 0) + 1
            marker = f"drop ({job.employment_type})"

        print(f"  [{i}/{len(in_scope)}] {job.identifier or job.native_job_id} "
              f"{job.title!r} → {marker}", flush=True)

    print(flush=True)
    print(f"Job-type filter {sorted(JOB_TYPES_IN_SCOPE)}:", flush=True)
    print(f"  kept    : {len(kept)}", flush=True)
    print(f"  dropped : {sum(dropped_by_type.values())} "
          f"(by type: {dict(dropped_by_type)})", flush=True)
    print(f"  failed  : {failed}", flush=True)

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
        desc_preview = (j["description"] or "").strip()
        # strip HTML tags for the preview only
        desc_preview = BeautifulSoup(desc_preview, "html.parser").get_text(" ", strip=True)
        desc_preview = desc_preview[:200] + ("…" if len(desc_preview) > 200 else "")

        print(f"[{j['identifier'] or j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc_preview}")
        print()
