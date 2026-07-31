"""Valeo job scraper — France, tech families (IS + R&D-tech), all contract types.

Valeo's single global board is hosted on Workday at
valeo.wd3.myworkdayjobs.com/valeo_jobs. It exposes the usual public CXS JSON API:

  POST /wday/cxs/valeo/valeo_jobs/jobs            (listing, faceted)
  GET  /wday/cxs/valeo/valeo_jobs<externalPath>   (detail: desc + startDate)

Why this board needs BOTH a family facet AND a title gate:
  Valeo is an automotive-parts manufacturer, so the board is dominated by
  manufacturing / mechanical / quality / production / logistics / maintenance
  roles (Manufacturing 101, Quality 100, Logistics 93, Maintenance 78 globally).
  The `jobFamilyGroup` facet ("Job Category") gives two tech-bearing buckets:

    - "Information Systems"    — a CLEAN tech bucket (IT / Cyber / Cloud / Data
                                infra / sysadmin / PLM). Kept WHOLESALE. Its few
                                is_tech_role misses ("Administration des systèmes
                                d'informations", "Product Owner CAO / PLM") are
                                genuinely IT/tech-adjacent, so no title gate here
                                (feedback_prefer_platform_category_over_is_tech_role).

    - "Research & Development" — a MIXED bucket. It holds the software/AI/data
                                roles we want (valeo.ai research in Paris: Data
                                Engineer, Knowledge Graph Engineer, AI Adoption
                                Leader, C++/Python, 3D SDK) but is FULL of
                                physical-product eng (mechanical / optical /
                                optronic / test-&-validation / inverter design).
                                So we gate R&D rows with is_tech_role(title) to
                                drop the physical/mechanical junk — the same
                                problem the defense scrapers solve
                                (project_defense_physeng_junk).

Scope (locked):
  - Country     : France only (locationCountry facet — Valeo has a real one; the
                  France id is the Workday-global 54c5b6971ffb4bf0b116fe7651ec789a).
  - Families    : Information Systems (wholesale) + Research & Development (gated
                  by is_tech_role). Covers Data, AI/ML, Software/IT, Cloud/Infra/
                  SRE/DevOps, Cybersecurity, and data/AI-adjacent engineering.
  - Job type    : ALL contract types kept (CDI-inclusive; we deliberately keep
                  alternance / apprentissage / stage / VIE because the AI & data
                  roles here are heavily work-study — see
                  feedback_include_data_adjacent_ai_roles). Employment-type
                  filtering is dashboard-side (feedback_noise_filters_dashboard_only);
                  we only TAG each row's contract via a best-effort workerSubType
                  pass.

Recall safety (never false-close):
  - The recall-critical enumeration is the two per-family France listings with NO
    workerSubType filter — so we never miss (and thus false-close) a row of a
    contract type we didn't map. If a family listing fails we ABORT (raise); a
    partial return would let db.persist_run_results false-close the missing slice
    (feedback_partial_scrape_false_close).
  - The workerSubType tagging pass is pure enrichment: any failure there is
    swallowed and those rows just keep employment_type=None.
  - The only tolerated per-row drop is a detail 404 (the posting closed between
    the listing snapshot and the detail fetch).

Workday quirks (this tenant):
  - native_job_id = detail `jobReqId` (e.g. "REQ2026078035"), also in the
    listing's bulletFields[0]. Used for dedup.
  - The listing `externalPath` occasionally carries a posting-revision suffix
    (…_REQ...-1); the detail fetch MUST use the FULL externalPath.
  - `startDate` is already ISO YYYY-MM-DD — used directly as posted_date.
  - Detail `country.descriptor` == "France" and carries the clean city in
    `location` (+ `additionalLocations` for multi-location reqs).
  - CXS is UA-agnostic; we still run cookie-free with retries (standard
    Workday-behind-Cloudflare defence) so it stays CI-safe.
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

TENANT = "valeo"
SITE = "valeo_jobs"
HOST = f"https://{TENANT}.wd3.myworkdayjobs.com"
LIST_URL = f"{HOST}/wday/cxs/{TENANT}/{SITE}/jobs"
DETAIL_URL_TEMPLATE = f"{HOST}/wday/cxs/{TENANT}/{SITE}{{external_path}}"

# Workday-global France country id (same value across every tenant).
FILTER_COUNTRY_FRANCE = "54c5b6971ffb4bf0b116fe7651ec789a"

# jobFamilyGroup ("Job Category") labels we keep, and whether the family is a
# clean tech bucket (kept wholesale) or a mixed bucket (gated by is_tech_role).
# Matched by descriptor against the LIVE facet list so a rotated WID can't silently
# empty the scope — resolved in _resolve_families().
FAMILY_WHOLESALE = "Information Systems"     # clean tech bucket
FAMILY_TITLE_GATED = "Research & Development"  # mixed: software/AI/data + physical eng
WANTED_FAMILIES = {FAMILY_WHOLESALE, FAMILY_TITLE_GATED}

# native_job_id shape, e.g. "REQ2026078035". Validated (not trusted verbatim) so a
# listing-schema change can't collapse every row onto one dedup key.
REQ_ID_RE = re.compile(r"REQ\d+")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
    # English locale: Workday LOCALISES facet descriptors to Accept-Language, and
    # we match jobFamilyGroup / workerSubType by their English descriptors
    # ("Information Systems", "Regular", "Apprentice (Fixed Term)"…). With fr-FR
    # the facet labels come back French ("Systèmes d'information", "CDI"…) and the
    # matches silently fail. Job *titles* stay French either way (as authored).
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": HOST,
    "Referer": f"{HOST}/{SITE}",
}

PAGE_SIZE = 20
MAX_PAGES = 40  # per family — generous defensive cap (Valeo is a large ~900-FR board)
REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.2   # between requests (JSON API, ≥ 1.0s per playbook)
RETRY_BACKOFF_SECONDS = 15.0
MAX_RETRIES = 3


@dataclass
class Job:
    native_job_id: str          # jobReqId, e.g. "REQ2026078035"
    title: str
    location: str               # France-preferring location string
    category: str | None        # jobFamilyGroup label (IS or R&D)
    apply_url: str              # detail's externalUrl
    employment_type: str | None = None  # French contract label from workerSubType, or None
    description: str | None = None
    posted_date: str | None = None      # YYYY-MM-DD from detail's startDate
    identifier: str | None = None       # detail's jobPostingInfo.id (internal hash)
    raw_payload: dict | None = None


def _employment_label(descriptor: str) -> str | None:
    """Map a Workday workerSubType descriptor to a French contract label.

    Descriptor-based (not WID-based) so it survives WID rotation. Unknown types
    return None (the row is still kept — recall comes from the unfiltered family
    listings, tagging is pure enrichment)."""
    d = (descriptor or "").lower()
    if "regular" in d:
        return "CDI"
    if "apprentice" in d:
        return "Alternance"
    if "trainee" in d:
        return "Stage"
    if "vie" in d:
        return "VIE"
    if "consultant" in d:
        return "Consultant"
    if "fixed term" in d:
        return "CDD"
    return None


def _clean_description(content: str | None) -> str | None:
    if not content:
        return None
    text = BeautifulSoup(html.unescape(content), "html.parser").get_text(" ", strip=True)
    return text or None


def _is_error_payload(data: object) -> bool:
    return isinstance(data, dict) and "errorCode" in data


def _new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def _request_with_retry(
    session: requests.Session, method: str, url: str, json_body: dict | None = None
) -> dict:
    """POST/GET with linear-backoff retry. Raises on a terminal failure so the
    caller can ABORT (never returns a partial/empty payload silently). A 404 is
    re-raised immediately as an HTTPError carrying the response (caller drops the
    single row)."""
    last_err: str | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        # Cookie-free: Workday fronts CXS with Cloudflare; a __cf_bm cookie tagged
        # "suspicious" on a datacenter ASN (GitHub Actions) makes follow-up requests
        # keep failing, while cookie-free callers are scored fresh each time.
        session.cookies.clear()
        if method == "POST":
            response = session.post(url, json=json_body, timeout=REQUEST_TIMEOUT)
        else:
            response = session.get(url, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            if not _is_error_payload(data):
                return data
            last_err = f"error payload {data.get('errorCode')}"
        elif response.status_code == 404:
            raise requests.HTTPError(f"404 for {url}", response=response)
        else:
            last_err = f"HTTP {response.status_code}: {response.text[:160]}"
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS)
    raise requests.HTTPError(f"request failed ({url}): {last_err}")


def _resolve_families(session: requests.Session) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve, from a live France-only POST, the WIDs of our wanted jobFamilyGroup
    facets and the present workerSubType id->French-label map.

    Returns ({family_id: family_label}, {workersubtype_id: french_label}).
    Raises if neither wanted family is present (structural change worth hearing
    about)."""
    payload = _request_with_retry(
        session, "POST", LIST_URL,
        json_body={
            "appliedFacets": {"locationCountry": [FILTER_COUNTRY_FRANCE]},
            "limit": 1,
            "offset": 0,
            "searchText": "",
        },
    )

    family_ids: dict[str, str] = {}
    subtype_labels: dict[str, str] = {}
    for facet in payload.get("facets") or []:
        param = facet.get("facetParameter")
        if param == "jobFamilyGroup":
            for v in facet.get("values") or []:
                if (v.get("descriptor") or "") in WANTED_FAMILIES:
                    family_ids[v["id"]] = v["descriptor"]
        elif param == "workerSubType":
            for v in facet.get("values") or []:
                label = _employment_label(v.get("descriptor") or "")
                if label and (v.get("count") or 0) > 0:
                    subtype_labels[v["id"]] = label

    if not family_ids:
        raise RuntimeError(
            f"Valeo: none of the wanted jobFamilyGroup facets {WANTED_FAMILIES} "
            "found in France — Workday facet structure may have changed."
        )
    return family_ids, subtype_labels


