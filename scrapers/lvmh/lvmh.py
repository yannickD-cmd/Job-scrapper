"""LVMH job scraper — France, tech-adjacent (Technologie + Omnicanal/data), CDI only.

LVMH's careers site at https://www.lvmh.com/en/join-us/our-job-offers is a
Next.js SPA backed by Algolia. The site proxies queries through
https://www.lvmh.com/api/search, which is behind Akamai Bot Manager and blocks
GitHub Actions IPs. We bypass the proxy and hit Algolia directly — the appId
and apiKey are public, search-only credentials extracted from the JS bundle
(chunk 12-93bd6a4071f1a94c.js: `n.n(a)()("SDMQTD2J9T","a5c6f4c...")`).

Algolia config
  App ID  : SDMQTD2J9T
  API key : a5c6f4c87dea9aac0732631cd87583b2  (search-only, public)
  Index   : PRD-fr-fr-timestamp-desc          (PRD-<locale>-timestamp-desc;
            fr-fr returns French facet labels so contractFilter values are
            "CDI"/"CDD"/"Stage" and functionFilter is "Technologie" etc.)

Index name is composed in the bundle as `i.Dg + lang + i.nX` (module 94656):
  Dg = "PRD-"                lang ∈ {fr-fr, en-us, it-it, ja-jp, zh-cn}
  nX = "-timestamp-desc"     (DESC default)   p3 = "-timestamp-asc"

Filter
  category:job AND country:"France"
  AND contractFilter IN {CDI}
  AND functionFilter IN {"Technologie", "Omnicanal et données"}

functionFilter (FR-side facet) buckets used here:
  - "Technologie"          → IT / Software / Cloud / Infra / Security
  - "Omnicanal et données" → Digital / E-commerce / Data / CRM

Each Algolia hit is self-contained (title, description, ATS apply URL,
publication timestamp, maison, contractFilter), so this is a single-phase
scrape — no detail-page fetches.

native_job_id  = Algolia objectID (brand-prefixed, e.g. RIM01831, GUER05274 —
                 stable across the doc's lifetime)
identifier     = upstream ATS id (atsId, e.g. 1073026)
apply_url      = `link` field, which points to the maison's actual ATS
                 (recruitmentplatform.com, taleo, workday, etc.)

To widen scope, edit CONTRACTS_IN_SCOPE / FUNCTIONS_IN_SCOPE.
"""
from __future__ import annotations

import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

ALGOLIA_APP_ID = "SDMQTD2J9T"
ALGOLIA_API_KEY = "a5c6f4c87dea9aac0732631cd87583b2"
ALGOLIA_INDEX = "PRD-fr-fr-timestamp-desc"
ALGOLIA_URL = (
    f"https://{ALGOLIA_APP_ID.lower()}-dsn.algolia.net/1/indexes/*/queries"
)

COUNTRY_IN_SCOPE = "France"
CONTRACTS_IN_SCOPE: set[str] = {"CDI"}
FUNCTIONS_IN_SCOPE: set[str] = {"Technologie", "Omnicanal et données"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "X-Algolia-Application-Id": ALGOLIA_APP_ID,
    "X-Algolia-API-Key": ALGOLIA_API_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json",
}

REQUEST_DELAY_SECONDS = 1.0
REQUEST_TIMEOUT = 30
HITS_PER_PAGE = 100
MAX_PAGES = 20


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


def _build_filters() -> str:
    contract_clause = " OR ".join(
        f'contractFilter:"{c}"' for c in sorted(CONTRACTS_IN_SCOPE)
    )
    function_clause = " OR ".join(
        f'functionFilter:"{f}"' for f in sorted(FUNCTIONS_IN_SCOPE)
    )
    return (
        f'category:job'
        f' AND country:"{COUNTRY_IN_SCOPE}"'
        f' AND ({contract_clause})'
        f' AND ({function_clause})'
    )


def _location(hit: dict) -> str | None:
    parts = [hit.get("city"), hit.get("country")]
    parts = [p for p in parts if p]
    return ", ".join(parts) if parts else None


def _posted_date(hit: dict) -> str | None:
    ts = hit.get("publicationTimestamp")
    if not isinstance(ts, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
    except (OSError, ValueError, OverflowError):
        return None


def _to_job(hit: dict) -> Job | None:
    object_id = hit.get("objectID")
    if not object_id:
        return None

    apply_url = (hit.get("link") or "").strip()
    if not apply_url:
        return None

    description = hit.get("description") or ""
    job_resp = hit.get("jobResponsabilities") or ""
    profile = hit.get("profile") or ""
    full_desc = "\n".join(part for part in (description, job_resp, profile) if part) or None

    return Job(
        native_job_id=str(object_id),
        title=str(hit.get("name") or "").strip(),
        apply_url=apply_url,
        description=full_desc,
        location=_location(hit),
        category=hit.get("functionFilter") or hit.get("function"),
        posted_date=_posted_date(hit),
        employment_type=hit.get("contractFilter") or hit.get("contract"),
        identifier=str(hit["atsId"]) if hit.get("atsId") else None,
        raw_payload=hit,
    )


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    filters = _build_filters()
    print(f"Algolia index : {ALGOLIA_INDEX}", flush=True)
    print(f"Filters       : {filters}", flush=True)
    print(flush=True)

    seen: dict[str, Job] = {}
    page = 0
    total_pages = 1
    started = time.time()

    while page < total_pages and page < MAX_PAGES:
        body = {
            "requests": [
                {
                    "indexName": ALGOLIA_INDEX,
                    "params": (
                        f"hitsPerPage={HITS_PER_PAGE}"
                        f"&page={page}"
                        f"&facets=%5B%22functionFilter%22%2C%22maison%22%5D"
                    ),
                    "filters": filters,
                }
            ]
        }
        response = session.post(ALGOLIA_URL, json=body, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        result = payload["results"][0]
        total_pages = int(result.get("nbPages", 1))
        hits = result.get("hits", [])

        added = 0
        for hit in hits:
            job = _to_job(hit)
            if not job:
                continue
            if job.native_job_id in seen:
                continue
            seen[job.native_job_id] = job
            added += 1

        print(
            f"  page {page + 1}/{total_pages}: "
            f"{len(hits)} hits, {added} new "
            f"(nbHits={result.get('nbHits')}, kept={len(seen)})",
            flush=True,
        )

        page += 1
        if page < total_pages and page < MAX_PAGES:
            time.sleep(REQUEST_DELAY_SECONDS)

    elapsed = time.time() - started
    print(flush=True)
    print(f"=== {len(seen)} jobs in scope (runtime {elapsed:.1f}s) ===", flush=True)

    return [asdict(j) for j in seen.values()]


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    jobs = scrape()
    print(flush=True)

    for j in jobs:
        desc = (j["description"] or "").strip()
        desc = BeautifulSoup(desc, "html.parser").get_text(" ", strip=True)
        desc = desc[:200] + ("…" if len(desc) > 200 else "")
        maison = (j["raw_payload"] or {}).get("maison")

        print(f"[{j['identifier'] or j['native_job_id']}] {j['title']}")
        print(f"  Maison     : {maison}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
