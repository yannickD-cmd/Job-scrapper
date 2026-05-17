"""Artefact job scraper — Paris office, data/AI/consulting/digital-marketing, Full-time only.

Artefact's careers page (artefact.com/careers/explore-our-jobs/) is powered
by Greenhouse, so we hit the standard public Job Board API:

    https://boards-api.greenhouse.io/v1/boards/artefact/jobs?content=true

`content=true` makes Greenhouse return each posting's description HTML
inline, so a single GET (127 postings as of writing) is the whole scrape —
no per-job detail calls.

Scope is enforced client-side because the API takes no filter params:

- OFFICES_IN_SCOPE  — Artefact is a 20+ city consultancy; we keep "Paris".
  Note the office strings can carry trailing whitespace ("Paris ") so we
  strip both sides before comparing.

- DEPARTMENTS_IN_SCOPE — Data Science / Engineering / Analytics / Consulting
  plus the digital-marketing sub-departments (Artefact is a data-driven
  marketing consultancy, so SEO/SEA/Affiliate/Display all sit on top of the
  data practice). Departments deliberately excluded: Corporate Functions,
  HR, Admin, Studio, Creation, Artefact Open Innovation, Open Application —
  all confirmed via probe to be either non-data or internship-only.

- Employment Type = "Full-time" (the Greenhouse metadata field).

Internship metadata defence: at least one Artefact posting in scope is
mistagged — e.g. "Stage Software Engineer - Paris" has dept=Data Engineering
AND Employment Type=Full-time despite "Stage" being French for internship.
So we additionally drop any title matching INTERNSHIP_TITLE_RE. This is a
metadata-correctness fix, not a scope-narrowing keyword filter.

Native job id: Greenhouse `id` (numeric, e.g. 4127302002). Stable.

Apply URL: the API exposes `absolute_url`, which already points at the
canonical board page `https://job-boards.greenhouse.io/artefact/jobs/<id>`.
Artefact has no public per-job slug on artefact.com, so unlike Doctolib
there's no richer URL to prefer.

Description: Greenhouse double-encodes the HTML (entity-wrapped tags); we
html.unescape once then strip with BeautifulSoup. Same pattern as Doctolib /
Capgemini.

To widen scope, edit OFFICES_IN_SCOPE or DEPARTMENTS_IN_SCOPE.
"""
from __future__ import annotations

import html
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

API_URL = "https://boards-api.greenhouse.io/v1/boards/artefact/jobs"

OFFICES_IN_SCOPE = {"Paris"}

DEPARTMENTS_IN_SCOPE = {
    "Data Science",
    "Data Engineering",
    "Data Analytics",
    "Data Consulting",
    "Consulting & Data",
    "Digital Marketing",
    "SEO",
    "SEA/PPC",
    "Affiliate Marketing",
    "Display & Social Advertising Expert",
}

EMPLOYMENT_TYPES_IN_SCOPE = {"Full-time"}

# Defensive title filter: Artefact mistags some internships as Full-time
# (e.g. "Stage Software Engineer - Paris"). These tokens are unambiguous
# internship/apprenticeship markers in French and English.
INTERNSHIP_TITLE_RE = re.compile(
    r"\b(stage|stagiaire|intern(?:ship)?|apprenti(?:e|s|ssage)?|alternan(?:t|te|ce))\b",
    re.IGNORECASE,
)

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
    native_job_id: str             # Greenhouse posting id, e.g. "4127302002"
    title: str
    location: str                  # location.name, free-text (e.g. "9th arrondissement of Paris, 75009, ...")
    category: str | None           # in-scope dept names joined by " | "
    apply_url: str                 # https://job-boards.greenhouse.io/artefact/jobs/<id>
    employment_type: str           # "Full-time" / "Part-time" / "Internship"
    description: str | None = None
    posted_date: str | None = None # first_published, YYYY-MM-DD
    identifier: str | None = None  # requisition_id if Artefact filled it in
    raw_payload: dict | None = None


def _office_names(doc: dict) -> set[str]:
    return {(o.get("name") or "").strip() for o in (doc.get("offices") or [])}


def _dept_names(doc: dict) -> list[str]:
    return [(d.get("name") or "").strip() for d in (doc.get("departments") or [])]


def _employment_type(doc: dict) -> str:
    for m in doc.get("metadata") or []:
        if m.get("name") == "Employment Type":
            return (m.get("value") or "").strip()
    return ""


def _in_scope(doc: dict) -> bool:
    if not (OFFICES_IN_SCOPE & _office_names(doc)):
        return False
    if not (DEPARTMENTS_IN_SCOPE & set(_dept_names(doc))):
        return False
    if _employment_type(doc) not in EMPLOYMENT_TYPES_IN_SCOPE:
        return False
    title = doc.get("title") or ""
    if INTERNSHIP_TITLE_RE.search(title):
        return False
    return True


def _category(doc: dict) -> str | None:
    in_scope = [n for n in _dept_names(doc) if n in DEPARTMENTS_IN_SCOPE]
    return " | ".join(in_scope) if in_scope else None


def _clean_description(content: str | None) -> str | None:
    """Greenhouse returns `content` as HTML with the tags entity-encoded.
    One unescape pass yields normal HTML; strip tags with BeautifulSoup."""
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
    return jobs


def _doc_to_job(doc: dict) -> Job:
    job_id = doc.get("id")
    if not job_id:
        raise RuntimeError(f"Artefact posting missing id (title={doc.get('title')!r})")
    job_id = str(job_id)

    title = (doc.get("title") or "").strip()
    apply_url = (doc.get("absolute_url") or "").strip()

    location_obj = doc.get("location") or {}
    location = (location_obj.get("name") or "").strip()

    req_id = doc.get("requisition_id")
    if isinstance(req_id, str):
        req_id = req_id.strip() or None

    return Job(
        native_job_id=job_id,
        title=title,
        location=location,
        category=_category(doc),
        apply_url=apply_url,
        employment_type=_employment_type(doc),
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
        print(f"  {job.native_job_id} {job.title!r} -> KEEP ({job.category})", flush=True)

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
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
