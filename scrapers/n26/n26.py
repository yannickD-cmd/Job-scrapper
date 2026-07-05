"""N26 job scraper — France (Paris), Tech + Data/AI, permanent roles only.

N26's careers site (n26.com/en-eu/careers) is a public Greenhouse job board,
so we hit the standard Greenhouse Job Board API directly:

  https://boards-api.greenhouse.io/v1/boards/n26/jobs?content=true

`content=true` embeds each posting's description HTML plus `departments[]`
and `metadata[]` in the listing payload, so a single anonymous GET covers
everything — no per-job detail calls. Same pattern as Mirakl / Datadog /
Doctolib.

Scope (locked 2026-07): France, Tech + Data/AI, permanent only. The boards
API has no filter params, so everything is enforced client-side:

  - Location: `location.name` is bare free-text city names ("Paris",
    "Berlin", or multi-city "Berlin, Vienna, Barcelona"). N26's only French
    office is Paris, so we keep any posting whose location mentions Paris (or
    the word France). Widen FRANCE_RE if N26 opens another FR city.

  - Relevance: N26 files its whole engineering org under `Tech - <subteam>`
    departments (Core Systems, Analytics Engineering, Runtime Platform,
    Security Engineering, Regulatory Technology, ...). So the PRIMARY gate is
    `department startswith "Tech"` — this keeps the eng org wholesale,
    including titles the shared keyword predicate would miss on its own
    ("Android Engineer", "Engineering Manager", "iOS Engineer" carry no
    bare-"engineer" keyword). As a SAFETY NET we also keep any posting whose
    TITLE passes the shared is_tech_role() predicate, to catch data / AI /
    software roles that N26 files under a NON-Tech department (e.g. a Data
    Scientist under Risk, or Marketing Data analytics). Product-management and
    UX roles are intentionally out of scope (Tech + Data/AI only), so a bare
    "Product Manager" under Product-* is dropped.

  - Employment type: N26 exposes NO permanent/fixed-term metadata field (the
    only metadata is "Time Type": Full time, which is FT-vs-part-time, not
    CDI-vs-CDD). N26 signals non-permanent roles in the TITLE instead — e.g.
    "AFC Associate - SAR Delegate (CDD / Fixed-term contract)". So permanent-
    only is a title gate: we drop intern / apprentice / alternance / stage /
    working-student AND CDD / fixed-term / temporary / interim.

NOTE: at scope-lock time N26's only two Paris roles were AFC compliance
("SAR Delegate" / "Déclarant Tracfin") under the Risk department — both are
non-Tech and one is a CDD, so both are correctly filtered out. A run
yielding 0 jobs is therefore a NORMAL outcome, not a failure: N26's
engineering hires in Berlin / Barcelona / Madrid, and this scraper exists to
catch a Paris Tech/Data CDI the day it posts. The DB layer treats an empty
return as "close nothing, log success", so empty runs are safe in CI.
(Same situation as the Mirakl scraper — see project_mirakl_scraper memory.)

Native job id: Greenhouse `id` (e.g. 7965433), stable across the posting's
lifetime. posted_date: `first_published` (fallback `updated_at`), YYYY-MM-DD.
identifier: `requisition_id` (e.g. R6557). apply_url: `absolute_url`
(n26.com/en-eu/careers/positions/<id>?gh_jid=<id>) — N26's own branded page.

To widen scope, edit FRANCE_RE / TECH_DEPT_PREFIX / EXCLUDED_TITLE_RE.
"""
from __future__ import annotations

import html
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

from scrapers._relevance import is_tech_role

API_URL = "https://boards-api.greenhouse.io/v1/boards/n26/jobs"

# N26's only French office is Paris; multi-city listings ("Paris, Berlin")
# still count as available in France. Add cities here if N26 expands in FR.
FRANCE_RE = re.compile(r"\b(paris|france)\b", re.IGNORECASE)

# N26's engineering org lives under "Tech - <subteam>" department names.
TECH_DEPT_PREFIX = "tech"

# No employment-type metadata field on this board: gate out non-permanent
# roles by title. Covers English + French variants of intern / apprentice /
# working-student AND fixed-term (CDD / fixed-term / temporary / interim).
# Deliberately NOT matching bare "temp" (would hit template/temperature).
EXCLUDED_TITLE_RE = re.compile(
    r"\b("
    r"intern(ship)?|apprentice(ship)?|apprenti(e)?|alternan(ce|t|te)"
    r"|stagiaire|stage|freelance|werkstudent|working student"
    r"|cdd|fixed[ -]term|temporary|interim|int[ée]rim"
    r")\b",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": "https://n26.com",
    "Referer": "https://n26.com/en-eu/careers",
    "From": "yannickarieldossa@gmail.com",
}

