"""Databricks job scraper — Engineering, France.

Databricks publishes its job board through Greenhouse's public boards API.
Board token: `databricks`. No auth required, no pagination — a single
GET returns every open position with full HTML content embedded.

We fetch once, then filter client-side on two axes:
1. Department contains "Engineering" (id 4001015002) or one of its children.
2. Location string contains "France" or "Paris".

Greenhouse stores `location.name` as free text (e.g. "Paris, France",
"France - Remote", "Remote - France"), so a substring check on the
country name plus the capital catches the variants Databricks actually
uses. We also fall back to checking `offices[].location` in case a
posting omits the country from the headline location.

API reference: https://developers.greenhouse.io/job-board.html
"""
from __future__ import annotations

import sys
import time
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import datetime

import requests

BOARD_TOKEN = "databricks"
JOBS_URL = f"https://boards-api.greenhouse.io/v1/boards/{BOARD_TOKEN}/jobs?content=true"

# Department IDs from /v1/boards/databricks/departments. "Engineering"
# is the parent; Greenhouse exposes sub-teams as children whose
# `parent_id` points back to 4001015002. We accept either.
ENGINEERING_DEPT_ID = 4001015002

SCOPE_COUNTRY = "France"
SCOPE_CATEGORY = "Engineering"

# Free-text location tokens we accept. Lowercased before comparison.
# "paris" catches postings tagged only with the city; "france" catches
# everything else including remote-France variants.
FRANCE_LOCATION_TOKENS = ("france", "paris")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "From": "yannickarieldossa@gmail.com",
}

REQUEST_TIMEOUT = 30


@dataclass
class Job:
    native_job_id: str
    title: str
    location: str
    category: str
    apply_url: str
    employment_type: str | None = None
    description: str | None = None
    posted_date: str | None = None
    identifier: str | None = None      # Databricks requisition_id
    raw_payload: dict | None = None


def _fetch_all_jobs(session: requests.Session) -> list[dict]:
    response = session.get(JOBS_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    return payload.get("jobs", []) or []


def _is_engineering(job: dict) -> bool:
    for dept in job.get("departments") or []:
        if dept.get("id") == ENGINEERING_DEPT_ID:
            return True
        if dept.get("parent_id") == ENGINEERING_DEPT_ID:
            return True
    return False


def _location_strings(job: dict) -> list[str]:
    """Every free-text location label attached to the job, lowercased."""
    out: list[str] = []
    loc = job.get("location") or {}
    if isinstance(loc, dict) and loc.get("name"):
        out.append(loc["name"].lower())
    for office in job.get("offices") or []:
        if office.get("location"):
            out.append(office["location"].lower())
        if office.get("name"):
            out.append(office["name"].lower())
    return out


def _is_france(job: dict) -> bool:
    for label in _location_strings(job):
        if any(token in label for token in FRANCE_LOCATION_TOKENS):
            return True
    return False


def _parse_posted_date(job: dict) -> str | None:
    """Greenhouse emits ISO-8601 with Z; we keep just the date."""
    raw = job.get("first_published") or job.get("updated_at")
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _decode_content(content: str | None) -> str | None:
    """Greenhouse returns the description with HTML entities URL-encoded
    (e.g. '%3Cp%3E' for '<p>'). Decode so consumers see real HTML."""
    if not content:
        return None
    return urllib.parse.unquote(content)


def _employment_type(job: dict) -> str | None:
    """Look for an Employment Type field in Greenhouse `metadata[]`.
    Databricks may or may not populate it; return None if absent."""
    for entry in job.get("metadata") or []:
        name = (entry.get("name") or "").strip().lower()
        if name in ("employment type", "job type", "contract type"):
            value = entry.get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list) and value:
                return ", ".join(str(v) for v in value if v)
    return None


def _doc_to_job(doc: dict) -> Job | None:
    native_id = doc.get("id")
    if native_id is None:
        return None

    apply_url = (doc.get("absolute_url") or "").strip()
    if not apply_url:
        return None

    location_label = ""
    loc = doc.get("location") or {}
    if isinstance(loc, dict):
        location_label = (loc.get("name") or "").strip()

    return Job(
        native_job_id=str(native_id),
        title=(doc.get("title") or "").strip(),
        location=location_label,
        category=SCOPE_CATEGORY,
        apply_url=apply_url,
        employment_type=_employment_type(doc),
        description=_decode_content(doc.get("content")),
        posted_date=_parse_posted_date(doc),
        identifier=(doc.get("requisition_id") or "").strip() or None,
        raw_payload=doc,
    )


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    print("Fetch phase...", flush=True)
    started = time.time()
    all_docs = _fetch_all_jobs(session)
    print(f"  total open positions: {len(all_docs)}", flush=True)

    eng_docs = [d for d in all_docs if _is_engineering(d)]
    print(f"  after Engineering filter: {len(eng_docs)}", flush=True)

    fr_docs = [d for d in eng_docs if _is_france(d)]
    print(f"  after France filter     : {len(fr_docs)}", flush=True)

    jobs: list[Job] = []
    for doc in fr_docs:
        job = _doc_to_job(doc)
        if job is not None:
            jobs.append(job)

    elapsed = time.time() - started
    print(f"  -> {len(jobs)} jobs in {elapsed:.1f}s\n", flush=True)

    return [asdict(j) for j in jobs]


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
    print(f"\n=== {len(jobs)} jobs final (total runtime {elapsed:.1f}s) ===\n")

    for j in jobs:
        from bs4 import BeautifulSoup
        desc = BeautifulSoup(j["description"] or "", "html.parser").get_text(" ", strip=True)
        desc = desc[:200] + ("..." if len(desc) > 200 else "")

        print(f"[{j['identifier'] or j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
