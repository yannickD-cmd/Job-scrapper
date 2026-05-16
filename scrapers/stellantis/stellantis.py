"""Stellantis job scraper — France, CDI, strict Data/AI title filter.

Stellantis's career site (careers.stellantis.com) is built on Findly's CWS
("Career Web Site") plugin, backed by Google Cloud Talent Solution. The
listing page is a thin Angular/jQuery wrapper around a JSON API:

    GET https://jobsapi-google.m-cloud.io/api/job/search

Discovered by reading cws.jobs.js (which posts to `api_url + 'job'`) and
the google-filters bundle that rewrites the URL to `.../job/search`. The
endpoint is open and accepts GET with these params:

  companyName            companies/<uuid>      Stellantis tenant id
  pageSize, offset       int                   Pagination (offset is 0-based)
  customAttributeFilter  GCTS filter expr      e.g. primary_country="FR"
  orderBy                e.g. posting_publish_time desc

Job-shape fields used:
  ref          stable Stellantis ref (e.g. "2026-19154") -> native_job_id
  id           internal numeric -> identifier
  title, primary_category, job_type, primary_city, primary_country
  open_date    "2026-05-13T10:06:06" -> posted_date YYYY-MM-DD
  seo_url      apply URL (-> TalentSoft jobs.groupe-psa.com)
  description  HTML; stripped for description field, full dict in raw_payload

Filter strategy (the one that survived empirical testing):

Stellantis is an auto-OEM. Of 101 FR CDI roles in the inventory, ~97%
are mechanical / embedded / manufacturing engineering. We want pure
Data/AI/Analytics roles only, of which Stellantis has typically 3-5.

Three signals were tried and rejected:
  - primary_category="ICT, Digital and Data" : misses Data Engineer
    (Supply Chain bucket), Agentic AI (Supply Chain bucket). Yields 3.
  - google_categories CONTAINS "COMPUTER_AND_IT" : Google's classifier
    over-tags automotive engineering because descriptions mention
    CAD/sim/ECUs/AUTOSAR. Yields 28 with ~85% noise.
  - Broad title regex (software|developer|architect|analyst|...) :
    catches "Senior Buyer Software", "Supplier Cost Analyst", etc.

What works: STRICT title regex on Data/AI/ML/Analytics/BI terms only.
No `software`, no `developer`, no `architect`, no `analyst` alone.
Yields 3 genuine roles (Data Engineer, Agentic AI, BI Operations Lead)
with effectively zero false positives at the time of writing.

Stellantis FR tags employment as French job_type values: CDI / Apprentissage /
Stage. No Stellantis-tagged CDD in FR inventory, so CDI alone covers
"regular salaried position".

To change scope, edit COUNTRY / JOB_TYPES_IN_SCOPE / TITLE_DATA_RE.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any

import requests
from bs4 import BeautifulSoup

API_URL = "https://jobsapi-google.m-cloud.io/api/job/search"
COMPANY_NAME = "companies/16115603-6c1b-4c45-b544-238a4e6c51b3"

COUNTRY = "FR"
JOB_TYPES_IN_SCOPE: set[str] = {"CDI"}

# Strict Data/AI/ML/Analytics keyword regex. Intentionally narrow: NO bare
# "software" / "developer" / "architect" / "analyst" / "digital", because at
# Stellantis those terms attach to procurement, marketing, and automotive
# engineering roles rather than data/AI work.
TITLE_DATA_RE = re.compile(
    r"(?i)\b("
    r"AI|IA|ML|BI|"
    r"data|"
    r"machine\s+learning|deep\s+learning|"
    r"analytics|"
    r"agentic|"
    r"MLOps|LLM|"
    r"big\s+data|"
    r"business\s+intelligence|"
    r"power\s*bi"
    r")\b"
)

PAGE_SIZE = 100
MAX_PAGES = 20  # 2000 jobs ceiling — well above any country bucket
REQUEST_DELAY_SECONDS = 1.0
REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
    "Origin": "https://careers.stellantis.com",
    "Referer": "https://careers.stellantis.com/job-search-results/",
}


@dataclass
class Job:
    native_job_id: str        # Stellantis ref, e.g. "2026-19154"
    title: str
    location: str
    category: str
    apply_url: str
    employment_type: str
    description: str | None = None
    posted_date: str | None = None
    identifier: str | None = None        # internal numeric id as string
    raw_payload: dict | None = None


def _build_filter() -> str:
    parts = [f'primary_country="{COUNTRY}"']
    if len(JOB_TYPES_IN_SCOPE) == 1:
        (jt,) = JOB_TYPES_IN_SCOPE
        parts.append(f'job_type="{jt}"')
    elif JOB_TYPES_IN_SCOPE:
        ors = " OR ".join(f'job_type="{j}"' for j in sorted(JOB_TYPES_IN_SCOPE))
        parts.append(f"({ors})")
    return " AND ".join(parts)


def _clean_description(html_str: str | None) -> str | None:
    if not html_str:
        return None
    text = BeautifulSoup(html_str, "html.parser").get_text(" ", strip=True)
    return text or None


def _posted_date(raw: str | None) -> str | None:
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    return raw[:10]


def _location(job: dict[str, Any]) -> str:
    city = (job.get("primary_city") or "").strip()
    country = (job.get("primary_country") or "").strip()
    if city and country:
        return f"{city.title()}, {country}"
    return city.title() or country or ""


def _doc_to_job(doc: dict[str, Any]) -> Job:
    native_id = (doc.get("ref") or "").strip()
    if not native_id:
        # Fall back to the numeric Findly id if ref is ever missing — keeps
        # the row insertable rather than crashing the whole run.
        native_id = str(doc.get("id") or "").strip()
    if not native_id:
        raise RuntimeError(f"Stellantis posting missing ref+id (title={doc.get('title')!r})")

    return Job(
        native_job_id=native_id,
        title=(doc.get("title") or "").strip(),
        location=_location(doc),
        category=(doc.get("primary_category") or "").strip(),
        apply_url=(doc.get("seo_url") or doc.get("url") or "").strip(),
        employment_type=(doc.get("job_type") or "").strip(),
        description=_clean_description(doc.get("description")),
        posted_date=_posted_date(doc.get("open_date")),
        identifier=str(doc["id"]) if doc.get("id") is not None else None,
        raw_payload=doc,
    )


def _fetch_page(session: requests.Session, offset: int, filt: str) -> dict[str, Any]:
    params = {
        "companyName": COMPANY_NAME,
        "pageSize": PAGE_SIZE,
        "offset": offset,
        "customAttributeFilter": filt,
        "orderBy": "posting_publish_time desc",
    }
    response = session.get(API_URL, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    filt = _build_filter()
    print(f"Filter: {filt}", flush=True)

    started = time.time()
    all_jobs: dict[str, Job] = {}
    offset = 0
    total_hits: int | None = None

    for page in range(1, MAX_PAGES + 1):
        if page > 1:
            time.sleep(REQUEST_DELAY_SECONDS)

        payload = _fetch_page(session, offset, filt)
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

    kept = {
        nid: j for nid, j in all_jobs.items() if TITLE_DATA_RE.search(j.title)
    }
    dropped = len(all_jobs) - len(kept)

    elapsed = time.time() - started
    print(
        f"\n  title regex kept {len(kept)}/{len(all_jobs)} "
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
