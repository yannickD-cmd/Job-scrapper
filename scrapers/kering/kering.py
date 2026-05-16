"""Kering Group job scraper — France, Tech & Digital, Regular only.

Kering's careers site (https://www.kering.com/en/talent/job-offers/) is a
Next.js front-end that ships the full listing inside a `__NEXT_DATA__`
<script> on every paginated page. There is no public JSON API and the
filter UI (country, brand, job family) is purely client-side — query-string
filters are silently ignored by the server, so we have to walk every page
and filter in Python.

The board aggregates Kering Corporate + every house (Gucci, Saint Laurent,
Bottega Veneta, Balenciaga, Boucheron, Brioni, McQueen, Pomellato, DoDo,
Ginori 1735, Qeelin, Kering Eyewear). Per scope decision: keep all houses
listed on the Kering board, accepting possible future overlap if any house
gets its own dedicated scraper later.

Each `jobList[i]` carries enough to populate the full Job contract without
hitting detail pages — description is server-rendered as HTML inside the
listing payload itself.

URL pattern:
    https://www.kering.com/en/talent/job-offers/?page={page}

`totalPages` is read off page 1 and used as the upper bound (with a
defensive MAX_PAGES cap).

To change scope, edit COUNTRIES_IN_SCOPE / JOB_FAMILY_IDS_IN_SCOPE /
WORKER_SUBTYPES_IN_SCOPE.
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any

import requests
from bs4 import BeautifulSoup

LISTING_URL = "https://www.kering.com/en/talent/job-offers/?page={page}"

COUNTRIES_IN_SCOPE: set[str] = {"France"}
# Filter on the stable filter id, not the human-facing label.
JOB_FAMILY_IDS_IN_SCOPE: set[str] = {"Information_&_Digital_Technologies"}
# "Regular" is Kering's tag for permanent salaried roles (CDI).
# Excludes Agency, Fixed Term, Trainee, Student (Fixed Term), Apprenticeship.
WORKER_SUBTYPES_IN_SCOPE: set[str] = {"Regular"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_DELAY_SECONDS = 2.0
REQUEST_TIMEOUT = 30
MAX_PAGES = 200

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>', re.DOTALL
)


@dataclass
class Job:
    native_job_id: str
    title: str
    apply_url: str
    description: str | None = None
    location: str | None = None
    category: str | None = None
    posted_date: str | None = None
    employment_type: str | None = None
    identifier: str | None = None
    raw_payload: dict | None = None


def _extract_next_data(html: str) -> dict | None:
    m = NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _job_search_section(next_data: dict) -> dict | None:
    sections = (
        next_data.get("props", {})
        .get("pageProps", {})
        .get("props", {})
        .get("sections", [])
    )
    for s in sections:
        if s.get("type") == "job-search":
            return s.get("props") or {}
    return None


def _html_to_text(html: str | None) -> str | None:
    if not html:
        return None
    return BeautifulSoup(html, "html.parser").get_text("\n", strip=True)


def _format_location(city: str | None, country: str | None) -> str | None:
    parts = []
    if city:
        # Kering yells city names in ALL CAPS; tidy for display.
        parts.append(city.title())
    if country:
        parts.append(country)
    return ", ".join(parts) if parts else None


def _identifier_from_url(url: str | None) -> str | None:
    """Pull the human-friendly req id (e.g. R165571) from the apply URL tail."""
    if not url:
        return None
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return tail or None


def _build_job(raw: dict[str, Any]) -> Job | None:
    job_id = raw.get("jobId")
    title = raw.get("jobPosting") or ""
    url = raw.get("url") or ""
    if not job_id or not url:
        return None

    posted = raw.get("publishedAt")
    posted_date = posted[:10] if isinstance(posted, str) and len(posted) >= 10 else None

    worker_subtype = raw.get("workerSubType")
    job_time = raw.get("jobTimeType")
    employment_type = (
        f"{worker_subtype} ({job_time})"
        if worker_subtype and job_time
        else worker_subtype or job_time
    )

    return Job(
        native_job_id=str(job_id),
        title=title.strip(),
        apply_url=url,
        description=_html_to_text(raw.get("description")),
        location=_format_location(raw.get("locationCity"), raw.get("locationCountry")),
        category=raw.get("jobFamily"),
        posted_date=posted_date,
        employment_type=employment_type,
        identifier=_identifier_from_url(url),
        raw_payload=raw,
    )


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    all_listings: dict[str, dict] = {}  # jobId -> raw job dict (dedup)
    page = 1
    total_pages = 1
    started = time.time()

    print("Listing phase (filters are client-side, walking all pages)...", flush=True)

    while page <= total_pages and page <= MAX_PAGES:
        url = LISTING_URL.format(page=page)
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        next_data = _extract_next_data(response.text)
        if not next_data:
            print(f"  page {page}: no __NEXT_DATA__ — aborting", flush=True)
            break

        section = _job_search_section(next_data)
        if section is None:
            print(f"  page {page}: no job-search section — aborting", flush=True)
            break

        page_jobs = section.get("jobList") or []
        for raw in page_jobs:
            jid = raw.get("jobId")
            if jid:
                all_listings.setdefault(str(jid), raw)

        if page == 1:
            total_pages = int(section.get("totalPages") or 1)
            print(
                f"  total advertised: {section.get('totalJobNumber')} jobs / "
                f"{total_pages} pages",
                flush=True,
            )

        print(
            f"  page {page}/{total_pages}: {len(page_jobs)} jobs "
            f"({len(all_listings)} unique so far)",
            flush=True,
        )

        page += 1
        if page <= total_pages and page <= MAX_PAGES:
            time.sleep(REQUEST_DELAY_SECONDS)

    elapsed = time.time() - started
    print(
        f"  → {len(all_listings)} unique jobs in {elapsed:.1f}s\n",
        flush=True,
    )

    # Filter in Python. Track drop counts per axis so the smoke test makes the
    # scope explicit.
    kept: list[Job] = []
    dropped_country = 0
    dropped_family = 0
    dropped_subtype: dict[str | None, int] = {}

    for raw in all_listings.values():
        country = raw.get("locationCountry")
        family_id = raw.get("jobFamilyId")
        subtype = raw.get("workerSubType")

        if country not in COUNTRIES_IN_SCOPE:
            dropped_country += 1
            continue
        if family_id not in JOB_FAMILY_IDS_IN_SCOPE:
            dropped_family += 1
            continue
        if subtype not in WORKER_SUBTYPES_IN_SCOPE:
            dropped_subtype[subtype] = dropped_subtype.get(subtype, 0) + 1
            continue

        job = _build_job(raw)
        if job is not None:
            kept.append(job)

    print(f"Filters:", flush=True)
    print(f"  countries        : {sorted(COUNTRIES_IN_SCOPE)}", flush=True)
    print(f"  job families     : {sorted(JOB_FAMILY_IDS_IN_SCOPE)}", flush=True)
    print(f"  worker subtypes  : {sorted(WORKER_SUBTYPES_IN_SCOPE)}", flush=True)
    print(f"  dropped by country     : {dropped_country}", flush=True)
    print(f"  dropped by job family  : {dropped_family}", flush=True)
    print(f"  dropped by worker type : {sum(dropped_subtype.values())} "
          f"(by type: {dict(dropped_subtype)})", flush=True)
    print(f"  kept                   : {len(kept)}\n", flush=True)

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
    print(f"=== {len(jobs)} jobs final (total runtime {elapsed:.1f}s) ===\n")

    for j in jobs:
        desc = (j.get("description") or "").strip()
        desc_preview = desc[:200] + ("…" if len(desc) > 200 else "")

        print(f"[{j['identifier'] or j['native_job_id']}] {j['title']}")
        print(f"  Brand      : {(j.get('raw_payload') or {}).get('houseName')}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc_preview}")
        print()
