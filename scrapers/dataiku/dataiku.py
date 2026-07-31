"""Dataiku job scraper — France, tech / data / AI / software departments.

Dataiku's careers site is a public Greenhouse job board, so we hit the standard
Greenhouse Job Board API:

  https://boards-api.greenhouse.io/v1/boards/dataiku/jobs?content=true

`content=true` makes Greenhouse return each posting's description HTML plus its
`departments[]` / `offices[]` arrays in the listing payload, so a single
anonymous GET covers everything — no per-job detail calls. Same shape as
Doctolib / Datadog. The board is small (~24 live postings) and, despite Dataiku
being Paris-HQ, overwhelmingly GTM: the bulk is Account Executives / Sales /
Marketing spread across the US, Japan, Korea, UK, UAE. France yield is tiny.

Scope (locked 2026-07): France-linked, tech/data/AI/software/eng roles, all
employment types (the board carries no CDI/intern flag anyway). The Greenhouse
boards API has no filter params, so everything is client-side:

  - Location: we keep a posting if France shows up in EITHER `location.name`
    OR any `offices[].name`. offices[] is the reliable signal here — e.g. the
    "Software Engineer in Test (FR, UK, DE, NL)" posting has
    location.name="Europe, Middle East, and Africa" (no France token) but
    offices=["France, Paris"]. Matching France across both catches it. Every
    French entry on this board is suffixed ", France"; we also match a set of
    French city tokens defensively in case a bare-city location ever appears.
  - Departments: Dataiku files roles under coarse buckets. The clean tech/data
    buckets — Engineering, Data Science, Product (R&D reserved for future) — are
    kept wholesale (Product = the product-engineering org at a data/AI-platform
    company; err inclusive on AI/data). Every OTHER bucket (Business Solutions,
    Business Applications, Account Executives, Sales *, Marketing *, Finance,
    Partnerships) is GTM/non-tech and is only rescued if `is_tech_role(title)`
    fires — so a "Product Manager, Business Applications" under Business
    Solutions is dropped, but a hypothetical "Data Engineer" mis-filed under a
    solutions bucket would still be kept.

Employment type: this board exposes no Employment-Type / Time-Type metadata, so
`employment_type` is "" for every row. We keep all employment types in scope by
design (the user wants AI/data roles across CDI / intern / apprenticeship).

Cross-listing: the same role can appear under several Greenhouse ids. Each id is
a distinct row; dedup is by native_job_id.

Native job id: Greenhouse `id` (string), stable across the posting's lifetime.
apply_url: Greenhouse `absolute_url` (job-boards.greenhouse.io/dataiku/jobs/<id>).
posted_date: `first_published` (fallback `updated_at`), YYYY-MM-DD.

To widen scope, edit TECH_DEPARTMENTS / FRANCE_RE / FRENCH_CITY_RE.
"""
from __future__ import annotations

import html
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

from scrapers._relevance import is_tech_role

API_URL = "https://boards-api.greenhouse.io/v1/boards/dataiku/jobs"

# Clean tech / data / eng buckets kept wholesale. Everything else is GTM/non-tech
# and only survives via the is_tech_role(title) rescue below.
TECH_DEPARTMENTS = {"Engineering", "Data Science", "Product", "R&D"}

FRANCE_RE = re.compile(r"\bfrance\b", re.IGNORECASE)
# Defensive: a bare French-city location with no "France" token. All current
# France entries already carry ", France", so this is belt-and-braces.
FRENCH_CITY_RE = re.compile(
    r"\b(?:paris|lyon|bordeaux|toulouse|nantes|lille|grenoble|marseille|"
    r"nice|strasbourg|rennes|montpellier|sophia[- ]antipolis)\b",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": "https://careers.dataiku.com",
    "Referer": "https://careers.dataiku.com/",
    "From": "yannickarieldossa@gmail.com",
}

REQUEST_TIMEOUT = 30
# One-shot endpoint; the cap only guards a future paginated rewrite from looping.
MAX_PAGES = 20


@dataclass
class Job:
    native_job_id: str         # Greenhouse posting id (string), e.g. "5072788004"
    title: str
    location: str              # location.name from payload (free-text, may list several regions)
    category: str | None       # in-scope dept name(s), " | "-joined
    apply_url: str             # Greenhouse absolute_url
    employment_type: str       # no employment-type metadata on this board -> ""
    description: str | None = None
    posted_date: str | None = None    # first_published, YYYY-MM-DD
    identifier: str | None = None     # requisition_id if Dataiku filled it in
    raw_payload: dict | None = None


def _dept_names(doc: dict) -> set[str]:
    return {(d.get("name") or "").strip() for d in (doc.get("departments") or [])}


def _office_names(doc: dict) -> list[str]:
    return [(o.get("name") or "").strip() for o in (doc.get("offices") or [])]


def _france_text(doc: dict) -> str:
    """Union of location.name + every office name — the surface we scan for France."""
    parts = [(doc.get("location") or {}).get("name") or ""]
    parts.extend(_office_names(doc))
    return " ; ".join(p for p in parts if p)


def _is_france(doc: dict) -> bool:
    text = _france_text(doc)
    return bool(FRANCE_RE.search(text) or FRENCH_CITY_RE.search(text))


def _in_scope(doc: dict) -> bool:
    if not _is_france(doc):
        return False
    if TECH_DEPARTMENTS & _dept_names(doc):
        return True
    # Mixed / GTM bucket: rescue only genuine tech/data/AI titles.
    return is_tech_role(doc.get("title"))


def _category(doc: dict) -> str | None:
    names = sorted(n for n in _dept_names(doc) if n)
    return " | ".join(names) if names else None


def _employment_type(doc: dict) -> str:
    # Board exposes no Employment/Time Type metadata; probe generically anyway so
    # a future field is picked up without a code change.
    for m in doc.get("metadata") or []:
        name = (m.get("name") or "").lower()
        if name in {"employment type", "time type", "contract type"}:
            value = m.get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _clean_description(content: str | None) -> str | None:
    """Greenhouse returns `content` as HTML with the tags entity-encoded
    (e.g. &lt;p&gt; and &#39;). One unescape pass yields normal HTML; strip tags
    with BeautifulSoup. Same pattern as Doctolib / Datadog."""
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
    # posting. If Greenhouse ever stops doing that, the wholesale gate collapses
    # to the is_tech_role rescue silently — fail loudly instead.
    if jobs and not any("departments" in j for j in jobs):
        raise RuntimeError("Greenhouse payload has no departments[] — gate cannot run")
    return jobs


def _doc_to_job(doc: dict) -> Job:
    job_id = doc.get("id")
    if not job_id:
        raise RuntimeError(f"Dataiku posting missing id (title={doc.get('title')!r})")
    job_id = str(job_id)

    apply_url = (doc.get("absolute_url") or "").strip()
    if not apply_url:
        apply_url = f"https://job-boards.greenhouse.io/dataiku/jobs/{job_id}"

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
        print(f"  {job.native_job_id} {job.title!r} [{job.category}] -> KEEP", flush=True)

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
