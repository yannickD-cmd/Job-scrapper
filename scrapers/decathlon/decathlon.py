"""Decathlon Digital job scraper — France, CDI (Permanent FT), Engineering & Ops + Data.

The careers page at https://digital.decathlon.net/jobs is a Next.js shell
that embeds a jQuery widget. The widget pulls two Greenhouse public boards:
  FR: https://boards-api.greenhouse.io/v1/boards/decathlontechnology/jobs?content=true
  EN: https://boards-api.greenhouse.io/v1/boards/decathlontechnologyen/jobs?content=true

Greenhouse's public boards-api needs no auth and serves the full list in a
single response (Decathlon's total fits comfortably — ~55 FR + ~7 EN as of
2026-05), so no pagination logic.

The two boards mirror each other for many roles: same `requisition_id`
(e.g. "3427"), different Greenhouse job `id` (off by 1, EN+1). We dedupe on
requisition_id and prefer the FR row (matches our France scope). Truly EN-only
postings (no FR sibling) are kept too.

Why requisition_id and not Greenhouse `id` as native_job_id: id is per-board,
so the same role from the EN board would look like a new job on a rerun if
Decathlon ever flips a posting between boards. requisition_id is the stable,
human-meaningful key (it's also what appears on internal ATS dashboards).

Filters — see filters.md. To widen scope edit TEAMS_IN_SCOPE,
EMPLOYMENT_TYPES_IN_SCOPE, or COUNTRY_IN_SCOPE.
"""
from __future__ import annotations

import html
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

FR_BOARD_URL = (
    "https://boards-api.greenhouse.io/v1/boards/decathlontechnology"
    "/jobs?content=true"
)
EN_BOARD_URL = (
    "https://boards-api.greenhouse.io/v1/boards/decathlontechnologyen"
    "/jobs?content=true"
)

# Stable Greenhouse field-IDs on the decathlontechnology board, taken from
# scrapers/decathlon/material/sample_fr.json. Greenhouse exposes custom fields
# by numeric ID rather than name, so a label rename on Decathlon's side
# wouldn't break the scraper — but a field deletion would, and these IDs are
# the right thing to grep for if filtering ever drops to zero results.
META_EMPLOYMENT_TYPE_ID = 4246780101
META_COUNTRY_ID = 4462527101

COUNTRY_IN_SCOPE = "FRANCE"
EMPLOYMENT_TYPES_IN_SCOPE = {"Permanent (full time 100%)"}
# Public site groups departments under 4 team buckets; we keep two of them:
#   "Engineering & Ops"            -> Engineering + Operations
#   "Data science & engineering"   -> Data
TEAMS_IN_SCOPE = {"Engineering", "Operations", "Data"}

SCOPE_COUNTRY = "France"
SCOPE_EMPLOYMENT_TYPE = "CDI"

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
REQUEST_DELAY_SECONDS = 1.0


@dataclass
class Job:
    native_job_id: str         # Greenhouse requisition_id, e.g. "3427"
    title: str
    location: str
    category: str              # department[0].name (Engineering / Operations / Data)
    apply_url: str
    employment_type: str       # always SCOPE_EMPLOYMENT_TYPE for in-scope jobs
    description: str | None = None
    posted_date: str | None = None   # YYYY-MM-DD
    identifier: str | None = None    # Greenhouse internal job id (per-board)
    raw_payload: dict | None = None


def _meta_value(job: dict, field_id: int) -> str | None:
    for entry in job.get("metadata") or []:
        if entry.get("id") == field_id:
            value = entry.get("value")
            if isinstance(value, str):
                return value.strip()
    return None


def _in_scope(job: dict) -> bool:
    if _meta_value(job, META_COUNTRY_ID) != COUNTRY_IN_SCOPE:
        return False
    if _meta_value(job, META_EMPLOYMENT_TYPE_ID) not in EMPLOYMENT_TYPES_IN_SCOPE:
        return False
    departments = job.get("departments") or []
    if not departments:
        return False
    return departments[0].get("name") in TEAMS_IN_SCOPE


