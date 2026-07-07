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

Filter strategy — TITLE-based, because the CATEGORY fields are unusable:

Stellantis is an auto-OEM: of the ~100 FR CDI roles in the inventory, the
large majority are mechanical / manufacturing / embedded / sales engineering.
We keep Data & AI PLUS all Software/IT engineering, and drop the rest.

Reverse-engineering the feed (see scrapers/stellantis/material/reeng_fr_all.json)
showed the ATS category fields cannot gate scope:
  - `department` is the literal string "EE" on EVERY FR row — no signal.
  - `primary_category` labels "DATA ENGINEER" merely "Software", and
    `parent_category` files it under "Software Electric & Electronics"
    (a hardware-sounding bucket). Genuine Data/AI roles are scattered across
    Software E&E, Engineering, Supply Chain, Finance and Quality — no single
    category holds them, and each of those buckets is full of non-tech roles.
  - google_categories over-tags automotive engineering as COMPUTER_AND_IT
    because JDs mention CAD / simulation / ECUs / AUTOSAR.

So we filter on the TITLE, using the shared `is_tech_role` allow-list
(data / AI / ML / BI / software / cloud / devops / IT / architecture …) —
the same predicate Schneider / Safran / Thales / ServiceNow use for
unusable-category boards. On top of it we apply a small Stellantis-local
HARD-EXCLUDE for the auto-OEM titles that collide with a tech keyword but are
out of scope: CAD / vehicle-architecture ("Concepteur CAO", "EE architecture"),
electrical-distribution hardware, procurement ("Buyer"), instructional-content
("Training Developer") and graphic/motion design. This keeps genuine software
roles the old strict Data-only regex missed (Software Development Engineer,
Java dev, Platform Operations Engineer, software toolchain dev) without the
noise. Yields ~13 clean FR CDI roles at time of writing.

Stellantis FR tags employment as French job_type values: CDI / Apprentissage /
Stage. Scope is CDI only (permanent salaried), so job_type="CDI".

To change scope, edit COUNTRY / JOB_TYPES_IN_SCOPE / _STELLANTIS_EXCLUDE_RE,
or swap the `is_tech_role` gate in `_in_scope`.
"""
from __future__ import annotations

import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any

import requests
from bs4 import BeautifulSoup

from scrapers._relevance import is_tech_role

API_URL = "https://jobsapi-google.m-cloud.io/api/job/search"
COMPANY_NAME = "companies/16115603-6c1b-4c45-b544-238a4e6c51b3"

COUNTRY = "FR"
JOB_TYPES_IN_SCOPE: set[str] = {"CDI"}

# Scope = Data/AI + all Software/IT, gated on the TITLE via the shared
# `is_tech_role` allow-list (category fields are unusable here — see module
# docstring). On top of it, a Stellantis-local HARD-EXCLUDE for auto-OEM titles
# that match a tech keyword (architecte / software / developer / designer /
# plateforme) but are out of scope: CAD & vehicle-architecture, electrical
# distribution hardware, procurement buyers, instructional-content developers,
# and graphic/motion designers. Matched on the deburred (accent-stripped) title.
_STELLANTIS_EXCLUDE_RE = re.compile(
    r"concepteur cao"
    r"|architecture plateforme vehicule"
    r"|distribution electrique"
    r"|vehicle configuration|ee architecture"
    r"|\bbuyer\b|acheteur"
    r"|training developer|master training"
    r"|motion designer|graphiste"
)


def _deburr(s: str) -> str:
    """Lowercase + strip diacritics so accented titles match the ASCII exclude
    patterns ('Véhicule' -> 'vehicule', 'électrique' -> 'electrique')."""
    nfkd = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _in_scope(title: str | None) -> bool:
    """Keep in-scope tech/data/AI/software titles, drop auto-OEM collisions."""
    if not is_tech_role(title):
        return False
    return not _STELLANTIS_EXCLUDE_RE.search(_deburr(title or ""))


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
        nid: j for nid, j in all_jobs.items() if _in_scope(j.title)
    }
    dropped = len(all_jobs) - len(kept)

    elapsed = time.time() - started
    print(
        f"\n  title filter kept {len(kept)}/{len(all_jobs)} "
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
