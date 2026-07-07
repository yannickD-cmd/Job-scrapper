"""Snowflake job scraper — France, Data & AI + Software/IT, all employment types.

careers.snowflake.com is a Phenom People careers hub, but it is only a *mirror*:
every posting's Apply button jumps to Ashby (jobs.ashbyhq.com/snowflake/<uuid>),
and Ashby is the source of truth. Phenom's own country facet is loose — it leaks
London "Observe by Snowflake" roles into a France search because they carry an
`FR-France-Remote` secondary location — so we skip Phenom entirely and read Ashby
directly, exactly like the Alan scraper:

  https://api.ashbyhq.com/posting-api/job-board/snowflake

One anonymous GET returns every listed posting (~400) with descriptions inline —
no pagination, no per-job detail calls. Scope is enforced client-side.

Filtering
---------
Ashby gives each posting a `department` (== `team` on this board). We split them:

  * WHOLESALE  — core tech families kept in full:
        Engineering, Data Analytics and AI, Enterprise Technology (internal IT),
        Security.
  * MIXED      — technical-*adjacent* families that are mostly GTM/delivery, kept
        only when the TITLE carries a data/AI/software keyword (is_tech_role):
        Solution Engineering (pre-sales), Professional Services (delivery),
        Product Management, Global Support.
  * everything else is dropped as pure GTM / G&A (Sales, Sales Development,
        Marketing, Alliances and Channels, Revenue Operations, Finance, People,
        Legal, Office of the CEO).

`is_tech_role` (scrapers/_relevance.py) matches on the title only. Snowflake's
France Solution Engineers are titled plainly ("Senior Solution Engineer"), which
carries no tech keyword, so they are dropped — consistent with the chosen scope
(Sales Engineering refined by title, not kept wholesale). To keep Solution
Engineering in full, move it from MIXED_DEPARTMENTS to WHOLESALE_DEPARTMENTS.

Employment type: Snowflake's board is FullTime + a few Interns. Per the "err
inclusive on data/AI roles" rule we keep ALL types — no employment-type gate.

Country gate: France if the primary location is `FR-*` / `addressCountry` is
France, OR any secondaryLocation is a France entry (`FR-...`) — the latter catches
a role based elsewhere but explicitly open to France (e.g. `FR-France-Remote`).

Expected yield: Snowflake's France office is GTM-heavy (Sales, Marketing, pre-sales
Solution Engineering); engineering sits in the US / other EMEA hubs. So France +
in-scope-tech is frequently **0 rows today** — that is the correct result, not a
failure (same as the Mirakl / N26 / Richemont scrapers). The scraper is ready to
capture the first France Software / Data / ML / AI role the moment it posts.

Native job id: Ashby posting `id` (a stable UUID). Apply URL: `jobUrl` (the Ashby
posting page, which renders the full description). Description: `descriptionPlain`,
already plain text. Posted date: `publishedAt` (YYYY-MM-DD).
"""
from __future__ import annotations

import sys
import time
from dataclasses import asdict, dataclass

import requests

from scrapers._relevance import is_tech_role

API_URL = "https://api.ashbyhq.com/posting-api/job-board/snowflake"

# Core tech families — kept in full.
WHOLESALE_DEPARTMENTS = {
    "Engineering",
    "Data Analytics and AI",
    "Enterprise Technology",   # Snowflake's internal IT / corporate engineering
    "Security",
}

# Technical-adjacent families — kept only when the title looks tech (is_tech_role).
MIXED_DEPARTMENTS = {
    "Solution Engineering",    # pre-sales; France titles are plain => usually dropped
    "Professional Services",   # delivery consultants / solution architects
    "Product Management",
    "Global Support",          # keeps "Cloud Support Engineer", drops generic support
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
}

REQUEST_TIMEOUT = 30


@dataclass
class Job:
    native_job_id: str          # Ashby posting UUID
    title: str
    location: str               # "Paris, France" (+ "(open to France)" if remote-eligible)
    category: str | None        # Ashby department (e.g. "Engineering")
    apply_url: str              # jobs.ashbyhq.com/snowflake/<id>
    employment_type: str        # "FullTime" / "Intern"
    description: str | None = None
    posted_date: str | None = None    # publishedAt, YYYY-MM-DD
    identifier: str | None = None     # Ashby public API exposes no separate req id
    raw_payload: dict | None = None


