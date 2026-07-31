"""Ledger job scraper — France, tech families, category-first gate.

Ledger's career site (careers.ledger.com) is a Findly WordPress front-end
("cws.jobs.js") over Google Cloud Talent Solution — the SAME engine as
Stellantis. The listing page is a thin wrapper around the open JSON API:

    GET https://jobsapi-google.m-cloud.io/api/job/search

Discovered by reading careers.ledger.com/job-search-results/ (which loads
cws.jobs.js posting to `jobsapi-google.m-cloud.io/api`) and pulling the
tenant id out of the page config. The endpoint is open and accepts GET:

  companyName            companies/781c5218-...   Ledger tenant id
  pageSize, offset       int                       Pagination (offset 0-based)
  orderBy                posting_publish_time desc

Ledger's true ATS is Ashby (`jobs.ashbyhq.com/ledger`, the `seo_url`); Findly
mirrors it. The whole board is TINY — ~10 open reqs worldwide at time of
writing — so we crawl it UNFILTERED and filter client-side. No customAttribute
country facet is needed (and none is trusted) at this size.

Job-shape fields used:
  ref              Ashby posting UUID (== clientid == seo_url tail) -> native_job_id
  id               numeric Findly id (e.g. 23621340) -> identifier
  title
  primary_category "Software Engineering - Backend", "Security", "People", ...
  parent_category  "Tech" | "Go-to-Market" | "Corporate Functions"  <- clean facet
  primary_city, primary_country ("FR"/"US"/"GB")
  employment_type  "FullTime" | "Intern" | "Contract"   (job_type is always None)
  open_date        "2026-07-22T11:22:51" -> posted_date YYYY-MM-DD
  seo_url          apply URL -> jobs.ashbyhq.com/ledger/<uuid>
  description      HTML; stripped for description field, full dict in raw_payload

Filter strategy — CATEGORY-first, unlike Stellantis.

Unlike Stellantis (whose category fields were garbage), Ledger's Findly feed
carries a CLEAN taxonomy: `parent_category` cleanly separates "Tech" from
"Go-to-Market" and "Corporate Functions", and `primary_category` labels roles
precisely ("Software Engineering - Backend", "Security", ...). Per
feedback_prefer_platform_category_over_is_tech_role we gate on the platform
category, not the title:

  - Country gate: primary_country == "FR" (Paris HQ + Vierzon/Grenoble/
    Montpellier; London/US/Geneva/Singapore dropped).
  - Scope gate: parent_category in TECH_PARENTS ({"Tech"}) — this holds all
    Engineering / Data / AI-ML / Security / Cloud-Infra roles at Ledger.
  - Inclusive rescue: to avoid missing a Data/AI role that might one day be
    filed under a non-Tech parent (Product, a future "Data" bucket, ...), we
    ALSO keep a role whose title passes the shared `is_tech_role` allow-list —
    but only when its parent is NOT one of the known non-tech families
    (Go-to-Market / Corporate Functions), where that rescue would only
    false-positive (e.g. "Affiliate Business Developer" matches \bdeveloper\b).
    Same category-primary + suppressed-rescue shape as the Disney scraper.

Employment: CDI-inclusive. We do NOT filter on employment_type — the category
gate already scopes to tech, and the user explicitly wants AI/data internships /
apprenticeships / new-grad kept. FullTime / Intern / Contract tech roles all
pass.

At time of writing France + Tech yields 3 roles (Engineering Manager - Cloud
Wallet, Staff / Senior Security Operations Engineer). Yield is small by design:
Ledger's board is tiny and Paris-heavy on GTM/Corporate. 0 rows is acceptable if
France tech yield is genuinely zero.

To change scope, edit COUNTRY / TECH_PARENTS / _RESCUE_SUPPRESS_PARENTS.
"""
from __future__ import annotations

import sys
import time
from dataclasses import asdict, dataclass
from typing import Any

import requests
from bs4 import BeautifulSoup

from scrapers._relevance import is_tech_role

API_URL = "https://jobsapi-google.m-cloud.io/api/job/search"
COMPANY_NAME = "companies/781c5218-6b4e-4f25-b078-be0db1bc7445"

COUNTRY = "FR"

# Category-first gate (Ledger's parent_category taxonomy is clean).
# Wholesale in-scope parent families (deburred/lowercased compare):
TECH_PARENTS: set[str] = {"tech"}
# Parents where the is_tech_role(title) rescue only false-positives and must be
# suppressed (GTM "Business Developer" matches \bdeveloper\b; HR/Finance noise).
_RESCUE_SUPPRESS_PARENTS: set[str] = {"go-to-market", "corporate functions"}

