"""BNP Paribas job scraper — France, Digital transformation and data, Permanent.

The filter is fully encoded in the BNP URL path, so unlike Sanofi we
don't need a client-side category/employment-type pass: every row the
listing returns already matches the scope. URL path order is
`type → domain → country` — any other order 404s (probed).

Two-pass scrape (same shape as Sanofi):

1. LISTING. Walks `?page=N` of the filtered URL. Each `<article class="card-offer">`
   gives us native_job_id (URL slug), title, location, apply_url. Stop
   condition is the page header's "<N> job offers" count.

2. ENRICHMENT. For each listing, fetches the detail page and reads the
   embedded schema.org/JobPosting JSON-LD block — same pattern Sanofi
   uses. Gives description, posted_date, BNP req-id (identifier).

Akamai note: BNP sits behind Akamai Bot Manager. The very first
request to the origin returns 403 with an `ak_bot` cookie set on the
session. The *next* request — any path, same session, with
browser-shaped headers + a Referer — goes through and seeds the full
cookie set (`_abck`, `ak_bmsc`, `bm_sz`) that the rest of the session
inherits. We warm up by hitting the homepage first.

To change scope: edit BASE_URL. URL path slugs come from BNP's facet
markup (see scrapers/bnp/material/all_job_offers.html).
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

HOST = "https://group.bnpparibas"
BASE_URL = (
    HOST
    + "/en/careers/all-job-offers/permanent/digital-transformation-and-data/france"
)

# A browser-shaped UA + Sec-Fetch-* headers are required to clear Akamai.
# The plain "personal-job-tracker" UA we use for Sanofi gets a 403 here.
# We still identify ourselves via the `From:` header so a sysadmin
# looking at access logs can reach us if anything looks wrong.
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

REQUEST_DELAY_SECONDS = 2.0
REQUEST_TIMEOUT = 30

# Hardcoded because every job we return matches these by construction
# (the filter is in BASE_URL's path). Storing the values explicitly so
# downstream readers don't have to re-derive the filter from the URL.
SCOPE_CATEGORY = "Digital transformation and data"
SCOPE_EMPLOYMENT_TYPE = "Permanent"
SCOPE_COUNTRY = "France"


@dataclass
class Job:
    native_job_id: str          # URL slug — stable per BNP, known at listing time
    title: str
    location: str
    category: str
    apply_url: str
    employment_type: str        # always SCOPE_EMPLOYMENT_TYPE for this scraper
    # Filled by detail-page enrichment:
    description: str | None = None
    posted_date: str | None = None
    identifier: str | None = None
    raw_payload: dict | None = None


def _warmup(session: requests.Session) -> None:
    """One throwaway GET so Akamai issues us session cookies.

    Returns 403 — that's expected and fine. The point is that the
    `_abck` / `ak_bmsc` / `bm_sz` cookies set on the response carry
    forward in the session, and the *next* request (with a Referer)
    passes the bot check.
    """
    session.get(HOST + "/", headers=HEADERS, timeout=REQUEST_TIMEOUT)


def _request(session: requests.Session, url: str, *, referer: str) -> requests.Response:
    headers = dict(HEADERS)
    headers["Referer"] = referer
    headers["Sec-Fetch-Site"] = "same-origin"
    response = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response


def _parse_listing_page(html: str) -> tuple[list[Job], int]:
    """Return (jobs_on_page, total_jobs_for_filter).

    Each `<article class="card-offer">` is one job. The header above
    the list says "<N> job offers in <K> locations" — we use N as the
    stop condition for pagination.
    """
    soup = BeautifulSoup(html, "html.parser")

    jobs: list[Job] = []
    for card in soup.select("article.card-offer"):
        link = card.select_one("a.card-link")
        href = link.get("href", "") if link else ""
        if not href:
            continue

        # /en/careers/job-offer/<slug>  — slug is our native_job_id.
        slug = href.rsplit("/", 1)[-1].strip()
        if not slug:
            continue

        title_el = card.select_one("h3.title-4")
        title = title_el.get_text(" ", strip=True) if title_el else ""

        loc_el = card.select_one(".offer-location")
        if loc_el:
            # Strip the leading icon span so it doesn't end up in the text.
            for icon in loc_el.select(".icon"):
                icon.extract()
            location = " ".join(loc_el.get_text(" ", strip=True).split())
        else:
            location = ""

        apply_url = HOST + href if href.startswith("/") else href

        jobs.append(Job(
            native_job_id=slug,
            title=title,
            location=location,
            category=SCOPE_CATEGORY,
            employment_type=SCOPE_EMPLOYMENT_TYPE,
            apply_url=apply_url,
        ))

    total_el = soup.select_one("span.nb-total")
    if total_el:
        try:
            total = int(total_el.get_text(strip=True).replace(",", "").replace(".", ""))
        except ValueError:
            total = len(jobs)
    else:
        total = len(jobs)

    return jobs, total


def _parse_detail_payload(html: str) -> dict | None:
    """Find the schema.org JobPosting JSON-LD block on a detail page."""
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def _extract_identifier(payload: dict) -> str | None:
    """BNP's identifier field is a PropertyValue object, not a string.

    Example: {"@type": "PropertyValue", "name": "BNP Paribas ...", "value": "!I_LSCOR_IT_0241"}
    """
    ident = payload.get("identifier")
    if isinstance(ident, dict):
        v = ident.get("value")
        if isinstance(v, str) and v:
            return v
    if isinstance(ident, str) and ident:
        return ident
    return None


def _enrich(session: requests.Session, job: Job, referer: str) -> bool:
    """Fetch detail page, fill enrichment fields. Returns True on success."""
    response = _request(session, job.apply_url, referer=referer)
    payload = _parse_detail_payload(response.text)
    if not payload:
        return False

    job.description = payload.get("description")
    job.posted_date = payload.get("datePosted")  # BNP emits zero-padded ISO already
    job.identifier = _extract_identifier(payload)
    job.raw_payload = payload
    return True


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    print("Akamai warmup...", flush=True)
    _warmup(session)
    time.sleep(REQUEST_DELAY_SECONDS)

    # Listing phase
    print("Listing phase...", flush=True)
    all_listings: dict[str, Job] = {}  # dedup by slug
    page = 1
    started = time.time()

    while True:
        url = BASE_URL if page == 1 else f"{BASE_URL}?page={page}"
        referer = HOST + "/en/careers/all-job-offers" if page == 1 else BASE_URL
        response = _request(session, url, referer=referer)

        page_jobs, total = _parse_listing_page(response.text)
        new_this_page = 0
        for j in page_jobs:
            if j.native_job_id not in all_listings:
                all_listings[j.native_job_id] = j
                new_this_page += 1

        print(
            f"  page {page}: {len(page_jobs)} cards "
            f"({new_this_page} new, {len(all_listings)}/{total} cumulative)",
            flush=True,
        )

        # Stop conditions: either we've matched the header total, or the
        # page yielded no new slugs (e.g. paginating past the end loops).
        if len(all_listings) >= total or new_this_page == 0:
            break

        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    listing_elapsed = time.time() - started
    print(
        f"  → {len(all_listings)} jobs in {listing_elapsed:.1f}s\n",
        flush=True,
    )

    # Enrichment phase
    in_scope = list(all_listings.values())
    print(
        f"Enrichment phase: fetching {len(in_scope)} detail pages "
        f"(~{int(len(in_scope) * REQUEST_DELAY_SECONDS)}s)...",
        flush=True,
    )

    kept: list[Job] = []
    failed = 0
    for i, job in enumerate(in_scope, 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            ok = _enrich(session, job, referer=BASE_URL)
        except Exception as exc:
            print(
                f"  [{i}/{len(in_scope)}] {job.native_job_id} FAILED: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            failed += 1
            continue

        if not ok:
            print(
                f"  [{i}/{len(in_scope)}] {job.native_job_id} no JSON-LD found",
                flush=True,
            )
            failed += 1
            continue

        kept.append(job)
        print(
            f"  [{i}/{len(in_scope)}] {job.identifier or job.native_job_id} "
            f"{job.title!r} → KEEP",
            flush=True,
        )

    print(flush=True)
    print(f"Enrichment summary:", flush=True)
    print(f"  kept    : {len(kept)}", flush=True)
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
