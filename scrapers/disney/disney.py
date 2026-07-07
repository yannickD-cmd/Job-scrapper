"""Disney (The Walt Disney Company) job scraper — France, Data & AI + Software/IT, CDI.

emplois.disneycareers.com is Disney's French-language careers portal. It runs on
Radancy / TalentBrew (tenant company 17204) — the same ATS as VINCI and Veolia,
different skin. The board is EMEA/global rendered in French (~717 jobs across 34
countries); France is ~45 of them, almost all Disneyland Paris (hospitality,
culinary, park maintenance, show engineering) plus a small Paris EMEA corporate
office. Genuine Software/IT / Data roles in France are a handful.

Three shaping facts (all verified against the live board):

1. The AJAX listing endpoint `/search-jobs/results` IGNORES every filter query
   param (Keyword, Location, category/country facet ids) and always returns the
   full global list — exactly like VINCI/Veolia. Server-side facet filtering only
   works through an anti-forgery-token POST we can't reproduce headless. So we
   crawl the whole board (8 pages @ 100) and filter client-side.

2. The listing card carries title, date, brand ("Disneyland Paris") and location
   ("City, France") — but NO job family and NO contract type. The job family
   (the thing our scope is defined on) exists ONLY as a server-side facet, and it
   surfaces on the DETAIL page as `<meta name="job-category-ids" content="...">`.
   So category can't be read from the card; every France candidate needs a detail
   fetch. France is small enough (~45) that fetching all of them is cheap.

3. Contract type is NOT in schema.org `employmentType`. That field uses Disney's
   own vocabulary — "Apprenti" (alternance), "Trainee" (stage), "Classique"
   (regular) — and "Classique" covers BOTH CDI and CDD (a title-"CDD"
   Electrotechnicien also reads "Classique"). So employmentType only reliably
   flags alternance/stage; the CDI-vs-CDD split must come from the TITLE, where
   Disneyland Paris spells it out ("... - CDI", "... CDD", "SAISON ...").

Scope gate (user-locked: France · Data/AI + Software/IT · CDI):
  - PRIMARY: job family in {Technologie, Science et analyse des données} —
    wholesale. This cleanly isolates true software/data roles from the many
    Disneyland-Paris "Ingénieur" roles that are mechanical / ride / show
    engineering (filed under Ingénierie / Spectacles / Bâtiment), which are out of
    a data+software scope.
  - RESCUE: a genuine IT/data role that Disney misfiled into a corporate family
    (e.g. "Manager Systèmes Financiers SAP" filed under Finance) is recovered via
    is_tech_role(title). The rescue is suppressed for pure operational/service
    families (call-centre, restauration, hôtellerie, …) where is_tech_role can
    only false-positive — e.g. "Conseiller Clientèle … Trilingue IT-FR-AN" trips
    is_tech_role on the language code "IT" but is a call-centre role. See
    feedback_prefer_platform_category_over_is_tech_role: category is the primary
    gate; is_tech_role is only the miscategorisation safety net.

Three-pass scrape:

1. LISTING. Walk every page of the AJAX endpoint at 100/page. Each card yields
   native_job_id (Radancy numeric id, the dedup key), title, location, brand,
   date and the detail href.

2. CLIENT-SIDE FILTER (cheap, from card). Keep cards whose location mentions
   France (or a DROM).

3. ENRICHMENT + SCOPE/CDI GATE. Fetch each France detail page, read the
   job-category-ids meta + job-location-ids meta and the schema.org JobPosting
   JSON-LD (description, datePosted, req identifier, employmentType). Apply the
   category/rescue scope gate and the CDI gate. A detail fetch that fails for a
   non-404 reason ABORTS the run (a partial return would let
   db.persist_run_results false-close the rows we couldn't confirm — see
   feedback_partial_scrape_false_close).

To change scope, edit TECH_CATEGORY_IDS / DENY_RESCUE_CATEGORY_IDS / the CDI gate.
"""
from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from scrapers._relevance import is_tech_role

HOST = "https://emplois.disneycareers.com"
RESULTS_URL = f"{HOST}/search-jobs/results"

RECORDS_PER_PAGE = 100
MAX_PAGES = 40  # defensive cap; the board is ~8 pages at 100/page

