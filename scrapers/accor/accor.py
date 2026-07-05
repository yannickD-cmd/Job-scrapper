"""Accor careers scraper — France, Tech & Digital + data-adjacent, Permanent (CDI).

Accor runs the **Attrax** ATS (careers.accor.com, served through an Azure Front
Door CDN). The job list is plain server-rendered HTML:

    https://careers.accor.com/global/en/jobs?options=<facetIds>&page=<n>&ln=&la=0&lo=0&lr=1&li=

`options` is a comma list of Attrax facet-value IDs; within one facet group the
IDs OR, across groups they AND. Pagination is JS-rendered (there are no page
links in the HTML), so we walk page=1,2,3… until a page returns zero vacancy
tiles. NB: Attrax's *unfiltered* enumeration is unstable — a bare France+Permanent
crawl shifts page count run-to-run (38↔58 pages) and silently caps around 300,
dropping jobs that a facet query proves exist. So we only ever issue *narrow*
queries (facet + keyword), which are stable and complete (the category pass
returns the same 28 every run); we never rely on a full unfiltered crawl. A single
retry on an empty page guards against a transient mid-sequence blank truncating a
pass (which would false-close the missing rows). Each page holds 12
`div.attrax-vacancy-tile` cards; every card already
carries native_job_id (`data-jobid`), title, apply_url AND the job category,
location, contract type and Attrax GUID. So category/contract filtering needs no
detail fetch — the detail page is read only for `description` + `posted_date`,
from its schema.org/JobPosting JSON-LD block.

Scope (locked with the user):
  - Country  : France            -> facet 405
  - Contract : Permanent (CDI)   -> facet 191
  - Families : the three tech/data categories, kept wholesale —
        Tech & Digital                          -> 1343
        Digital Products, IT, Data & Analytics  ->  271
        Product Design, IT & Data Analysis      ->  259
  - Data-adjacent net: Accor files virtually all of its Data/AI roles into the
    three categories above, but the occasional stray (e.g. a gender-notation
    variant of a data role tagged to e-Commerce/Marketing) lands in a non-tech
    family. A light keyword pass (France+Permanent + q=<data term>) catches those,
    gated to genuine data/AI TITLES so it does not drag in the rest of the
    corporate feed. This honours the user's "Tech & Digital + data-adjacent" scope.

Facet IDs come from the filter checkboxes' `data-option-id` / addFilterOptionId().
To change scope, edit the FACET_* / *_OPTIONS / DATA_ADJACENT_QUERIES constants.
"""
from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

HOST = "https://careers.accor.com"
LISTING_PATH = "/global/en/jobs"

# --- Attrax facet-value IDs (from the filter checkboxes' data-option-id) -------
FACET_FRANCE = "405"
FACET_PERMANENT = "191"
FACET_TECH_CATEGORIES = ("1343", "271", "259")  # Tech&Digital, DigitalProducts, ProductDesign

# France AND Permanent AND (any of the three tech/data categories).
CATEGORY_OPTIONS = ",".join((FACET_FRANCE, FACET_PERMANENT, *FACET_TECH_CATEGORIES))
# France AND Permanent, category-free — combined with q= for the data-adjacent net.
BASE_OPTIONS = ",".join((FACET_FRANCE, FACET_PERMANENT))

# Free-text terms for the data-adjacent net (FR + EN). Results are title-gated below.
DATA_ADJACENT_QUERIES = ("data", "analytics", "intelligence artificielle")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_DELAY_SECONDS = 1.5
REQUEST_TIMEOUT = 30
MAX_PAGES = 30  # defensive: category pass is ~3 pages, each q-pass ~1-2.


