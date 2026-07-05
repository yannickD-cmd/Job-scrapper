"""Danone job scraper — France, Data & AI roles, CDI (permanent) only.

Danone's careers front (careers.danone.com) is an Adobe AEM site, but the job
search is powered by iCIMS' **Jibe** search app hosted on a separate origin:

    https://notifications.careers.danone.com/api/jobs

That endpoint answers plain `requests` with a polite UA — no cookies, no
warm-up, UA-agnostic — so it's CI-safe. It's the same JSON the Angular
`jibeapply` SPA reads. Filters are ordinary query params; we fetch the whole
`country=France` slice in one paginated query (`page` + `limit`, 1-based) and
filter Data/AI + contract client-side, so a facet-loop bug can never produce
the partial-result false-close problem (see feedback_partial_scrape_false_close).

Response shape:
  totalCount            -> total for the country=France query (the pagination gate)
  jobs[].data           -> the posting; fields we use:
    req_id / slug       -> native id (stable, also the apply-URL path segment)
    title               -> title; carries the CONTRACT TYPE as a prefix (see below)
    categories[].name   -> family ("Information Technology", "Data", "Research & Innovation", ...)
    full_location       -> "Gif-Sur-Yvette, France"
    posted_date         -> ISO with time, e.g. "2026-04-10T13:00:00+0000"
    description/qualifications/responsibilities -> full JD text
    apply_url           -> iCIMS apply page, e.g. https://enapply-danone.icims.com/jobs/23602/login
    employment_type     -> FULL_TIME/TEMPORARY/PART_TIME — this is HOURS, not contract
                           permanence (FULL_TIME contains CDI, CDD *and* interns),
                           so it is NOT the CDI signal.

Scope (locked 2026-07-04): France · Data & AI · CDI (permanent).

  - Country gate: server-side `country=France` (facet-verified 125 postings).
  - Contract gate (CONTRACT_* below): Danone France encodes the contract in the
    title prefix ("CDI - ...", "ALTERNANCE - ...", "CDD - ...", "STAGE - ...",
    "APPRENTICESHIP - ..."). We keep a job as permanent when it is CDI-prefixed
    OR carries no temporary prefix and isn't flagged TEMPORARY — this keeps the
    unprefixed senior/director roles (e.g. "Director DDAI AI & Tech") that are
    permanent but simply not labelled. Note the misspelt "APRENTICESHIP"
    (single P) that appears on the board — CONTRACT_TEMP_RE matches AP+RENT\\w*.
  - Data/AI gate (DATA_AI_RE + the "Data" family kept wholesale): the ATS has no
    category *facet*, and Data/AI leaks out of the "Information Technology"
    family into "Research & Innovation" (Danone's DDAI = Data, Digital & AI org:
    "Manager R&I AI & Algorithm", "Senior Data Manager", "Director DDAI AI &
    Tech"). So we match the TITLE, not the family. Deliberately NOT keeping all
    of "Information Technology": the user chose Data & AI, not Software/IT, and
    that family's SAP-platform / cybersecurity / infra-architecture CDIs are
    Software-IT, correctly dropped.

Yield today is ~6 roles — small is expected and fine (cf. Salesforce/SAP). The
count grows when Danone posts a Data/AI CDI in France; don't "fix" a small
return. To widen to all tech, add "Information Technology" to WHOLESALE_FAMILIES.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

SEARCH_URL = "https://notifications.careers.danone.com/api/jobs"

COUNTRY_IN_SCOPE = "France"

# Families kept wholesale (every in-scope-contract role), regardless of title.
WHOLESALE_FAMILIES = {"Data"}

# Contract type lives in the title prefix, not in the employment_type field.
CONTRACT_CDI_RE = re.compile(r"^\s*CDI\b", re.IGNORECASE)
CONTRACT_TEMP_RE = re.compile(
    r"^\s*(?:ALTERNANCE|AP+RENT\w*|CDD|STAGE|INTERNSHIP|"
    r"V\.?I\.?E\.?|VIP|GRADUATE\s+PROGRAM|SUMMER|TH[EÈ]SE|THESIS)\b",
    re.IGNORECASE,
)
# employment_type values that mean "not permanent" when the title gives no prefix.
TEMPORARY_EMPLOYMENT_TYPES = {"TEMPORARY", "CONTRACT_TO_HIRE"}

# Data & AI title predicate (EN + FR). Word-bounded on the short/ambiguous tokens
# (AI/IA/ML/BI) so they don't match inside unrelated words.
DATA_AI_RE = re.compile(
    r"\b(?:data|donn[eé]es|analytics|analytique|analyst|"
    r"AI|A\.I\.|IA|artificial intelligence|intelligence artificielle|"
    r"machine learning|ML|deep learning|mlops|llm|genai|"
    r"algorithm|algorithme|data scien|big data|business intelligence|DDAI)\b",
    re.IGNORECASE,
)

PAGE_SIZE = 100
MAX_PAGES = 10          # defensive cap; France is ~125 postings (2 pages) today
REQUEST_DELAY_SECONDS = 1.0
REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
}


@dataclass
class Job:
    native_job_id: str          # req_id, e.g. "23602" (also the apply-URL segment)
    title: str
    location: str               # full_location, e.g. "Gif-Sur-Yvette, France"
    category: str | None        # categories[0].name, e.g. "Research & Innovation"
    apply_url: str              # iCIMS apply page
    employment_type: str        # derived contract label: "CDI" / "Permanent"
    description: str | None = None
    posted_date: str | None = None    # YYYY-MM-DD (from posted_date, time trimmed)
    identifier: str | None = None      # = native_job_id
    raw_payload: dict | None = None


def _is_permanent(doc: dict) -> bool:
    """CDI / permanent by Danone's title-prefix convention (see docstring)."""
    title = doc.get("title") or ""
    if CONTRACT_CDI_RE.match(title):
        return True
    if CONTRACT_TEMP_RE.match(title):
        return False
    # No contract prefix: permanent unless the hours field flags it temporary.
    return str(doc.get("employment_type")) not in TEMPORARY_EMPLOYMENT_TYPES


