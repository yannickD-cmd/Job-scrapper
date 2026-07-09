"""Pernod Ricard job scraper — France, tech/data/AI roles (all employment types).

Pernod Ricard's single global board is hosted on Workday at
pernodricard.wd3.myworkdayjobs.com/pernod-ricard. Workday exposes the usual
public JSON CXS API:

  POST /wday/cxs/pernodricard/pernod-ricard/jobs       (listing)
  GET  /wday/cxs/pernodricard/pernod-ricard<externalPath>   (detail)

WHY THIS SCRAPER IS *UNFACETED* (the whole story — read before "optimising"):
  The obvious approach is the site's own filtered query (the UI URL carries
  `jobFamilyGroup=<Tech>&jobFamilyGroup=<IT>&locationCountry=<France>`), which
  returns ~30 clean rows. That filtered query is a FACETED POST (non-empty
  `appliedFacets`), and *that endpoint is metered by a tiny, escalating token
  bucket* (~3 tokens, slow refill, far more hostile to programmatic/datacenter
  callers). It 400s with an empty-message `HTTP_400` for hours once drained, and
  in-run retries only deepen the ban — it failed in CI and locally for days.
  Diagnosis proved the request itself is fine; it is purely the throttle.

  The UNFACETED POST (`appliedFacets: {}`) is on a SEPARATE, far looser budget:
  it returns 200 with the full catalog even moments after a faceted 400. So we
  crawl the whole board unfaceted and filter CLIENT-SIDE. Heavier over the wire
  (~17 pages of 20), but it never touches the endpoint that fails. This is the
  ban-proof rewrite; do NOT reintroduce `appliedFacets` filters.

  Honest-request note: the unfaceted endpoint needs NO browser disguise — a bare
  request (default UA, no Referer/Origin/locale header) returns 200. We therefore
  send only a polite, project-identifying User-Agent + JSON headers. No cookie
  tricks, no stealth TLS, no proxy. (`cookies.clear()` is kept purely because a
  stale Cloudflare cookie can score a fresh fingerprint worse — not to hide.)

Scope (client-side, off the listing + a detail confirm for country):
  - Country     : France. A row is France if its listing `bulletFields` carry
                  the name "France" (or a French city as fallback). Rows with NO
                  country in the listing (a handful of internships/talent pools)
                  are confirmed via the detail's `country.descriptor` before we
                  keep them — that recovers real FR roles ("Alternance IT Support")
                  without letting through country-less non-FR ones ("… Austria").
  - Relevance   : `scrapers._relevance.is_tech_role(title)` — the shared
                  data/AI/software/cyber/cloud title predicate. Replaces the lost
                  jobFamilyGroup facet (family is absent from listing rows AND the
                  detail, so title is the only lever). Yields ~13, matching the
                  old faceted "Tech + Information Technology · France" output.
  - Employment  : ALL types kept (CDI/CDD/alternance/stage/VIE). Off-facet there
                  is no reliable contract signal (the detail only carries
                  `timeType: "Full time"`), and the goal is coverage over
                  precision — so we keep everything and label best-effort from the
                  title. See feedback_include_data_adjacent_ai_roles.

Robustness contract (goal: jobs flowing, no false-closes):
  - The LISTING crawl is the integrity anchor. If any page fails after a couple
    of gentle retries, we ABORT (raise) — a *partial* listing would make
    db.persist_run_results retire every France/tech row it didn't see this run
    (the empty-guard only fires on a fully-empty return). See
    feedback_partial_scrape_false_close.
  - The DETAIL fetch is pure ENRICHMENT (description, ISO posted_date, clean
    location). The detail GET is NOT throttled. For a row already confirmed
    France off the listing, a detail failure does NOT drop or abort — we emit the
    job from listing fields alone. Only the country-*unknown* candidates depend on
    the detail; if theirs fails we skip just that one (we can't confirm France).
  - jobReqId ("JR-053956") is the clean native id; the listing's bulletFields
    also carry it, so we always have a native_job_id even without the detail.
"""
from __future__ import annotations

import html
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

from scrapers._relevance import is_tech_role

TENANT = "pernodricard"
SITE = "pernod-ricard"
HOST = f"https://{TENANT}.wd3.myworkdayjobs.com"
LIST_URL = f"{HOST}/wday/cxs/{TENANT}/{SITE}/jobs"
DETAIL_URL_TEMPLATE = f"{HOST}/wday/cxs/{TENANT}/{SITE}{{external_path}}"