# --- data/AI TITLE gate for the data-adjacent net -----------------------------
# Narrow on purpose: only genuine data/AI titles, NOT all-tech. The three tech
# categories already carry the Software/IT/design roles; this net exists solely
# to recover a data/AI role mis-filed under a non-tech family. Matched on a
# deburred (accent-stripped, lowercased) title so FR/EN spellings share a pattern.
def _deburr(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


_DATA_AI_TITLE = re.compile("|".join((
    r"\bdata\b", r"donnee", r"datavi[sz]",
    r"\banalytics?\b", r"\banalytique", r"business intelligence", r"\bbi\b",
    r"decisionn?el",
    r"\bia\b", r"\bai\b", r"intelligence artificielle", r"artificial intelligence",
    r"machine learning", r"deep learning", r"\bml\b", r"\bmlops\b", r"\bnlp\b",
    r"\bllms?\b", r"generativ",
    r"data ?(?:scien|engineer|analy|architec|steward|lake|ops|warehouse|platform)",
    r"statisti",  # matches EN 'statistic*' AND FR 'statistique(s)' (deburred c->q break)
    r"snowflake", r"databricks", r"\bdbt\b",
)))


# Veto the accounting sense of "analytique" (comptabilité/gestion analytique =
# cost accounting / management control, a non-tech finance family) so the net
# does not admit finance rows whose JD merely mentions "data".
_DATA_AI_EXCLUDE = re.compile("|".join((
    r"comptab\w* analytique", r"analytique comptable", r"gestion analytique",
)))


def _is_data_ai_title(title: str | None) -> bool:
    if not title:
        return False
    t = _deburr(title)
    if _DATA_AI_EXCLUDE.search(t):
        return False
    return bool(_DATA_AI_TITLE.search(t))


@dataclass
class Job:
    native_job_id: str
    title: str
    apply_url: str
    # Present already in the listing tile:
    location: str | None = None
    category: str | None = None
    employment_type: str | None = None   # contract type, e.g. "Permanent"
    schedule: str | None = None          # e.g. "Full-Time"
    experience_level: str | None = None
    identifier: str | None = None        # Attrax GUID (also in JSON-LD)
    # Filled by detail-page enrichment:
    description: str | None = None
    posted_date: str | None = None
    raw_payload: dict | None = None


def _tile_field(tile, name: str) -> str | None:
    el = (tile.select_one(f".attrax-vacancy-tile__{name}-valueset")
          or tile.select_one(f".attrax-vacancy-tile__{name}-value"))
    text = el.get_text(" ", strip=True) if el else None
    return text or None


def _parse_tile(tile) -> Job | None:
    job_id = (tile.get("data-jobid") or "").strip()
    anchor = tile.select_one("a.attrax-vacancy-tile__title")
    if not job_id or anchor is None:
        return None

    title = anchor.get_text(" ", strip=True)
    href = anchor.get("href") or ""
    apply_url = HOST + href if href.startswith("/") else href

    return Job(
        native_job_id=job_id,
        title=title,
        apply_url=apply_url,
        location=_tile_field(tile, "option-locations"),
        category=_tile_field(tile, "option-job-category"),
        employment_type=_tile_field(tile, "option-job-type"),
        schedule=_tile_field(tile, "option-job-schedule"),
        experience_level=_tile_field(tile, "option-experience-level"),
        identifier=_tile_field(tile, "reference"),
    )


def _fetch(session: requests.Session, options: str, page: int, q: str = "") -> str:
    qs = f"options={options}&page={page}&ln=&la=0&lo=0&lr=1&li="
    if q:
        qs += "&q=" + quote(q)
    url = f"{HOST}{LISTING_PATH}?{qs}"
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    response.encoding = "utf-8"  # page is UTF-8; header may omit charset
    return response.text


def _fetch_page(session: requests.Session, options: str, page: int, q: str):
    """Return (tiles, parsed_jobs) for one listing page."""
    html = _fetch(session, options, page, q)
    tiles = BeautifulSoup(html, "html.parser").select("div.attrax-vacancy-tile")
    page_jobs = [j for j in (_parse_tile(t) for t in tiles) if j is not None]
    return tiles, page_jobs


def _listing_pass(session: requests.Session, options: str, q: str = "") -> list[Job]:
    """Walk every page of one filtered listing until a page yields no tiles."""
    collected: list[Job] = []
    for page in range(1, MAX_PAGES + 1):
        tiles, page_jobs = _fetch_page(session, options, page, q)
        if not tiles:
            # No tiles: likely end-of-results, but Attrax pagination can transiently
            # return a blank page mid-sequence. Retry once so a blip doesn't truncate
            # the pass and false-close the rows it dropped.
            time.sleep(REQUEST_DELAY_SECONDS)
            tiles, page_jobs = _fetch_page(session, options, page, q)
            if not tiles:
                break
        # Tiles present but some didn't parse to a Job => markup drift. A silent drop
        # here would return a non-empty partial and false-close the unparsed rows, so
        # abort loudly instead (per the "abort on partial listing" rule).
        if len(page_jobs) < len(tiles):
            raise RuntimeError(
                f"Accor listing markup drift on page {page} (q={q!r}): "
                f"{len(tiles)} vacancy tiles but only {len(page_jobs)} parsed. "
                f"Aborting to avoid false-closing the {len(tiles) - len(page_jobs)} "
                f"unparsed row(s); the tile parser needs updating."
            )
        collected.extend(page_jobs)
        label = f"q={q!r} " if q else ""
        print(f"  {label}page {page}: {len(page_jobs)} tiles", flush=True)
        if page < MAX_PAGES:
            time.sleep(REQUEST_DELAY_SECONDS)
    return collected


def _parse_jobposting(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    return None


def _iso_date(raw: str | None) -> str | None:
    """'2026-07-01T10:46:57+00:00' -> '2026-07-01'. None/garbage -> None."""
    if not raw:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    return m.group(1) if m else None


def _enrich(session: requests.Session, job: Job) -> bool:
    """Fetch the detail page, add description + posted_date. Returns True on success.

    A failure here does NOT drop the job: category/contract were already decided
    from the listing tile, so we still return the row (with description=None) to
    avoid a partial scrape false-closing DB rows.
    """
    response = session.get(job.apply_url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    response.encoding = "utf-8"

    data = _parse_jobposting(response.text)
    if not data:
        return False

    job.description = data.get("description")
    job.posted_date = _iso_date(data.get("datePosted"))
    if not job.identifier:
        ident = data.get("identifier")
        if isinstance(ident, dict):
            job.identifier = ident.get("value")
    if job.raw_payload is None:
        job.raw_payload = {}
    job.raw_payload["jsonld"] = data
    return True


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    jobs: dict[str, Job] = {}  # dedup by native_job_id

    # --- Pass 1: the three tech/data categories, kept wholesale ---------------
    print("Category pass (Tech & Digital + Data families)...", flush=True)
    for job in _listing_pass(session, CATEGORY_OPTIONS):
        jobs.setdefault(job.native_job_id, job)
    print(f"  → {len(jobs)} unique tech/data jobs\n", flush=True)

    # --- Pass 2: data-adjacent net across all other families ------------------
    print("Data-adjacent pass (data/AI titles in any family)...", flush=True)
    added = 0
    for q in DATA_ADJACENT_QUERIES:
        for job in _listing_pass(session, BASE_OPTIONS, q=q):
            if job.native_job_id in jobs:
                continue
            if not _is_data_ai_title(job.title):
                continue
            jobs[job.native_job_id] = job
            added += 1
            print(f"    + [{job.native_job_id}] {job.title} <{job.category}>", flush=True)
        time.sleep(REQUEST_DELAY_SECONDS)
    print(f"  → {added} extra data/AI jobs from non-tech families\n", flush=True)

    # --- Pass 3: detail-page enrichment (description + posted_date) -----------
    everything = list(jobs.values())
    # snapshot the listing fields into raw_payload for forensics (survives enrich fail)
    for job in everything:
        job.raw_payload = {"listing": {
            "location": job.location, "category": job.category,
            "employment_type": job.employment_type, "schedule": job.schedule,
            "experience_level": job.experience_level, "reference": job.identifier,
        }}

    print(f"Enrichment: fetching {len(everything)} detail pages "
          f"(~{int(len(everything) * REQUEST_DELAY_SECONDS)}s)...", flush=True)
    enriched = failed = 0
    for i, job in enumerate(everything, 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            ok = _enrich(session, job)
        except Exception as exc:
            print(f"  [{i}/{len(everything)}] {job.native_job_id} FAILED: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            failed += 1
            continue
        if ok:
            enriched += 1
        else:
            print(f"  [{i}/{len(everything)}] {job.native_job_id} no JSON-LD", flush=True)
            failed += 1

    print(f"\nEnriched {enriched}/{len(everything)} "
          f"({failed} without detail; kept anyway)\n", flush=True)

    return [asdict(j) for j in everything]


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
    print(f"=== {len(results)} jobs final (runtime {elapsed:.1f}s) ===\n")
    for j in results:
        desc = BeautifulSoup(j["description"] or "", "html.parser").get_text(" ", strip=True)
        desc = desc[:180] + ("…" if len(desc) > 180 else "")
        print(f"[{j['native_job_id']}] {j['title']}")
        print(f"  Category : {j['category']}")
        print(f"  Type     : {j['employment_type']} / {j['schedule']}")
        print(f"  Location : {j['location']}")
        print(f"  Posted   : {j['posted_date']}")
        print(f"  Apply    : {j['apply_url']}")
        print(f"  Desc     : {desc}")
        print()
