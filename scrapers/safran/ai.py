"""Safran.AI (ex-Preligens) job scraper — France, Data/AI + Software/IT.

Safran.AI is a subsidiary of Safran Electronics & Defense (the former Preligens,
acquired by Safran in 2024) building AI applied to defense/geospatial data. It
recruits on a PUBLIC Lever board — entirely separate from the Safran group
Drupal board in scrapers/safran/group.py (which is WAF-blocked from CI). This
one hits Lever's open JSON API with plain `requests`, so it is CI-safe:

  https://api.lever.co/v0/postings/safran-ai?mode=json

One GET returns every posting (no pagination) with the full description inline
(`descriptionPlain`) — no per-job detail fetch needed, unlike voodoo. Each record
carries a clean `country` code and a `categories` object:
  { commitment, department, team, location, allLocations }

Scope (France, Data/AI + Software/IT):
  country == "FR"  AND
  ( department == "Product & Engineering"      # AI Platform / SW Production /
                                               # Cyber & Infra — all tech, kept
                                               # wholesale
    OR is_tech_role(title) )                   # rescue tech titles filed under
                                               # other departments (the two
                                               # "Ingénieur Système Linux" roles
                                               # under Solutions & Services Lines,
                                               # the "Deep Learning Scientist"
                                               # intern with a null department)

This gate drops the Markets & Sales GTM roles, the PMO "Head of Project Manager"
/ "Solution Engineer" / "Technical Project Manager" strays, and the Lever
"Candidature spontanée" placeholder, while keeping every genuine eng/data role.
Non-France postings (Montreal / Singapore / Delhi / Abu Dhabi) are dropped by the
country gate.

Lever caveat (see project_lever_createdat_evergreen memory): the public API exposes
only `createdAt` (creation, not last-refresh). Evergreen reqs stay open for months
so `posted_date` can read old while the role is live — dedup is by native_job_id
(the Lever posting UUID), never by date. Employment type is Lever's `commitment`
(Full-time / Internship); there is no CDI/CDD distinction, so intern AI roles are
kept per the board's inclusive Data/AI policy.

To widen scope, edit COUNTRY_IN_SCOPE or DEPARTMENTS_WHOLESALE.
"""
from __future__ import annotations

import datetime as _dt
import sys
import time
from dataclasses import asdict, dataclass

import requests

from scrapers._relevance import is_tech_role

LEVER_SITE = "safran-ai"
POSTINGS_URL = f"https://api.lever.co/v0/postings/{LEVER_SITE}?mode=json"

COUNTRY_IN_SCOPE = "FR"
# Departments kept wholesale (every team under them is in-scope tech/data).
DEPARTMENTS_WHOLESALE = {"Product & Engineering"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
}

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.0


@dataclass
class Job:
    native_job_id: str          # Lever posting UUID (the id in the hostedUrl)
    title: str
    apply_url: str              # canonical hostedUrl (posting page)
    location: str | None = None
    category: str | None = None       # "{department} / {team}"
    description: str | None = None
    posted_date: str | None = None    # from createdAt (creation, not recency)
    employment_type: str | None = None  # Lever commitment: Full-time / Internship
    identifier: str | None = None
    raw_payload: dict | None = None


def _category(cats: dict) -> str | None:
    dept = (cats.get("department") or "").strip()
    team = (cats.get("team") or "").strip()
    if dept and team and dept != team:
        return f"{dept} / {team}"
    return dept or team or None


def _location(cats: dict) -> str | None:
    loc = (cats.get("location") or "").strip()
    if loc:
        return loc
    all_locs = cats.get("allLocations") or []
    return ", ".join(a for a in all_locs if a) or None


def _posted_date(created_at) -> str | None:
    """Lever createdAt is epoch milliseconds. -> YYYY-MM-DD (UTC), or None."""
    if not isinstance(created_at, (int, float)):
        return None
    try:
        return (
            _dt.datetime.fromtimestamp(created_at / 1000, _dt.timezone.utc)
            .date()
            .isoformat()
        )
    except (ValueError, OverflowError, OSError):
        return None


def _in_scope(doc: dict) -> bool:
    if doc.get("country") != COUNTRY_IN_SCOPE:
        return False
    cats = doc.get("categories") or {}
    if cats.get("department") in DEPARTMENTS_WHOLESALE:
        return True
    return is_tech_role(doc.get("text"))


def _doc_to_job(doc: dict) -> Job:
    job_id = (doc.get("id") or "").strip()
    if not job_id:
        raise RuntimeError(f"Lever posting missing id (title={doc.get('text')!r})")

    cats = doc.get("categories") or {}
    apply_url = (doc.get("hostedUrl") or doc.get("applyUrl") or "").strip()
    desc = doc.get("descriptionPlain")
    if not (isinstance(desc, str) and desc.strip()):
        desc = None

    # Keep raw_payload lean: the structured fields (JSONB-queryable), not the
    # multi-KB HTML/plain description blobs already captured in `description`.
    raw = {
        "id": job_id,
        "text": doc.get("text"),
        "categories": cats,
        "country": doc.get("country"),
        "workplaceType": doc.get("workplaceType"),
        "createdAt": doc.get("createdAt"),
        "hostedUrl": doc.get("hostedUrl"),
    }

    return Job(
        native_job_id=job_id,
        title=(doc.get("text") or "").strip(),
        apply_url=apply_url,
        location=_location(cats),
        category=_category(cats),
        description=desc,
        posted_date=_posted_date(doc.get("createdAt")),
        employment_type=(cats.get("commitment") or "").strip() or None,
        identifier=None,
        raw_payload=raw,
    )


def _fetch_postings(session: requests.Session) -> list[dict]:
    print(f"  GET {POSTINGS_URL}", flush=True)
    resp = session.get(POSTINGS_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise RuntimeError(
            f"Lever API returned {type(data).__name__}, expected a JSON list"
        )
    print(f"    {len(data)} postings total", flush=True)
    return data


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Listing phase (single Lever GET)...", flush=True)
    time.sleep(REQUEST_DELAY_SECONDS)
    docs = _fetch_postings(session)

    print("Filter phase...", flush=True)
    kept: dict[str, Job] = {}
    for doc in docs:
        if not _in_scope(doc):
            continue
        job = _doc_to_job(doc)
        if job.native_job_id in kept:
            continue
        kept[job.native_job_id] = job
        print(f"  KEEP {job.native_job_id[:8]} {job.title!r}", flush=True)

    elapsed = time.time() - started
    print(
        f"\n  -> {len(kept)} France jobs kept "
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