# --- Job-family facet ids (facet-type 1). The full map is kept so the stored
#     `category` field is human-readable and scope stays editable by name. ------
CATEGORY_NAMES: dict[str, str] = {
    "1924": "Administration",
    "8221632": "Affaires juridiques et commerciales",
    "8260176": "Affaires publiques",
    "8221808": "Animation et effets visuels",
    "74041": "Architecture et Design",
    "8221648": "Bâtiment, construction et installations",
    "74044": "Centre d'appels",
    "74046": "Communication",
    "8204176": "Conception graphique",
    "1927": "Création",
    "4760": "Culinaire",
    "8221792": "Développement commercial et stratégie",
    "74511": "Édition",
    "60487": "Finance et comptabilité",
    "8204400": "Gestion de projet",
    "8315328": "Horticulture et paysage",
    "8221728": "Hôtel et complexes hôteliers",
    "21579": "Ingénierie",
    "8221696": "Jeu et interactif",
    "55913": "Logistique",
    "8221680": "Marketing et médias numériques",
    "74114": "Merchandising",
    "70122": "Opérations",
    "8221664": "Opérations du parc de loisirs",
    "8221712": "Opérations maritimes et de croisière",
    "74125": "Opérations Retail",
    "8014352": "Production",
    "74515": "Responsabilité sociale",
    "1955": "Ressources humaines",
    "74090": "Restauration",
    "8221776": "Science et analyse des données",
    "74514": "Sciences et Animaux",
    "28354": "Sécurité",
    "8221744": "Services de santé",
    "8204160": "Spectacles",
    "8233264": "Sports et loisirs",
    "8204464": "Talents",
    "26715": "Technologie",
    "1949": "Ventes",
}

# PRIMARY in-scope families — kept wholesale (these ARE the tech/data families).
TECH_CATEGORY_IDS: frozenset[str] = frozenset({
    "26715",    # Technologie
    "8221776",  # Science et analyse des données
})

# Operational / service families where an is_tech_role(title) rescue can only be a
# false positive (language codes, service words). Rescue is disabled for these.
# NB: Ingénierie / Spectacles / Bâtiment / creative families are deliberately NOT
# here — a genuine embedded/software/pipeline role can legitimately be filed under
# them, and is_tech_role's allow-list is precise enough to only catch those.
DENY_RESCUE_CATEGORY_IDS: frozenset[str] = frozenset({
    "74044",    # Centre d'appels  (source of the "IT-FR-AN" language-code hit)
    "4760",     # Culinaire
    "74090",    # Restauration
    "8221728",  # Hôtel et complexes hôteliers
    "8221664",  # Opérations du parc de loisirs
    "8221712",  # Opérations maritimes et de croisière
    "74125",    # Opérations Retail
    "8221744",  # Services de santé
    "8233264",  # Sports et loisirs
    "8315328",  # Horticulture et paysage
    "55913",    # Logistique
    "74514",    # Sciences et Animaux
})

# France country facet id (facet-type 2) — used as a belt-and-suspenders country
# confirm on the detail page's job-location-ids meta.
FRANCE_COUNTRY_ID = "3017382"

# Country gate on the listing card: France or a French overseas territory anywhere
# in the card's location text (cards can list several "City, Country" segments).
_FRENCH_COUNTRY_TOKENS = (
    "france",
    "reunion", "la reunion",
    "guadeloupe", "martinique", "guyane", "mayotte",
    "nouvelle-caledonie", "polynesie francaise", "polynesie",
    "saint-martin", "saint-barthelemy", "saint-pierre-et-miquelon",
    "wallis-et-futuna",
)

# Detail employmentType values that are unambiguously NOT permanent (Disney's own
# vocabulary). "Classique" is intentionally absent — it covers both CDI and CDD.
_NON_PERMANENT_EMPLOYMENT = ("apprenti", "trainee", "stage", "intern")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}
AJAX_HEADERS = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}

REQUEST_DELAY_SECONDS = 1.0       # JSON listing endpoint
DETAIL_DELAY_SECONDS = 2.0        # HTML detail pages
REQUEST_TIMEOUT = 30


@dataclass
class Job:
    native_job_id: str
    title: str
    location: str
    apply_url: str
    brand: str | None = None
    category: str | None = None
    employment_type: str | None = None
    # Filled by detail-page enrichment:
    description: str | None = None
    posted_date: str | None = None
    identifier: str | None = None
    raw_payload: dict | None = None


def _norm(text: str) -> str:
    """Lower-case, strip accents — for accent-insensitive matching."""
    decomposed = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").lower()