def _fr_primary(doc: dict) -> bool:
    """True if the posting's *primary* location is in France."""
    addr = ((doc.get("address") or {}).get("postalAddress") or {})
    if "France" in str(addr.get("addressCountry") or ""):
        return True
    loc = doc.get("location") or ""
    return loc == "FR" or loc.startswith("FR-")


def _fr_secondary(doc: dict) -> bool:
    """True if any *secondary* location is a France entry (role open to France)."""
    for sec in doc.get("secondaryLocations") or []:
        loc = str(sec.get("location") or "")
        if loc.startswith("FR-") or "-FR-" in loc:   # "-FR-" catches "Fins Only-FR-Paris"
            return True
    return False


def _in_france(doc: dict) -> bool:
    return _fr_primary(doc) or _fr_secondary(doc)


def _in_scope_department(doc: dict) -> bool:
    dept = (doc.get("department") or "").strip()
    if dept in WHOLESALE_DEPARTMENTS:
        return True
    if dept in MIXED_DEPARTMENTS:
        return is_tech_role(doc.get("title"))
    return False


def _in_scope(doc: dict) -> bool:
    if not doc.get("isListed", True):
        return False
    if not _in_scope_department(doc):
        return False
    return _in_france(doc)


def _pretty_location(doc: dict) -> str:
    """Human-readable location. Prefer the structured address; flag France-remote."""
    addr = ((doc.get("address") or {}).get("postalAddress") or {})
    locality, country = addr.get("addressLocality"), addr.get("addressCountry")
    parts = [p for p in (locality, country) if p]
    base = ", ".join(parts) if parts else (doc.get("location") or "").replace("-", ", ")
    base = base.strip() or "Unknown"
    # In scope via a France secondary location but based elsewhere → say so.
    if not _fr_primary(doc):
        return f"{base} (open to France)"
    return base


def _posted_date(doc: dict) -> str | None:
    raw = doc.get("publishedAt")
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    return raw[:10]


def _doc_to_job(doc: dict) -> Job:
    job_id = doc.get("id")
    if not job_id:
        raise RuntimeError(f"Snowflake posting missing id (title={doc.get('title')!r})")

    desc = doc.get("descriptionPlain")
    if isinstance(desc, str):
        desc = desc.strip() or None

    return Job(
        native_job_id=str(job_id),
        title=(doc.get("title") or "").strip(),
        location=_pretty_location(doc),
        category=(doc.get("department") or "").strip() or None,
        apply_url=doc.get("jobUrl") or f"https://jobs.ashbyhq.com/snowflake/{job_id}",
        employment_type=(doc.get("employmentType") or "").strip(),
        description=desc,
        posted_date=_posted_date(doc),
        identifier=None,
        raw_payload=doc,
    )


def _fetch_jobs(session: requests.Session) -> list[dict]:
    print(f"  GET {API_URL} ...", flush=True)
    response = session.get(API_URL, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise RuntimeError(f"Unexpected Ashby payload shape: keys={list(payload)}")
    print(f"    {len(jobs)} postings on the board", flush=True)
    return jobs


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Listing phase...", flush=True)
    docs = _fetch_jobs(session)

    print("Filter phase...", flush=True)
    kept: dict[str, Job] = {}
    for doc in docs:
        if not _in_scope(doc):
            continue
        job = _doc_to_job(doc)
        if job.native_job_id in kept:
            continue
        kept[job.native_job_id] = job
        print(
            f"  {job.native_job_id} [{job.category}] {job.title!r} "
            f"({job.location}) -> KEEP",
            flush=True,
        )

    elapsed = time.time() - started
    print(
        f"\n  -> {len(kept)} jobs kept "
        f"(dropped {len(docs) - len(kept)} out-of-scope) in {elapsed:.1f}s\n",
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
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
