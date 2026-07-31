"""BlaBlaCar job scraper — France, tech/data/AI roles, all employment types.

BlaBlaCar (the Paris-HQ French mobility unicorn) publishes its careers page from
the standard Lever public postings API:

  https://api.lever.co/v0/postings/blablacar?mode=json

A single GET returns every public posting (~17 today) as a flat JSON array, with
the full description already inline as `descriptionPlain` — so there is NO
per-job detail call to make, unlike Ashby/Greenhouse boards.

Shape notes (verified against the live payload, saved in material/postings.json):
- `country` is an ISO-2 code (FR / UA / BR ...). We gate on `country == "FR"`;
  "Paris or Remote from France" roles keep country=FR and so are included.
- `categories.department` is BlaBlaCar's own free-text bucket and is MESSY —
  values include "Engineering", but also "Western Europe", "Business developpment",
  "General Administration", "Product & Experience". So department alone is not a
  clean tech facet.
- `categories.team` is the finer, more reliable label ("Back-end", "Front-end",
  "Infrastructure", "Legal", "Marketing", "Product Management", ...).

Scope decision (locked with the user — France, Data/AI/Software/Cloud/Infra/Cyber):
- Country : France only (`country == "FR"`), remote-from-France included.
- Tech gate (category-first, per feedback_prefer_platform_category_over_is_tech_role):
    * department "Engineering" wholesale (its Back-end / Front-end / Infrastructure
      teams, and any future Data/ML/Platform teams that land under it), PLUS
    * a curated TECH_TEAMS set as a safety net in case a data/ML/security role is
      mis-filed under a non-Engineering department (BlaBlaCar's dept data is dirty),
      PLUS
    * an is_tech_role(title) RESCUE so a stray AI/data role whose department/team
      is unusable is not missed. Erring inclusive on AI/data is intentional.
  Everything else (Sales/GTM, Marketing, People/HR, Legal, Ops, Product Management,
  Customer Service, Finance) is dropped.
- Employment : NO commitment filter. Permanent (CDI) roles are kept, AND the user
  explicitly wants AI/data-adjacent internships / apprenticeships / fixed-term kept
  — so every commitment is kept within the tech gate.

Native job id: Lever's per-posting UUID (`id`), the same id its hostedUrl uses.
apply_url = hostedUrl (the public Lever posting page).

posted_date caveat (project_lever_createdat_evergreen): Lever's only date field is
`createdAt` (when the posting RECORD was created); there is no updatedAt. Evergreen
reqs stay open for years, so an old createdAt can still be actively hiring. Same id +
old date = a continuously-open posting, not stale. Dedup/closure is by native_job_id
(Orange-style) — do NOT treat posted_date as recency.

To widen scope, edit COUNTRIES_IN_SCOPE / TECH_DEPARTMENTS / TECH_TEAMS.
"""
from __future__ import annotations

import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import requests

from scrapers._relevance import is_tech_role

POSTINGS_URL = "https://api.lever.co/v0/postings/blablacar?mode=json"

COUNTRIES_IN_SCOPE = {"FR"}

# Wholesale-tech department: keep every posting under it regardless of team.
TECH_DEPARTMENTS = {"Engineering"}

# Safety net for tech roles mis-filed under a non-Engineering department.
# Deburred/case-insensitive compare (see _team_is_tech).
TECH_TEAMS = {
    "back-end", "backend", "front-end", "frontend", "full-stack", "fullstack",
    "infrastructure", "infra", "platform", "sre", "site reliability", "devops",
    "data", "data engineering", "data science", "data platform", "analytics",
    "machine learning", "ml", "ai", "artificial intelligence",
    "security", "cybersecurity", "mobile", "android", "ios", "qa",
    "product engineering",
}

HEADERS = {
    "User-Agent": (
        "Job-scrapper/1.0 (+https://github.com/yannickD-cmd; "
        "yannickarieldossa@gmail.com) python-requests"
    ),
    "Accept": "application/json",
    "Referer": "https://blablacar.com/careers",
}

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.0
MAX_POSTINGS = 5000  # defensive cap; the board is a single un-paginated response


@dataclass
class Job:
    native_job_id: str          # Lever posting UUID (same id hostedUrl uses)
    title: str
    location: str               # categories.location (primary city)
    category: str | None        # "{department} / {team}"
    apply_url: str              # Lever hostedUrl (public posting page)
    employment_type: str        # categories.commitment (Permanent / Apprenticeship / ...)
    description: str | None = None
    posted_date: str | None = None   # createdAt (epoch ms) -> YYYY-MM-DD
    identifier: str | None = None    # ISO country code (kept for parity/forensics)
    raw_payload: dict | None = None


def _team_is_tech(team: str | None) -> bool:
    if not team:
        return False
    return team.strip().lower() in TECH_TEAMS


def _in_scope(doc: dict) -> bool:
    if doc.get("country") not in COUNTRIES_IN_SCOPE:
        return False
    cats = doc.get("categories") or {}
    dept = (cats.get("department") or "").strip()
    team = cats.get("team")
    title = doc.get("text")
    # Category-first: Engineering department wholesale, or a known tech team.
    if dept in TECH_DEPARTMENTS:
        return True
    if _team_is_tech(team):
        return True
    # Rescue: a stray AI/data/software role whose dept+team are unusable.
    return is_tech_role(title)


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
    if len(payload) > MAX_POSTINGS:
        raise RuntimeError(
            f"Lever postings API returned {len(payload)} postings, "
            f"exceeds MAX_POSTINGS={MAX_POSTINGS} — aborting to avoid runaway parse"
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
            f"[{job.employment_type}] -> KEEP",
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
