"""Alan job scraper — France, Engineering + Data, Full Time.

Alan's careers site (alan.com/en/careers) is a marketing landing page; the
real board lives on Ashby. Ashby exposes a public, anonymous posting API that
returns every listed posting (with descriptions inline) in a single GET:

  https://api.ashbyhq.com/posting-api/job-board/alan

One request covers the whole board — no pagination, no per-job detail calls.

Scope is enforced client-side (the endpoint has no filter params). Each
posting has:
  - department      — top-level family. We keep {Engineering, Data}. Product /
                      Operations / Sales / Insurance / Care / etc. are dropped.
  - employmentType  — one of FullTime / Intern / Contract / PartTime. We keep
                      FullTime only (permanent roles), which also drops Alan's
                      one in-scope internship.
  - location        — a free-text city list ("Paris, France; Lyon, France; …")
                      or an "Anywhere in <countries>" string.

Country gate: `address.postalAddress.addressCountry` is a useless generic
("European Union"), so France is read from the free-text `location`. Most
listings spell out ", France" or "Anywhere in France, …"; a few list bare
city names ("Paris"), so we also match a small French-city token set. That
keeps roles open to France (incl. multi-country "Anywhere in France, Belgium,
Spain") and drops Spain-only / Belgium-only / Canada listings.

Native job id: Ashby posting `id` (a UUID, stable across the posting's life).

Apply URL: `jobUrl` — the Ashby posting page (renders the full description).
The sibling `applyUrl` jumps straight to the application form; we prefer the
posting page for a human reviewing the alert.

Description: `descriptionPlain` is already plain text — no HTML stripping.

To widen scope, edit DEPARTMENTS_IN_SCOPE / EMPLOYMENT_TYPES_IN_SCOPE, or add
cities to FRENCH_CITY_TOKENS.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass

import requests

API_URL = "https://api.ashbyhq.com/posting-api/job-board/alan"

DEPARTMENTS_IN_SCOPE = {"Engineering", "Data"}
EMPLOYMENT_TYPES_IN_SCOPE = {"FullTime"}

# Bare city listings ("Paris") carry no country suffix, so a plain "France"
# substring test would miss them. Match these French cities as a fallback.
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


@dataclass
class Job:
    native_job_id: str          # Ashby posting UUID
    title: str
    location: str               # free-text city list / "Anywhere in ..."
    category: str | None        # department (e.g. "Engineering", "Data")
    apply_url: str              # jobs.ashbyhq.com/alan/<id>
    employment_type: str        # "FullTime"
    description: str | None = None
    posted_date: str | None = None    # publishedAt, YYYY-MM-DD
    identifier: str | None = None     # Ashby has no separate req id here
    raw_payload: dict | None = None


def _in_france(location: str | None) -> bool:
    loc = (location or "").lower()
    if "france" in loc:                       # "Anywhere in France, ...", "Paris, France"
        return True
    tokens = [t.strip() for t in re.split(r"[;,]", loc)]
    return any(t in FRENCH_CITY_TOKENS for t in tokens)


def _in_scope(doc: dict) -> bool:
    if not doc.get("isListed", True):
        return False
    if (doc.get("department") or "").strip() not in DEPARTMENTS_IN_SCOPE:
        return False
    if (doc.get("employmentType") or "").strip() not in EMPLOYMENT_TYPES_IN_SCOPE:
        return False
    return _in_france(doc.get("location"))


def _posted_date(doc: dict) -> str | None:
    raw = doc.get("publishedAt")
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    return raw[:10]


def _doc_to_job(doc: dict) -> Job:
    job_id = doc.get("id")
    if not job_id:
        raise RuntimeError(f"Alan posting missing id (title={doc.get('title')!r})")

    desc = doc.get("descriptionPlain")
    if isinstance(desc, str):
        desc = desc.strip() or None

    return Job(
        native_job_id=str(job_id),
        title=(doc.get("title") or "").strip(),
        location=(doc.get("location") or "").strip(),
        category=(doc.get("department") or "").strip() or None,
        apply_url=doc.get("jobUrl") or f"https://jobs.ashbyhq.com/alan/{job_id}",
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
