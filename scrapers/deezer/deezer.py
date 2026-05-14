"""Deezer job scraper — Paris, Product & Tech, Permanent.

Two-pass scrape (same shape as Sanofi/BNP):

1. LISTING. Fetch the deezerjobs.com WordPress landing page (single
   request, no pagination). Each `<div class="lst"> > <a>` card gives us
   native_job_id (numeric `jid`), title, location, category. We filter
   to `category == "Product & Tech"` (visible card text — never inferred
   from description) and drop titles matching Intern/Apprentice/Stage/
   Alternance/Fixed-term, which is the only place Deezer surfaces a
   "permanent vs not" signal.

2. ENRICHMENT. Deezer's actual ATS is Teamtailor. Each job has a
   canonical Teamtailor page at https://deezer.teamtailor.com/jobs/<jid>
   that serves a clean schema.org/JobPosting JSON-LD block — datePosted,
   employmentType, identifier, description (HTML). We fetch one
   Teamtailor page per kept job to fill those fields. The deezerjobs.com
   detail page is intentionally NOT used: its description block mixes in
   the title and "Apply now / Share this job" boilerplate.

   Probed Teamtailor: plain `requests.get` with browser-shaped UA returns
   200 with no auth/captcha. Sequential fetches at 1.5s spacing don't
   trigger rate-limiting on the 4-job batch. If that ever changes, the
   per-job try/except keeps the listing data usable even when enrichment
   fails — description / posted_date just stay None for that job.

Listing card shape:

    <div class="lst">
      <a class="cat-XXXXXX" href=".../job-details/?jid=NUMBER">
        <div class="inner">
          <div class="jobup">
            <h3>TITLE</h3>
            <span class="jobloc">LOCATION</span>
          </div>
          <div class="jobdw"><div class="jobdef">CATEGORY</div></div>
        </div>
      </a>
      ...
    </div>

To change scope: edit SCOPE_CATEGORY. To include other contract types,
relax NON_PERMANENT_TITLE_PATTERN.
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

LISTING_URL = "https://www.deezerjobs.com/en/jobs/"
TEAMTAILOR_JOB_URL = "https://deezer.teamtailor.com/jobs/{jid}"

# Brotli is intentionally absent — `requests` can't decode br without the
# brotli package, and the landing came back as raw binary on probe 1.
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
    "Accept-Encoding": "gzip, deflate",
    "From": "yannickarieldossa@gmail.com",
}

REQUEST_DELAY_SECONDS = 1.5
REQUEST_TIMEOUT = 30

SCOPE_CATEGORY = "Product & Tech"
SCOPE_EMPLOYMENT_TYPE = "Permanent"

# Title patterns that mean the job is NOT permanent. Word-boundary
# anchored so "International" doesn't match "Intern".
NON_PERMANENT_TITLE_PATTERN = re.compile(
    r"\b("
    r"intern|internship|"
    r"apprentice|apprenticeship|"
    r"stage|stagiaire|"
    r"alternance|alternant|"
    r"fixed[\s-]?term"
    r")\b",
    re.IGNORECASE,
)


@dataclass
class Job:
    native_job_id: str       # numeric jid from the URL — Deezer's stable ID
    title: str
    location: str
    category: str
    employment_type: str     # always SCOPE_EMPLOYMENT_TYPE for this scraper
    apply_url: str
    identifier: str          # same as native_job_id (kept for schema parity)
    # Filled by Teamtailor enrichment:
    description: str | None = None
    posted_date: str | None = None
    raw_payload: dict | None = None


def _parse_listing(html: str) -> list[Job]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[Job] = []

    for a in soup.select("div.openJobs div.lst > a"):
        href = a.get("href", "")
        m = re.search(r"jid=(\d+)", href)
        if not m:
            continue
        jid = m.group(1)

        title_el = a.find("h3")
        loc_el = a.select_one(".jobloc")
        cat_el = a.select_one(".jobdef")

        title = title_el.get_text(" ", strip=True) if title_el else ""
        location = loc_el.get_text(" ", strip=True) if loc_el else ""
        category = cat_el.get_text(" ", strip=True) if cat_el else ""

        if not title or not category:
            continue

        jobs.append(Job(
            native_job_id=jid,
            title=title,
            location=location,
            category=category,
            employment_type=SCOPE_EMPLOYMENT_TYPE,
            apply_url=href,
            identifier=jid,
        ))

    return jobs


def _parse_teamtailor_payload(html: str) -> dict | None:
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


def _enrich(session: requests.Session, job: Job) -> bool:
    """Fetch Teamtailor page, fill description / posted_date / raw_payload.

    Returns True on success. We don't drop jobs that fail enrichment —
    listing data alone is still useful — but the caller logs failures.
    """
    url = TEAMTAILOR_JOB_URL.format(jid=job.native_job_id)
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = _parse_teamtailor_payload(response.text)
    if not payload:
        return False
    job.description = payload.get("description")
    job.posted_date = payload.get("datePosted")
    job.raw_payload = payload
    return True


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    print(f"Fetching listing: {LISTING_URL}", flush=True)
    response = session.get(LISTING_URL, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    all_jobs = _parse_listing(response.text)
    print(f"  → {len(all_jobs)} cards parsed", flush=True)

    by_category: dict[str, int] = {}
    for j in all_jobs:
        by_category[j.category] = by_category.get(j.category, 0) + 1
    print("  Categories seen:", flush=True)
    for cat, n in sorted(by_category.items()):
        marker = " <-- in scope" if cat == SCOPE_CATEGORY else ""
        print(f"    {n:>3}  {cat}{marker}", flush=True)

    in_category = [j for j in all_jobs if j.category == SCOPE_CATEGORY]
    print(f"\n  In category '{SCOPE_CATEGORY}': {len(in_category)}", flush=True)

    kept: list[Job] = []
    for j in in_category:
        if NON_PERMANENT_TITLE_PATTERN.search(j.title):
            print(f"    DROP (non-permanent title) {j.native_job_id}: {j.title!r}", flush=True)
            continue
        kept.append(j)

    print(f"  Permanent only: {len(kept)}\n", flush=True)

    print(
        f"Enrichment phase (Teamtailor): fetching {len(kept)} pages "
        f"(~{int(len(kept) * REQUEST_DELAY_SECONDS)}s)...",
        flush=True,
    )
    enriched = 0
    failed = 0
    for i, job in enumerate(kept, 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            ok = _enrich(session, job)
        except Exception as exc:
            print(
                f"  [{i}/{len(kept)}] {job.native_job_id} FAILED: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            failed += 1
            continue

        if not ok:
            print(
                f"  [{i}/{len(kept)}] {job.native_job_id} no JobPosting JSON-LD",
                flush=True,
            )
            failed += 1
            continue

        enriched += 1
        print(
            f"  [{i}/{len(kept)}] {job.native_job_id} {job.title!r} "
            f"posted={job.posted_date}",
            flush=True,
        )

    print(flush=True)
    print(f"Enrichment summary:", flush=True)
    print(f"  enriched : {enriched}", flush=True)
    print(f"  failed   : {failed}", flush=True)

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

        print(f"[{j['identifier']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc_preview}")
        print()