def _clean_description(content: str | None) -> str | None:
    """Greenhouse returns `content` as double-escaped HTML (e.g. `&lt;p&gt;`).
    Unescape once, then strip tags. Returns plain text or None."""
    if not content:
        return None
    unescaped = html.unescape(content)
    text = BeautifulSoup(unescaped, "html.parser").get_text(" ", strip=True)
    return text or None


def _posted_date(job: dict) -> str | None:
    raw = job.get("first_published") or job.get("updated_at")
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    return raw[:10]


def _doc_to_job(doc: dict) -> Job:
    req_id = str(doc.get("requisition_id") or "").strip()
    if not req_id:
        # Greenhouse always emits requisition_id for Decathlon's board, but if
        # one ever slipped through empty we'd collide on our (company, native_job_id)
        # unique key. Fail loudly so it's obvious which posting needs fixing.
        raise RuntimeError(
            f"Greenhouse job missing requisition_id (gh id={doc.get('id')!r})"
        )

    location_name = ((doc.get("location") or {}).get("name") or "").strip()
    apply_url = (doc.get("absolute_url") or "").strip()
    if not apply_url:
        raise RuntimeError(f"Greenhouse job missing absolute_url (req={req_id!r})")

    return Job(
        native_job_id=req_id,
        title=(doc.get("title") or "").strip(),
        location=location_name,
        category=doc["departments"][0]["name"],
        apply_url=apply_url,
        employment_type=SCOPE_EMPLOYMENT_TYPE,
        description=_clean_description(doc.get("content")),
        posted_date=_posted_date(doc),
        identifier=str(doc["id"]) if doc.get("id") is not None else None,
        raw_payload=doc,
    )


def _fetch_board(session: requests.Session, url: str, label: str) -> list[dict]:
    print(f"  fetching {label} board...", flush=True)
    response = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    jobs = payload.get("jobs") or []
    total = (payload.get("meta") or {}).get("total")
    print(f"    {len(jobs)} jobs (meta.total={total})", flush=True)
    return jobs


def _merge_locations(existing: str, new: str) -> str:
    """Same-req_id postings across offices: combine `location.name` strings,
    splitting on `; ` (Decathlon's own separator for multi-office rows) and
    preserving insertion order."""
    parts: list[str] = []
    seen: set[str] = set()
    for loc in (existing + "; " + new).split(";"):
        loc = loc.strip()
        if loc and loc not in seen:
            seen.add(loc)
            parts.append(loc)
    return "; ".join(parts)


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Fetch phase...", flush=True)
    fr_jobs = _fetch_board(session, FR_BOARD_URL, "FR")
    time.sleep(REQUEST_DELAY_SECONDS)
    en_jobs = _fetch_board(session, EN_BOARD_URL, "EN")

    print("Filter + dedup phase...", flush=True)
    by_req: dict[str, Job] = {}

    fr_kept = fr_merged = 0
    for doc in fr_jobs:
        if not _in_scope(doc):
            continue
        job = _doc_to_job(doc)
        existing = by_req.get(job.native_job_id)
        if existing is None:
            by_req[job.native_job_id] = job
        else:
            # Same FR requisition posted twice for different offices — merge
            # locations onto the earlier row so we don't lose either.
            existing.location = _merge_locations(existing.location, job.location)
            fr_merged += 1
        fr_kept += 1

    en_kept = en_dropped_dup = 0
    for doc in en_jobs:
        if not _in_scope(doc):
            continue
        job = _doc_to_job(doc)
        if job.native_job_id in by_req:
            en_dropped_dup += 1
            continue
        by_req[job.native_job_id] = job
        en_kept += 1

    print(
        f"  in scope: {fr_kept} FR ({fr_merged} merged) + {en_kept} EN-only "
        f"(skipped {en_dropped_dup} EN dups of FR)",
        flush=True,
    )

    elapsed = time.time() - started
    print(f"  -> {len(by_req)} jobs in {elapsed:.1f}s\n", flush=True)
    return [asdict(j) for j in by_req.values()]


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
        print(f"[{j['native_job_id']} / gh-{j['identifier']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
