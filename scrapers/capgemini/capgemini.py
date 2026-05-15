"""Capgemini job scraper — France, CDI, Data & AI professional community.

The careers UI at www.capgemini.com/careers/join-capgemini/job-search/ is a
React widget (cg-jobs WordPress plugin) that calls a public Azure-hosted JSON
API: https://cg-jobstream-api.azurewebsites.net/api/job-search

The bundled JS (cg-jobs-search-frontend.build.js) builds the request as:
  GET {API}/job-search?country_code=...&location=...&contract_type=...
                       &professional_communities=...&page=N&size=M
No auth, no CSRF — just an Origin/Referer that matches the public site.

Filter mapping for this scope (FR Data & AI CDI in the Paris area):
  country_code             = "fr-fr,FRA"
  location                 = "Issy-les-Moulineaux,PARIS,PARIS CEDEX 16"
  contract_type            = "CDI"
  professional_communities = "Data & AI"

The country_code value uses both the IETF tag (fr-fr) and the ISO-3166 alpha-3
(FRA) because Capgemini's index stores either depending on the source ATS;
the UI sends both as a comma-joined string. Same for `location` — comma-joined
is OR semantics. To widen scope, edit the FILTER_* constants.

Native job id: we use `ref` (e.g. "212502-fr_FR") rather than `id`
("212502-fr_FR_SAPBTP"). `id` embeds the ATS source (SAP_BTP today), so if
Capgemini ever re-imports the same posting from a different source the row
would look like a brand-new job on rerun. `ref` is the source-agnostic
human-meaningful key.

Pagination: the API caps response size at ~100 per page regardless of the
`size` param, so we loop until we've collected `total` rows. Current total
for this filter is ~25, so this is just defensive.
"""
from __future__ import annotations

import html
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

API_URL = "https://cg-jobstream-api.azurewebsites.net/api/job-search"

FILTER_COUNTRY_CODE = "fr-fr,FRA"
FILTER_LOCATION = "Issy-les-Moulineaux,PARIS,PARIS CEDEX 16"
FILTER_CONTRACT_TYPE = "CDI"
FILTER_PROFESSIONAL_COMMUNITIES = "Data & AI"

PAGE_SIZE = 100

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": "https://www.capgemini.com",
    "Referer": "https://www.capgemini.com/careers/join-capgemini/job-search/",
    "From": "yannickarieldossa@gmail.com",
}

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.0

MAX_PAGES = 50  # hard cap so a misconfigured filter can't loop forever


@dataclass
class Job:
    native_job_id: str         # Capgemini `ref`, e.g. "212502-fr_FR"
    title: str
    location: str
    category: str | None       # professional_communities (e.g. "Data & AI")
    apply_url: str
    employment_type: str       # contract_type (e.g. "CDI")
    description: str | None = None
    posted_date: str | None = None   # YYYY-MM-DD
    identifier: str | None = None    # full `id` with source suffix
    raw_payload: dict | None = None


def _clean_description(content: str | None) -> str | None:
    """Capgemini returns `description` as HTML with named/numeric entities
    (e.g. &eacute;, &#039;). Unescape, strip tags, return plain text."""
    if not content:
        return None
    unescaped = html.unescape(content)
    text = BeautifulSoup(unescaped, "html.parser").get_text(" ", strip=True)
    return text or None


def _posted_date(doc: dict) -> str | None:
    raw = doc.get("updated_at") or doc.get("indexed_at")
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    return raw[:10]


def _doc_to_job(doc: dict) -> Job:
    ref = (doc.get("ref") or "").strip()
    if not ref:
        raise RuntimeError(
            f"Capgemini job missing ref (id={doc.get('id')!r})"
        )

    apply_url = (doc.get("apply_job_url") or "").strip()
    if not apply_url:
        raise RuntimeError(f"Capgemini job missing apply_job_url (ref={ref!r})")

    category = (doc.get("professional_communities") or "").strip() or None

    return Job(
        native_job_id=ref,
        title=(doc.get("title") or "").strip(),
        location=(doc.get("location") or "").strip(),
        category=category,
        apply_url=apply_url,
        employment_type=(doc.get("contract_type") or "").strip(),
        description=_clean_description(doc.get("description")),
        posted_date=_posted_date(doc),
        identifier=(doc.get("id") or None),
        raw_payload=doc,
    )


def _fetch_page(session: requests.Session, page: int) -> dict:
    params = {
        "country_code": FILTER_COUNTRY_CODE,
        "location": FILTER_LOCATION,
        "contract_type": FILTER_CONTRACT_TYPE,
        "professional_communities": FILTER_PROFESSIONAL_COMMUNITIES,
        "page": str(page),
        "size": str(PAGE_SIZE),
    }
    print(f"  fetching page {page} (size={PAGE_SIZE})...", flush=True)
    response = session.get(
        API_URL, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    payload = response.json()
    total = payload.get("total")
    count = payload.get("count")
    print(f"    {count} jobs on this page (total={total})", flush=True)
    return payload


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Fetch phase...", flush=True)

    page = 1
    payload = _fetch_page(session, page)
    total = int(payload.get("total") or 0)
    docs: list[dict] = list(payload.get("data") or [])

    while len(docs) < total and page < MAX_PAGES:
        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)
        payload = _fetch_page(session, page)
        new = payload.get("data") or []
        if not new:
            break
        docs.extend(new)

    if len(docs) < total:
        print(
            f"  WARN: collected {len(docs)} of {total} (stopped at page {page})",
            flush=True,
        )

    by_ref: dict[str, Job] = {}
    for doc in docs:
        job = _doc_to_job(doc)
        # Same ref appearing twice (different page bucket) — keep the first.
        by_ref.setdefault(job.native_job_id, job)

    elapsed = time.time() - started
    print(f"  -> {len(by_ref)} jobs in {elapsed:.1f}s\n", flush=True)
    return [asdict(j) for j in by_ref.values()]


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
        desc = j["description"] or ""
        desc = desc[:200] + ("..." if len(desc) > 200 else "")
        print(f"[{j['native_job_id']} / {j['identifier']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
