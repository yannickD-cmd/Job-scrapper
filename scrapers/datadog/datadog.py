"""Datadog job scraper — France, core engineering departments.

Datadog's careers site (careers.datadoghq.com) is a custom front-end over a
public Greenhouse job board, so we hit the standard Greenhouse Job Board API:

  https://boards-api.greenhouse.io/v1/boards/datadog/jobs?content=true

`content=true` makes Greenhouse return each posting's description HTML plus
its `departments[]` / `offices[]` arrays in the listing payload, so a single
anonymous GET covers everything — no per-job detail calls. Same pattern as
Doctolib, minus the sitemap lookup: Datadog's `absolute_url` already points
at careers.datadoghq.com/detail/<id>/?gh_jid=<id>.

Scope (locked 2026-07): France-linked, core engineering, full-time. The
Greenhouse boards API has no filter params, so everything is client-side:

  - Location: `location.name` is free text and often multi-location
    ("Dublin, Ireland; Madrid, Spain; Paris, France" or "France, Remote;
    Germany, Remote; ..."). We keep any posting whose location mentions
    France — i.e. the role is hirable from France. Every French city entry
    on this board is suffixed ", France", so the word match is sufficient.
  - Departments: Dev Eng and Security unconditionally. Leadership only when
    the posting carries "Area - Engineering" metadata — Datadog files its
    engineering-manager reqs under the generic Leadership department, and
    the Area metadata is what separates them from sales/marketing leadership.
  - Time Type metadata must be "Full time" when present. Fail-open when the
    metadata is absent so a tagging slip can't silently drop real roles
    (today all 411 postings carry it). Interns and new-grads sit in the
    "Early Career" department, which the department gate excludes anyway.

Cross-listing: the same role can appear under several Greenhouse ids (e.g.
one "Paris, France" posting and one multi-country "Remote" posting). Each id
is a distinct row; dedup is by native_job_id.

Native job id: Greenhouse `id` (e.g. 7194969), stable across the posting's
lifetime. posted_date: `first_published` (fallback `updated_at`), YYYY-MM-DD.

To widen scope, edit DEPARTMENTS_IN_SCOPE / FRANCE_RE.
"""
from __future__ import annotations

import html
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

API_URL = "https://boards-api.greenhouse.io/v1/boards/datadog/jobs"

DEPARTMENTS_IN_SCOPE = {"Dev Eng", "Security"}
LEADERSHIP_DEPARTMENT = "Leadership"
AREA_METADATA = "Area - Engineering"
FRANCE_RE = re.compile(r"\bfrance\b", re.IGNORECASE)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": "https://careers.datadoghq.com",
    "Referer": "https://careers.datadoghq.com/",
    "From": "yannickarieldossa@gmail.com",
}

REQUEST_TIMEOUT = 30


@dataclass
class Job:
    native_job_id: str         # Greenhouse posting id (string), e.g. "7194969"
    title: str
    location: str              # location.name from payload (free-text, may list several cities)
    category: str | None       # dept name(s) + "Area - Engineering" metadata, " | "-joined
    apply_url: str             # careers.datadoghq.com detail page (Greenhouse absolute_url)
    employment_type: str       # from metadata "Time Type" (or "")
    description: str | None = None
    posted_date: str | None = None    # first_published, YYYY-MM-DD
    identifier: str | None = None     # requisition_id if Datadog filled it in
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

    time_type = _metadata_value(doc, "Time Type")
    if time_type and time_type.lower() != "full time":
        return False

    dept_names = _dept_names(doc)
    if DEPARTMENTS_IN_SCOPE & dept_names:
        return True
    return LEADERSHIP_DEPARTMENT in dept_names and bool(_metadata_value(doc, AREA_METADATA))


def _category(doc: dict) -> str | None:
    keep = DEPARTMENTS_IN_SCOPE | {LEADERSHIP_DEPARTMENT}
    parts = [n for n in sorted(_dept_names(doc)) if n in keep]
    area = _metadata_value(doc, AREA_METADATA)
    if area:
        parts.append(area)
    return " | ".join(parts) if parts else None


def _clean_description(content: str | None) -> str | None:
    """Greenhouse returns `content` as HTML with the tags entity-encoded
    (e.g. &lt;p&gt; and &#39;). One unescape pass yields normal HTML; strip
    tags with BeautifulSoup. Same pattern as Doctolib."""
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
        raise RuntimeError(f"Datadog posting missing id (title={doc.get('title')!r})")
    job_id = str(job_id)

    apply_url = (doc.get("absolute_url") or "").strip()
    if not apply_url:
        apply_url = f"https://boards.greenhouse.io/datadog/jobs/{job_id}"

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
        employment_type=_metadata_value(doc, "Time Type") or "",
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
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
