"""Hermès job scraper — France, tech (Data/AI + Software/IT/Digital), CDI only.

Hermès's careers site (https://talents.hermes.com/fr-FR/sites/CX) is an
**Oracle Cloud Recruiting (ORC) Candidate Experience** SPA. Its public JSON API
answers plain `requests` with a polite UA — no auth, no cookies, no warm-up,
UA-agnostic — so it's CI-safe. Two endpoints, both under the Fusion host:

  LISTING  /hcmRestApi/resources/latest/recruitingCEJobRequisitions
           ?finder=findReqs;siteNumber=CX_12001,limit=...,offset=...
           -> requisitionList[]: Id, Title, PostedDate, PrimaryLocation,
              PrimaryLocationCountry. NOTE: JobFamily / JobFunction / ContractType
              are all null in the listing — the useful category & contract fields
              only exist on the detail record.

  DETAIL   /hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails
           ?finder=ById;Id=<Id>,siteNumber=CX_12001&expand=all
           -> JobFunction (the clean tech signal), RequisitionType (the contract),
              Category (French label), ExternalDescriptionStr (full HTML JD).

Scope (locked 2026-07-04): France · tech (Data/AI + Software/IT/Digital) · CDI.

Why this shape (non-obvious, see comments below):

  * Country gate — `PrimaryLocationCountry == "FR"`, client-side after a full
    board crawl (817 reqs, ~544 FR). A whole-board paginated crawl + client
    filter means a facet-loop bug can never produce the partial-result
    false-close problem (feedback_partial_scrape_false_close).

  * Tech gate — this is a LUXURY house: "développement / développeur" in a title
    is overwhelmingly *product/craft* development (cuir, pièces métalliques,
    haute joaillerie, formulation parfum, prototypiste, CNC), NOT software. A
    title-only predicate (_relevance.is_tech_role) is ~25% craft false positives
    here (and the craft is filed under Production / Supply Chain, not just under
    one "craft" family, so a keyword exclude list can't catch it). The ATS *does*
    separate tech from craft cleanly, but only on the detail record's filing:
        JobFunction "Systèmes d'Informations" / Category "SI - ..."  -> KEEP
        JobFunction "Digital"                 / Category "Digital -" -> KEEP
        any other family (Production, Supply Chain, Développement-Innovation, ...)
                                                                    -> DROP
    So the final tech decision is the ATS category, per
    feedback_prefer_platform_category_over_is_tech_role. The CATEGORIES search
    facet is capped at 10 values (Hermès has ~15 categories: three SI sub-cats +
    Digital don't all fit), so the SI/Digital reqs can't be enumerated
    server-side — the category has to be read off each detail. A few genuinely-
    tech reqs are left fully unfiled (JobFunction AND Category both null, e.g.
    "Lead AI Security", "DevSecOps"); those are recovered by is_tech_role(title).
    Crucially the title fallback fires ONLY for unfiled reqs — a role Hermès filed
    under Production/Supply is never re-admitted by its title.

  * Contract gate — `RequisitionType == "CDI"` on the detail. The title *prefix*
    ("CDI - ", "STAGE - ", "CDD - ", "ALTERNANCE - ") is only a hint; we use it
    to skip detail fetches for the obviously-temporary reqs (CDI scope), then
    confirm CDI on the detail. ContractType / WorkerType listing fields are null.

Cost: the tech category is detail-only and can't be enumerated server-side, so we
fetch a detail for every non-temporary France req (~350) and let the detail decide
— no title pre-filter, because one would silently drop SI/Digital reqs with
unconventional titles (recall matters more than a few extra requests here). At
REQUEST_DELAY_SECONDS spacing that is a few minutes per run; fine for a scheduled
matrix job. Most fetched details are discarded (retail/production/finance); that
waste buys complete recall.

Yield today is ~40 roles. Small is expected for a house whose core is craft and
retail; the count grows when Hermès posts an SI/Digital CDI in France.
"""
from __future__ import annotations

import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

from .._relevance import is_tech_role

HOST = "https://fa-eoic-saasfaprod1.fa.ocs.oraclecloud.com"
LIST_URL = f"{HOST}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
DETAIL_URL = f"{HOST}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
VANITY_JOB_URL = "https://talents.hermes.com/fr-FR/sites/CX/job/{job_id}"

SITE_NUMBER = "CX_12001"
COUNTRY_IN_SCOPE = "FR"          # PrimaryLocationCountry code for France

PAGE_SIZE = 200
MAX_PAGES = 20                   # defensive cap; board is ~817 reqs (5 pages) today
REQUEST_DELAY_SECONDS = 1.0      # JSON API — polite spacing between requests
REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
}

# The in-scope tech bucket = the "Systèmes d'Informations" (SI) and "Digital"
# families. Both are exposed twice on the detail record — as JobFunction (a coarse
# label, sometimes null) and as Category (a finer "SI - <sub>" / "Digital - <sub>"
# label, populated on some reqs that have a null JobFunction). We test both and
# keep the union, so a req filed under either wins. Values are deburred first.
TECH_FUNCTIONS = {"systemes d'informations", "digital"}
TECH_CATEGORY_PREFIXES = ("si -", "digital -", "digital-")

