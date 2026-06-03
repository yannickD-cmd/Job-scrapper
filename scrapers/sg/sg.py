"""Société Générale job scraper — France, CDI + Alternance, IT + Innovation/Digital.

Single-pass scrape. SG's careers site is Drupal-fronted, but the listing
is rendered entirely client-side from a backend search service they call
"Quantum" (`api.socgen.com/.../search-profile`). The frontend talks to
it through a same-origin proxy (`/search-proxy.php`) that injects auth
and forwards via the `X-Proxy-URL` header. We hit that proxy directly.

Each Quantum result already contains title, description (HTML), location,
category, contract type, dates and apply URL, so unlike BNP/Sanofi there
is no detail-page enrichment phase.

Auth (3 calls, then paginated POSTs reuse the bearer):
1. GET  /rechercher                       — seed Drupal+Imperva session
                                            cookies, grab `csrfToken`
                                            from inline drupalSettings.
2. POST /sg-careers-offers/get-token      — with `X-CSRF-Token`, returns
                                            a JWT (lifetime ~60min, well
                                            beyond a single scrape).
3. POST /search-proxy.php                 — paged search; needs
                                            `Authorization-API: Bearer <jwt>`
                                            and `X-Proxy-URL` pointing
                                            at the real Quantum endpoint.

Filter constants come from drupalSettings.quantum.quantum_filters in the
listing page; see scrapers/sg/material/rechercher.html. Field-name
constants (`sourcestr8`, `sourcecsv1`, …) come from the global-quantum.js
bundle saved alongside it.

To change scope: edit COUNTRY_IDS / CONTRACT_IDS / JOB_FAMILY_IDS.
SG has no separate "Data / AI" job family — those roles live under IT
(BJ725) or Innovation/Digital (JN482).
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime

import requests

HOST = "https://careers.societegenerale.com"
LISTING_PAGE = HOST + "/rechercher"
TOKEN_URL = HOST + "/sg-careers-offers/get-token"
PROXY_URL = HOST + "/search-proxy.php"
QUANTUM_SEARCH_URL = (
    "https://api.socgen.com/business-support/it-for-it-support"
    "/cognitive-service-knowledge/api/v1/search-profile"
)

# Field-name constants from global-quantum.js. The Quantum API uses
# opaque positional field names; keeping the indirection here so future
# readers can see what each one maps to without re-reading the JS bundle.
FIELD_TYPE = "sourcestr6"          # always "job" for job postings
FIELD_LOCATION_FULL = "sourcecsv1"  # multi-segment CSV: country;region;city
FIELD_CONTRACT = "sourcestr8"      # contract type label (CDI, Stage, …)
FIELD_JOB_FAMILY = "sourcestr10"   # job family label (IT, Innovation/Digital, …)
FIELD_REQ_ID = "sourcestr4"        # bare req-id (e.g. "25000GY7")
FIELD_FULL_ID = "sourcestr12"      # req-id with locale suffix ("25000GY7-fr")
FIELD_DESCRIPTION = "sourcevarchar2"
FIELD_LOCATION_LABEL = "sourcestr7"  # human-readable city, country
FIELD_POSTED_AT = "sourcedatetime2"  # original posting date (older of the two)

# Filter IDs taken from drupalSettings.quantum.quantum_filters on the
# /rechercher page. The frontend uses these exact values.
COUNTRY_IDS = ["FRA"]
CONTRACT_IDS = ["STANDARD", "APPRENTICESHIP"]  # CDI, Alternance
JOB_FAMILY_IDS = ["BJ725", "JN482"]  # IT, Innovation/Digital

SCOPE_COUNTRY = "France"
# employment_type is no longer single-valued (CDI + Alternance), so it is
# read per row from FIELD_CONTRACT (sourcestr8), which carries the FR label.

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "From": "yannickarieldossa@gmail.com",
}

# Headers the XHR layer sends. Imperva/Drupal blocks /sg-careers-offers/get-token
# with 403 unless Origin + Referer + X-Requested-With are present (probed).
XHR_HEADERS = {
    "Accept": "*/*",
    "Origin": HOST,
    "Referer": LISTING_PAGE,
    "X-Requested-With": "XMLHttpRequest",
}

REQUEST_DELAY_SECONDS = 1.5
REQUEST_TIMEOUT = 30
PAGE_SIZE = 50  # SG cap not documented; 50 returns fine, keeps page count low

CSRF_RE = re.compile(r'"csrfToken":"([0-9a-f]{64})"')


@dataclass
class Job:
    native_job_id: str          # bare req-id, e.g. "25000GY7" — locale-agnostic
    title: str
    location: str
    category: str
    apply_url: str
    employment_type: str | None = None  # FR contract label from sourcestr8 (CDI / Alternance)
    description: str | None = None
    posted_date: str | None = None  # YYYY-MM-DD
    identifier: str | None = None   # full ID incl. locale, e.g. "25000GY7-fr"
    raw_payload: dict | None = None


def _grab_csrf(session: requests.Session) -> str:
    """Hit the listing page and pull `csrfToken` out of drupalSettings JSON.

    Also seeds the Drupal + Imperva session cookies the later POSTs need.
    """
    response = session.get(LISTING_PAGE, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    match = CSRF_RE.search(response.text)
    if not match:
        raise RuntimeError("csrfToken not found in /rechercher drupalSettings")
    return match.group(1)


def _get_bearer(session: requests.Session, csrf: str) -> str:
    headers = {**HEADERS, **XHR_HEADERS, "X-CSRF-Token": csrf}
    response = session.post(TOKEN_URL, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    token = payload.get("token")
    if not token:
        raise RuntimeError(f"get-token returned no token: {payload!r}")
    return token


def _build_query(skip_from: int) -> dict:
    return {
        "profile": "ces_profile_sgcareers",
        "query": {
            "advanced": [
                {"type": "simple", "name": FIELD_TYPE, "op": "eq", "value": "job"},
                {"type": "multi", "name": FIELD_LOCATION_FULL, "op": "eq", "values": COUNTRY_IDS},
                {"type": "multi", "name": FIELD_CONTRACT, "op": "eq", "values": CONTRACT_IDS},
                {"type": "multi", "name": FIELD_JOB_FAMILY, "op": "eq", "values": JOB_FAMILY_IDS},
            ],
            "skipCount": PAGE_SIZE,
            "skipFrom": skip_from,
        },
        "lang": "fr",
        "responseType": "SearchResult",
    }


def _search_page(session: requests.Session, bearer: str, skip_from: int) -> dict:
    headers = {
        **HEADERS,
        **XHR_HEADERS,
        "Content-Type": "application/json",
        "Authorization-API": "Bearer " + bearer,
        "X-Proxy-URL": QUANTUM_SEARCH_URL,
    }
    response = session.post(
        PROXY_URL,
        headers=headers,
        data=json.dumps(_build_query(skip_from)),
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _parse_posted_date(doc: dict) -> str | None:
    """Quantum emits 'YYYY-MM-DD HH:MM:SS'; we only keep the date part."""
    raw = doc.get(FIELD_POSTED_AT)
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").date().isoformat()
    except ValueError:
        # Defensive: SG sometimes already gives a bare ISO date.
        try:
            return date.fromisoformat(raw[:10]).isoformat()
        except ValueError:
            return None


def _doc_to_job(doc: dict) -> Job | None:
    req_id = (doc.get(FIELD_REQ_ID) or "").strip()
    if not req_id:
        return None

    apply_url = (doc.get("resulturl") or doc.get("url1") or "").strip()
    if not apply_url:
        return None

    return Job(
        native_job_id=req_id,
        title=(doc.get("title") or doc.get("resulttitle") or "").strip(),
        location=(doc.get(FIELD_LOCATION_LABEL) or "").strip(),
        category=(doc.get(FIELD_JOB_FAMILY) or "").strip(),
        apply_url=apply_url,
        employment_type=(doc.get(FIELD_CONTRACT) or "").strip() or None,
        description=doc.get(FIELD_DESCRIPTION),
        posted_date=_parse_posted_date(doc),
        identifier=(doc.get(FIELD_FULL_ID) or "").strip() or None,
        raw_payload=doc,
    )


def _prefer_french(existing: Job, candidate: Job) -> Job:
    """SG indexes each posting twice — once under /offres-d-emploi/...-fr
    and once under /en/job-offers/...-en. Same req-id. We dedupe on the
    bare req-id and keep the French row when both exist (matches our
    `lang: 'fr'` request)."""
    if "/offres-d-emploi/" in existing.apply_url:
        return existing
    if "/offres-d-emploi/" in candidate.apply_url:
        return candidate
    return existing


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    print("Auth phase...", flush=True)
    csrf = _grab_csrf(session)
    print(f"  csrf token   : {csrf[:12]}…", flush=True)
    time.sleep(REQUEST_DELAY_SECONDS)

    bearer = _get_bearer(session, csrf)
    print(f"  bearer token : {bearer[:24]}… ({len(bearer)} chars)", flush=True)
    time.sleep(REQUEST_DELAY_SECONDS)

    print("Search phase...", flush=True)
    started = time.time()
    all_jobs: dict[str, Job] = {}
    skip_from = 0
    total = None

    while True:
        payload = _search_page(session, bearer, skip_from)
        if total is None:
            total = payload.get("TotalCount", 0)
            print(f"  TotalCount: {total}", flush=True)

        result = payload.get("Result") or {}
        docs = result.get("Docs") or []
        new_this_page = 0
        for doc in docs:
            job = _doc_to_job(doc)
            if not job:
                continue
            existing = all_jobs.get(job.native_job_id)
            if existing is None:
                all_jobs[job.native_job_id] = job
                new_this_page += 1
            else:
                all_jobs[job.native_job_id] = _prefer_french(existing, job)

        print(
            f"  skipFrom={skip_from:>4}: {len(docs)} docs "
            f"({new_this_page} new, {len(all_jobs)}/{total} cumulative)",
            flush=True,
        )

        # Stop when we've collected the announced total, when a page
        # returns nothing, or when we've moved past the announced total.
        if not docs or len(all_jobs) >= total or skip_from + len(docs) >= total:
            break

        skip_from += PAGE_SIZE
        time.sleep(REQUEST_DELAY_SECONDS)

    elapsed = time.time() - started
    print(f"  → {len(all_jobs)} jobs in {elapsed:.1f}s\n", flush=True)

    return [asdict(j) for j in all_jobs.values()]


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
        from bs4 import BeautifulSoup
        desc = BeautifulSoup(j["description"] or "", "html.parser").get_text(" ", strip=True)
        desc = desc[:200] + ("…" if len(desc) > 200 else "")

        print(f"[{j['identifier'] or j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
