"""ServiceNow job scraper — France, Data/AI + adjacent tech, all employment types.

ServiceNow's career board (careers.servicenow.com) is a custom Umbraco site.
Results are server-rendered, and the server honours the `country`/`region`/
`location`/`search`/`team` query params — BUT only when the request carries a
real browser User-Agent. A bot/library UA is served a static, unfiltered
"393 jobs" default snapshot (country=India, search=data, etc. all return the
exact same page). So this scraper MUST send a full Chrome UA, same tradeoff
already accepted for Revolut/BNP in this repo. robots.txt allows `/jobs/`.

Two-pass scrape:

1. LISTING. Walks the France-filtered list (`?country=France&page=N`, 20 rows
   per page). France is a GTM/sales office, so the whole board is tiny (~8 open
   reqs) — engineering sits in the US/India/EU dev hubs, not Issy. Each card
   gives native_job_id (the numeric listing id), title, location, apply_url.
   There is NO usable category/department signal on the card or the detail that
   isolates Data/AI, so we filter on the TITLE via the shared is_tech_role
   predicate (like Schneider/Sia/N26) — keep AI/data/software/architect/platform
   roles, drop pure Sales/GTM (Account Executives, Partner Manager, COO, ...).

2. ENRICHMENT. For each surviving listing, fetch the detail page and read the
   embedded schema.org/JobPosting JSON-LD (`<script id="js-job-posting">`, always
   present). That gives description, posted_date (real datePosted, already ISO),
   employmentType (only FULL_TIME is exposed — there is no CDI/CDD split, this is
   a US company) and the human req id (identifier, e.g. "JB0072409"). Enrichment
   is best-effort: a transient detail failure keeps the listing-only row rather
   than dropping it, so a missing detail can't false-close the DB row (db.py
   COALESCEs description/posted_date on re-persist).

To widen/narrow scope, adjust the is_tech_role gate or COUNTRY.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

from scrapers._relevance import is_tech_role

HOST = "https://careers.servicenow.com"
LISTING_URL = HOST + "/jobs/?country={country}&page={page}"
COUNTRY = "France"

PAGE_SIZE = 20          # ServiceNow renders 20 cards per results page
MAX_PAGES = 30          # defensive cap — France is ~1 page; guards a pagination bug

# A browser UA is REQUIRED (see module docstring): the server serves a static
# unfiltered default to non-browser UAs, so a "polite project" UA would silently
# return the wrong (worldwide) job set.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

REQUEST_DELAY_SECONDS = 2.0
REQUEST_TIMEOUT = 30


@dataclass
class Job:
    native_job_id: str
    title: str
    location: str
    apply_url: str
    # Filled by detail-page enrichment:
    description: str | None = None
    posted_date: str | None = None
    employment_type: str | None = None
    identifier: str | None = None
    raw_payload: dict | None = None


def _parse_listing_page(html: str) -> tuple[list[Job], int]:
    """Return (jobs on this page, total result count from data-results)."""
    soup = BeautifulSoup(html, "html.parser")

    total = 0
    results_el = soup.select_one("#js-job-search-results[data-results]")
    if results_el:
        try:
            total = int(results_el["data-results"])
        except (TypeError, ValueError, KeyError):
            total = 0

    jobs: list[Job] = []
    for card in soup.select("div.card.card-job"):
        anchor = card.select_one("a.js-view-job[href]")
        if not anchor:
            continue
        href = anchor.get("href") or ""
        # href looks like /jobs/744000129996714/senior-ai-agent-engineer.../
        parts = [p for p in href.split("/") if p]
        job_id = parts[1] if len(parts) >= 2 and parts[0] == "jobs" else ""
        if not job_id.isdigit():
            continue

        title = anchor.get_text(" ", strip=True)
        loc_el = card.select_one(".job-meta li")
        location = loc_el.get_text(" ", strip=True) if loc_el else ""
        apply_url = HOST + href if href.startswith("/") else href

        jobs.append(Job(
            native_job_id=job_id,
            title=title,
            location=location,
            apply_url=apply_url,
        ))

    return jobs, total


def _parse_detail_payload(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    # The tag's type is HTML-entity-encoded (ld&#x2B;json); BeautifulSoup decodes
    # it, but selecting by the stable id is simplest and unambiguous.
    script = soup.select_one("script#js-job-posting")
    if not script or not script.string:
        return None
    try:
        data = json.loads(script.string)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(data, dict) and data.get("@type") == "JobPosting":
        return data
    return None


def _location_from_payload(payload: dict) -> str | None:
    loc = payload.get("jobLocation")
    if isinstance(loc, dict):
        name = loc.get("name")
        if name:
            return name
    return None


def _enrich(session: requests.Session, job: Job) -> bool:
    """Fetch detail page, fill enrichment fields. Returns True on success."""
    response = session.get(job.apply_url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    payload = _parse_detail_payload(response.text)
    if not payload:
        return False

    job.description = payload.get("description")
    job.posted_date = payload.get("datePosted") or None
    job.employment_type = payload.get("employmentType")
    job.identifier = payload.get("identifier")
    job.location = _location_from_payload(payload) or job.location
    job.raw_payload = payload
    return True


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    print("Listing phase...", flush=True)
    all_listings: dict[str, Job] = {}  # dedup by native_job_id
    total = 0

    for page in range(1, MAX_PAGES + 1):
        url = LISTING_URL.format(country=COUNTRY, page=page)
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        page_jobs, page_total = _parse_listing_page(response.text)
        if page_total:
            total = page_total
        for j in page_jobs:
            all_listings.setdefault(j.native_job_id, j)

        print(
            f"  page {page}: {len(page_jobs)} cards "
            f"({len(all_listings)}/{total or '?'} unique so far)",
            flush=True,
        )

        # Stop when this page had no cards, or we've collected the full count.
        if not page_jobs or (total and len(all_listings) >= total):
            break
        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"  → {len(all_listings)} unique {COUNTRY} jobs\n", flush=True)

    # Title filter — Data/AI + adjacent tech; drop pure Sales/GTM.
    in_scope = [j for j in all_listings.values() if is_tech_role(j.title)]
    dropped = [j for j in all_listings.values() if not is_tech_role(j.title)]
    print(f"Title filter (is_tech_role): {len(in_scope)}/{len(all_listings)} kept",
          flush=True)
    for j in dropped:
        print(f"  drop: {j.title!r}", flush=True)
    print(flush=True)

    # Enrichment — best-effort; keep listing-only rows on failure (no false-close).
    print(
        f"Enrichment phase: fetching {len(in_scope)} detail pages "
        f"(~{int(len(in_scope) * REQUEST_DELAY_SECONDS)}s)...",
        flush=True,
    )
    failed = 0
    for i, job in enumerate(in_scope, 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            ok = _enrich(session, job)
        except Exception as exc:
            print(f"  [{i}/{len(in_scope)}] {job.native_job_id} enrich FAILED: "
                  f"{type(exc).__name__}: {exc} (kept with listing data)", flush=True)
            failed += 1
            continue
        if not ok:
            print(f"  [{i}/{len(in_scope)}] {job.native_job_id} no JSON-LD "
                  f"(kept with listing data)", flush=True)
            failed += 1
            continue
        print(f"  [{i}/{len(in_scope)}] {job.identifier or job.native_job_id} "
              f"{job.title!r} → {job.employment_type} · {job.posted_date}", flush=True)

    print(f"\nEnrichment: {len(in_scope) - failed} enriched, {failed} listing-only",
          flush=True)
    return [asdict(j) for j in in_scope]


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
        desc_preview = BeautifulSoup(desc_preview, "html.parser").get_text(" ", strip=True)
        desc_preview = desc_preview[:200] + ("…" if len(desc_preview) > 200 else "")

        print(f"[{j['identifier'] or j['native_job_id']}] {j['title']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc_preview}")
        print()