def _listing_req_id(row: dict) -> str | None:
    """Public req id ("REQ..."), VALIDATED to the REQ<digits> shape so a listing
    reconfiguration can't hand back a shared value that collapses every row onto
    one dedup key (which would sail past the empty-return guard and false-close
    every other open Valeo row)."""
    bullets = row.get("bulletFields") or []
    if bullets and isinstance(bullets[0], str):
        candidate = bullets[0].strip()
        if REQ_ID_RE.fullmatch(candidate):
            return candidate
    m = re.search(r"_(REQ\d+)", row.get("externalPath") or "")
    return m.group(1) if m else None


def _post_family_page(session: requests.Session, family_id: str, offset: int) -> dict:
    body = {
        "appliedFacets": {
            "locationCountry": [FILTER_COUNTRY_FRANCE],
            "jobFamilyGroup": [family_id],
        },
        "limit": PAGE_SIZE,
        "offset": offset,
        "searchText": "",
    }
    return _request_with_retry(session, "POST", LIST_URL, json_body=body)


def _collect_family_rows(
    session: requests.Session, family_id: str, family_name: str
) -> list[dict]:
    """Fetch the full France listing for one family, paginating. Stamps each row
    with `_family_name`. Raises on any page failure (abort, don't partial-return)."""
    payload = _post_family_page(session, family_id, offset=0)
    total = int(payload.get("total") or 0)
    rows: list[dict] = list(payload.get("jobPostings") or [])

    page = 0
    while len(rows) < total and page < MAX_PAGES:
        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)
        payload = _post_family_page(session, family_id, offset=page * PAGE_SIZE)
        new = payload.get("jobPostings") or []
        if not new:
            break
        rows.extend(new)

    for r in rows:
        r["_family_name"] = family_name
    print(f"  {family_name}: {len(rows)}/{total} France rows", flush=True)
    return rows