# Honest, minimal headers. A bare request (proven) 200s on the unfaceted endpoint,
# so we add nothing that mimics a browser — just a UA that names the project.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
    "Content-Type": "application/json",
}

FRANCE_COUNTRY_NAME = "France"

# Bare city listings (no "France" bulletField, e.g. some internships) — match a
# French city in locationsText as a fallback. Same technique as Alan's scraper.
FRENCH_CITY_TOKENS = {
    "paris", "lyon", "bordeaux", "marseille", "nantes", "lille", "toulouse",
    "nice", "strasbourg", "montpellier", "rennes", "grenoble", "nancy",
    "cognac", "reims", "epernay", "valence", "caen", "thuir", "perpignan",
    "la londe", "lormont", "rouillac", "bouzy", "chalon-sur-saone",
}
# Word-boundary matcher so a token never matches inside a longer foreign name
# (e.g. "nice" must not match "Venice", "paris" not inside another word).
_FRENCH_CITY_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(c) for c in sorted(FRENCH_CITY_TOKENS)) + r")\b")

PAGE_SIZE = 20                   # Workday hard-caps `limit` at 20 (limit>20 -> 400)
MAX_PAGES = 30                   # defensive: ~17 pages for the current catalog
REQUEST_TIMEOUT = 30
LISTING_PAGE_DELAY_SECONDS = 3.0   # between unfaceted listing pages (be polite)
DETAIL_DELAY_SECONDS = 1.5         # between detail GETs (not throttled)

# The unfaceted endpoint is on a loose budget, so a couple of gentle retries on a
# transient blip are safe here (unlike the faceted endpoint we deliberately avoid).
LISTING_RETRY_BACKOFFS: tuple[float, ...] = (20.0, 40.0)
DETAIL_RETRY_BACKOFF_SECONDS = 20.0
DETAIL_MAX_RETRIES = 3

JR_RE = re.compile(r"\bJR[-\d]+\b")


@dataclass
class Job:
    native_job_id: str          # jobReqId, e.g. "JR-053956"
    title: str
    location: str
    category: str | None        # coarse family derived from the title
    apply_url: str
    employment_type: str        # best-effort from title (CDI/CDD/Alternance/Stage/VIE)
    description: str | None = None
    posted_date: str | None = None   # YYYY-MM-DD from detail's startDate (else None)
    identifier: str | None = None    # detail's jobPostingInfo.id (internal hash)
    raw_payload: dict | None = None


def _deburr(s: str | None) -> str:
    """Lowercase + strip diacritics so accent/no-accent spellings match one pattern."""
    nfkd = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _clean_description(content: str | None) -> str | None:
    if not content:
        return None
    text = BeautifulSoup(html.unescape(content), "html.parser").get_text(" ", strip=True)
    return text or None


def _native_job_id_from_listing(row: dict) -> str | None:
    """The JR id is the last "JR-…" token in bulletFields, e.g. "JR-053956"."""
    for field in reversed(row.get("bulletFields") or []):
        if isinstance(field, str) and JR_RE.fullmatch(field.strip()):
            return field.strip()
    path = row.get("externalPath") or ""
    m = re.search(r"_(JR-\d+)", path)
    return m.group(1) if m else None


def _listing_country_name(row: dict) -> str | None:
    """First bulletField that isn't the JR id — the plain country name, or None.

    bulletFields is ["France", "JR-…"] on most rows and ["JR-…"] (no country) on a
    few talent-pool/internship rows.
    """
    for field in row.get("bulletFields") or []:
        if isinstance(field, str) and field.strip() and not JR_RE.fullmatch(field.strip()):
            return field.strip()
    return None


def _row_france_state(row: dict) -> str:
    """"FR" | "OTHER" | "UNKNOWN" from the listing alone."""
    country = _listing_country_name(row)
    if country == FRANCE_COUNTRY_NAME:
        return "FR"
    if country:
        # Explicit non-France country — never override it on a city name (a city
        # substring like "nice"⊂"Venice" would wrongly flag an Italian row).
        return "OTHER"
    # No country in the listing: a French-city token (word-boundary) flags
    # likely-France. Country-less rows are still detail-confirmed downstream.
    if _FRENCH_CITY_RE.search(_deburr(row.get("locationsText"))):
        return "FR"
    return "UNKNOWN"


