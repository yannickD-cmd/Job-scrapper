"""Safran job scraper — France, CDI (Permanent), tech-track job-fields.

Safran's careers board is a Drupal listing at https://www.safran-group.com/jobs.
All filters we need are URL-encoded query params, so the server returns a
pre-filtered listing (no client-side filter needed):

    /jobs
      ?countries[]=1002-france
      &contracts[]=66-permanent
      &job_fields[]=1578-data
      &job_fields[]=1656-software
      &job_fields[]=1608-it
      &job_fields[]=1620-mathematics-and-algorithms
      &job_fields[]=1566-architecture-and-systems-engineering
      &page={N}        # 0-indexed: page=0 is "Page 1"

Two-pass scrape (Sanofi template):

1. LISTING. Walks all pages of the filtered list. Each <div.c-offer-item>
   gives us native_job_id (trailing numeric segment of the slug), title,
   apply_url, company (Safran subsidiary), location, contract, job-field,
   and job-status.

2. ENRICHMENT. For each unique URL, fetches the detail page and reads the
   schema.org/JobPosting JSON-LD block (always present on Safran detail
   pages — confirmed across IT/Software/Data offers). That yields the full
   description, identifier (e.g. "2026-174982"), datePosted (ISO), and the
   structured jobLocation address.

WAF note: safran-group.com TLS-fingerprints clients — plain `requests`
gets a flat 403 regardless of UA/headers, while `curl` passes. We use
`curl_cffi` with a Chrome impersonation profile (same workaround BNP,
L'Oréal, and CGI use). If CI ever starts failing with 403, the next
step is to exclude `safran` from .github/workflows/scrape.yml the same
way BNP is — Akamai-class WAFs sometimes also IP-block GitHub Actions.

To change scope, edit COUNTRY_FACETS / CONTRACT_FACETS / JOB_FIELD_FACETS.
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

HOST = "https://www.safran-group.com"
LISTING_PATH = "/jobs"

# Server-side facets (slug = "<numeric-id>-<slug>", taken from the form's
# <option value="..."> attributes).
COUNTRY_FACETS: list[str] = ["1002-france"]
CONTRACT_FACETS: list[str] = ["66-permanent"]
JOB_FIELD_FACETS: list[str] = [
    "1578-data",
    "1656-software",
    "1608-it",
    "1620-mathematics-and-algorithms",
    "1566-architecture-and-systems-engineering",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "From": "yannickarieldossa@gmail.com",
}

# curl_cffi TLS impersonation profile. Plain `requests` gets a flat
# 403 from the Safran WAF regardless of headers, so we mimic Chrome's
# JA3/JA4 handshake. See BNP scraper for the canonical pattern.
IMPERSONATE_PROFILE = "chrome131"

REQUEST_DELAY_SECONDS = 2.0
REQUEST_TIMEOUT = 30
MAX_PAGES = 50  # safety cap; real total is ~27 today

_JOB_ID_RE = re.compile(r"-(\d+)/?$")


@dataclass
class Job:
    native_job_id: str
    title: str
    apply_url: str
    # From listing card:
    company: str | None = None
    location: str | None = None
    category: str | None = None  # Safran "Domaine" (job-field)
    employment_type: str | None = None  # Contract (Permanent)
    job_status: str | None = None  # Professional, Engineer & Manager / Technician / ...
    # From detail-page JSON-LD enrichment:
    description: str | None = None
    posted_date: str | None = None
    identifier: str | None = None
    raw_payload: dict | None = None


def _listing_url(page: int) -> str:
    params: list[tuple[str, str]] = []
    for v in COUNTRY_FACETS:
        params.append(("countries[]", v))
    for v in CONTRACT_FACETS:
        params.append(("contracts[]", v))
    for v in JOB_FIELD_FACETS:
        params.append(("job_fields[]", v))
    params.append(("page", str(page)))
    return f"{HOST}{LISTING_PATH}?{urlencode(params)}"


def _extract_job_id(url: str) -> str | None:
    m = _JOB_ID_RE.search(url)
    return m.group(1) if m else None


def _parse_listing_page(html: str) -> tuple[list[Job], int]:
    """Return (jobs, last_page_index). last_page_index is 0-based."""
    soup = BeautifulSoup(html, "html.parser")

    jobs: list[Job] = []
    for card in soup.select("div.c-offer-item"):
        title_a = card.select_one("a.c-offer-item__title")
        if not title_a:
            continue
        href = (title_a.get("href") or "").strip()
        apply_url = href if href.startswith("http") else HOST + href
        title = title_a.get_text(" ", strip=True)

        job_id = _extract_job_id(apply_url)
        if not job_id:
            continue

        info_items = card.select(".c-offer-item__infos__item")
        # The first item is the Safran subsidiary (icon-hierarchy); the rest
        # vary in order but each carries a single descriptor.
        company = (
            info_items[0].get_text(" ", strip=True) if info_items else None
        )

        location = category = employment_type = job_status = None
        for item in info_items[1:] if info_items else []:
            text = item.get_text(" ", strip=True)
            classes = " ".join(
                cls
                for child in item.find_all(True, recursive=False)
                for cls in (child.get("class") or [])
            )
            if "icon-location" in classes:
                location = text
            elif "icon-tags" in classes:
                category = text
            elif "icon-file1" in classes:
                employment_type = text
            elif "icon-status" in classes:
                job_status = text

        jobs.append(Job(
            native_job_id=job_id,
            title=title,
            apply_url=apply_url,
            company=company,
            location=location,
            category=category,
            employment_type=employment_type,
            job_status=job_status,
        ))

    # Pagination: find anchor titled "Go to last page" → its &page=N is the
    # last 0-based page index. Fall back to scanning all pagination links.
    last_page_idx = 0
    last_anchor = soup.select_one('a.pagination__page[title="Go to last page"]')
    if last_anchor and last_anchor.get("href"):
        m = re.search(r"[?&]page=(\d+)", last_anchor["href"])
        if m:
            last_page_idx = int(m.group(1))
    if last_page_idx == 0:
        for a in soup.select("a.pagination__page"):
            m = re.search(r"[?&]page=(\d+)", a.get("href", ""))
            if m:
                last_page_idx = max(last_page_idx, int(m.group(1)))

    return jobs, last_page_idx


def _parse_detail_payload(html: str) -> dict | None:
    """Return the schema.org JobPosting dict from the detail page, or None.

    Safran wraps it in an @graph array under @context=https://schema.org.
    """
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or ""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        nodes: list[dict] = []
        if isinstance(data, dict):
            if data.get("@type") == "JobPosting":
                return data
            graph = data.get("@graph")
            if isinstance(graph, list):
                nodes.extend(n for n in graph if isinstance(n, dict))
        elif isinstance(data, list):
            nodes.extend(n for n in data if isinstance(n, dict))
        for node in nodes:
            if node.get("@type") == "JobPosting":
                return node
    return None


def _enrich(session: cffi_requests.Session, job: Job) -> bool:
    response = session.get(job.apply_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    payload = _parse_detail_payload(response.text)
    if not payload:
        return False

    job.description = payload.get("description")
    job.posted_date = payload.get("datePosted")
    job.identifier = payload.get("identifier")
    # Prefer JSON-LD employmentType when present; listing already pre-filtered
    # by contract, but JSON-LD is canonical.
    if payload.get("employmentType"):
        job.employment_type = payload["employmentType"]
    job.raw_payload = payload
    return True


def scrape() -> list[dict]:
    session = cffi_requests.Session(impersonate=IMPERSONATE_PROFILE)

    print("Listing phase...", flush=True)
    all_listings: dict[str, Job] = {}
    page = 0
    last_page_idx = 0
    started = time.time()

    while page < MAX_PAGES:
        url = _listing_url(page)
        response = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        page_jobs, page_last_idx = _parse_listing_page(response.text)
        last_page_idx = max(last_page_idx, page_last_idx)
        for j in page_jobs:
            all_listings.setdefault(j.native_job_id, j)

        print(
            f"  page {page + 1}/{last_page_idx + 1}: "
            f"{len(page_jobs)} jobs ({len(all_listings)} unique so far)",
            flush=True,
        )

        if not page_jobs:
            # End of pagination: server returned an empty results block.
            break
        if page >= last_page_idx:
            break

        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    listing_elapsed = time.time() - started
    print(
        f"  → {len(all_listings)} unique jobs in {listing_elapsed:.1f}s\n",
        flush=True,
    )

    # Enrichment phase
    print(
        f"Enrichment phase: fetching {len(all_listings)} detail pages "
        f"(~{int(len(all_listings) * REQUEST_DELAY_SECONDS)}s)...",
        flush=True,
    )

    kept: list[Job] = []
    failed = 0
    listings = list(all_listings.values())
    for i, job in enumerate(listings, 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            ok = _enrich(session, job)
        except Exception as exc:
            print(
                f"  [{i}/{len(listings)}] {job.native_job_id} FAILED: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            failed += 1
            continue

        if not ok:
            print(
                f"  [{i}/{len(listings)}] {job.native_job_id} no JSON-LD found",
                flush=True,
            )
            # Still keep the row — listing meta is enough to satisfy the
            # contract (native_job_id + title + apply_url).
        kept.append(job)
        print(
            f"  [{i}/{len(listings)}] "
            f"{job.identifier or job.native_job_id} {job.title!r}",
            flush=True,
        )

    print(flush=True)
    print(f"  kept   : {len(kept)}", flush=True)
    print(f"  failed : {failed}", flush=True)

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
        desc_preview = BeautifulSoup(desc_preview, "html.parser").get_text(" ", strip=True)
        desc_preview = desc_preview[:200] + ("…" if len(desc_preview) > 200 else "")

        print(f"[{j['identifier'] or j['native_job_id']}] {j['title']}")
        print(f"  Company    : {j['company']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Status     : {j['job_status']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc_preview}")
        print()
