"""Contentsquare job scraper — France, tech families (Eng / Data / Security / IT).

Contentsquare publishes its open roles on the standard Lever public postings API:

  https://api.lever.co/v0/postings/contentsquare?mode=json

A single GET returns every public posting (~25 today) as a flat JSON array, with
the full job description already inline as `descriptionPlain` — so there is NO
per-job detail call to make, unlike Ashby/Greenhouse boards.

Shape per posting: {id, text (title), hostedUrl, applyUrl, country (ISO-2),
workplaceType, createdAt (epoch ms), descriptionPlain, categories:{department,
team, location (free-text), commitment, allLocations}}.

Scope decision (locked with the user):
- Country : France only. Contentsquare's Lever feed exposes a reliable ISO-2
  `country` field, so the primary gate is `country == "FR"`. We also accept a
  posting whose free-text `categories.location` names France or a French city
  (belt-and-braces for any remote-in-France row that leaves `country` blank).
- Families : Data, AI/ML, Software/IT, Cloud/Infra/SRE/DevOps, Cybersecurity and
  data/AI-adjacent engineering. Category-first: Contentsquare files roles under a
  coarse `categories.department`, so we keep the wholesale TECH departments
  {"Engineering, Product, and Design", "Security Trust", "ISD"} and rescue any
  OTHER department by `is_tech_role(title)` (drops Sales/GTM/Legal/Finance/HR).
  The Eng department is a combined "Engineering, Product, and Design" bucket kept
  wholesale on purpose (err inclusive on data/AI per the user's standing rule).
- Employment : CDI-inclusive. Lever's `commitment` is often null on this board
  and internships/apprenticeships in Data/AI are explicitly wanted, so we do NOT
  gate on employment type — the family gate alone decides scope.

Native job id: Lever's per-posting UUID (`id`), the same id its hostedUrl uses.

posted_date caveat: Lever's only date field is `createdAt` (when the posting
RECORD was created); the public API has no updatedAt. Contentsquare runs
evergreen reqs left open for a long time, so posted_date can read old yet still
be actively hiring. Same ID + old date = a continuously-open posting, not a
stale one — do NOT treat posted_date as recency. Dedup/closure is by
native_job_id (project_lever_createdat_evergreen).

France yield is genuinely small (a mostly-GTM global board); 0-2 tech rows is
expected, not a bug. To widen scope, edit COUNTRIES_IN_SCOPE / TECH_DEPARTMENTS.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import requests

from scrapers._relevance import is_tech_role

POSTINGS_URL = "https://api.lever.co/v0/postings/contentsquare?mode=json"

COUNTRIES_IN_SCOPE = {"FR"}

# Wholesale tech departments (category-first gate). Any other department is
# rescued only when is_tech_role(title) matches.
TECH_DEPARTMENTS = {
    "Engineering, Product, and Design",
    "Security Trust",
    "ISD",
}

# Fallback France detector for rows whose ISO-2 `country` is blank: match the
# free-text categories.location against "France" or a major French city.
FRANCE_LOCATION_RE = re.compile(
    r"\b(france|paris|lyon|bordeaux|toulouse|nantes|lille|grenoble|"
    r"marseille|nice|rennes|strasbourg|montpellier|sophia\s*antipolis)\b",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Job-scrapper/1.0 (+https://github.com/yannickD-cmd; "
        "yannickarieldossa@gmail.com) python-requests"
    ),
    "Accept": "application/json",
    "Referer": "https://contentsquare.com/careers/",
}

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.0
MAX_PAGES = 1  # Lever returns the whole board in one response; guard anyway.


@dataclass
class Job:
    native_job_id: str          # Lever posting UUID (same id hostedUrl uses)
    title: str
    location: str               # categories.location (free-text primary city)
    category: str | None        # "{department} / {team}"
    apply_url: str              # Lever hostedUrl (public posting page)
    employment_type: str        # categories.commitment (Full-time / Internship / ... or "")
    description: str | None = None
    posted_date: str | None = None   # createdAt (epoch ms) -> YYYY-MM-DD
    identifier: str | None = None    # ISO country code (kept for parity/forensics)
    raw_payload: dict | None = None


def _is_france(doc: dict) -> bool:
    if doc.get("country") in COUNTRIES_IN_SCOPE:
        return True
    # country blank -> fall back to the free-text location.
    if doc.get("country"):
        return False
    cats = doc.get("categories") or {}
    loc = cats.get("location") or ""
    return bool(FRANCE_LOCATION_RE.search(loc))


def _is_tech(doc: dict) -> bool:
    cats = doc.get("categories") or {}
    dept = cats.get("department")
    if dept in TECH_DEPARTMENTS:
        return True
    # Mixed / non-tech department: rescue only genuine tech titles.
    return is_tech_role(doc.get("text"), dept)


def _in_scope(doc: dict) -> bool:
    return _is_france(doc) and _is_tech(doc)


def _category(cats: dict) -> str | None:
    dept = (cats.get("department") or "").strip()
    team = (cats.get("team") or "").strip()
    if dept and team and dept != team:
        return f"{dept} / {team}"
    return dept or team or None


def _posted_date(doc: dict) -> str | None:
    ts = doc.get("createdAt")
    if not isinstance(ts, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OSError, OverflowError):
        return None


def _doc_to_job(doc: dict) -> Job:
    job_id = (doc.get("id") or "").strip()
    if not job_id:
        raise RuntimeError(f"Lever posting missing id (text={doc.get('text')!r})")

    cats = doc.get("categories") or {}
    desc = doc.get("descriptionPlain")
    return Job(
        native_job_id=job_id,
        title=(doc.get("text") or "").strip(),
        location=(cats.get("location") or "").strip(),
        category=_category(cats),
        apply_url=(doc.get("hostedUrl") or "").strip(),
        employment_type=(cats.get("commitment") or "").strip(),
        description=desc if isinstance(desc, str) and desc else None,
        posted_date=_posted_date(doc),
        identifier=(doc.get("country") or None),
        raw_payload=doc,
    )


def _fetch_postings(session: requests.Session) -> list[dict]:
    print(f"  GET {POSTINGS_URL}", flush=True)
    response = session.get(POSTINGS_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError(
            f"Lever postings API returned {type(payload).__name__}, expected list"
        )
    print(f"    {len(payload)} postings total", flush=True)
    return payload


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Listing phase...", flush=True)
    docs = _fetch_postings(session)
    # Polite pause even though it's a single call — parity with other scrapers.
    time.sleep(REQUEST_DELAY_SECONDS)

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
            f"  {job.native_job_id[:8]} {job.title!r} "
            f"[{job.employment_type or '-'}] {job.location!r} -> KEEP",
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