PAGE_SIZE = 100
MAX_PAGES = 20  # 2000-job ceiling — the board is ~10 today, this is a safety cap
REQUEST_DELAY_SECONDS = 1.0
REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
    "Origin": "https://careers.ledger.com",
    "Referer": "https://careers.ledger.com/job-search-results/",
}


@dataclass
class Job:
    native_job_id: str        # Ashby posting UUID (ref)
    title: str
    location: str
    category: str
    apply_url: str
    employment_type: str
    description: str | None = None
    posted_date: str | None = None
    identifier: str | None = None        # numeric Findly id as string
    raw_payload: dict | None = None


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _in_scope(doc: dict[str, Any]) -> bool:
    """France + tech, category-first with a suppressed title rescue."""
    if (doc.get("primary_country") or "").strip().upper() != COUNTRY:
        return False
    parent = _norm(doc.get("parent_category"))
    if parent in TECH_PARENTS:
        return True
    if parent in _RESCUE_SUPPRESS_PARENTS:
        return False
    # Unknown / new parent bucket: rescue genuine tech/data/AI titles.
    return is_tech_role(doc.get("title"))


def _clean_description(html_str: str | None) -> str | None:
    if not html_str:
        return None
    text = BeautifulSoup(html_str, "html.parser").get_text(" ", strip=True)
    return text or None


def _posted_date(raw: str | None) -> str | None:
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    return raw[:10]


def _location(doc: dict[str, Any]) -> str:
    city = (doc.get("primary_city") or "").strip()
    country = (doc.get("primary_country") or "").strip()
    if city and country:
        return f"{city.title()}, {country}"
    return city.title() or country or ""


def _doc_to_job(doc: dict[str, Any]) -> Job:
    native_id = (doc.get("ref") or "").strip()
    if not native_id:
        # Fall back to the numeric Findly id if ref is ever missing — keeps the
        # row insertable rather than crashing the whole run.
        native_id = str(doc.get("id") or "").strip()
    if not native_id:
        raise RuntimeError(f"Ledger posting missing ref+id (title={doc.get('title')!r})")

    return Job(
        native_job_id=native_id,
        title=(doc.get("title") or "").strip(),
        location=_location(doc),
        category=(doc.get("primary_category") or "").strip(),
        apply_url=(doc.get("seo_url") or doc.get("url") or "").strip(),
        employment_type=(doc.get("employment_type") or "").strip(),
        description=_clean_description(doc.get("description")),
        posted_date=_posted_date(doc.get("open_date")),
        identifier=str(doc["id"]) if doc.get("id") is not None else None,
        raw_payload=doc,
    )


def _fetch_page(session: requests.Session, offset: int) -> dict[str, Any]:
    params = {
        "companyName": COMPANY_NAME,
        "pageSize": PAGE_SIZE,
        "offset": offset,
        "orderBy": "posting_publish_time desc",
    }
    response = session.get(API_URL, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    all_jobs: dict[str, Job] = {}
    offset = 0
    total_hits: int | None = None

    for page in range(1, MAX_PAGES + 1):
        if page > 1:
            time.sleep(REQUEST_DELAY_SECONDS)

        payload = _fetch_page(session, offset)
        results = payload.get("searchResults") or []
        if total_hits is None:
            total_hits = payload.get("totalHits") or 0
            print(f"  totalHits={total_hits}", flush=True)

        if not results:
            print(f"  page {page}: 0 results — done", flush=True)
            break

        for sr in results:
            doc = sr.get("job") or {}
            try:
                job = _doc_to_job(doc)
            except Exception as exc:
                print(f"  WARN: skipping malformed row: {type(exc).__name__}: {exc}", flush=True)
                continue
            all_jobs.setdefault(job.native_job_id, job)

        offset += len(results)
        print(
            f"  page {page}: {len(results)} jobs "
            f"({len(all_jobs)} unique, offset now {offset}/{total_hits})",
            flush=True,
        )

        if offset >= total_hits:
            break
        if not payload.get("nextPageToken"):
            break

    # `all_jobs` is keyed by ref; re-derive scope from the raw payload so the
    # category-first gate sees parent_category / primary_country.
    kept = {
        nid: j for nid, j in all_jobs.items() if _in_scope(j.raw_payload or {})
    }
    dropped = len(all_jobs) - len(kept)

    elapsed = time.time() - started
    print(
        f"\n  France+tech filter kept {len(kept)}/{len(all_jobs)} "
        f"(dropped {dropped}) in {elapsed:.1f}s\n",
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
        desc = (j["description"] or "")
        desc = desc[:200] + ("..." if len(desc) > 200 else "")
        print(f"[{j['identifier'] or j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
