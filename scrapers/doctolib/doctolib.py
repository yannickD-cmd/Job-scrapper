"""Doctolib job scraper — Paris office, tech-track departments.

The careers UI at https://careers.doctolib.com/ links every posting to
boards.greenhouse.io/doctolib, i.e. Doctolib's ATS is a public Greenhouse
job board. We hit the standard Greenhouse Job Board API:

  https://boards-api.greenhouse.io/v1/boards/doctolib/jobs?content=true

`content=true` makes Greenhouse return each posting's description HTML in
the listing payload, so a single GET covers everything — no per-job detail
calls needed. The endpoint is open, anonymous, and stable.

Scope is enforced client-side because the Greenhouse API has no filter
params on this endpoint. Each posting has:
  - offices[]   — Doctolib's office locations (we keep `Paris`)
  - departments[] — top-level dept (we keep Eng / Product / Design / IT & Security)

A posting may be cross-listed across offices (same role, distinct Greenhouse
ids per office), so we don't try to dedup by title — each posting id is a
distinct row.

Native job id: Greenhouse `id` (e.g. 7583949003), the same id that appears in
the public URL `boards.greenhouse.io/doctolib/jobs/<id>`. Stable across the
posting's lifetime. The Greenhouse `internal_job_id` is a separate internal
key we don't need.

Description: Greenhouse double-encodes the HTML (entities wrap tags), so we
html.unescape once, then strip tags with BeautifulSoup. Same pattern as
Capgemini.

To widen scope, edit OFFICES_IN_SCOPE or DEPARTMENTS_IN_SCOPE.
"""
from __future__ import annotations

import html
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

API_URL = "https://boards-api.greenhouse.io/v1/boards/doctolib/jobs"
PUBLIC_JOB_URL_TEMPLATE = "https://boards.greenhouse.io/doctolib/jobs/{job_id}"

OFFICES_IN_SCOPE = {"Paris"}
DEPARTMENTS_IN_SCOPE = {"Engineering", "Product", "Design", "IT & Security"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": "https://careers.doctolib.com",
    "Referer": "https://careers.doctolib.com/",
    "From": "yannickarieldossa@gmail.com",
}

REQUEST_TIMEOUT = 30


@dataclass
class Job:
    native_job_id: str         # Greenhouse posting id (string), e.g. "7583949003"
    title: str
    location: str              # location.name from payload (free-text)
    category: str | None       # in-scope dept names joined by " | "
    apply_url: str             # canonical boards.greenhouse.io/doctolib/jobs/<id>
    employment_type: str       # from metadata "Employment Type" (or "")
    description: str | None = None
    posted_date: str | None = None    # first_published, YYYY-MM-DD
    identifier: str | None = None     # requisition_id if Doctolib filled it in
    raw_payload: dict | None = None


def _in_scope(doc: dict) -> bool:
    office_names = {(o.get("name") or "").strip() for o in (doc.get("offices") or [])}
    if not (OFFICES_IN_SCOPE & office_names):
        return False
    dept_names = {(d.get("name") or "").strip() for d in (doc.get("departments") or [])}
    return bool(DEPARTMENTS_IN_SCOPE & dept_names)


def _category(doc: dict) -> str | None:
    dept_names = [(d.get("name") or "").strip() for d in (doc.get("departments") or [])]
    in_scope = [n for n in dept_names if n in DEPARTMENTS_IN_SCOPE]
    return " | ".join(in_scope) if in_scope else None


def _employment_type(doc: dict) -> str:
    for m in doc.get("metadata") or []:
        if m.get("name") == "Employment Type":
            return (m.get("value") or "").strip()
    return ""


def _clean_description(content: str | None) -> str | None:
    """Greenhouse returns `content` as HTML with the tags entity-encoded
    (e.g. &lt;p&gt; and &#39;). One unescape pass yields normal HTML; strip
    tags with BeautifulSoup."""
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


def _doc_to_job(doc: dict) -> Job:
    job_id = doc.get("id")
    if not job_id:
        raise RuntimeError(f"Doctolib posting missing id (title={doc.get('title')!r})")
    job_id = str(job_id)

    # absolute_url points at job-boards.greenhouse.io (the new host); the
    # public-facing careers links use boards.greenhouse.io. Both resolve, but
    # boards.greenhouse.io is what the careers page renders, so keep parity.
    apply_url = PUBLIC_JOB_URL_TEMPLATE.format(job_id=job_id)

    location_obj = doc.get("location") or {}
    location = (location_obj.get("name") or "").strip()

    req_id = doc.get("requisition_id")
    if isinstance(req_id, str):
        req_id = req_id.strip() or None

    return Job(
        native_job_id=job_id,
        title=(doc.get("title") or "").strip(),
        location=location,
        category=_category(doc),
        apply_url=apply_url,
        employment_type=_employment_type(doc),
        description=_clean_description(doc.get("content")),
        posted_date=_posted_date(doc),
        identifier=req_id,
        raw_payload=doc,
    )


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
        print(
            f"  {job.native_job_id} {job.title!r} -> KEEP",
            flush=True,
        )

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