def _keep_row(family_name: str, title: str) -> bool:
    """Category-first gate: wholesale-keep the clean IS bucket; gate the mixed R&D
    bucket with is_tech_role(title) to drop physical/mechanical eng."""
    if family_name == FAMILY_WHOLESALE:
        return True
    return is_tech_role(title)


def _build_employment_labels(
    session: requests.Session, subtype_labels: dict[str, str]
) -> dict[str, str]:
    """Best-effort: for each present workerSubType, enumerate France + that type
    (all families) and map its req ids -> French label. Any failure is swallowed
    (recall is guaranteed by the unfiltered family listings)."""
    out: dict[str, str] = {}
    for subtype_id, label in subtype_labels.items():
        try:
            offset = 0
            page = 0
            while page < MAX_PAGES:
                time.sleep(REQUEST_DELAY_SECONDS)
                payload = _request_with_retry(
                    session, "POST", LIST_URL,
                    json_body={
                        "appliedFacets": {
                            "locationCountry": [FILTER_COUNTRY_FRANCE],
                            "workerSubType": [subtype_id],
                        },
                        "limit": PAGE_SIZE,
                        "offset": offset,
                        "searchText": "",
                    },
                )
                rows = payload.get("jobPostings") or []
                if not rows:
                    break
                for r in rows:
                    rid = _listing_req_id(r)
                    if rid:
                        out[rid] = label
                total = int(payload.get("total") or 0)
                offset += PAGE_SIZE
                page += 1
                if offset >= total:
                    break
        except Exception as exc:
            print(
                f"  employment tag '{label}' failed "
                f"({type(exc).__name__}: {exc}); leaving those None",
                flush=True,
            )
            continue
    return out