# Contract type lives in the title prefix as a hint (detail RequisitionType is the
# source of truth). Dropping obviously-temporary reqs before spending a detail
# fetch is recall-safe under the CDI scope — a STAGE/CDD/ALTERNANCE-prefixed req
# is never a CDI, and RequisitionType confirms CDI on the survivors anyway.
TEMP_TITLE_RE = re.compile(
    r"^\s*(?:STAGE|STAGIAIRE|ALTERNAN|APPRENT|CDD|V\.?I\.?E\.?|"
    r"GRADUATE|SUMMER|TH[EÈ]SE|THESIS|INTERNSHIP)\b",
    re.IGNORECASE,
)

# Recovery for the handful of genuinely-tech reqs Hermès leaves *fully unfiled*
# (both JobFunction and Category null) whose title carries no is_tech_role keyword
# but an unambiguous French IT token, e.g. "Référent Solutions SI Fabrication".
# Case-sensitive on the raw title: standalone uppercase "SI" is Systèmes
# d'Information; lowercase "si" is the French word "if". Only consulted for
# unfiled reqs, so it can't re-admit a filed non-tech role.
UNFILED_RECOVERY_RE = re.compile(r"\bSI\b|\bDSI\b|\bERP\b")


@dataclass
class Job:
    native_job_id: str          # ORC requisition Id, e.g. "22572"
    title: str
    location: str               # PrimaryLocation, e.g. "PANTIN, Île-de-France, France"
    category: str | None        # detail Category (FR), e.g. "SI - Support et Opérations"
    apply_url: str              # candidate deep-link
    employment_type: str        # RequisitionType, e.g. "CDI"
    description: str | None = None
    posted_date: str | None = None    # YYYY-MM-DD (listing PostedDate)
    identifier: str | None = None      # = native_job_id
    raw_payload: dict | None = None


def _deburr(s: str | None) -> str:
    """Lowercase + strip diacritics so accented JobFunction labels match ASCII."""
    nfkd = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def _is_cdi_candidate_title(title: str) -> bool:
    """Not obviously temporary — worth a detail fetch under the CDI scope."""
    return not TEMP_TITLE_RE.match(title or "")


def _is_si_or_digital(label: str) -> bool:
    """True if a deburred JobFunction/Category label is the SI or Digital family."""
    return label in TECH_FUNCTIONS or label.startswith(TECH_CATEGORY_PREFIXES)


def _keep(detail: dict, title: str) -> bool:
    """Final in-scope decision, from the DETAIL record.

    CDI only, and tech by the ATS's own filing (JobFunction / Category):

      * SI or Digital in either field  -> keep wholesale (this IS the user's
        "IT / Digital" scope; includes MOA/ERP/functional IT roles).
      * filed under any OTHER family (Production, Supply Chain, Retail, Finance,
        Développement-Innovation-craft, ...) -> drop, even if the title matches
        is_tech_role. A "Développeur Peaux Précieuses" (Supply Chain) or
        "Programmeur régleur CNC" (Production) is not software — trust the filing.
      * fully unfiled (both fields null) -> keep only when the TITLE is
        unambiguously tech (is_tech_role) or carries a bare FR IT token
        (UNFILED_RECOVERY_RE). This recovers the null-JobFunction tech reqs
        (Lead AI Security, DevSecOps, Data Integration) without a title gamble on
        the filed ones.
    """
    if (detail.get("RequisitionType") or "").strip().upper() != "CDI":
        return False
    jf = _deburr(detail.get("JobFunction"))
    cat = _deburr(detail.get("Category"))
    if _is_si_or_digital(jf) or _is_si_or_digital(cat):
        return True
    if jf or cat:                       # filed under a non-tech family -> drop
        return False
    return is_tech_role(title) or bool(UNFILED_RECOVERY_RE.search(title or ""))


def _strip_html(fragment: str | None) -> str | None:
    if not fragment:
        return None
    text = BeautifulSoup(fragment, "html.parser").get_text(" ", strip=True)
    return text or None


def _description(detail: dict) -> str | None:
    sections = [
        _strip_html(detail.get(key))
        for key in (
            "ExternalDescriptionStr",
            "ExternalResponsibilitiesStr",
            "ExternalQualificationsStr",
        )
    ]
    joined = "\n\n".join(s for s in sections if s)
    return joined or None


def _posted_date(req: dict) -> str | None:
    raw = req.get("PostedDate")
    if isinstance(raw, str) and len(raw) >= 10:
        return raw[:10]
    return None


