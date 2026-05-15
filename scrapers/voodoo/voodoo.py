"""Voodoo job scraper — Paris office, every employment type the careers page shows.

The public careers UI at https://voodoo.io/careers#jobs is a Framer site whose
jobs table is hydrated client-side by the bundled `Jobs.mjs` component, which
calls Voodoo's own Ashby-backed board API:

  https://jobs.voodoo.io/board/989a55fe-f19c-4379-b680-2029aab87cbe

This is NOT the public Lever board at api.lever.co/v0/postings/voodoo — that
endpoint also resolves but contains stale/internal postings the careers page
deliberately hides. Always use the jobs.voodoo.io board for parity with the
public site.

The page applies only Team / Location / Workplace / text filters client-side;
it does NOT filter on employmentType. So matching the page for "Location:
Paris" means returning every Paris posting regardless of FullTime / Intern /
Temporary / Contract status. Sanity-check against the page: Engineering &
Data + Paris in the API == 9 postings, which matches the chip count visible
on the page.

The board endpoint returns ~114 postings in one response (no pagination —
the page's own fetch is a single GET, no query params). Description text is
not included in the listing payload, so we make one detail call per kept job:
  https://jobs.voodoo.io/job/<id>   -> { results: { descriptionPlain, ... } }

Native job id: the board uses a stable UUID per posting (the same `id`
Voodoo's own URLs reference at `/careers/job?id=<id>` and that Ashby exposes
at `jobs.ashbyhq.com/voodoo/<id>`). Used as `native_job_id`.

To widen scope, edit LOCATIONS_IN_SCOPE or DEPARTMENTS_IN_SCOPE.
"""
from __future__ import annotations

import sys
import time
from dataclasses import asdict, dataclass

import requests

BOARD_ID = "989a55fe-f19c-4379-b680-2029aab87cbe"
BOARD_URL = f"https://jobs.voodoo.io/board/{BOARD_ID}"
DETAIL_URL_TEMPLATE = "https://jobs.voodoo.io/job/{job_id}"

# Canonical job link shown on voodoo.io (the page that opens when a user
# clicks a row in the careers table). Falls back to ashbyhq.com if absent.
PUBLIC_JOB_URL_TEMPLATE = "https://voodoo.io/careers/job?id={job_id}"

LOCATIONS_IN_SCOPE = {"Paris"}
DEPARTMENTS_IN_SCOPE = {"Engineering & Data"}

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
REQUEST_DELAY_SECONDS = 1.0


@dataclass
class Job:
    native_job_id: str         # Ashby UUID (same id Voodoo's URLs use)
    title: str
    location: str              # locationName (city)
    category: str | None       # "{departmentName} / {teamName}"
    apply_url: str             # https://voodoo.io/careers/job?id=...
    employment_type: str       # Ashby raw value: FullTime / Intern / Contract / Temporary
    description: str | None = None
    posted_date: str | None = None    # publishedDate, already YYYY-MM-DD
    identifier: str | None = None     # Ashby internal jobId (rarely needed; kept for parity)
    raw_payload: dict | None = None


def _in_scope(doc: dict) -> bool:
    if doc.get("isListed") is False:
        return False
    if doc.get("locationName") not in LOCATIONS_IN_SCOPE:
        return False
    return doc.get("departmentName") in DEPARTMENTS_IN_SCOPE


def _category(doc: dict) -> str | None:
    dept = (doc.get("departmentName") or "").strip()
    team = (doc.get("teamName") or "").strip()
    if dept and team and dept != team:
        return f"{dept} / {team}"
    return dept or team or None


def _doc_to_job(doc: dict, description: str | None) -> Job:
    job_id = (doc.get("id") or "").strip()
    if not job_id:
        raise RuntimeError(
            f"jobs.voodoo.io posting missing id (title={doc.get('title')!r})"
        )

    apply_url = PUBLIC_JOB_URL_TEMPLATE.format(job_id=job_id)
    posted = doc.get("publishedDate")
    if isinstance(posted, str) and len(posted) >= 10:
        posted = posted[:10]
    else:
        posted = None

    return Job(
        native_job_id=job_id,
        title=(doc.get("title") or "").strip(),
        location=(doc.get("locationName") or "").strip(),
        category=_category(doc),
        apply_url=apply_url,
        employment_type=(doc.get("employmentType") or "").strip(),
        description=description,
        posted_date=posted,
        identifier=(doc.get("jobId") or None),
        raw_payload=doc,
    )


def _fetch_board(session: requests.Session) -> list[dict]:
    print(f"  fetching board {BOARD_ID}...", flush=True)
    response = session.get(BOARD_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        raise RuntimeError(f"jobs.voodoo.io board returned success=false: {payload}")
    results = payload.get("results") or []
    print(f"    {len(results)} postings total", flush=True)
    return results


def _fetch_description(session: requests.Session, job_id: str) -> str | None:
    response = session.get(
        DETAIL_URL_TEMPLATE.format(job_id=job_id),
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        return None
    result = payload.get("results") or {}
    desc = result.get("descriptionPlain")
    return desc if isinstance(desc, str) and desc else None


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Listing phase...", flush=True)
    docs = _fetch_board(session)

    print("Filter phase...", flush=True)
    candidates = [d for d in docs if _in_scope(d)]
    print(
        f"  kept {len(candidates)} (dropped {len(docs) - len(candidates)} out-of-scope)",
        flush=True,
    )

    print(
        f"Enrichment phase: fetching {len(candidates)} detail pages "
        f"(~{int(len(candidates) * REQUEST_DELAY_SECONDS / 60)} min)...",
        flush=True,
    )

    kept: dict[str, Job] = {}
    failed = 0
    for i, doc in enumerate(candidates, 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        job_id = doc.get("id") or ""
        try:
            desc = _fetch_description(session, job_id)
        except Exception as exc:
            # Description is non-essential — log and keep the row with desc=None.
            print(
                f"  [{i}/{len(candidates)}] {job_id[:8]} description fetch FAILED: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            desc = None
            failed += 1

        job = _doc_to_job(doc, desc)
        if job.native_job_id in kept:
            continue
        kept[job.native_job_id] = job
        print(
            f"  [{i}/{len(candidates)}] {job.native_job_id[:8]} {job.title!r} -> KEEP",
            flush=True,
        )

    elapsed = time.time() - started
    print(flush=True)
    print(
        f"  -> {len(kept)} jobs in {elapsed:.1f}s "
        f"({failed} description fetches failed)\n",
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