REQUEST_TIMEOUT = 30


@dataclass
class Job:
    native_job_id: str         # Greenhouse posting id (string), e.g. "7965433"
    title: str
    location: str              # location.name from payload (free-text, may list several cities)
    category: str | None       # department names, " | "-joined
    apply_url: str             # n26.com/en-eu/careers/positions/<id> (absolute_url)
    employment_type: str | None = None  # not exposed by this board
    description: str | None = None
    posted_date: str | None = None     # first_published, YYYY-MM-DD
    identifier: str | None = None      # requisition_id when N26 filled it in
    raw_payload: dict | None = None


def _dept_names(doc: dict) -> list[str]:
    return [(d.get("name") or "").strip() for d in (doc.get("departments") or [])]


def _is_tech_dept(doc: dict) -> bool:
    return any(n.lower().startswith(TECH_DEPT_PREFIX) for n in _dept_names(doc))


def _in_scope(doc: dict) -> bool:
    location = (doc.get("location") or {}).get("name") or ""
    if not FRANCE_RE.search(location):
        return False
    title = doc.get("title") or ""
    if EXCLUDED_TITLE_RE.search(title):
        return False
    # Primary gate: N26's engineering org (Tech-* departments). Safety net:
    # a data/AI/software title filed under a non-Tech department.
    return _is_tech_dept(doc) or is_tech_role(title)


def _category(doc: dict) -> str | None:
    parts = [n for n in _dept_names(doc) if n]
    return " | ".join(parts) if parts else None


def _clean_description(content: str | None) -> str | None:
    """Greenhouse returns `content` as HTML with the tags entity-encoded
    (e.g. &lt;p&gt; and &#39;). One unescape pass yields normal HTML; strip
    tags with BeautifulSoup. Same pattern as Mirakl / Datadog / Doctolib."""
    if not content:
        return None
    unescaped = html.unescape(content)
    text = BeautifulSoup(unescaped, "html.parser").get_text(" ", strip=True)
    return text or None


def _posted_date(doc: dict) -> str | None:
    raw = doc.get("first_published") or doc.get("updated_at")
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    return raw[:10]


def _fetch_jobs(session: requests.Session) -> list[dict]:
    print(f"  GET {API_URL}?content=true ...", flush=True)
    response = session.get(
        API_URL,
        params={"content": "true"},
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    jobs = payload.get("jobs") or []
    meta = payload.get("meta") or {}
    print(f"    {len(jobs)} postings (meta.total={meta.get('total')})", flush=True)

    # The Tech-department gate depends on content=true embedding departments[]
    # per posting. If Greenhouse ever stops doing that, every posting would be
    # filtered out and the run would look "empty but successful" — fail loudly
    # instead so the DB safety guard isn't the only thing standing.
    if jobs and not any("departments" in j for j in jobs):
        raise RuntimeError("Greenhouse payload has no departments[] — gate cannot run")
    return jobs


def _doc_to_job(doc: dict) -> Job:
    job_id = doc.get("id")
    if not job_id:
        raise RuntimeError(f"N26 posting missing id (title={doc.get('title')!r})")
    job_id = str(job_id)

    apply_url = (doc.get("absolute_url") or "").strip()
    if not apply_url:
        apply_url = f"https://n26.com/en-eu/careers/positions/{job_id}?gh_jid={job_id}"

    location_obj = doc.get("location") or {}
    req_id = doc.get("requisition_id")
    if isinstance(req_id, str):
        req_id = req_id.strip() or None

    return Job(
        native_job_id=job_id,
        title=(doc.get("title") or "").strip(),
        location=(location_obj.get("name") or "").strip(),
        category=_category(doc),
        apply_url=apply_url,
        description=_clean_description(doc.get("content")),
        posted_date=_posted_date(doc),
        identifier=req_id,
        raw_payload=doc,
    )


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Listing phase...", flush=True)
    docs = _fetch_jobs(session)

    print("Filter phase...", flush=True)
    candidates = [d for d in docs if _in_scope(d)]
    print(
        f"  kept {len(candidates)} (dropped {len(docs) - len(candidates)} out-of-scope)",
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
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
