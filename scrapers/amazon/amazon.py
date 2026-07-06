"""Amazon / AWS France job scraper — core tech & data roles.

Amazon (and AWS — AWS is a *business_category* inside the same board, not a
separate portal) publishes every posting through the public amazon.jobs search
API:

  https://www.amazon.jobs/en/search.json?country=FRA&result_limit=100&offset=0

The listing already embeds the full `description`, so a single crawl (no detail
calls) covers everything. `country=FRA` returns France-only (~286 today), and
the payload is served to a plain polite User-Agent — no browser/Playwright, so
this is CI-safe.

Scope (locked 2026-07): France, core tech & data, all full-time.

  - Contract type is NOT exposed: `job_schedule_type` is "full-time" for every
    French posting and there is no CDI/CDD field anywhere in the payload. So the
    usual employment-type axis collapses — we keep everything and record the raw
    schedule type. (Amazon's CDD/alternance roles are simply not distinguished
    here; the student/apprentice roles live under a separate business_category.)

  - Category gate on `job_category` (the board's own facet — see
    `feedback_prefer_platform_category_over_is_tech_role`). The board is ~90%
    fulfillment/warehouse noise (152 "fulfillment-ops"); the clean signal is the
    job_category. TECH_CATEGORIES is the in-scope set. This deliberately EXCLUDES
    "Operations, IT, & Support Engineering" and "Facilities, Maintenance, & Real
    Estate" — those are AWS data-center / fulfillment-center *infra ops* (Data
    Center Manager, DCEO Engineer, electricity alternances, FC IT support), which
    the user scoped OUT. Applying is_tech_role() on the title instead would drag
    that whole bucket back in, so we do NOT layer it here.

  - Student / apprentice programs: the user chose to keep *data/AI* ones. Amazon
    files its tech interns under the tech job_categories already (e.g. "Associate
    Solutions Architect Intern" sits in Solutions Architect and is kept by the
    category gate). The dedicated `studentprograms` business_category is, today,
    100% logistics + marketing — so it is correctly dropped by the category gate.
    As a surgical future-proof for the user's choice, a role in `studentprograms`
    (or flagged `university_job`) with a data/AI/software *title* is also kept via
    is_tech_role(). This adds 0 rows today and — because it is scoped to student
    programs only — cannot re-introduce the excluded infra-ops bucket.

AWS vs Amazon: both flow through this one scraper; `business_category` ("aws",
"retail", "amazon-security", ...) is preserved in raw_payload for forensics.

Native job id: `id_icims` (the numeric requisition id shown in every apply URL),
stable across the posting's life. posted_date: parsed from the human string
"July  6, 2026" (note the double space) into ISO YYYY-MM-DD.

To widen scope, edit TECH_CATEGORIES.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from scrapers._relevance import is_tech_role

SEARCH_URL = "https://www.amazon.jobs/en/search.json"

# The board's own job_category facet — core tech & data only. NB: excludes
# "Operations, IT, & Support Engineering" and "Facilities, ..." (infra ops,
# scoped out — see module docstring).
TECH_CATEGORIES = frozenset({
    "Software Development",
    "Solutions Architect",
    "Machine Learning Science",
    "Business Intelligence",
    "Systems, Quality, & Security Engineering",
    "Project/Program/Product Management--Technical",
})

HEADERS = {
    "User-Agent": (
        "JobScrapperBot/1.0 (+https://github.com/yannickD-cmd/Job-scrapper; "
        "contact yannickarieldossa@gmail.com) France Data/AI job board"
    ),
    "Accept": "application/json",
    "From": "yannickarieldossa@gmail.com",
}

RESULT_LIMIT = 100          # max page size the API honours
MAX_PAGES = 20              # defensive cap: 20 * 100 = 2000 France postings ceiling
REQUEST_DELAY_SECONDS = 1.2
REQUEST_TIMEOUT = 30


@dataclass
class Job:
    native_job_id: str          # id_icims (numeric requisition id), e.g. "10466145"
    title: str
    location: str               # normalized_location, e.g. "Paris, Ile-de-France, FRA"
    category: str | None        # job_category
    apply_url: str              # amazon.jobs posting page
    employment_type: str        # job_schedule_type (always "full-time" today)
    description: str | None = None
    posted_date: str | None = None    # YYYY-MM-DD
    identifier: str | None = None     # UUID `id` (forensic)
    raw_payload: dict | None = None


def _clean_description(raw: str | None) -> str | None:
    """Descriptions come as HTML fragments (<br/>, <b>, ...). Strip to text."""
    if not raw:
        return None
    text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    return text or None


def _posted_date(raw: str | None) -> str | None:
    """Parse "July  6, 2026" (month name is English; note the double space)."""
    if not raw or not raw.strip():
        return None
    collapsed = re.sub(r"\s+", " ", raw.strip())
    try:
        return datetime.strptime(collapsed, "%B %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _in_scope(doc: dict) -> bool:
    if (doc.get("country_code") or "").upper() != "FRA":
        return False
    if doc.get("job_category") in TECH_CATEGORIES:
        return True
    # data/AI student-program catch (see docstring) — 0 rows today, future-proof,
    # scoped to student programs so it cannot pull in the excluded infra-ops bucket.
    is_student = (doc.get("business_category") == "studentprograms") or bool(doc.get("university_job"))
    return is_student and is_tech_role(doc.get("title"))


def _fetch_all_fr(session: requests.Session) -> list[dict]:
    """Crawl every France posting (offset pagination). Returns raw docs."""
    docs: list[dict] = []
    for page in range(MAX_PAGES):
        offset = page * RESULT_LIMIT
        params = {
            "base_query": "",
            "country": "FRA",
            "result_limit": RESULT_LIMIT,
            "sort": "recent",
            "offset": offset,
        }
        print(f"  GET {SEARCH_URL} offset={offset} ...", flush=True)
        resp = session.get(SEARCH_URL, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        jobs = payload.get("jobs") or []
        hits = payload.get("hits")
        if page == 0:
            print(f"    hits={hits}", flush=True)
        if not jobs:
            break
        docs.extend(jobs)
        if hits is not None and offset + RESULT_LIMIT >= hits:
            break
        time.sleep(REQUEST_DELAY_SECONDS)
    else:
        # Loop ran to MAX_PAGES without a natural stop — pagination may be broken.
        raise RuntimeError(f"hit MAX_PAGES={MAX_PAGES} without exhausting results")
    return docs


def _doc_to_job(doc: dict) -> Job:
    icims = doc.get("id_icims") or doc.get("id")
    if not icims:
        raise RuntimeError(f"Amazon posting missing id (title={doc.get('title')!r})")

    job_path = (doc.get("job_path") or "").strip()
    apply_url = f"https://www.amazon.jobs{job_path}" if job_path else (doc.get("url_next_step") or "").strip()

    return Job(
        native_job_id=str(icims),
        title=(doc.get("title") or "").strip(),
        location=(doc.get("normalized_location") or doc.get("location") or "").strip(),
        category=doc.get("job_category"),
        apply_url=apply_url,
        employment_type=(doc.get("job_schedule_type") or "").strip(),
        description=_clean_description(doc.get("description")),
        posted_date=_posted_date(doc.get("posted_date")),
        identifier=doc.get("id"),
        raw_payload=doc,
    )


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Listing phase (France crawl)...", flush=True)
    docs = _fetch_all_fr(session)
    print(f"  {len(docs)} France postings fetched", flush=True)

    print("Filter phase...", flush=True)
    candidates = [d for d in docs if _in_scope(d)]
    print(f"  kept {len(candidates)} (dropped {len(docs) - len(candidates)} out-of-scope)", flush=True)

    kept: dict[str, Job] = {}
    for doc in candidates:
        job = _doc_to_job(doc)
        if job.native_job_id in kept:
            continue
        kept[job.native_job_id] = job
        biz = doc.get("business_category")
        print(f"  {job.native_job_id} [{biz}] {job.title!r} -> KEEP", flush=True)

    elapsed = time.time() - started
    print(f"\n  -> {len(kept)} jobs in {elapsed:.1f}s\n", flush=True)
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
