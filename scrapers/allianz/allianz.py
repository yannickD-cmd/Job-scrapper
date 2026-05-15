"""Allianz job scraper — France, IT & Data roles.

The public careers site at https://careers.allianz.com/global/en is a Phenom
People CareerConnect tenant (`AISAIPGB`) backed by SAP SuccessFactors company
`AZGROUPPROD`. The search-results page server-side-renders an `eagerLoadRefineSearch`
JSON blob containing the current page of jobs plus global aggregations:

  https://careers.allianz.com/global/en/search-results?from=<n>&size=<≤500>

Filter URL params (e.g. `?country=France`) are accepted but ignored by the
SSR — the page always returns the global result set and filters client-side.
The internal `/api/careers/searchJobs` endpoint rejects direct calls with
"Tenant not identified" (CSRF + session-bound). So we walk SSR pages and
filter on the client.

Page size caps at 500. Total ≈2015 postings → 5 SSR calls.

Multi-location postings appear in more than one SSR page window, so we
deduplicate by `jobId` after collecting all pages.

Native job id: SuccessFactors requisition id (`jobId == reqId`, an integer
string). Stable per posting and the same id SF uses at:
  https://career5.successfactors.eu/careers?company=AZGROUPPROD&career_job_req_id=<id>

To widen scope, edit COUNTRIES_IN_SCOPE or CATEGORIES_IN_SCOPE.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass

import requests

BASE_URL = "https://careers.allianz.com/global/en/search-results"
PAGE_SIZE = 500
TOTAL_HITS_HARD_CAP = 5000

PUBLIC_JOB_URL_TEMPLATE = "https://careers.allianz.com/global/en/job/{job_id}"
APPLY_URL_TEMPLATE = (
    "https://career5.successfactors.eu/careers"
    "?company=AZGROUPPROD&career_job_req_id={job_id}&career_ns=job_application"
)

COUNTRIES_IN_SCOPE = {"France"}
CATEGORIES_IN_SCOPE = {"Data & AI", "IT & Tech Engineering"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://careers.allianz.com/global/en",
    "From": "yannickarieldossa@gmail.com",
}

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.0

_EAGER_KEY = '"eagerLoadRefineSearch":'


@dataclass
class Job:
    native_job_id: str         # SuccessFactors reqId (integer string)
    title: str
    location: str              # "city, state, country" composed from fields
    category: str | None       # Allianz `category` (e.g. "IT & Tech Engineering")
    apply_url: str             # canonical careers.allianz.com detail page
    employment_type: str       # raw `employmentType` (Permanent / Temporary)
    description: str | None = None
    posted_date: str | None = None    # YYYY-MM-DD
    identifier: str | None = None     # same as native_job_id, kept for parity
    raw_payload: dict | None = None


def _extract_eager_blob(html: str) -> dict:
    """Walk JSON braces to extract the eagerLoadRefineSearch object."""
    idx = html.find(_EAGER_KEY)
    if idx < 0:
        raise RuntimeError("eagerLoadRefineSearch SSR blob not found on page")
    i = idx + len(_EAGER_KEY)
    while i < len(html) and html[i] in " \t\r\n":
        i += 1
    if i >= len(html) or html[i] != "{":
        raise RuntimeError("eagerLoadRefineSearch SSR blob not a JSON object")

    start = i
    depth = 0
    in_str = False
    esc = False
    backslash = chr(92)
    while i < len(html):
        c = html[i]
        if in_str:
            if esc:
                esc = False
            elif c == backslash:
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(html[start:i + 1])
        i += 1
    raise RuntimeError("unterminated eagerLoadRefineSearch JSON object")


def _in_scope(doc: dict) -> bool:
    if doc.get("country") not in COUNTRIES_IN_SCOPE:
        return False
    return doc.get("category") in CATEGORIES_IN_SCOPE


def _compose_location(doc: dict) -> str:
    """`location` field includes postcode; build a cleaner city/state/country."""
    parts = [doc.get("city"), doc.get("state"), doc.get("country")]
    return ", ".join(p for p in parts if p)


def _doc_to_job(doc: dict) -> Job:
    job_id = str(doc.get("jobId") or doc.get("reqId") or "").strip()
    if not job_id:
        raise RuntimeError(
            f"Allianz posting missing jobId/reqId (title={doc.get('title')!r})"
        )

    posted = doc.get("postedDate") or doc.get("dateCreated")
    if isinstance(posted, str) and len(posted) >= 10:
        posted = posted[:10]
    else:
        posted = None

    desc = doc.get("descriptionTeaser")
    if isinstance(desc, str):
        desc = desc.strip() or None
    else:
        desc = None

    return Job(
        native_job_id=job_id,
        title=(doc.get("title") or "").strip(),
        location=_compose_location(doc),
        category=(doc.get("category") or None),
        apply_url=PUBLIC_JOB_URL_TEMPLATE.format(job_id=job_id),
        employment_type=(doc.get("employmentType") or "").strip(),
        description=desc,
        posted_date=posted,
        identifier=job_id,
        raw_payload=doc,
    )


def _fetch_page(session: requests.Session, offset: int) -> tuple[list[dict], int]:
    url = f"{BASE_URL}?from={offset}&size={PAGE_SIZE}"
    print(f"  fetching from={offset} size={PAGE_SIZE}...", flush=True)
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    obj = _extract_eager_blob(response.text)
    if obj.get("status") != 200:
        raise RuntimeError(
            f"SSR eagerLoadRefineSearch returned status={obj.get('status')} "
            f"errorMsg={obj.get('errorMsg')!r}"
        )
    data = obj.get("data") or {}
    docs = data.get("jobs") or []
    total = int(obj.get("totalHits") or 0)
    print(f"    {len(docs)} jobs (totalHits={total})", flush=True)
    return docs, total


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Listing phase...", flush=True)

    all_docs: dict[str, dict] = {}
    offset = 0
    total_hits = 0
    while True:
        docs, total_hits = _fetch_page(session, offset)
        if not docs:
            break
        for d in docs:
            key = str(d.get("jobId") or d.get("reqId") or "")
            if key:
                all_docs[key] = d
        offset += PAGE_SIZE
        if offset >= total_hits or offset >= TOTAL_HITS_HARD_CAP:
            break
        time.sleep(REQUEST_DELAY_SECONDS)

    print(
        f"  collected {len(all_docs)} unique postings "
        f"(totalHits={total_hits})",
        flush=True,
    )

    print("Filter phase...", flush=True)
    candidates = [d for d in all_docs.values() if _in_scope(d)]
    print(
        f"  kept {len(candidates)} (dropped {len(all_docs) - len(candidates)} out-of-scope)",
        flush=True,
    )

    kept: dict[str, Job] = {}
    for doc in candidates:
        job = _doc_to_job(doc)
        if job.native_job_id in kept:
            continue
        kept[job.native_job_id] = job
        print(f"  {job.native_job_id} {job.title!r} -> KEEP", flush=True)

    elapsed = time.time() - started
    print(flush=True)
    print(f"  -> {len(kept)} jobs in {elapsed:.1f}s\n", flush=True)
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
        desc = j["description"] or ""
        desc = desc[:200] + ("..." if len(desc) > 200 else "")
        print(f"[{j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
