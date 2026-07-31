"""OVHcloud job scraper — France, tech families (IT/Cloud/SRE/Data/AI/Software).

OVHcloud's careers site (careers.ovhcloud.com) runs on **SAP SuccessFactors
Career Site Builder** (jobs2web / Recruiting-Marketing), the same stack as the
`sap` scraper — NOT the Radancy/TalentBrew guess. (The US-only JazzHR board at
ovhus.applytojob.com is a separate, out-of-scope system and is ignored.) Plain
`requests` works from CI — no Cloudflare/Akamai challenge — so this stays in the
CI matrix.

The board is small: ~72 open reqs worldwide today, ~52 in France. OVHcloud uses
the **tile** layout (not the SAP table layout), and every field we need is on the
tile itself, so scope can be decided without any detail-page fetch:

    a.jobTitle-link ............. title + apply href (native id = numeric segment)
    section-field customfield1 .. contract type ("CDI" / "CDD" / "Stage" / ...)
    section-field department .... OVHcloud's job-family taxonomy (the category)
    section-field multilocation . "CITY, FR, 59100 · CITY, FR, 75017 · ..."

The `/search/` endpoint's own country facet is unreliable (passing
`optionsFacetsDD_country=France` returns only ~10 of the ~52 FR roles), and the
tile endpoint returns *all* rows from `startrow` onward in one response. So, like
the VINCI/Veolia Radancy scrapers, we full-crawl the whole board and filter
client-side rather than trusting a server facet.

Scope (France + Data/AI/Software/Cloud/Infra/SRE/Cyber), driven by the
**department** facet first:

* OVHcloud has one clean tech bucket, **"IT, Technologie & Produit"**, that holds
  the entire core in-scope set (SRE, Software Engineer, Cloud/OpenStack/Kubernetes,
  AI, Product). It is kept **wholesale** — some of its titles ("Product Manager
  Block & File Services") carry no tech keyword and would be lost to a title
  filter, which is exactly why we gate on the category, not the title
  (feedback_prefer_platform_category_over_is_tech_role).
* Every OTHER department (Industrie, Finance, Marketing, RH, ...) is a non-tech
  bucket that occasionally hides a data/AI/IT role ("Finance Data Analyst",
  "Stage - Data Scientist", datacenter "Technicien … informatique"). Those are
  rescued with the shared title predicate `is_tech_role` (scrapers/_relevance.py),
  err-inclusive on data/AI per feedback_include_data_adjacent_ai_roles.

Employment type is NOT filtered: the user wants AI/data-adjacent internships /
apprenticeships kept alongside CDI, and the department+title gate already drops
the non-tech stages/alternances. `employment_type` is recorded, not gated.

Country gate = the tile's location country code: a job is French if any of its
`multilocation` segments carries the ISO code "FR" (multi-country reqs that list
a French site are kept — they are open in France).

Two-pass scrape:

1. LISTING. Walk `/search/` by `startrow`; parse every tile. Dedup by native id.
2. FILTER + ENRICH. Keep France ∩ (tech department ∨ is_tech_role(title)); then
   fetch each survivor's detail page for the full description
   (`data-careersite-propertyid="description"`). A detail fetch that 404s drops
   only that row; any other fetch error ABORTS the run so db.persist_run_results
   never false-closes rows we couldn't confirm (feedback_partial_scrape_false_close).

To change scope, edit _is_tech_department / the country logic.
"""
from __future__ import annotations

import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

from scrapers._relevance import is_tech_role

HOST = "https://careers.ovhcloud.com"
SEARCH_URL = f"{HOST}/search/"
LOCALE = "fr_FR"

PAGE_SIZE = 25          # SF CSB step; the tile endpoint returns all-from-startrow
MAX_PAGES = 40          # defensive cap: 1000 rows; the board is ~72 today

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

REQUEST_DELAY_SECONDS = 1.0       # JSON-ish listing endpoint
DETAIL_DELAY_SECONDS = 2.0        # HTML detail pages
REQUEST_TIMEOUT = 30

_JOB_ID_RE = re.compile(r"/job/[^/]+/(\d+)/?$")
_TOTAL_RE = re.compile(r"([\d.,\s]+?)\s+offres", re.I)


