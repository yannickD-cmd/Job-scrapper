"""Hugging Face job scraper — France (incl. EMEA-remote based in FR), tech roles.

Hugging Face is French-founded (Paris) but heavily remote. Its board runs on
Workable. Two public JSON endpoints exist:

  (a) POST https://apply.workable.com/api/v3/accounts/huggingface/jobs
      -> {total, results:[...], nextPage} — structured but NO descriptions;
         would need an N+1 detail call per posting.
  (b) GET  https://apply.workable.com/api/v1/widget/accounts/huggingface?details=true
      -> {name, description, jobs:[...]} — the embed endpoint. Returns the
         WHOLE board in a single request with the full description inline, a
         human `employment_type` ("Full-time"), and a `locations` array whose
         entries carry an ISO `countryCode`.

We use (b): it returns the fullest structured JSON in one shot (no pagination,
no per-job detail fetch), which is the politest option for a small board (~7
postings today). The endpoint is unpaginated — it emits every published job in
one array — so there is no page loop; `_fetch_jobs` still guards against a
non-list payload and the caller aborts (never returns partial) on any HTTP
error so run.py logs a failed run and the DB closes nothing.

Country gate
------------
HF lists each remote role twice — once as "… EMEA Remote" (tagged
country=France, city=Paris) and once as "… US Remote" (country=United States).
We keep a posting when ANY of its `locations` is `countryCode == "FR"` (or
`country == "France"`). That captures the France-relevant / EMEA-remote
listings and drops the duplicate US ones. Scope is France-only, but because HF
files its France-open remote roles under country=France this also keeps the
"remote-open-to-France" roles the user wants — there is no separate Global
listing to rescue here.

Scope gate (category-first, is_tech_role rescue)
------------------------------------------------
HF's `department` values are coarse and mixed ("Product", "Open Source",
"Wild Card") — not a clean tech-family facet — so category alone is unusable
(Schneider-style). We therefore:
  - wholesale-keep departments that are unambiguously engineering/ML/research
    (TECH_DEPARTMENTS);
  - hard-drop unambiguously non-tech departments (NON_TECH_DEPARTMENTS:
    Sales/GTM/Marketing/People/Finance/Legal/Support/Ops);
  - for everything else (the mixed "Product" / "Wild Card" buckets) keep iff
    `scrapers._relevance.is_tech_role(title)` matches — erring inclusive on
    AI/data per feedback_include_data_adjacent_ai_roles.

Native job id : Workable `shortcode` (stable per posting).
Apply URL     : `application_url` (falls back to `url` / the /j/<shortcode> page).
Description   : `description` is HTML — stripped to plain text for the alert.

To widen scope, edit TECH_DEPARTMENTS / NON_TECH_DEPARTMENTS or the country gate.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass

import requests

from scrapers._relevance import is_tech_role

WIDGET_URL = "https://apply.workable.com/api/v1/widget/accounts/huggingface"
WIDGET_PARAMS = {"details": "true"}

# Departments kept wholesale — unambiguously engineering / ML / research at HF.
TECH_DEPARTMENTS = {
    "engineering", "open source", "machine learning", "ml", "research",
    "science", "data", "infrastructure", "security", "platform",
    "science & research", "research & science",
}

# Departments dropped wholesale — no tech role lives here.
NON_TECH_DEPARTMENTS = {
    "sales", "gtm", "go-to-market", "marketing", "growth", "people", "hr",
    "human resources", "talent", "recruiting", "finance", "legal", "support",
    "customer support", "operations", "ops", "business development",
    "partnerships", "communications", "administration",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
}

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.0

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\f\v]+")


@dataclass
class Job:
    native_job_id: str          # Workable shortcode
    title: str
    location: str               # "Paris, France" / "Remote — France" style
    category: str | None        # department (e.g. "Open Source", "Product")
    apply_url: str              # application_url / /j/<shortcode>
    employment_type: str        # "Full-time", "Internship", ...
    description: str | None = None
    posted_date: str | None = None    # published_on, YYYY-MM-DD
    identifier: str | None = None     # Workable `code` (req code) if present
    raw_payload: dict | None = None


def _strip_html(html: str | None) -> str | None:
    if not html:
        return None
    text = _TAG_RE.sub(" ", html)
    text = (
        text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        .replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    )
    text = _WS_RE.sub(" ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip() or None


def _in_france(doc: dict) -> bool:
    for loc in doc.get("locations") or []:
        if (loc.get("countryCode") or "").upper() == "FR":
            return True
        if (loc.get("country") or "").strip().lower() == "france":
            return True
    # Fall back to the top-level country when locations[] is empty/absent.
    return (doc.get("country") or "").strip().lower() == "france"


def _department(doc: dict) -> str | None:
    dept = doc.get("department")
    if isinstance(dept, list):
        dept = dept[0] if dept else None
    if isinstance(dept, str):
        return dept.strip() or None
    return None


def _in_scope(doc: dict) -> bool:
    if not _in_france(doc):
        return False

    dept = (_department(doc) or "").lower()
    title = doc.get("title") or ""

    if dept in NON_TECH_DEPARTMENTS:
        return False
    if dept in TECH_DEPARTMENTS:
        return True
    # Mixed / unknown bucket ("Product", "Wild Card"): rescue on the title.
    return is_tech_role(title)


def _location_str(doc: dict) -> str:
    parts = []
    for loc in doc.get("locations") or []:
        city = (loc.get("city") or "").strip()
        country = (loc.get("country") or "").strip()
        chunk = ", ".join(p for p in (city, country) if p)
        if chunk:
            parts.append(chunk)
    if not parts:
        city = (doc.get("city") or "").strip()
        country = (doc.get("country") or "").strip()
        chunk = ", ".join(p for p in (city, country) if p)
        if chunk:
            parts.append(chunk)
    loc = " / ".join(dict.fromkeys(parts))
    if doc.get("telecommuting"):
        loc = f"{loc} (Remote)" if loc else "Remote"
    return loc


def _posted_date(doc: dict) -> str | None:
    raw = doc.get("published_on") or doc.get("created_at")
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    return raw[:10]


def _doc_to_job(doc: dict) -> Job:
    shortcode = doc.get("shortcode")
    if not shortcode:
        raise RuntimeError(f"HF posting missing shortcode (title={doc.get('title')!r})")

    apply_url = (
        doc.get("application_url")
        or doc.get("url")
        or doc.get("shortlink")
        or f"https://apply.workable.com/j/{shortcode}"
    )

    return Job(
        native_job_id=str(shortcode),
        title=(doc.get("title") or "").strip(),
        location=_location_str(doc),
        category=_department(doc),
        apply_url=apply_url,
        employment_type=(doc.get("employment_type") or "").strip(),
        description=_strip_html(doc.get("description")),
        posted_date=_posted_date(doc),
        identifier=(doc.get("code") or "").strip() or None,
        raw_payload=doc,
    )


def _fetch_jobs(session: requests.Session) -> list[dict]:
    print(f"  GET {WIDGET_URL}?details=true ...", flush=True)
    response = session.get(
        WIDGET_URL, params=WIDGET_PARAMS, headers=HEADERS, timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()      # abort (never partial) on any HTTP error
    payload = response.json()
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise RuntimeError(f"Unexpected Workable widget shape: keys={list(payload)}")
    print(f"    {len(jobs)} postings on the board", flush=True)
    time.sleep(REQUEST_DELAY_SECONDS)
    return jobs


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Listing phase...", flush=True)
    docs = _fetch_jobs(session)

    print("Filter phase...", flush=True)
    kept: dict[str, Job] = {}
    for doc in docs:
        if not _in_scope(doc):
            continue
        job = _doc_to_job(doc)
        if job.native_job_id in kept:
            continue
        kept[job.native_job_id] = job
        print(
            f"  {job.native_job_id} [{job.category}] {job.title!r} -> KEEP",
            flush=True,
        )

    elapsed = time.time() - started
    print(
        f"\n  -> {len(kept)} jobs kept "
        f"(dropped {len(docs) - len(kept)} out-of-scope) in {elapsed:.1f}s\n",
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
