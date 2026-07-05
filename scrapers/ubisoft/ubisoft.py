"""Ubisoft job scraper — France, Tech + Data/AI job families, CDI only.

Ubisoft's careers site at ubisoft.com/en-us/company/careers/search is a React
SPA (window.__PRELOADED_STATE__), but the "apply" flow and application status
all run through SmartRecruiters (tenant ``Ubisoft2``). So we skip the SPA and
hit the SmartRecruiters public postings API — clean JSON, no auth, no bot wall
(same pattern as the Sia Partners scraper).

Two-pass scrape (SmartRecruiters public API):

1. LISTING. ``/v1/companies/Ubisoft2/postings?country=fr`` returns every French
   posting (~79 today) with paging (limit 100). Each item already carries
   location, standard function, typeOfEmployment, experienceLevel, releasedDate
   and a set of Ubisoft-native ``customField`` buckets — including "Job Family"
   and "Contract" — so we filter to scope here, before any detail fetch:
     - "Job Family" custom field in FAMILIES_IN_SCOPE (Software Development /
       Data / Information & Systems — Ubisoft's own tech taxonomy, the same
       Job Family filter the careers site exposes), and
     - "Contract" custom field == "Permanent" (== CDI).

   Why the "Contract" custom field and not ``typeOfEmployment``: SmartRecruiters
   types CDD (Fixed Term) postings as typeOfEmployment.id="permanent" /
   label="Full-time", so that standard field lumps CDD in with CDI. Ubisoft's
   own "Contract" custom field (Permanent / Fixed Term / Internship /
   Apprenticeship...) is the accurate CDI signal, so we gate on that.

2. ENRICHMENT. For each surviving posting, fetch ``/postings/{id}`` for the
   job-ad description sections and the canonical applyUrl. Enrichment is
   best-effort: a detail-fetch failure keeps the row (constructed apply URL,
   no description) rather than dropping it — dropping would false-close the DB
   row on the next persist (see feedback_partial_scrape_false_close). Only a
   LISTING failure aborts the run.

Scope decision (locked with the user): France, Tech + Data/AI families, CDI
only. We filter on Ubisoft's native "Job Family" custom field rather than a
title/keyword predicate because the ATS exposes a usable category facet (see
feedback_prefer_platform_category_over_is_tech_role). To widen/narrow scope,
edit FAMILIES_IN_SCOPE / CONTRACTS_IN_SCOPE.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

TENANT = "Ubisoft2"
API_BASE = f"https://api.smartrecruiters.com/v1/companies/{TENANT}"
POSTING_URL = "https://jobs.smartrecruiters.com/" + TENANT + "/{id}"

COUNTRY = "fr"

# Ubisoft's "Job Family" custom field is its careers-site taxonomy. The three
# tech/data families in scope — taken whole, no title/keyword gate. Everything
# else (Art, Animation, Marketing, Finance, Design, Game Quality, HR, Legal...)
# is out of scope for a Tech + Data/AI feed.
FAMILIES_IN_SCOPE: set[str] = {
    "Software Development",
    "Data",
    "Information & Systems",
}

# CDI only. "Contract" custom field values seen: Permanent / Fixed Term /
# Internship / Apprenticeship and Professional Training Contract.
CONTRACTS_IN_SCOPE: set[str] = {"Permanent"}

# Defense-in-depth for the CDI-only scope: if Ubisoft ever mistypes an intern
# posting's Contract as "Permanent" (Sia does exactly this), drop anything whose
# title literally says internship/stage/apprenticeship regardless of the field.
_INTERNSHIP_TITLE = re.compile(
    r"internship|\bintern\b|\bstage\b|stagiaire|alternance|apprentiss|apprentice"
    r"|work[- ]study|\bv\.?i\.?e\.?\b",
    re.I,
)

# Job-ad sections worth keeping as the description. companyDescription is the
# same Ubisoft boilerplate on every posting, so it's dropped.
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


def _custom_field(posting: dict, label: str) -> str | None:
    for cf in posting.get("customField") or []:
        if cf.get("fieldLabel") == label:
            return cf.get("valueLabel")
    return None


def _job_family(posting: dict) -> str | None:
    return _custom_field(posting, "Job Family")


def _contract(posting: dict) -> str | None:
    return _custom_field(posting, "Contract")


def _in_scope(posting: dict) -> bool:
    """Tech/Data family + Permanent contract, minus any mistyped intern posting."""
    return (
        _job_family(posting) in FAMILIES_IN_SCOPE
        and _contract(posting) in CONTRACTS_IN_SCOPE
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
    return raw[:10] if raw else None  # "2026-07-03T14:38:58.553Z" -> "2026-07-03"


def _to_job(posting: dict) -> Job:
    """Build a Job from a listing item (enrichment fills description later)."""
    pid = str(posting["id"])
    return Job(
        native_job_id=pid,
        title=(posting.get("name") or "").strip(),
        apply_url=POSTING_URL.format(id=pid),  # fallback; enrichment upgrades it
        location=_location_str(posting),
        category=_job_family(posting),          # Ubisoft's native family label
        posted_date=_posted_date(posting),
        employment_type=_contract(posting),     # Permanent / Fixed Term / ...
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


def _enrich(session: requests.Session, job: Job) -> None:
    """Fetch the posting detail; fill description + canonical apply_url.

    On failure the caller keeps the row as-is (with the constructed apply URL) —
    enrichment gaps must never drop a row (feedback_partial_scrape_false_close).
    """
    response = session.get(
        f"{API_BASE}/postings/{job.native_job_id}", timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    detail = response.json()

    job.apply_url = detail.get("applyUrl") or detail.get("postingUrl") or job.apply_url
    job.description = _description_html(detail)
    job.raw_payload = detail


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    print("Listing phase (SmartRecruiters postings, country=fr)...", flush=True)
    started = time.time()
    postings = _fetch_all_fr(session)
    print(f"  -> {len(postings)} FR postings in {time.time() - started:.1f}s\n", flush=True)

    # Scope filter (on the listing, before any detail fetch):
    #   Job Family in {Software Development, Data, Information & Systems} + Permanent.
    in_scope = [p for p in postings if _in_scope(p)]
    print(
        f"Scope filter (families={sorted(FAMILIES_IN_SCOPE)}; "
        f"contracts={sorted(CONTRACTS_IN_SCOPE)}): {len(in_scope)}/{len(postings)} kept\n",
        flush=True,
    )

    # Enrich each in-scope posting (best-effort; failures keep the row).
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
        print(f"[{j['identifier'] or j['native_job_id']}] {j['title']}")
        print(f"  Family     : {j['category']}")
        print(f"  Contract   : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {preview}")
        print()
