"""Back Market job scraper — France, Data / AI / Software / Tech, all employment types.

Back Market (French refurbished-electronics unicorn; HQ Paris, second office
Bordeaux) runs its careers board on Ashby. Ashby exposes a public, anonymous
posting API that returns every listed posting (descriptions inline) in one GET:

  https://api.ashbyhq.com/posting-api/job-board/backmarket?includeCompensation=true

One request covers the whole board — no pagination, no per-job detail calls.
(Canonical Ashby template: scrapers/alan/alan.py — this mirrors it.)

Scope is enforced client-side (the endpoint has no filter params). Each posting
carries:
  - department        — a COARSE org bucket. Back Market's engineering org is
                        "BUREAU OF TECHNOLOGY"; but data roles are scattered:
                        Data Scientists sit under BUREAU OF TECHNOLOGY while
                        Data Analysts sit under PRODUCT (team "DATA"). PRODUCT
                        is a MIXED bucket (also holds product-marketing / UX
                        research), so it is not wholesale-keepable.
  - team              — finer label ("DATA", "DATA ENG & SCIENCE", "FRONT-END"),
                        used only as the human-facing category.
  - employmentType    — all FullTime on the board today. We do NOT filter on it:
                        the user wants AI/data interns & apprentices kept too, so
                        the tech gate (below) is the only scope filter and any
                        employment type that passes it is kept.
  - location / secondaryLocations — free-text city names ("Paris", "Bordeaux",
                        "London", "Barcelona", "Tokyo"); no country suffix.

Category-first gate (feedback_prefer_platform_category_over_is_tech_role):
  - BUREAU OF TECHNOLOGY  -> kept WHOLESALE (Back Market's engineering org).
  - every other department -> kept only if the TITLE passes the shared
    is_tech_role() predicate (rescues the Data Analysts filed under PRODUCT/DATA
    while dropping the UX researcher / product-marketing roles in the same team,
    and every Ops / People / Marketing role). This is the mixed-bucket rescue,
    not a blanket title filter.

Country gate: `address.postalAddress.addressCountry` is a useless generic, so
France is read from the free-text `location` + `secondaryLocations`. Locations
are bare city names, so a plain "France" substring test would miss them — we
match Alan's FRENCH_CITY_TOKENS set (Paris / Bordeaux / Lyon / …). A role counts
as French if ANY of its primary/secondary locations is in France.

Native job id: Ashby posting `id` (UUID, stable across the posting's life).
Apply URL: `jobUrl` — the Ashby posting page (renders the full description).
Description: `descriptionPlain` is already plain text — no HTML stripping.

To widen scope, add departments to TECH_DEPARTMENTS or cities to
FRENCH_CITY_TOKENS.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass

import requests

from scrapers._relevance import is_tech_role

API_URL = "https://api.ashbyhq.com/posting-api/job-board/backmarket?includeCompensation=true"

# Whole engineering org — kept regardless of title. Every other department is a
# mixed bucket refined by is_tech_role(title).
TECH_DEPARTMENTS = {"BUREAU OF TECHNOLOGY"}

# Bare city listings ("Paris", "Bordeaux") carry no country suffix, so a plain
# "France" substring test would miss them. Match these French cities as well.
FRENCH_CITY_TOKENS = {
    "paris", "lyon", "bordeaux", "marseille", "biarritz", "nantes", "annecy",
    "lille", "toulouse", "nice", "strasbourg", "montpellier", "rennes",
    "grenoble", "nancy", "sophia antipolis",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
}

REQUEST_TIMEOUT = 30
MAX_POSTINGS = 5000  # defensive cap — the board should be well under this


@dataclass
class Job:
    native_job_id: str          # Ashby posting UUID
    title: str
    location: str               # free-text city (+ secondary cities appended)
    category: str | None        # team (falls back to department)
    apply_url: str              # jobs.ashbyhq.com/backmarket/<id>
    employment_type: str        # e.g. "FullTime"
    description: str | None = None
    posted_date: str | None = None    # publishedAt, YYYY-MM-DD
    identifier: str | None = None     # Ashby has no separate req id here
    raw_payload: dict | None = None


def _all_locations(doc: dict) -> list[str]:
    """Primary location + every secondary location, as free-text city strings."""
    locs: list[str] = []
    primary = doc.get("location")
    if isinstance(primary, str) and primary.strip():
        locs.append(primary.strip())
    for sec in doc.get("secondaryLocations") or []:
        loc = sec.get("location") if isinstance(sec, dict) else None
        if isinstance(loc, str) and loc.strip():
            locs.append(loc.strip())
    return locs


def _loc_in_france(location: str) -> bool:
    loc = location.lower()
    if "france" in loc:                        # "Anywhere in France, ...", "Paris, France"
        return True
    tokens = [t.strip() for t in re.split(r"[;,]", loc)]
    return any(t in FRENCH_CITY_TOKENS for t in tokens)


def _in_france(doc: dict) -> bool:
    return any(_loc_in_france(loc) for loc in _all_locations(doc))


def _is_tech(doc: dict) -> bool:
    """Category-first: tech department wholesale, else is_tech_role(title) rescue."""
    if (doc.get("department") or "").strip() in TECH_DEPARTMENTS:
        return True
    return is_tech_role(doc.get("title"), doc.get("team"))


def _in_scope(doc: dict) -> bool:
    if not doc.get("isListed", True):
        return False
    if not _is_tech(doc):
        return False
    return _in_france(doc)


def _posted_date(doc: dict) -> str | None:
    raw = doc.get("publishedAt")
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    return raw[:10]


def _doc_to_job(doc: dict) -> Job:
    job_id = doc.get("id")
    if not job_id:
        raise RuntimeError(f"Back Market posting missing id (title={doc.get('title')!r})")

    desc = doc.get("descriptionPlain")
    if isinstance(desc, str):
        desc = desc.strip() or None

    return Job(
        native_job_id=str(job_id),
        title=(doc.get("title") or "").strip(),
        location="; ".join(_all_locations(doc)),
        category=(doc.get("team") or doc.get("department") or "").strip() or None,
        apply_url=doc.get("jobUrl") or f"https://jobs.ashbyhq.com/backmarket/{job_id}",
        employment_type=(doc.get("employmentType") or "").strip(),
        description=desc,
        posted_date=_posted_date(doc),
        identifier=None,
        raw_payload=doc,
    )


def _fetch_jobs(session: requests.Session) -> list[dict]:
    print(f"  GET {API_URL} ...", flush=True)
    response = session.get(API_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise RuntimeError(f"Unexpected Ashby payload shape: keys={list(payload)}")
    if len(jobs) > MAX_POSTINGS:
        raise RuntimeError(f"Board returned {len(jobs)} postings (> cap {MAX_POSTINGS})")
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
        print(f"  {job.native_job_id} [{job.category}] {job.title!r} -> KEEP", flush=True)

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