def _category_from_title(title: str) -> str:
    t = _deburr(title)
    if re.search(r"\bdata\b|donnee|\bai\b|\bia\b|\bml\b|machine learning|analyt|"
                 r"scientist|intelligence artif", t):
        return "Data & AI"
    if re.search(r"cyber|security|securite|\bsoc\b", t):
        return "Cybersecurity"
    if re.search(r"software|logiciel|develop|architect|\bsap\b|cloud|infra|"
                 r"platform|devops|salesforce", t):
        return "Software & IT"
    return "Tech"


def _employment_type_from_title(title: str) -> str:
    t = _deburr(title)
    if re.search(r"\bstage\b|stagiaire|internship|\bintern\b", t):
        return "Stage"
    if re.search(r"alternance|apprentice|apprentissage", t):
        return "Alternance"
    if re.search(r"\bvie\b|\bv\.i\.e\b", t):
        return "VIE"
    if re.search(r"\bcdd\b|fixed.?term|temporary", t):
        return "CDD"
    return "CDI"


def _new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def _is_error_payload(data: object) -> bool:
    return isinstance(data, dict) and "errorCode" in data


def _post_listing(session: requests.Session, offset: int) -> dict:
    """One unfaceted listing page, with gentle retries (loose budget)."""
    body = {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset, "searchText": ""}
    last_err: str | None = None
    for attempt in range(1 + len(LISTING_RETRY_BACKOFFS)):
        session.cookies.clear()
        response = session.post(LIST_URL, json=body, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            if not _is_error_payload(data):
                return data
            last_err = f"error payload {data.get('errorCode')}"
        else:
            last_err = f"HTTP {response.status_code}: {response.text[:200]}"
        if attempt < len(LISTING_RETRY_BACKOFFS):
            backoff = LISTING_RETRY_BACKOFFS[attempt]
            print(f"    listing offset {offset} attempt {attempt + 1} failed "
                  f"({last_err}); retrying in {backoff:.0f}s", flush=True)
            time.sleep(backoff)
    raise requests.HTTPError(f"unfaceted listing failed at offset {offset}: {last_err}")


def _crawl_listing(session: requests.Session) -> list[dict]:
    """Full unfaceted catalog. Aborts (raises) on any page failure — a partial
    crawl would false-close the rows we didn't see."""
    rows: list[dict] = []
    total: int | None = None
    total_seen = False
    reached_end = False
    offset = 0
    for _page in range(MAX_PAGES):
        payload = _post_listing(session, offset)
        if not total_seen:
            raw_total = payload.get("total")   # Workday sends `total` on page 1 only
            total = int(raw_total) if isinstance(raw_total, (int, float)) else None
            total_seen = True
        batch = payload.get("jobPostings") or []
        rows.extend(batch)
        if not batch:
            reached_end = True                 # server has no more rows to give
            break
        if total is not None and len(rows) >= total:
            reached_end = True
            break
        offset += len(batch)
        time.sleep(LISTING_PAGE_DELAY_SECONDS)
    print(f"  crawled {len(rows)}/{total if total is not None else '?'} rows unfaceted", flush=True)
    # Fail CLOSED on any doubt about completeness: a partial return would
    # false-close every row we didn't see (the empty-return guard only covers []).
    # NB: a falsy `total` must NOT double as "done" — that collapsed both the
    # loop terminator and this guard at once (review finding). We only trust
    # completeness via an explicit `total` OR a natural empty-page end.
    if total is not None and len(rows) < total:
        raise requests.HTTPError(
            f"incomplete unfaceted crawl ({len(rows)}/{total}) — aborting to avoid false-close")
    if total is None and not reached_end:
        raise requests.HTTPError(
            "unfaceted listing gave no `total` and never reached a natural end "
            f"(stopped at {len(rows)} rows / MAX_PAGES) — refusing a partial that would false-close")
    return rows


def _get_detail(session: requests.Session, external_path: str) -> dict:
    url = DETAIL_URL_TEMPLATE.format(external_path=external_path)
    last_err: str | None = None
    for attempt in range(1, DETAIL_MAX_RETRIES + 1):
        session.cookies.clear()
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            if not _is_error_payload(data):
                return data
            last_err = f"error payload {data.get('errorCode')}"
        elif response.status_code == 404:
            raise requests.HTTPError(f"detail 404 for {external_path}", response=response)
        else:
            last_err = f"HTTP {response.status_code}: {response.text[:200]}"
        if attempt < DETAIL_MAX_RETRIES:
            time.sleep(DETAIL_RETRY_BACKOFF_SECONDS)
    raise requests.HTTPError(f"detail failed for {external_path}: {last_err}")


def _job_from_listing(row: dict) -> Job | None:
    """Build a Job from listing fields alone (all MUST fields guaranteed)."""
    native_job_id = _native_job_id_from_listing(row)
    if not native_job_id:
        return None
    ext = row.get("externalPath") or ""
    apply_url = f"{HOST}/{SITE}{ext}" if ext else ""
    if not apply_url:
        return None
    title = (row.get("title") or "").strip()
    return Job(
        native_job_id=native_job_id,
        title=title,
        location=(row.get("locationsText") or "").strip(),
        category=_category_from_title(title),
        apply_url=apply_url,
        employment_type=_employment_type_from_title(title),
        raw_payload={"listing": row},
    )


def _detail_country_is_france(detail: dict) -> bool:
    info = detail.get("jobPostingInfo") or {}
    for path in (info.get("country") or {}, (info.get("jobRequisitionLocation") or {}).get("country") or {}):
        if isinstance(path, dict) and (path.get("descriptor") or "").strip() == FRANCE_COUNTRY_NAME:
            return True
    return False


def _enrich_from_detail(job: Job, detail: dict) -> None:
    """Overlay the richer detail fields onto a listing-built Job (best effort)."""
    info = detail.get("jobPostingInfo") or {}
    if info.get("jobReqId"):
        job.native_job_id = info["jobReqId"].strip()
    if info.get("externalUrl"):
        job.apply_url = info["externalUrl"].strip()
    if info.get("title"):
        job.title = info["title"].strip()
        job.category = _category_from_title(job.title)
        job.employment_type = _employment_type_from_title(job.title)
    location = (
        (info.get("location") or "").strip()
        or (info.get("jobRequisitionLocation") or {}).get("descriptor")
        or job.location
    )
    job.location = (location or "").strip()
    posted = info.get("startDate")
    if isinstance(posted, str) and len(posted) >= 10:
        job.posted_date = posted[:10]
    job.description = _clean_description(info.get("jobDescription"))
    job.identifier = info.get("id")
    job.raw_payload = {"listing": job.raw_payload.get("listing") if job.raw_payload else None,
                       "detail": info}


def scrape() -> list[dict]:
    session = _new_session()
    started = time.time()

    print("Listing phase (unfaceted crawl, France + tech-title client filter)...", flush=True)
    rows = _crawl_listing(session)

    # Candidates: in-scope by title, France-or-unknown by listing. Dedup by JR id.
    candidates: dict[str, tuple[dict, str]] = {}
    for row in rows:
        if not is_tech_role(row.get("title")):
            continue
        state = _row_france_state(row)
        if state == "OTHER":
            continue
        jr = _native_job_id_from_listing(row)
        if jr:
            candidates.setdefault(jr, (row, state))

    fr = sum(1 for _, s in candidates.values() if s == "FR")
    unknown = sum(1 for _, s in candidates.values() if s == "UNKNOWN")
    print(f"  -> {len(candidates)} tech candidates ({fr} France, {unknown} country-unknown)",
          flush=True)

    print("\nDetail phase (enrichment; country-confirm for the unknowns)...", flush=True)
    jobs: list[Job] = []
    for jr, (row, state) in candidates.items():
        job = _job_from_listing(row)
        if job is None:
            print(f"  skip {jr}: unusable listing row", flush=True)
            continue

        ext = row.get("externalPath") or ""
        detail: dict | None = None
        if ext:
            try:
                detail = _get_detail(session, ext)
            except requests.HTTPError as exc:
                status = getattr(exc.response, "status_code", None)
                if state == "UNKNOWN":
                    # Can't confirm France without the detail — skip this one only.
                    print(f"  {jr}: country-unknown and detail unavailable ({status or exc}); "
                          f"skipping", flush=True)
                    continue
                # Known-France row: enrichment is optional. Keep it from listing data.
                print(f"  {jr}: detail unavailable ({status or exc}); keeping listing-only",
                      flush=True)
                jobs.append(job)
                continue
            time.sleep(DETAIL_DELAY_SECONDS)

        if state == "UNKNOWN":
            if not (detail and _detail_country_is_france(detail)):
                print(f"  {jr}: country-unknown resolved to non-France; dropping", flush=True)
                continue
        if detail:
            _enrich_from_detail(job, detail)
        jobs.append(job)

    elapsed = time.time() - started
    print(f"\n  -> {len(jobs)} France tech/data jobs in {elapsed:.1f}s\n", flush=True)
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
