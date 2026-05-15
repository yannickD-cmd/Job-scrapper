"""Doctolib job scraper — Paris office, tech-track departments.

Doctolib's ATS is a public Greenhouse job board, and we hit the standard
Greenhouse Job Board API:

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

Native job id: Greenhouse `id` (e.g. 7583949003). Stable across the posting's
lifetime. The Greenhouse `internal_job_id` is a separate internal key we
don't need.

Apply URL: prefer the rich careers.doctolib.com page (which renders the full
description in Doctolib's own layout) over the bare Greenhouse board page.
URL shape is `careers.doctolib.com/jobs/<slug>-<id>/`. We can NOT derive the
slug from the current title — Doctolib's WordPress permalinks are frozen at
posting-creation time, so when a role gets retitled the slug stays stale
(seen in the wild: id 6681347003's current title is "Machine Learning
Engineer …" but its live URL still says "senior-machine-learning-engineer-…").

So we look up the real slug from Doctolib's job sitemap, which lists every
indexed posting's URL with the Greenhouse id as the trailing segment:

  https://careers.doctolib.com/dl_job-sitemap.xml

The sitemap covers ~80% of Greenhouse postings — the gap is recently-
published roles that haven't been crawled yet. For those, we fall back to
the Greenhouse board URL, which always resolves.

Description: Greenhouse double-encodes the HTML (entities wrap tags), so we
html.unescape once, then strip tags with BeautifulSoup. Same pattern as
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

API_URL = "https://boards-api.greenhouse.io/v1/boards/doctolib/jobs"
SITEMAP_URL = "https://careers.doctolib.com/dl_job-sitemap.xml"
GREENHOUSE_JOB_URL_TEMPLATE = "https://boards.greenhouse.io/doctolib/jobs/{job_id}"

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

# Each entry in the job sitemap ends with `-<greenhouse_id>/`. Capture both
# the full URL and the trailing id so we can build the lookup map.
_SITEMAP_URL_RE = re.compile(
    r"https://careers\.doctolib\.com/jobs/[a-z0-9-]+-(\d+)/"
)


@dataclass
class Job:
    native_job_id: str         # Greenhouse posting id (string), e.g. "7583949003"
    title: str
    location: str              # location.name from payload (free-text)
    category: str | None       # in-scope dept names joined by " | "
    apply_url: str             # careers.doctolib.com/jobs/<slug>-<id>/ if indexed,
                               # else boards.greenhouse.io/doctolib/jobs/<id>
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


def _fetch_sitemap_urls(session: requests.Session) -> dict[str, str]:
    """Return {greenhouse_id: doctolib_url} from the job sitemap. If the
    sitemap is unavailable, return an empty map and rely on Greenhouse
    fallbacks — the scrape stays functional, links just look less polished."""
    try:
        response = session.get(SITEMAP_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except Exception as exc:
        print(
            f"  WARN: sitemap fetch failed ({type(exc).__name__}: {exc}); "
            f"every apply_url will fall back to Greenhouse",
            flush=True,
        )
        return {}

    mapping: dict[str, str] = {}
    for match in _SITEMAP_URL_RE.finditer(response.text):
        url, job_id = match.group(0), match.group(1)
        mapping.setdefault(job_id, url)
    print(f"  sitemap has {len(mapping)} indexed job URLs", flush=True)
    return mapping


def _doc_to_job(doc: dict, sitemap_urls: dict[str, str]) -> Job:
    job_id = doc.get("id")
    if not job_id:
        raise RuntimeError(f"Doctolib posting missing id (title={doc.get('title')!r})")
    job_id = str(job_id)

    title = (doc.get("title") or "").strip()
    apply_url = sitemap_urls.get(job_id) or GREENHOUSE_JOB_URL_TEMPLATE.format(job_id=job_id)

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

    print("Sitemap phase...", flush=True)
    sitemap_urls = _fetch_sitemap_urls(session)

    print("Filter phase...", flush=True)
    candidates = [d for d in docs if _in_scope(d)]
    print(
        f"  kept {len(candidates)} (dropped {len(docs) - len(candidates)} out-of-scope)",
        flush=True,
    )

    kept: dict[str, Job] = {}
    gh_fallback = 0
    for doc in candidates:
        job = _doc_to_job(doc, sitemap_urls)
        if job.native_job_id in kept:
            continue
        kept[job.native_job_id] = job
        from_sitemap = job.apply_url.startswith("https://careers.doctolib.com/")
        if not from_sitemap:
            gh_fallback += 1
        print(
            f"  {job.native_job_id} {job.title!r} -> KEEP "
            f"({'doctolib' if from_sitemap else 'greenhouse'})",
            flush=True,
        )

    elapsed = time.time() - started
    print(
        f"\n  -> {len(kept)} jobs in {elapsed:.1f}s "
        f"({gh_fallback} fell back to greenhouse URL)\n",
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
