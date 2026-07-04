"""McKinsey & Company job scraper — France, QuantumBlack + Tech & AI interests.

McKinsey's own careers pages (www.mckinsey.com, mckapi.mckinsey.com) sit
behind Akamai Bot Manager: plain curl gets TLS-reset and cold API calls get
503 challenge pages. But the job-search SPA actually reads its data from an
API-gateway host that carries no bot protection at all:

  https://gateway.mckinsey.com/apigw-x0cceuow60/v1/api/jobs/search

The URL is assembled client-side as
  API_HOST_GLOBAL_SEARCH_EXTERNALIZED_SERVICE + "/apigw-" +
  GLOBAL_SEARCH_API_GATEWAY_ID + "/v1"
where both values live in the `publicRuntimeConfig` JSON embedded in the
/careers/search-jobs page HTML. If the gateway id ever rotates, re-derive it
from there (saved probe: material/search-jobs.html).

The endpoint answers plain `requests` with a polite UA — no curl_cffi, no
cookies, no warm-up. Filters are ordinary query params (`countries=France`,
`interest=...`), but we fetch the whole France slice in one paginated query
and filter interests client-side, so a facet-loop bug can never produce the
partial-result false-close problem.

Each doc already carries the full posting: description sections
(`whoYouWillWorkWith`, `whatYouWillDo`, `yourBackground`, as HTML),
`jobApplyURL` (Avature apply form) and `friendlyURL` (public posting page).
No detail-page fetching — the scraper never touches the Akamai-guarded host.

Scope (locked 2026-07-03):
  - Country: France (cities today: Paris, Lyon).
  - Interests: "Analytics" (QuantumBlack, AI by McKinsey roles) + "Tech & AI".
  - Employment types: all kept — the API has no employment-type facet, and
    the user chose not to filter interns by title.

Date caveat: `postedToLinkedInDate` is the only date exposed, and evergreen
requisitions keep their original one for years (seen: 2019 on a live role).
Same story as Lever's createdAt — dedup is by native_job_id, don't trust
posted_date for recency.

Native job id: `jobID` (Avature folder id, e.g. "97947"). Stable; also the
`folderId` param of the apply URL.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

GATEWAY_BASE = "https://gateway.mckinsey.com/apigw-x0cceuow60/v1"
SEARCH_URL = f"{GATEWAY_BASE}/api/jobs/search"
DETAIL_URL_TEMPLATE = "https://www.mckinsey.com/careers/search-jobs/jobs/{friendly}"

COUNTRY_IN_SCOPE = "France"
INTERESTS_IN_SCOPE = {"Analytics", "Tech & AI"}

PAGE_SIZE = 50
MAX_PAGES = 10          # defensive cap; France is ~27 postings today
REQUEST_DELAY_SECONDS = 1.0
REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
    "Origin": "https://www.mckinsey.com",
    "Referer": "https://www.mckinsey.com/",
}

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class Job:
    native_job_id: str          # McKinsey/Avature folder id, e.g. "97947"
    title: str
    location: str               # "Paris, France" ("; "-joined if multi-city)
    category: str | None        # interest facet: "Analytics" / "Tech & AI"
    apply_url: str              # public posting page (falls back to Avature)
    employment_type: str | None = None   # not exposed by the API
    description: str | None = None
    posted_date: str | None = None       # postedToLinkedInDate — evergreen, see docstring
    identifier: str | None = None        # same as jobID
    raw_payload: dict | None = None


def _fetch_page(session: requests.Session, start: int) -> dict:
    params = {
        "countries": COUNTRY_IN_SCOPE,
        "pageSize": PAGE_SIZE,
        "start": start,
        "lang": "en",
    }
    print(f"  GET /api/jobs/search start={start} ...", flush=True)
    response = session.get(
        SEARCH_URL, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload.get("docs"), list) or "numFound" not in payload:
        # Unexpected shape means the API changed — abort rather than return a
        # partial/empty result that would close every open McKinsey row.
        raise RuntimeError(
            f"jobs/search payload has no docs/numFound "
            f"(keys={sorted(payload.keys())!r})"
        )
    return payload


def _fetch_all_docs(session: requests.Session) -> list[dict]:
    docs: list[dict] = []
    start = 0
    num_found: int | None = None
    for page in range(MAX_PAGES):
        if page:
            time.sleep(REQUEST_DELAY_SECONDS)
        payload = _fetch_page(session, start)
        num_found = payload["numFound"]
        batch = payload["docs"]
        docs.extend(batch)
        print(f"    +{len(batch)} docs ({len(docs)}/{num_found})", flush=True)
        start += PAGE_SIZE
        if start >= num_found or not batch:
            break
    else:
        raise RuntimeError(
            f"pagination did not converge after {MAX_PAGES} pages "
            f"({len(docs)}/{num_found} docs) — refusing partial result"
        )
    if num_found is not None and len(docs) < num_found:
        raise RuntimeError(
            f"collected {len(docs)} docs but numFound={num_found} — "
            f"refusing partial result"
        )
    return docs


def _strip_html(fragment: str | None) -> str | None:
    if not fragment:
        return None
    text = BeautifulSoup(fragment, "html.parser").get_text(" ", strip=True)
    return text or None


def _description(doc: dict) -> str | None:
    # Site order: Who You'll Work With / What You'll Do / Your Background.
    sections = [
        _strip_html(doc.get(key))
        for key in ("whoYouWillWorkWith", "whatYouWillDo", "yourBackground")
    ]
    joined = "\n\n".join(s for s in sections if s)
    return joined or None


def _location(doc: dict) -> str:
    cities = [c.strip() for c in (doc.get("cities") or []) if c and c.strip()]
    countries = doc.get("countries") or []
    if countries == [COUNTRY_IN_SCOPE]:
        return "; ".join(f"{c}, France" for c in cities)
    return "; ".join(cities)


def _posted_date(doc: dict) -> str | None:
    raw = doc.get("postedToLinkedInDate")
    if isinstance(raw, str) and _ISO_DATE_RE.match(raw.strip()):
        return raw.strip()
    return None


def _doc_to_job(doc: dict) -> Job:
    job_id = doc.get("jobID")
    if not job_id:
        raise RuntimeError(f"McKinsey doc missing jobID (title={doc.get('title')!r})")
    job_id = str(job_id)

    friendly = (doc.get("friendlyURL") or "").strip()
    apply_url = (
        DETAIL_URL_TEMPLATE.format(friendly=friendly)
        if friendly
        else (doc.get("jobApplyURL") or "").strip()
    )
    if not apply_url:
        raise RuntimeError(f"McKinsey doc {job_id} has no friendlyURL/jobApplyURL")

    return Job(
        native_job_id=job_id,
        title=(doc.get("title") or "").strip(),
        location=_location(doc),
        category=doc.get("interest") or None,
        apply_url=apply_url,
        description=_description(doc),
        posted_date=_posted_date(doc),
        identifier=job_id,
        raw_payload=doc,
    )


def scrape() -> list[dict]:
    session = requests.Session()

    started = time.time()
    print("Listing phase (single countries=France query)...", flush=True)
    docs = _fetch_all_docs(session)

    print("Filter phase...", flush=True)
    kept: dict[str, Job] = {}
    for doc in docs:
        interest = doc.get("interest")
        if interest not in INTERESTS_IN_SCOPE:
            continue
        job = _doc_to_job(doc)
        if job.native_job_id in kept:
            continue
        kept[job.native_job_id] = job
        print(f"  {job.native_job_id} [{interest}] {job.title!r} -> KEEP", flush=True)

    elapsed = time.time() - started
    print(
        f"\n  -> {len(kept)} jobs kept of {len(docs)} France postings "
        f"in {elapsed:.1f}s\n",
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
        desc = j["description"] or ""
        desc = desc[:200] + ("..." if len(desc) > 200 else "")
        print(f"[{j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
