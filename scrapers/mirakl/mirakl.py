"""Mirakl job scraper — France, Tech department, permanent roles only.

Mirakl's careers page (www.mirakl.com/careers) is a Next.js front whose
"SectionAllJobs" component fetches a public Greenhouse job board, so we hit
the standard Greenhouse Job Board API directly:

  https://boards-api.greenhouse.io/v1/boards/mirakl/jobs?content=true

`content=true` embeds each posting's description HTML plus `departments[]`
and `metadata[]` in the listing payload, so a single anonymous GET covers
everything — no per-job detail calls. Same pattern as Datadog / Doctolib.

Scope (locked 2026-07): France, Tech department, CDI-ish only. The boards
API has no filter params, so everything is client-side:

  - Location: `location.name` is free text and sometimes multi-location
    ("London, England, United Kingdom; Paris, France"). We keep any posting
    whose location mentions France. Every French entry on this board is
    suffixed ", France", so the word match is sufficient.
  - Department: `departments[].name == "Tech"`. Mirakl's board files data /
    engineering under Tech (Division metadata: BI, ...); Sales, Connect,
    Marketing and G&A are out of scope.
  - Employment type: Greenhouse exposes no employment-type field on this
    board, so non-permanent roles are excluded by title — Intern /
    Apprentice / Freelance and their French variants (stage, alternance...).

NOTE: this board is small (~22 postings) and at scope-lock time the Tech
department held only 2 Paris apprenticeships — both excluded by the title
gate. A run yielding 0 jobs is therefore a NORMAL outcome, not a failure:
the scraper exists to catch future Tech CDIs the day they post. The DB
layer treats an empty return as "close nothing, log success", so empty
runs are safe in CI.

Native job id: Greenhouse `id` (e.g. 5140266004), stable across the
posting's lifetime. posted_date: `first_published` (fallback `updated_at`),
YYYY-MM-DD. identifier: `requisition_id` (e.g. EMEA-GA-BIINTERN24).
apply_url: `absolute_url` (job-boards.greenhouse.io/mirakl/jobs/<id>).

To widen scope, edit DEPARTMENT_IN_SCOPE / FRANCE_RE / EXCLUDED_TITLE_RE.
"""
from __future__ import annotations

import html
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

API_URL = "https://boards-api.greenhouse.io/v1/boards/mirakl/jobs"

DEPARTMENT_IN_SCOPE = "Tech"
FRANCE_RE = re.compile(r"\bfrance\b", re.IGNORECASE)
# No employment-type field on this board: gate out non-permanent roles by
# title (English + French variants).
EXCLUDED_TITLE_RE = re.compile(
    r"\b(intern(ship)?|apprentice(ship)?|apprenti(e)?|alternan(ce|t|te)"
    r"|stagiaire|stage|freelance)\b",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": "https://www.mirakl.com",
    "Referer": "https://www.mirakl.com/",
    "From": "yannickarieldossa@gmail.com",
}

REQUEST_TIMEOUT = 30


@dataclass
class Job:
    native_job_id: str         # Greenhouse posting id (string), e.g. "5140266004"
    title: str
    location: str              # location.name from payload (free-text, may list several cities)
    category: str | None       # "Tech" + Division metadata, " | "-joined
    apply_url: str             # job-boards.greenhouse.io/mirakl/jobs/<id> (absolute_url)
    employment_type: str | None = None  # not exposed by this board
    description: str | None = None
    posted_date: str | None = None     # first_published, YYYY-MM-DD
    identifier: str | None = None      # requisition_id when Mirakl filled it in
    raw_payload: dict | None = None


def _metadata_value(doc: dict, name: str) -> str | None:
    for m in doc.get("metadata") or []:
        if m.get("name") == name:
            value = m.get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
            return None
    return None


def _dept_names(doc: dict) -> set[str]:
    return {(d.get("name") or "").strip() for d in (doc.get("departments") or [])}


def _in_scope(doc: dict) -> bool:
    location = ((doc.get("location") or {}).get("name") or "")
    if not FRANCE_RE.search(location):
        return False
    if DEPARTMENT_IN_SCOPE not in _dept_names(doc):
        return False
    return not EXCLUDED_TITLE_RE.search(doc.get("title") or "")


def _category(doc: dict) -> str | None:
    parts = [n for n in sorted(_dept_names(doc))]
    division = _metadata_value(doc, "Division")
    if division:
        parts.append(division)
    return " | ".join(parts) if parts else None


def _clean_description(content: str | None) -> str | None:
    """Greenhouse returns `content` as HTML with the tags entity-encoded
    (e.g. &lt;p&gt; and &#39;). One unescape pass yields normal HTML; strip
    tags with BeautifulSoup. Same pattern as Datadog / Doctolib."""
    if not content:
        return None
    unescaped = html.unescape(content)
    text = BeautifulSoup(unescaped, "html.parser").get_text(" ", strip=True)
    return text or None


def _posted_date(doc: dict) -> str | None:
    raw = doc.get("first_published") or doc.get("updated_at")
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    return raw[:10]


def _fetch_jobs(session: requests.Session) -> list[dict]:
    print(f"  GET {API_URL}?content=true ...", flush=True)
    response = session.get(
        API_URL,
        params={"content": "true"},
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    jobs = payload.get("jobs") or []
    meta = payload.get("meta") or {}
    print(f"    {len(jobs)} postings (meta.total={meta.get('total')})", flush=True)

    # The department gate depends on content=true embedding departments[] per
    # posting. If Greenhouse ever stops doing that, every posting would be
    # filtered out and the run would look "empty but successful" — fail loudly
    # instead so the DB safety guard isn't the only thing standing.
    if jobs and not any("departments" in j for j in jobs):
        raise RuntimeError("Greenhouse payload has no departments[] — gate cannot run")
    return jobs


def _doc_to_job(doc: dict) -> Job:
    job_id = doc.get("id")
    if not job_id:
        raise RuntimeError(f"Mirakl posting missing id (title={doc.get('title')!r})")
    job_id = str(job_id)

    apply_url = (doc.get("absolute_url") or "").strip()
    if not apply_url:
        apply_url = f"https://job-boards.greenhouse.io/mirakl/jobs/{job_id}"

    location_obj = doc.get("location") or {}
    req_id = doc.get("requisition_id")
    if isinstance(req_id, str):
        req_id = req_id.strip() or None

    return Job(
        native_job_id=job_id,
        title=(doc.get("title") or "").strip(),
        location=(location_obj.get("name") or "").strip(),
        category=_category(doc),
        apply_url=apply_url,
        description=_clean_description(doc.get("content")),
        posted_date=_posted_date(doc),
        identifier=req_id,
        raw_payload=doc,
    )


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Listing phase...", flush=True)
    docs = _fetch_jobs(session)

    print("Filter phase...", flush=True)
    candidates = [d for d in docs if _in_scope(d)]
    print(
        f"  kept {len(candidates)} (dropped {len(docs) - len(candidates)} out-of-scope)",
        flush=True,
    )

    kept: dict[str, Job] = {}
    for doc in candidates:
        job = _doc_to_job(doc)
        if job.native_job_id in kept:
            continue
        kept[job.native_job_id] = job
        print(f"  {job.native_job_id} {job.title!r} -> KEEP", flush=True)

    elapsed = time.time() - started
    print(f"\n  -> {len(kept)} jobs in {elapsed:.1f}s\n", flush=True)
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
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