def _families(doc: dict) -> list[str]:
    return [
        (c.get("name") or "").strip()
        for c in (doc.get("categories") or [])
        if isinstance(c, dict)
    ]


def _in_scope(doc: dict) -> bool:
    if not _is_permanent(doc):
        return False
    if any(f in WHOLESALE_FAMILIES for f in _families(doc)):
        return True
    return bool(DATA_AI_RE.search(doc.get("title") or ""))


def _contract_label(doc: dict) -> str:
    return "CDI" if CONTRACT_CDI_RE.match(doc.get("title") or "") else "Permanent"


def _strip_html(fragment: str | None) -> str | None:
    if not fragment:
        return None
    text = BeautifulSoup(fragment, "html.parser").get_text(" ", strip=True)
    return text or None


def _description(doc: dict) -> str | None:
    # `description` is the full posting; responsibilities/qualifications add the
    # "what you'll do" / "your background" sections when present.
    sections = [
        _strip_html(doc.get(key))
        for key in ("description", "responsibilities", "qualifications")
    ]
    joined = "\n\n".join(s for s in sections if s)
    return joined or None


def _posted_date(doc: dict) -> str | None:
    raw = doc.get("posted_date")
    if isinstance(raw, str) and len(raw) >= 10:
        return raw[:10]
    return None


def _location(doc: dict) -> str:
    loc = (doc.get("full_location") or "").strip()
    if loc:
        return loc
    city = (doc.get("city") or "").strip()
    country = (doc.get("country") or "").strip()
    return ", ".join(p for p in (city, country) if p)


def _doc_to_job(doc: dict) -> Job:
    job_id = doc.get("req_id") or doc.get("slug")
    if not job_id:
        raise RuntimeError(f"Danone doc missing req_id/slug (title={doc.get('title')!r})")
    job_id = str(job_id)

    apply_url = (doc.get("apply_url") or "").strip()
    if not apply_url:
        raise RuntimeError(f"Danone doc {job_id} has no apply_url")

    families = _families(doc)
    return Job(
        native_job_id=job_id,
        title=(doc.get("title") or "").strip(),
        location=_location(doc),
        category=(families[0] if families else None),
        apply_url=apply_url,
        employment_type=_contract_label(doc),
        description=_description(doc),
        posted_date=_posted_date(doc),
        identifier=job_id,
        raw_payload=doc,
    )


def _fetch_page(session: requests.Session, page: int) -> dict:
    params = {"country": COUNTRY_IN_SCOPE, "page": page, "limit": PAGE_SIZE}
    print(f"  GET /api/jobs country=France page={page} ...", flush=True)
    response = session.get(
        SEARCH_URL, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload.get("jobs"), list) or "totalCount" not in payload:
        # Unexpected shape means the API changed — abort rather than return a
        # partial/empty result that would close every open Danone row.
        raise RuntimeError(
            f"jobs payload has no jobs/totalCount (keys={sorted(payload.keys())!r})"
        )
    return payload


def _fetch_all_docs(session: requests.Session) -> list[dict]:
    docs: list[dict] = []
    total: int | None = None
    for page in range(1, MAX_PAGES + 1):
        if page > 1:
            time.sleep(REQUEST_DELAY_SECONDS)
        payload = _fetch_page(session, page)
        total = payload["totalCount"]
        batch = [j.get("data", {}) for j in payload["jobs"]]
        docs.extend(batch)
        print(f"    +{len(batch)} docs ({len(docs)}/{total})", flush=True)
        if not batch or len(docs) >= total:
            break
    else:
        raise RuntimeError(
            f"pagination did not converge after {MAX_PAGES} pages "
            f"({len(docs)}/{total} docs) — refusing partial result"
        )
    if total is not None and len(docs) < total:
        raise RuntimeError(
            f"collected {len(docs)} docs but totalCount={total} — "
            f"refusing partial result"
        )
    return docs


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Listing phase (single country=France query)...", flush=True)
    docs = _fetch_all_docs(session)

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
            f"  {job.native_job_id} [{job.category} | {job.employment_type}] "
            f"{job.title!r} -> KEEP",
            flush=True,
        )

    elapsed = time.time() - started
    print(
        f"\n  -> {len(kept)} jobs kept of {len(docs)} France postings "
        f"in {elapsed:.1f}s\n",
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