def _is_french(location: str) -> bool:
    n = _norm(location)
    return any(re.search(rf"\b{re.escape(tok)}\b", n) for tok in _FRENCH_COUNTRY_TOKENS)


def _normalize_date(raw: str | None) -> str | None:
    """Disney emits unpadded dates like '2026-7-3'. Pad to ISO YYYY-MM-DD."""
    if not raw:
        return None
    parts = raw.split("-")
    if len(parts) == 3:
        try:
            return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
        except ValueError:
            pass
    return raw


def _category_ids(raw: str) -> list[str]:
    """job-category-ids meta is a space-separated list of hierarchy chains like
    '8315328 8221648'; we only care about the leaf ids, which here are the ids
    themselves (Disney's category meta carries flat leaf ids, not chains)."""
    return [c for c in re.split(r"[\s]+", raw.strip()) if c]


def _in_scope(category_ids: list[str], title: str) -> bool:
    """PRIMARY: any tech/data family → keep. RESCUE: a tech-titled role misfiled
    into a corporate family → keep, unless it sits in a pure operational family
    where is_tech_role can only false-positive."""
    if any(cid in TECH_CATEGORY_IDS for cid in category_ids):
        return True
    if any(cid in DENY_RESCUE_CATEGORY_IDS for cid in category_ids):
        return False
    return is_tech_role(title)


def _is_cdi(title: str, employment_type: str | None) -> bool:
    """CDI gate. employmentType reliably rules OUT alternance/stage; the CDI-vs-CDD
    split lives in the title (Disneyland Paris spells it out)."""
    et = _norm(employment_type or "")
    if any(tok in et for tok in _NON_PERMANENT_EMPLOYMENT):
        return False
    t = _norm(title)
    if re.search(r"\b(stage|stagiaire|alternance|alternant|apprenti|apprentissage|vie)\b", t):
        return False
    if "saison" in t:  # "SAISON 2026 …" / saisonnier → seasonal CDD
        return False
    has_cdd = re.search(r"\bcdd\b", t)
    has_cdi = re.search(r"\bcdi\b", t)
    if has_cdd and not has_cdi:  # pure CDD; "CDI/CDD" (can be CDI) stays in
        return False
    return True


def _listing_params(page: int) -> str:
    return urlencode({
        "CurrentPage": page,
        "RecordsPerPage": RECORDS_PER_PAGE,
        "IsPagination": "True",
        "SearchResultsModuleName": "Search Results",
        "SortCriteria": 0,
        "SortDirection": 0,
        "Keyword": "",
        "Location": "",
    })


def _parse_listing_page(results_html: str) -> tuple[list[Job], int]:
    soup = BeautifulSoup(results_html, "html.parser")

    jobs: list[Job] = []
    for anchor in soup.select("a[data-job-id]"):
        job_id = (anchor.get("data-job-id") or "").strip()
        h2 = anchor.find("h2")
        if not job_id or h2 is None:
            # The chevron / "view offer" anchor also carries data-job-id but has
            # no <h2>; skip it so each job is counted once.
            continue

        row = anchor.find_parent("tr")
        loc_el = row.select_one(".job-location") if row else None
        brand_el = row.select_one(".job-brand") if row else None

        href = anchor.get("href") or ""
        apply_url = HOST + href if href.startswith("/") else href

        jobs.append(Job(
            native_job_id=job_id,
            title=h2.get_text(" ", strip=True),
            location=loc_el.get_text(" ", strip=True) if loc_el else "",
            brand=brand_el.get_text(" ", strip=True) if brand_el else None,
            apply_url=apply_url,
        ))

    total_pages = 1
    section = soup.select_one("[data-total-pages]")
    if section:
        try:
            total_pages = int(section["data-total-pages"])
        except (TypeError, ValueError, KeyError):
            pass

    return jobs, total_pages


