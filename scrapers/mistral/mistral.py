"""Mistral AI job scraper — France, tech / research / infra families.

Mistral's careers page is a thin front over Ashby. Ashby exposes a public,
anonymous posting API that returns every listed posting (descriptions inline)
in a single GET — the org token is literally "mistral.ai":

  https://api.ashbyhq.com/posting-api/job-board/mistral.ai?includeCompensation=true

One request covers the whole board (~169 postings) — no pagination, no
per-job detail calls. Same shape as Alan (see scrapers/alan/alan.py).

Each posting carries:
  - department      — top-level family: Engineering & Infra / Science /
                      Mistral Compute / Solutions / Product / Business /
                      Human Resources / Legal / Marketing / Finance / ...
  - team            — finer bucket ("Deployment Strategist", "HPC Engineering")
  - employmentType  — FullTime / Intern / Contract
  - location        — free-text city ("Paris", "Palo Alto", "London")
  - secondaryLocations — list of {location, address:{postalAddress:{...}}}
  - address.postalAddress.addressCountry — here it is a REAL country
                      ("France", "United States", ...), not the generic
                      "European Union" Alan returns. We use it as the primary
                      France signal and fall back to Alan's free-text/city
                      match for the handful of postings with a null country.

Scope (locked): France only, tech / research / infra families.
  Mistral hires globally (Paris, SF, London, Singapore, Seoul, ...), so the
  France gate matters — only ~110 of 169 postings are French.

  Department is a usable category facet, so we filter on it (category-first
  rule), NOT is_tech_role wholesale:
    * WHOLESALE keep — unambiguously in scope, no title gate:
        Engineering & Infra   (SWE / SRE / Security / Data / Research Eng / ...)
        Science               (AI Scientists, research, Human Data, safety)
        Mistral Compute       (HPC / datacenter / Managed-Kubernetes cloud)
    * RESCUE via is_tech_role(title) — mixed buckets where GTM/ops sit next to
      engineering:
        Solutions  (keeps Applied-AI / Forward-Deployed ML / Solution
                    Architect / Pentester engineers; drops "Solution
                    Operations Manager, Revenue Growth" style GTM/ops)
        Product    (keeps Product Designer / eng; drops Product Managers,
                    Product Ops, Pricing)
    * DROP entirely — Business, Human Resources, Legal, Marketing, Finance,
      Public Affairs and Communication.

  Employment type is NOT filtered: every kept posting already passes the
  tech/research gate, and the user explicitly wants AI/data roles kept across
  employment types (the two interns in scope are an Applied-Scientist and an
  Applied-AI ML intern; the contract is in-scope infra). Filtering FullTime
  would drop them for no benefit.

Native job id : Ashby posting `id` (UUID, stable across the posting's life).
Apply URL     : `jobUrl` — the Ashby posting page (renders full description).
Description   : `descriptionPlain` — already plain text, no HTML stripping.

To widen/narrow scope, edit WHOLESALE_DEPARTMENTS / RESCUE_DEPARTMENTS, or add
cities to FRENCH_CITY_TOKENS.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass

import requests

from scrapers._relevance import is_tech_role

API_URL = (
    "https://api.ashbyhq.com/posting-api/job-board/mistral.ai"
    "?includeCompensation=true"
)

# Department is a usable facet — filter on it, not is_tech_role wholesale.
WHOLESALE_DEPARTMENTS = {"Engineering & Infra", "Science", "Mistral Compute"}
# Mixed buckets: keep only titles is_tech_role recognises (drops GTM/ops).
RESCUE_DEPARTMENTS = {"Solutions", "Product"}

# Bare city listings ("Paris") carry no country suffix, so a plain "France"
# substring test would miss them. Match these French cities as a fallback for
# postings whose addressCountry is null.
FRENCH_CITY_TOKENS = {
    "paris", "lyon", "bordeaux", "marseille", "biarritz", "nantes", "annecy",
    "lille", "toulouse", "nice", "strasbourg", "montpellier", "rennes",
    "grenoble", "nancy", "sophia antipolis",
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
# Single-shot endpoint; the cap is a defensive guard against a future
# pagination bug, never reached today.
MAX_PAGES = 5


@dataclass
class Job:
    native_job_id: str          # Ashby posting UUID
    title: str
    location: str               # free-text city ("Paris")
    category: str | None        # department (e.g. "Engineering & Infra")
    apply_url: str              # jobs.ashbyhq.com/mistral.ai/<id>
    employment_type: str        # "FullTime" / "Intern" / "Contract"
    description: str | None = None
    posted_date: str | None = None    # publishedAt, YYYY-MM-DD
    identifier: str | None = None     # Ashby has no separate req id here
    raw_payload: dict | None = None


def _text_in_france(location: str | None) -> bool:
    loc = (location or "").lower()
    if "france" in loc:
        return True
    tokens = [t.strip() for t in re.split(r"[;,]", loc)]
    return any(t in FRENCH_CITY_TOKENS for t in tokens)


def _address_country(node: dict | None) -> str:
    pa = ((node or {}).get("address") or {}).get("postalAddress") or {}
    return (pa.get("addressCountry") or "").strip()


def _in_france(doc: dict) -> bool:
    # Primary: Mistral's addressCountry is a real country here.
    if _address_country(doc) == "France":
        return True
    # A France role can live only in a secondary location.
    for sl in doc.get("secondaryLocations") or []:
        if _address_country(sl) == "France":
            return True
        if _text_in_france(sl.get("location")):
            return True
    # Fallback for postings with a null country: free-text / city token.
    return _text_in_france(doc.get("location"))


def _in_scope(doc: dict) -> bool:
    if not doc.get("isListed", True):
        return False
    if not _in_france(doc):
        return False
    dept = (doc.get("department") or "").strip()
    if dept in WHOLESALE_DEPARTMENTS:
        return True
    if dept in RESCUE_DEPARTMENTS:
        return is_tech_role(doc.get("title"))
    return False


def _posted_date(doc: dict) -> str | None:
    raw = doc.get("publishedAt")
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    return raw[:10]


def _doc_to_job(doc: dict) -> Job:
    job_id = doc.get("id")
    if not job_id:
        raise RuntimeError(f"Mistral posting missing id (title={doc.get('title')!r})")

    desc = doc.get("descriptionPlain")
    if isinstance(desc, str):
        desc = desc.strip() or None

    return Job(
        native_job_id=str(job_id),
        title=(doc.get("title") or "").strip(),
        location=(doc.get("location") or "").strip(),
        category=(doc.get("department") or "").strip() or None,
        apply_url=doc.get("jobUrl") or f"https://jobs.ashbyhq.com/mistral.ai/{job_id}",
        employment_type=(doc.get("employmentType") or "").strip(),
        description=desc,
        posted_date=_posted_date(doc),
        identifier=None,
        raw_payload=doc,
    )


def _fetch_jobs(session: requests.Session) -> list[dict]:
    print(f"  GET {API_URL} ...", flush=True)
    response = session.get(API_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise RuntimeError(f"Unexpected Ashby payload shape: keys={list(payload)}")
    print(f"    {len(jobs)} postings on the board", flush=True)
    return jobs


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Listing phase...", flush=True)
    docs = _fetch_jobs(session)
    time.sleep(REQUEST_DELAY_SECONDS)

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
