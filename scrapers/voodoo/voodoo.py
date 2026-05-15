"""Voodoo job scraper — Paris, Permanent (CDI-equivalent) only.

The public careers UI at https://voodoo.io/careers#jobs is a Framer site
whose jobs table is hydrated client-side from Voodoo's Lever board:

  https://api.lever.co/v0/postings/voodoo?mode=json

`mode=json` returns the full list in a single response (~34 postings total
across all offices as of 2026-05), so no pagination logic is needed. The
endpoint is public — no auth, no CSRF.

Each posting carries `categories.location` (single city string like "Paris"),
`categories.commitment` ("Permanent" / "Internship" / "Remote"),
`categories.department` (top-level chip on the public page: "Gaming",
"BeReal", "Engineering & Data", etc.) and `categories.team` (the sub-team
shown in the careers table).

Native job id: Lever's `id` is a stable UUID per posting, so it's the right
key for the `(company, native_job_id)` unique constraint.

Filter scope: Paris + Permanent. The Paris office mixes onsite/hybrid/remote
postings — `workplaceType` is not part of the scope filter. To widen scope
edit LOCATIONS_IN_SCOPE or COMMITMENTS_IN_SCOPE.
"""
from __future__ import annotations

import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import requests

API_URL = "https://api.lever.co/v0/postings/voodoo"

LOCATIONS_IN_SCOPE = {"Paris"}
COMMITMENTS_IN_SCOPE = {"Permanent"}

SCOPE_EMPLOYMENT_TYPE = "Permanent"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": "https://voodoo.io",
    "Referer": "https://voodoo.io/careers",
    "From": "yannickarieldossa@gmail.com",
}

REQUEST_TIMEOUT = 30


@dataclass
class Job:
    native_job_id: str         # Lever posting UUID
    title: str
    location: str              # categories.location (city name)
    category: str | None       # "{department} / {team}" — department chip + sub-team
    apply_url: str             # Lever-hosted job page (not the /apply form)
    employment_type: str       # categories.commitment
    description: str | None = None
    posted_date: str | None = None    # YYYY-MM-DD (from createdAt ms epoch)
    identifier: str | None = None     # same UUID; kept for output-format parity
    raw_payload: dict | None = None


def _in_scope(doc: dict) -> bool:
    cats = doc.get("categories") or {}
    if cats.get("location") not in LOCATIONS_IN_SCOPE:
        return False
    if cats.get("commitment") not in COMMITMENTS_IN_SCOPE:
        return False
    return True


def _posted_date(doc: dict) -> str | None:
    """Lever `createdAt` is a millisecond epoch."""
    raw = doc.get("createdAt")
    if not isinstance(raw, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(raw / 1000, tz=timezone.utc).date().isoformat()
    except (OverflowError, ValueError, OSError):
        return None


def _category(doc: dict) -> str | None:
    cats = doc.get("categories") or {}
    dept = (cats.get("department") or "").strip()
    team = (cats.get("team") or "").strip()
    if dept and team:
        return f"{dept} / {team}"
    return dept or team or None


def _doc_to_job(doc: dict) -> Job:
    posting_id = (doc.get("id") or "").strip()
    if not posting_id:
        raise RuntimeError(f"Lever posting missing id (text={doc.get('text')!r})")

    apply_url = (doc.get("hostedUrl") or "").strip()
    if not apply_url:
        raise RuntimeError(
            f"Lever posting missing hostedUrl (id={posting_id!r})"
        )

    cats = doc.get("categories") or {}

    return Job(
        native_job_id=posting_id,
        title=(doc.get("text") or "").strip(),
        location=(cats.get("location") or "").strip(),
        category=_category(doc),
        apply_url=apply_url,
        employment_type=(cats.get("commitment") or "").strip() or SCOPE_EMPLOYMENT_TYPE,
        description=(doc.get("descriptionPlain") or None),
        posted_date=_posted_date(doc),
        identifier=posting_id,
        raw_payload=doc,
    )


def _fetch_postings(session: requests.Session) -> list[dict]:
    print("  fetching Lever board (mode=json)...", flush=True)
    response = session.get(
        API_URL, params={"mode": "json"}, headers=HEADERS, timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError(
            f"Lever returned unexpected payload type: {type(payload).__name__}"
        )
    print(f"    {len(payload)} postings total", flush=True)
    return payload


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Fetch phase...", flush=True)
    docs = _fetch_postings(session)

    print("Filter phase...", flush=True)
    by_id: dict[str, Job] = {}
    dropped = 0
    for doc in docs:
        if not _in_scope(doc):
            dropped += 1
            continue
        job = _doc_to_job(doc)
        by_id.setdefault(job.native_job_id, job)

    print(
        f"  in scope: {len(by_id)} (dropped {dropped} out-of-scope)",
        flush=True,
    )

    elapsed = time.time() - started
    print(f"  -> {len(by_id)} jobs in {elapsed:.1f}s\n", flush=True)
    return [asdict(j) for j in by_id.values()]


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