def _fetch_listing_page(session: requests.Session, offset: int) -> dict:
    params = {
        "onlyData": "true",
        "expand": "requisitionList.secondaryLocations",
        "finder": (
            f"findReqs;siteNumber={SITE_NUMBER},"
            f"limit={PAGE_SIZE},sortBy=POSTING_DATES_DESC,offset={offset}"
        ),
    }
    print(f"  GET listing offset={offset} ...", flush=True)
    response = session.get(
        LIST_URL, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise RuntimeError(f"listing payload has no items (keys={sorted(payload)!r})")
    search = items[0]
    if "requisitionList" not in search or "TotalJobsCount" not in search:
        # Shape changed — abort rather than return a partial/empty result that
        # would close every open Hermès row.
        raise RuntimeError(
            f"listing item missing requisitionList/TotalJobsCount "
            f"(keys={sorted(search)!r})"
        )
    return search


def _fetch_all_fr(session: requests.Session) -> list[dict]:
    """Crawl the whole board, keep France reqs. Aborts on non-convergence."""
    fr: list[dict] = []
    total: int | None = None
    collected = 0
    for page in range(MAX_PAGES):
        if page > 0:
            time.sleep(REQUEST_DELAY_SECONDS)
        search = _fetch_listing_page(session, offset=collected)
        total = search["TotalJobsCount"]
        batch = search["requisitionList"]
        collected += len(batch)
        fr.extend(r for r in batch if r.get("PrimaryLocationCountry") == COUNTRY_IN_SCOPE)
        print(f"    +{len(batch)} reqs ({collected}/{total}), FR so far {len(fr)}", flush=True)
        if not batch or collected >= total:
            break
    else:
        raise RuntimeError(
            f"pagination did not converge after {MAX_PAGES} pages "
            f"({collected}/{total}) — refusing partial result"
        )
    if total is not None and collected < total:
        raise RuntimeError(
            f"collected {collected} reqs but TotalJobsCount={total} — "
            f"refusing partial result"
        )
    return fr


def _fetch_detail(session: requests.Session, job_id: str) -> dict | None:
    """Fetch one requisition detail. Returns None only on 404 (req removed between
    listing and detail); any other error raises to abort the run — a partial
    return would false-close still-open rows (feedback_partial_scrape_false_close).
    """
    params = {
        "onlyData": "true",
        "expand": "all",
        "finder": f"ById;Id={job_id},siteNumber={SITE_NUMBER}",
    }
    response = session.get(
        DETAIL_URL, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT
    )
    if response.status_code == 404:
        print(f"    detail {job_id} -> 404 (gone), skipping", flush=True)
        return None
    response.raise_for_status()
    items = response.json().get("items")
    if not isinstance(items, list) or not items:
        raise RuntimeError(f"detail {job_id} has no items")
    return items[0]


def _to_job(req: dict, detail: dict) -> Job:
    job_id = str(req["Id"])
    return Job(
        native_job_id=job_id,
        title=(req.get("Title") or "").strip(),
        location=(req.get("PrimaryLocation") or "").strip(),
        category=(detail.get("Category") or "").strip() or None,
        apply_url=VANITY_JOB_URL.format(job_id=job_id),
        employment_type=(detail.get("RequisitionType") or "").strip() or None,
        description=_description(detail),
        posted_date=_posted_date(req),
        identifier=job_id,
        raw_payload={"listing": req, "detail": detail},
    )


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Listing phase (full board crawl, keep France)...", flush=True)
    fr = _fetch_all_fr(session)

    # Candidate set for detail fetch = every France req whose title is not
    # obviously temporary. We do NOT tech-filter on the title here: the tech
    # decision is the ATS's JobFunction/Category (detail-only), and a title net
    # would silently miss SI/Digital reqs with unconventional titles (Engineering
    # Manager, ServiceDesk, Référentiel Articles). Trading extra detail fetches
    # for complete recall is the right call for this board (see module docstring
    # and feedback_prefer_platform_category_over_is_tech_role / _include_data...).
    candidates = [r for r in fr if _is_cdi_candidate_title(r.get("Title") or "")]
    print(
        f"\nFrance reqs: {len(fr)} -> {len(candidates)} non-temporary candidates "
        f"to confirm via detail\n",
        flush=True,
    )

    print("Detail phase (confirm JobFunction + RequisitionType)...", flush=True)
    kept: dict[str, Job] = {}
    for req in candidates:
        job_id = str(req["Id"])
        if job_id in kept:
            continue
        time.sleep(REQUEST_DELAY_SECONDS)
        detail = _fetch_detail(session, job_id)
        if detail is None:
            continue
        title = req.get("Title") or ""
        if not _keep(detail, title):
            continue
        job = _to_job(req, detail)
        kept[job_id] = job
        print(
            f"  KEEP [{job_id}] ({detail.get('JobFunction')} | "
            f"{detail.get('RequisitionType')}) {job.title!r}",
            flush=True,
        )

    elapsed = time.time() - started
    print(
        f"\n  -> {len(kept)} jobs kept of {len(candidates)} candidates "
        f"({len(fr)} FR reqs) in {elapsed:.1f}s\n",
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
