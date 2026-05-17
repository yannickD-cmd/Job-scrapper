"""Schneider Electric job scraper — France, all categories and employment types.

Schneider's careers site at careers.se.com is an iCIMS Jibe front-end (the
Angular search bundle at app.jibecdn.com hits the host's own `/api/jobs`
JSON endpoint). The API is open and stable, but two things make it picky:

  1. It will 403 a cold request — the host issues a session cookie when you
     load the listing HTML and only honours `/api/jobs` calls that carry it.
     So we warm the session by GETting `/jobs?country=France&page=1` once
     before hitting the API.
  2. `limit` is server-capped at 20. Anything larger returns 403. We walk
     1..N at limit=20 until we've collected every row totalCount advertises.

Each job in the response has a `data` envelope with everything we need —
title, description (already plaintext), category, tags1 (employment-type
facet), full_location, req_id, apply_url, create_date, posted_date — so
this is single-phase: no detail-page enrichment.

Per project convention (noise filters live in the dashboard, not the DB),
we keep every France row regardless of category or employment type and
let the dashboard predicate decide what to show.

`posted_date` from Schneider comes formatted as "May 11, 2026". We parse
that to ISO YYYY-MM-DD; if parsing ever fails we fall back to `create_date`
which is already ISO.
"""
from __future__ import annotations

import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime

import requests

LISTING_URL = "https://careers.se.com/jobs"
API_URL = "https://careers.se.com/api/jobs"

COUNTRY_IN_SCOPE = "France"
PAGE_SIZE = 20  # server-enforced; larger values 403
MAX_PAGES = 60  # guard: ~1200 rows; France is ~410 today

HEADERS = {
    # Schneider's WAF 403s anything outside the standard browser UA range,
    # so we send the same Chrome string Doctolib/Voodoo use and identify
    # ourselves to the host through the `From` header.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "From": "yannickarieldossa@gmail.com",
}

API_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://careers.se.com",
}

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.0


@dataclass
class Job:
    native_job_id: str             # Schneider req_id, e.g. "109558"
    title: str
    location: str                  # full_location, e.g. "RUEIL MALMAISON, France"
    category: str | None           # joined `category` list, e.g. "Technical"
    apply_url: str                 # iCIMS apply page
    employment_type: str           # joined `tags1`, e.g. "Full-Time" / "Apprenti/e"
    description: str | None = None
    posted_date: str | None = None
    identifier: str | None = None  # ats_code, e.g. "icims"
    raw_payload: dict | None = None


def _parse_posted_date(raw: str | None, fallback: str | None) -> str | None:
    """Schneider's `posted_date` looks like "May 11, 2026". Parse to ISO.
    If that fails, fall back to the ISO-shaped `create_date` (first 10 chars)."""
    if isinstance(raw, str) and raw.strip():
        for fmt in ("%b %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    if isinstance(fallback, str) and len(fallback) >= 10:
        return fallback[:10]
    return None


def _join(values: list | None) -> str | None:
    if not values:
        return None
    cleaned = [str(v).strip() for v in values if v and str(v).strip()]
    return " | ".join(cleaned) if cleaned else None


def _doc_to_job(doc: dict) -> Job:
    req_id = doc.get("req_id")
    if not req_id:
        raise RuntimeError(f"Schneider posting missing req_id (title={doc.get('title')!r})")

    return Job(
        native_job_id=str(req_id),
        title=(doc.get("title") or "").strip(),
        location=(doc.get("full_location") or doc.get("short_location") or "").strip(),
        category=_join(doc.get("category")),
        apply_url=(doc.get("apply_url") or "").strip(),
        employment_type=_join(doc.get("tags1")) or "",
        description=(doc.get("description") or None),
        posted_date=_parse_posted_date(doc.get("posted_date"), doc.get("create_date")),
        identifier=(doc.get("ats_code") or None),
        raw_payload=doc,
    )


def _warm_session(session: requests.Session) -> None:
    """Pull the France listing HTML once so the host plants its session cookie.
    Without this the JSON API replies 403."""
    response = session.get(
        f"{LISTING_URL}?country={COUNTRY_IN_SCOPE}&page=1",
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()


def _fetch_page(session: requests.Session, page: int) -> dict:
    response = session.get(
        API_URL,
        params={"country": COUNTRY_IN_SCOPE, "page": page, "limit": PAGE_SIZE},
        headers={
            **API_HEADERS,
            "Referer": f"{LISTING_URL}?country={COUNTRY_IN_SCOPE}&page={page}",
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print(f"Warming session for {COUNTRY_IN_SCOPE}...", flush=True)
    _warm_session(session)

    print("Listing phase...", flush=True)
    kept: dict[str, Job] = {}  # dedup by req_id
    expected_total: int | None = None
    page = 1

    while page <= MAX_PAGES:
        payload = _fetch_page(session, page)
        if expected_total is None:
            expected_total = payload.get("totalCount")
            print(f"  totalCount = {expected_total}", flush=True)

        page_docs = [j.get("data") or {} for j in payload.get("jobs") or []]
        before = len(kept)
        for doc in page_docs:
            try:
                job = _doc_to_job(doc)
            except Exception as exc:
                print(
                    f"  page {page}: skip malformed row: {type(exc).__name__}: {exc}",
                    flush=True,
                )
                continue
            kept.setdefault(job.native_job_id, job)
        added = len(kept) - before

        print(
            f"  page {page}: +{added} unique ({len(page_docs)} rows on page, "
            f"{len(kept)} kept so far)",
            flush=True,
        )

        if not page_docs:
            break
        if expected_total is not None and len(kept) >= expected_total:
            break

        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)
    else:
        print(
            f"  MAX_PAGES={MAX_PAGES} hit before completing — "
            f"got {len(kept)} of {expected_total}",
            flush=True,
        )

    elapsed = time.time() - started
    print(
        f"\n  -> {len(kept)} jobs in {elapsed:.1f}s "
        f"(expected {expected_total})\n",
        flush=True,
    )
    return [asdict(j) for j in kept.values()]


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
        desc = (j["description"] or "").strip()
        desc = desc[:200] + ("..." if len(desc) > 200 else "")
        print(f"[{j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
