"""Qonto job scraper — France, Tech & Data, full-time (CDI-equivalent).

Qonto's careers page (https://qonto.com/en/careers) is a marketing shell whose
job list is hydrated from the standard Lever public postings API:

  https://api.lever.co/v0/postings/qonto?mode=json

A single GET returns every public posting (~43 today) as a flat JSON array, with
the full job description already inline as `descriptionPlain` — so there is NO
per-job detail call to make, unlike Ashby/Greenhouse boards.

Scope decision (locked with the user):
- Country : France only. The Lever `country` field is an ISO-2 code; we gate on
  `country == "FR"`. Multi-office "remote friendly" Paris roles keep country=FR
  (Paris is their home office) and so are kept; their other eligible cities are
  preserved in `categories.allLocations` inside raw_payload.
- Department : "Tech & Data" (engineering, data, ML, SRE, IT) plus "Qonto Lab",
  the small separate department that carries Qonto's "Qonto Lab - AI" roles.
- Commitment : "Full-time" only (Lever's CDI equivalent) — excludes Internship /
  Apprenticeship / Fixed-term / Freelance.
- No title-keyword filter: every title within Tech & Data is kept.

Native job id: Lever's per-posting UUID (`id`), the same id its hostedUrl uses.

posted_date caveat: Lever's only date field is `createdAt` (when the posting
RECORD was created); the public API has no updatedAt. Qonto runs evergreen reqs
left open for years, so posted_date can read 2021/2024 yet still be actively
hiring. Same ID + old date = a continuously-open posting, not a stale one — do
NOT treat posted_date as recency. Dedup/closure is by native_job_id (Orange-style).

To widen scope, edit COUNTRIES_IN_SCOPE / DEPARTMENTS_IN_SCOPE / COMMITMENTS_IN_SCOPE.
"""
from __future__ import annotations

import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import requests

POSTINGS_URL = "https://api.lever.co/v0/postings/qonto?mode=json"

COUNTRIES_IN_SCOPE = {"FR"}
# "Qonto Lab" is a small separate department carrying Qonto's AI-lab roles
# ("Qonto Lab - AI"); kept alongside "Tech & Data" so the AI-lab postings are
# not missed.
DEPARTMENTS_IN_SCOPE = {"Tech & Data", "Qonto Lab"}
COMMITMENTS_IN_SCOPE = {"Full-time"}

HEADERS = {
    "User-Agent": (
        "Job-scrapper/1.0 (+https://github.com/yannickD-cmd; "
        "yannickarieldossa@gmail.com) python-requests"
    ),
    "Accept": "application/json",
    "Referer": "https://qonto.com/en/careers",
}

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.0


@dataclass
class Job:
    native_job_id: str          # Lever posting UUID (same id hostedUrl uses)
    title: str
    location: str               # categories.location (primary city)
    category: str | None        # "{department} / {team}"
    apply_url: str              # Lever hostedUrl (public posting page)
    employment_type: str        # categories.commitment (Full-time / Internship / ...)
    description: str | None = None
    posted_date: str | None = None   # createdAt (epoch ms) -> YYYY-MM-DD
    identifier: str | None = None    # ISO country code (kept for parity/forensics)
    raw_payload: dict | None = None


def _in_scope(doc: dict) -> bool:
    cats = doc.get("categories") or {}
    if doc.get("country") not in COUNTRIES_IN_SCOPE:
        return False
    if cats.get("department") not in DEPARTMENTS_IN_SCOPE:
        return False
    return cats.get("commitment") in COMMITMENTS_IN_SCOPE


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
    # Polite pause even though it's a single call — keeps parity with other scrapers.
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