def _france_location(info: dict, listing_row: dict) -> str:
    """France-preferring location string. locationCountry=France filtered these in,
    but on a multi-location req Workday's primary `location` may be the non-France
    city; leading with the French entry keeps web/filters.is_idf from dropping it."""
    locs = [info.get("location")] + list(info.get("additionalLocations") or [])
    locs = [l.strip() for l in locs if isinstance(l, str) and l.strip()]
    locs = list(dict.fromkeys(locs))
    if not locs:
        return (listing_row.get("locationsText") or "").strip()
    return "; ".join(locs)


def _row_to_job(listing_row: dict, detail: dict, employment_type: str | None) -> Job:
    info = detail.get("jobPostingInfo") or {}
    if not info:
        raise RuntimeError(f"Valeo detail missing jobPostingInfo: {detail!r}")

    native_job_id = (
        (info.get("jobReqId") or "").strip()
        or _listing_req_id(listing_row)
        or ""
    )
    if not native_job_id:
        raise RuntimeError(f"Valeo row missing req id: {listing_row!r}")

    apply_url = (info.get("externalUrl") or "").strip()
    if not apply_url:
        ext = listing_row.get("externalPath") or ""
        apply_url = f"{HOST}/{SITE}{ext}" if ext else ""
    if not apply_url:
        raise RuntimeError(f"Valeo detail missing externalUrl: {info!r}")

    posted = info.get("startDate")
    posted_date = posted[:10] if isinstance(posted, str) and len(posted) >= 10 else None
    title = (info.get("title") or listing_row.get("title") or "").strip()

    return Job(
        native_job_id=native_job_id,
        title=title,
        location=_france_location(info, listing_row),
        category=listing_row.get("_family_name") or None,
        apply_url=apply_url,
        employment_type=employment_type,
        description=_clean_description(info.get("jobDescription")),
        posted_date=posted_date,
        identifier=info.get("id"),
        raw_payload={"listing": listing_row, "detail": info},
    )


def scrape() -> list[dict]:
    session = _new_session()
    started = time.time()

    family_ids, subtype_labels = _resolve_families(session)
    print(
        f"Resolved families={list(family_ids.values())}; "
        f"contract types present={sorted(set(subtype_labels.values()))}",
        flush=True,
    )

    # Recall-critical: enumerate each wanted family fully (no contract filter). A
    # per-family failure ABORTS (never partial-return -> never false-close).
    print("Listing phase (per family, France)...", flush=True)
    all_rows: list[dict] = []
    for family_id, family_name in family_ids.items():
        rows = _collect_family_rows(session, family_id, family_name)
        all_rows.extend(rows)
        time.sleep(REQUEST_DELAY_SECONDS)

    # Category-first gate, then dedup by req id (keep first-seen family).
    by_id: dict[str, dict] = {}
    for r in all_rows:
        rid = _listing_req_id(r)
        if not rid:
            continue
        if not _keep_row(r.get("_family_name") or "", r.get("title") or ""):
            continue
        by_id.setdefault(rid, r)
    print(f"  -> {len(by_id)} in-scope France tech roles after title gate", flush=True)

    # Best-effort employment-type tagging (never a recall risk — see docstring).
    labels = _build_employment_labels(session, subtype_labels)

    print("\nDetail phase...", flush=True)
    jobs: list[Job] = []
    for rid, row in by_id.items():
        ext = row.get("externalPath") or ""
        if not ext:
            print(f"  skip {rid}: missing externalPath", flush=True)
            continue
        try:
            detail = _request_with_retry(
                session, "GET", DETAIL_URL_TEMPLATE.format(external_path=ext)
            )
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            if status == 404:
                # Removed since the listing snapshot — drop this single row (it will
                # close, which is correct: it's genuinely gone).
                print(f"  {rid}: detail 404, dropping (job removed)", flush=True)
                continue
            # Any other detail failure means the result would be incomplete;
            # returning a partial list would false-close the dropped rows. Abort.
            raise
        jobs.append(_row_to_job(row, detail, labels.get(rid)))
        time.sleep(REQUEST_DELAY_SECONDS)

    elapsed = time.time() - started
    print(f"  -> {len(jobs)} jobs in {elapsed:.1f}s\n", flush=True)
    return [asdict(j) for j in jobs]


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