@dataclass
class Job:
    native_job_id: str
    title: str
    apply_url: str
    location: str = ""
    category: str | None = None            # OVHcloud department / job family
    employment_type: str | None = None     # customfield1: CDI / CDD / Stage / ...
    # Filled by detail-page enrichment:
    description: str | None = None
    posted_date: str | None = None         # not exposed by this SF instance
    identifier: str | None = None
    raw_payload: dict | None = None


def _deburr(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").lower()


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _tile_field(soup: BeautifulSoup, job_id: str, field: str) -> str | None:
    """Read a tile's labelled value span by its stable element id."""
    el = soup.find(id=f"job-{job_id}-desktop-section-{field}-value")
    if el is None:
        return None
    return _clean(el.get_text(" ", strip=True)) or None


def _parse_listing_page(html: str) -> tuple[list[Job], int]:
    """Return (jobs on this page, total result count)."""
    soup = BeautifulSoup(html, "html.parser")

    jobs: list[Job] = []
    seen: set[str] = set()
    for anchor in soup.select("a.jobTitle-link[href^='/job/']"):
        href = anchor.get("href") or ""
        m = _JOB_ID_RE.search(href)
        if not m:
            continue
        job_id = m.group(1)
        if job_id in seen:                 # mobile + desktop anchors repeat
            continue
        seen.add(job_id)

        jobs.append(Job(
            native_job_id=job_id,
            title=_clean(anchor.get_text(" ", strip=True)),
            apply_url=HOST + href if href.startswith("/") else href,
            employment_type=_tile_field(soup, job_id, "customfield1"),
            category=_tile_field(soup, job_id, "department"),
            location=_tile_field(soup, job_id, "multilocation") or "",
        ))

    total = 0
    m = _TOTAL_RE.search(html)
    if m:
        digits = re.sub(r"[^\d]", "", m.group(1))
        if digits:
            total = int(digits)
    return jobs, total


def _is_french(location: str) -> bool:
    """France if any location segment carries the ISO country code 'FR'.

    multilocation reads 'CITY, FR, 59100 · CITY, GB, DA8 · ...'; we accept the
    row if any comma-segment is exactly 'FR' (or the text names France).
    """
    if not location:
        return False
    if "france" in _deburr(location):
        return True
    return any(seg.strip() == "FR" for seg in location.split(","))


def _is_tech_department(dept: str | None) -> bool:
    """OVHcloud's one clean tech family — kept wholesale (FR + EN spellings)."""
    if not dept:
        return False
    n = _deburr(dept)
    return n.startswith("it, techno") or (
        "technolog" in n and ("produit" in n or "product" in n)
    )


# Local rescue-path denylist: titles that clear the shared is_tech_role ALLOW-list
# on a substring collision but are unmistakably non-tech at OVHcloud. Kept local
# (not in scrapers/_relevance.py) to avoid touching the other ~50 scrapers; same
# pattern as Disney's ops-family denylist. Applied ONLY to the title-rescue branch
# so the wholesale tech-department keep and genuine data rescues are unaffected.
#   - "Legal Counsel …" matches ALLOW via "infrastructure"; English "counsel" is
#     not covered by the central legal exclude (juriste/avocat only).
#   - "Chargé de développement RH …" matches ALLOW via "développement"; pure HR.
_LOCAL_RESCUE_EXCLUDE = re.compile(r"\bcounsel\b|developpement rh")


def _in_scope(job: Job) -> bool:
    """Category-first: keep the tech department wholesale, rescue the rest by title."""
    if _is_tech_department(job.category):
        return True
    if _LOCAL_RESCUE_EXCLUDE.search(_deburr(job.title)):
        return False
    return is_tech_role(job.title)


def _crawl_listing(session: requests.Session) -> dict[str, Job]:
    print("Listing phase (full board)...", flush=True)
    all_listings: dict[str, Job] = {}      # dedup by native_job_id
    total = 0
    started = time.time()

    for page in range(MAX_PAGES):
        params = {"q": "", "startrow": page * PAGE_SIZE, "locale": LOCALE}
        response = session.get(SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        page_jobs, page_total = _parse_listing_page(response.text)
        if page_total:
            total = page_total
        new = 0
        for j in page_jobs:
            if j.native_job_id not in all_listings:
                all_listings[j.native_job_id] = j
                new += 1

        print(
            f"  startrow {page * PAGE_SIZE}: {len(page_jobs)} tiles, "
            f"+{new} new ({len(all_listings)}/{total or '?'} unique)",
            flush=True,
        )

        if not page_jobs or new == 0:
            break
        if total and len(all_listings) >= total:
            break
        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"  -> {len(all_listings)} postings in {time.time() - started:.1f}s\n",
          flush=True)
    return all_listings


def _fetch_detail(session: requests.Session, job: Job) -> str | None:
    """GET detail HTML. None on a genuine 404 (job removed); re-raise any other
    error after one retry so the caller can abort rather than return a partial set."""
    for attempt in (1, 2):
        try:
            response = session.get(job.apply_url, timeout=REQUEST_TIMEOUT)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.text
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(DETAIL_DELAY_SECONDS)
    return None


def _enrich(html: str, job: Job) -> None:
    """Fill description + confirm category/contract from the detail page tokens."""
    soup = BeautifulSoup(html, "html.parser")

    desc_el = soup.select_one('[data-careersite-propertyid="description"]')
    if desc_el:
        job.description = desc_el.get_text("\n", strip=True)

    dept = soup.select_one('[data-careersite-propertyid="department"]')
    if dept and _clean(dept.get_text(" ", strip=True)):
        job.category = _clean(dept.get_text(" ", strip=True))

    contract = soup.select_one('[data-careersite-propertyid="customfield1"]')
    if contract and _clean(contract.get_text(" ", strip=True)):
        job.employment_type = _clean(contract.get_text(" ", strip=True))

    loc = soup.select_one('[data-careersite-propertyid="location"]')
    detail_location = _clean(loc.get_text(" ", strip=True)) if loc else ""
    if detail_location:
        job.location = detail_location

    job.raw_payload = {
        "department": job.category,
        "employment_type": job.employment_type,
        "location": job.location,
    }


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    # ---- Phase 1: full listing crawl ----------------------------------------
    all_listings = _crawl_listing(session)

    # ---- Phase 2: client-side filter (France ∩ tech family) -----------------
    candidates = [
        j for j in all_listings.values()
        if _is_french(j.location) and _in_scope(j)
    ]
    fr_total = sum(1 for j in all_listings.values() if _is_french(j.location))
    print(
        f"Filter [country=France, tech dept OR is_tech_role]: "
        f"{len(candidates)} kept of {fr_total} France / {len(all_listings)} total\n",
        flush=True,
    )
    for j in candidates:
        gate = "dept" if _is_tech_department(j.category) else "title"
        print(f"  KEEP ({gate}) [{j.category}] ({j.employment_type}) "
              f"{j.title!r}", flush=True)
    print(flush=True)

    # ---- Phase 3: enrich survivors with the full description ----------------
    print(f"Enrichment phase: {len(candidates)} detail pages "
          f"(~{int(len(candidates) * DETAIL_DELAY_SECONDS)}s)...", flush=True)

    kept: list[Job] = []
    dropped_gone = 0
    for i, job in enumerate(candidates, 1):
        time.sleep(DETAIL_DELAY_SECONDS)
        html = _fetch_detail(session, job)      # non-404 errors propagate -> abort
        if html is None:
            dropped_gone += 1
            print(f"  [{i}/{len(candidates)}] {job.native_job_id} -> 404, drop",
                  flush=True)
            continue
        _enrich(html, job)
        kept.append(job)
        print(f"  [{i}/{len(candidates)}] {job.native_job_id} {job.title!r} -> keep",
              flush=True)

    print(flush=True)
    print(f"Done: kept {len(kept)}, dropped(404) {dropped_gone}", flush=True)

    return [asdict(j) for j in kept]


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    started = time.time()
    try:
        jobs = scrape()
    except Exception as exc:
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise

    print(f"\n=== {len(jobs)} jobs final "
          f"(total runtime {time.time() - started:.1f}s) ===\n")

    for j in jobs:
        desc = (j["description"] or "").strip().replace("\n", " ")
        desc = desc[:200] + ("…" if len(desc) > 200 else "")
        print(f"[{j['native_job_id']}] {j['title']}")
        print(f"  Department : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