def _crawl_listing(session: requests.Session) -> dict[str, Job]:
    print("Listing phase...", flush=True)
    all_listings: dict[str, Job] = {}  # dedup by native_job_id
    page = 1
    total_pages = 1
    started = time.time()

    while page <= total_pages and page <= MAX_PAGES:
        url = f"{RESULTS_URL}?{_listing_params(page)}"
        response = session.get(url, headers=AJAX_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        results_html = (response.json() or {}).get("results") or ""
        page_jobs, total_pages = _parse_listing_page(results_html)
        for j in page_jobs:
            all_listings.setdefault(j.native_job_id, j)

        print(
            f"  page {page}/{total_pages}: {len(page_jobs)} jobs "
            f"({len(all_listings)} unique so far)",
            flush=True,
        )

        page += 1
        if page <= total_pages and page <= MAX_PAGES:
            time.sleep(REQUEST_DELAY_SECONDS)

    print(f"  → {len(all_listings)} unique jobs in {time.time() - started:.1f}s\n",
          flush=True)
    return all_listings


def _fetch_detail(session: requests.Session, job: Job) -> str | None:
    """GET the detail HTML. Returns None only on a genuine 404 (job removed);
    re-raises any other error after one retry so the caller can abort."""
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


def _meta(html: str, name: str) -> str:
    m = re.search(rf'<meta\s+name="{re.escape(name)}"\s+content="([^"]*)"', html)
    return m.group(1) if m else ""


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    # --- Phase 1: listing --------------------------------------------------
    all_listings = _crawl_listing(session)

    # --- Phase 2: client-side France filter (cheap, from card) -------------
    candidates = [j for j in all_listings.values() if _is_french(j.location)]
    print(f"Filter [country=France]: {len(candidates)}/{len(all_listings)} "
          f"France candidates\n", flush=True)

    # --- Phase 3: enrichment + scope + CDI gate ----------------------------
    print(
        f"Enrichment phase: fetching {len(candidates)} detail pages "
        f"(~{int(len(candidates) * DETAIL_DELAY_SECONDS)}s)...",
        flush=True,
    )

    kept: list[Job] = []
    dropped_scope = dropped_contract = dropped_foreign = dropped_gone = 0

    for i, job in enumerate(candidates, 1):
        time.sleep(DETAIL_DELAY_SECONDS)
        html = _fetch_detail(session, job)  # non-404 errors propagate -> abort
        if html is None:
            dropped_gone += 1
            print(f"  [{i}/{len(candidates)}] {job.native_job_id} → 404, drop",
                  flush=True)
            continue

        category_ids = _category_ids(_meta(html, "job-category-ids"))
        location_ids = _meta(html, "job-location-ids")
        job.category = " / ".join(
            CATEGORY_NAMES.get(cid, cid) for cid in category_ids
        ) or None

        payload = _parse_jobposting(html)
        if payload:
            job.description = payload.get("description")
            job.posted_date = _normalize_date(payload.get("datePosted"))
            job.identifier = payload.get("identifier")
            job.employment_type = payload.get("employmentType")
            job.raw_payload = {
                **payload,
                "job_category_ids": category_ids,
                "job_location_ids": location_ids,
            }

        # Belt-and-suspenders country confirm: if the detail explicitly lists
        # location ids and France's id isn't among them, the card was mislabelled.
        if location_ids and FRANCE_COUNTRY_ID not in location_ids:
            dropped_foreign += 1
            print(f"  [{i}/{len(candidates)}] {job.native_job_id} {job.title!r} "
                  f"→ drop (detail country not France)", flush=True)
            continue

        if not _in_scope(category_ids, job.title):
            dropped_scope += 1
            print(f"  [{i}/{len(candidates)}] {job.identifier or job.native_job_id} "
                  f"{job.title!r} → drop (out of scope: {job.category})", flush=True)
            continue

        if not _is_cdi(job.title, job.employment_type):
            dropped_contract += 1
            print(f"  [{i}/{len(candidates)}] {job.identifier or job.native_job_id} "
                  f"{job.title!r} → drop (not CDI: {job.employment_type})", flush=True)
            continue

        job.employment_type = "CDI"
        kept.append(job)
        print(f"  [{i}/{len(candidates)}] {job.identifier or job.native_job_id} "
              f"{job.title!r} → keep [{job.category}]", flush=True)

    print(flush=True)
    print(f"Enrichment: kept {len(kept)}, dropped(scope) {dropped_scope}, "
          f"dropped(contract) {dropped_contract}, dropped(foreign) {dropped_foreign}, "
          f"dropped(404) {dropped_gone}", flush=True)

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

    elapsed = time.time() - started
    print(f"\n=== {len(jobs)} jobs final (total runtime {elapsed:.1f}s) ===\n")

    for j in jobs:
        desc = BeautifulSoup(j["description"] or "", "html.parser").get_text(" ", strip=True)
        desc = desc[:200] + ("…" if len(desc) > 200 else "")
        print(f"[{j['identifier'] or j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Brand      : {j['brand']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
