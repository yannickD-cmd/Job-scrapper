"""Sia Partners job scraper — France, AI & Tech + Consulting, permanent only.

Sia Partners' public board at sia-partners.com/en/opportunities is a Drupal
front-end, but every "I'm interested" button points at SmartRecruiters
(tenant ``Sia``). So we skip the HTML entirely and hit the SmartRecruiters
public postings API — clean JSON, no auth, no bot wall.

Two-pass scrape (SmartRecruiters public API):

1. LISTING. ``/v1/companies/Sia/postings?country=fr`` returns every French
   posting with paging (limit 100). Each item already carries location,
   department, employment type, experience level, releasedDate and a
   ``Role`` custom field (AI & Tech / Consulting / Design / Internal Role) —
   so we filter to scope here, before any detail fetch:
     - typeOfEmployment.id in EMPLOYMENT_IDS_IN_SCOPE ("permanent" == CDI;
       this cleanly drops the intern / contract / part-time postings), and
     - Role custom field == "AI & Tech" (Sia's own curated data/AI/tech bucket),
       taken in full — no title/keyword filtering.

2. ENRICHMENT. For each surviving posting, fetch ``/postings/{id}`` for the
   job-ad description sections and the canonical postingUrl. Enrichment is
   best-effort: a detail-fetch failure keeps the row (constructed apply URL,
   no description) rather than dropping it — dropping would false-close the DB
   row on the next persist (see feedback_partial_scrape_false_close). Only a
   LISTING failure aborts the run.

Scope decision (locked with the user): France, permanent contracts only, and
the "AI & Tech" role family in full — nothing else. Earlier cuts that also
pulled Consulting (whole, then keyword-gated) were dropped as noise: Consulting
is ~85% non-tech management consulting, and Sia already curates its tech / data
/ AI roles under the AI & Tech bucket, so the family label alone is the filter.
To change scope, edit ROLES_IN_SCOPE / EMPLOYMENT_IDS_IN_SCOPE.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

TENANT = "Sia"
API_BASE = f"https://api.smartrecruiters.com/v1/companies/{TENANT}"
POSTING_URL = "https://jobs.smartrecruiters.com/" + TENANT + "/{id}"

COUNTRY = "fr"
EMPLOYMENT_IDS_IN_SCOPE: set[str] = {"permanent"}

# Sia's "Role" custom field buckets every posting into AI & Tech / Consulting /
# Design / Internal Role. We take the whole AI & Tech family — that IS Sia's
# curated data/AI/tech bucket — and nothing else. Consulting was tried and
# dropped: it's ~85% non-tech management consulting, and keyword-mining its few
# data/AI roles was more noise and complexity than it was worth.
ROLES_IN_SCOPE: set[str] = {"AI & Tech"}

# ATS-mislabel guard (NOT a scope/keyword filter): Sia types a couple of "Final
# Year Internship" postings as typeOfEmployment.id="permanent", so the permanent
# filter alone leaks them into a CDI-only feed. This drops anything whose title
# literally says internship/stage/apprenticeship, regardless of the ATS type.
_INTERNSHIP_TITLE = re.compile(
    r"internship|\bintern\b|\bstage\b|stagiaire|alternance|apprentiss|apprentice"
    r"|work[- ]study|\bv\.?i\.?e\.?\b",
    re.I,
)

# Job-ad sections worth keeping as the description (companyDescription is the
# same boilerplate on every posting, so it's dropped).
DESCRIPTION_SECTIONS = ("jobDescription", "qualifications", "additionalInformation")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

PAGE_LIMIT = 100          # SmartRecruiters hard max
MAX_PAGES = 20            # defensive cap: 20 * 100 = 2000 postings
REQUEST_DELAY_SECONDS = 1.0
REQUEST_TIMEOUT = 30


@dataclass
class Job:
    native_job_id: str
    title: str
    apply_url: str
    location: str | None = None
    category: str | None = None
    posted_date: str | None = None
    employment_type: str | None = None
    identifier: str | None = None
    # Filled by detail-page enrichment:
    description: str | None = None
    raw_payload: dict | None = None


def _role_family(posting: dict) -> str | None:
    for cf in posting.get("customField") or []:
        if cf.get("fieldLabel") == "Role":
            return cf.get("valueLabel")
    return None


def _employment_id(posting: dict) -> str | None:
    return (posting.get("typeOfEmployment") or {}).get("id")


def _in_scope(posting: dict) -> bool:
    """Permanent AI & Tech postings — whole family, minus ATS-mislabeled interns."""
    return (
        _employment_id(posting) in EMPLOYMENT_IDS_IN_SCOPE
        and _role_family(posting) in ROLES_IN_SCOPE
        and not _INTERNSHIP_TITLE.search(posting.get("name") or "")
    )


def _location_str(posting: dict) -> str | None:
    loc = posting.get("location") or {}
    full = loc.get("fullLocation")
    if full:
        return full
    parts = [loc.get("city"), loc.get("region"), loc.get("country")]
    return ", ".join(p for p in parts if p) or None


def _posted_date(posting: dict) -> str | None:
    raw = posting.get("releasedDate")
    return raw[:10] if raw else None  # "2026-07-03T08:52:20.807Z" -> "2026-07-03"


def _to_job(posting: dict) -> Job:
    """Build a Job from a listing item (enrichment fills description later)."""
    pid = str(posting["id"])
    return Job(
        native_job_id=pid,
        title=(posting.get("name") or "").strip(),
        apply_url=POSTING_URL.format(id=pid),  # fallback; enrichment upgrades it
        location=_location_str(posting),
        category=(posting.get("department") or {}).get("label"),
        posted_date=_posted_date(posting),
        employment_type=(posting.get("typeOfEmployment") or {}).get("label"),
        identifier=posting.get("refNumber"),
        raw_payload=posting,
    )


def _description_html(detail: dict) -> str | None:
    sections = ((detail.get("jobAd") or {}).get("sections")) or {}
    chunks: list[str] = []
    for key in DESCRIPTION_SECTIONS:
        sec = sections.get(key) or {}
        text = (sec.get("text") or "").strip()
        if not text:
            continue
        title = (sec.get("title") or "").strip()
        chunks.append(f"<h3>{title}</h3>\n{text}" if title else text)
    return "\n\n".join(chunks) or None


def _fetch_all_fr(session: requests.Session) -> list[dict]:
    """Page through every French posting. Raises on any request failure."""
    postings: list[dict] = []
    offset = 0
    total = None

    for page in range(MAX_PAGES):
        response = session.get(
            f"{API_BASE}/postings",
            params={"country": COUNTRY, "limit": PAGE_LIMIT, "offset": offset},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        batch = data.get("content") or []
        postings.extend(batch)
        total = data.get("totalFound", total)

        print(
            f"  listing offset={offset}: {len(batch)} postings "
            f"({len(postings)}/{total} total)",
            flush=True,
        )

        offset += PAGE_LIMIT
        if not batch or (total is not None and offset >= total):
            break
        time.sleep(REQUEST_DELAY_SECONDS)
    else:
        print(f"  ! hit MAX_PAGES={MAX_PAGES}; listing may be truncated", flush=True)

    return postings


def _enrich(session: requests.Session, job: Job) -> bool:
    """Fetch the posting detail; fill description + canonical apply_url.

    Returns True on success. On failure the caller keeps the row as-is (with
    the constructed apply URL) — enrichment gaps must never drop a row.
    """
    response = session.get(
        f"{API_BASE}/postings/{job.native_job_id}", timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    detail = response.json()

    job.apply_url = detail.get("applyUrl") or detail.get("postingUrl") or job.apply_url
    job.description = _description_html(detail)
    job.raw_payload = detail
    return True


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    print("Listing phase (SmartRecruiters postings, country=fr)...", flush=True)
    started = time.time()
    postings = _fetch_all_fr(session)
    print(f"  -> {len(postings)} FR postings in {time.time() - started:.1f}s\n", flush=True)

    # Phase 2: scope filter (done on the listing, before any detail fetch):
    #   permanent + Role == "AI & Tech" (whole family, no title gate).
    in_scope = [p for p in postings if _in_scope(p)]
    print(
        f"Scope filter (permanent; roles={sorted(ROLES_IN_SCOPE)}): "
        f"{len(in_scope)}/{len(postings)} kept\n",
        flush=True,
    )

    # Phase 3: enrich each in-scope posting (best-effort; failures keep the row).
    print(
        f"Enrichment phase: fetching {len(in_scope)} detail pages "
        f"(~{int(len(in_scope) * REQUEST_DELAY_SECONDS)}s)...",
        flush=True,
    )

    jobs: list[Job] = []
    failed = 0
    for i, posting in enumerate(in_scope, 1):
        job = _to_job(posting)
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            _enrich(session, job)
            marker = "ok"
        except Exception as exc:
            failed += 1
            marker = f"enrich-failed ({type(exc).__name__}); kept w/o description"
        jobs.append(job)
        print(
            f"  [{i}/{len(in_scope)}] {job.identifier or job.native_job_id} "
            f"{job.title!r} -> {marker}",
            flush=True,
        )

    print(flush=True)
    print(f"Done: {len(jobs)} jobs ({failed} enrichment failures kept).", flush=True)
    return [asdict(j) for j in jobs]


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    started = time.time()
    try:
        results = scrape()
    except Exception as exc:
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise

    elapsed = time.time() - started
    print(f"\n=== {len(results)} jobs final (total runtime {elapsed:.1f}s) ===\n")

    for j in results:
        preview = BeautifulSoup(j["description"] or "", "html.parser").get_text(" ", strip=True)
        preview = preview[:200] + ("…" if len(preview) > 200 else "")
        role = _role_family(j["raw_payload"]) if j["raw_payload"] else None
        print(f"[{j['identifier'] or j['native_job_id']}] {j['title']}")
        print(f"  Role       : {role}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {preview}")
        print()
