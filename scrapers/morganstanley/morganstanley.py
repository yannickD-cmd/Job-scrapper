"""Morgan Stanley job scraper — France, tech-adjacent roles (incl. Ops & Strats).

Morgan Stanley's public careers funnel is a chain of redirects:

    morganstanley.com/people                    -> marketing landing page
    morganstanley.com/careers/career-opportunities-search
                                                 -> in-house search (now DEAD:
       its /web/career_services/.../resultset.json API returns totalResults=0)
    -> everything routes to Eightfold AI:  morganstanley.eightfold.ai

The location-metadata file the old page loads
(`/content/dam/msdotcom/appdata/filter-metadata-location.json`) confirms every
region (Americas / EMEA / APAC / "All Others") now points at
`morganstanley.eightfold.ai/careers?source=mscom`. So the real board is Eightfold.

Eightfold here runs its newer **PCSX** career site (the page embeds a
`pcsxConfig`), which DISABLES the classic public `/api/apply/v2/jobs` endpoint
(it 403s "Not authorized for PCSX"). The live search is instead:

    GET https://morganstanley.eightfold.ai/api/pcsx/search
        ?domain=morganstanley.com
        &query=&start=<offset>
        &filter_country=France
        &filter_businessarea=<area>        (repeatable smart filter)

    GET https://morganstanley.eightfold.ai/api/pcsx/position_details
        ?position_id=<id>&domain=morganstanley.com&hl=en   (job description)

Both serve open JSON to a plain polite UA — no auth, cookie or browser-header
gate — so this is CI-safe (verified from a datacenter IP).

Search response shape (`data`):
  count                 -> TOTAL matching (not page size); page size is FIXED at 10
                           (a &num override is ignored), so we page on `start += 10`.
  positions[]           -> id, displayJobId ("JR037505"), name, locations[],
                           standardizedLocations[] ("Paris, IDF, FR"), postedTs,
                           creationTs, department ("Strats"), workLocationOption,
                           atsJobId, positionUrl ("/careers/job/<id>")
Detail response (`data`) adds:
  jobDescription             -> HTML
  efcustomTextTextTimeType   -> ["Full time"]        (employment/time type)
  efcustomTextPcsPostingJobLevel -> ["Vice President"] (seniority)
  publicUrl                  -> canonical apply URL

Scope (locked 2026-07): France, "all tech-adjacent incl. Ops".
Morgan Stanley France is a near-empty board — ~1 open role total today (a Paris
"IED - Equity Derivatives Strat - Associate/VP") and ZERO in the Technology
business area. MS engineering lives in London / Budapest / New York / Mumbai /
Glasgow, not Paris. Low (often 0-1) yield is EXPECTED here, same as Mirakl / N26 /
Salesforce — the row count grows when MS posts a France tech/quant role. Don't
"fix" a small return.

The keep-filter is a UNION of two passes, because a position object carries NO
business-area field — only `department` + title — so business-area membership can
only be learned by asking the server:

  1. Business-area sweep: query filter_country=France & filter_businessarea in
     {technology, technology and operations, operations}. These areas are kept
     WHOLESALE (this is what "incl. Ops" means — Operations is noisy but in
     scope). Today all three are empty in France; the sweep keeps them correct
     going forward.
  2. Role sweep: query all France roles and additionally keep any whose title
     passes the shared `is_tech_role` predicate OR whose department/title looks
     like a Strat / Quant / Data / engineering role. This catches quant roles MS
     files under the "Sales and Trading" business area — e.g. the current Paris
     Strat, which pass 1 misses (its area is not Technology/Ops) and `is_tech_role`
     alone also misses ("Equity Derivatives Strat" carries no tech keyword).

To tighten later, drop "operations" from KEEP_BUSINESS_AREAS. To widen, relax
_ROLE_CATCH. A client-side France gate guards against Eightfold's include-remote
default ever leaking a non-France remote role into a country query.
"""
from __future__ import annotations

import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from scrapers._relevance import is_tech_role

BASE = "https://morganstanley.eightfold.ai"
SEARCH_URL = f"{BASE}/api/pcsx/search"
DETAIL_URL = f"{BASE}/api/pcsx/position_details"
DOMAIN = "morganstanley.com"

COUNTRY_IN_SCOPE = "France"

# Business areas kept WHOLESALE (server-side facet values, lowercase as the API
# expects). "operations" is intentionally broad per the locked scope.
KEEP_BUSINESS_AREAS = ("technology", "technology and operations", "operations")

# Eightfold PCSX serves a FIXED page of 10; a &num override is ignored.
PAGE_SIZE = 10
MAX_PAGES = 60  # defensive cap (600 rows) — France never approaches this.

# Catch quant / strat / data / engineering roles MS files under non-tech business
# areas (matched on deburred department OR title, in addition to is_tech_role).
_ROLE_CATCH = re.compile(
    r"strat|quant|\bdata\b|analytic|technolog|\bengineer|developer|software|"
    r"infrastructure|cyber|\bcloud\b|machine learning|\bai\b|\bml\b|\bit\b|"
    r"platform|devops|\bsre\b|architect|\bapi\b|\bsql\b"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
}

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.0


@dataclass
class Job:
    native_job_id: str          # displayJobId / atsJobId, e.g. "JR037505" (stable)
    title: str
    apply_url: str              # publicUrl (Eightfold canonical job URL)
    location: str               # locations[] joined
    category: str | None        # department, e.g. "Strats"
    employment_type: str | None  # efcustomTextTextTimeType, e.g. "Full time"
    description: str | None = None
    posted_date: str | None = None    # from postedTs epoch, YYYY-MM-DD
    identifier: str | None = None      # = native_job_id
    raw_payload: dict | None = None


def _deburr(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _is_france(position: dict) -> bool:
    """Client-side France gate — belt-and-suspenders vs. include-remote leakage.

    Eightfold's search defaults includeRemote on; filter_country=France is a hard
    facet (verified: no leakage today) but we still confirm the posting is
    physically in France via its standardized location ("City, REGION, FR") or a
    free-text "..., France".
    """
    for loc in position.get("standardizedLocations") or []:
        if re.search(r",\s*FR$", str(loc).strip()):
            return True
    for loc in position.get("locations") or []:
        if re.search(r"\bfrance$", str(loc).strip(), re.I):
            return True
    return False


def _keep_by_role(position: dict) -> bool:
    """Pass 2 predicate — is this a tech / quant / data / eng role by title/dept?"""
    title = position.get("name") or ""
    if is_tech_role(title):
        return True
    haystack = _deburr(title) + " || " + _deburr(position.get("department") or "")
    return bool(_ROLE_CATCH.search(haystack))


def _search_page(session: requests.Session, params: dict) -> tuple[list[dict], int]:
    """One PCSX search page. Returns (positions, total_count). Raises on failure
    (a partial listing must ABORT, not silently drop a slice — that would let the
    DB retire the missing rows; see feedback_partial_scrape_false_close)."""
    response = session.get(SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data")
    if not isinstance(data, dict) or "positions" not in data:
        raise RuntimeError(
            f"unexpected PCSX search shape (status={payload.get('status')}, "
            f"keys={list(payload.keys())})"
        )
    return data.get("positions") or [], int(data.get("count") or 0)


def _paginate(session: requests.Session, base_params: dict, label: str) -> list[dict]:
    """Walk every page of a France query (start += 10 until start >= count)."""
    collected: list[dict] = []
    start = 0
    for _ in range(MAX_PAGES):
        params = {**base_params, "start": start}
        positions, count = _search_page(session, params)
        if start == 0:
            print(f"  [{label}] total={count}", flush=True)
        if not positions:
            break
        collected.extend(positions)
        start += PAGE_SIZE
        if start >= count:
            break
        time.sleep(REQUEST_DELAY_SECONDS)
    else:
        raise RuntimeError(f"[{label}] hit MAX_PAGES={MAX_PAGES} — pagination bug?")
    return collected


def _collect_france(session: requests.Session) -> dict[str, dict]:
    """Union of the two keep-passes, keyed by numeric id -> position (tagged)."""
    kept: dict[str, dict] = {}

    def _add(position: dict, reason: str) -> None:
        if not _is_france(position):
            return
        pid = str(position.get("id"))
        if pid in kept:
            return
        position = {**position, "_keep_reason": reason}
        kept[pid] = position

    # Pass 1 — business-area sweep (kept wholesale).
    for area in KEEP_BUSINESS_AREAS:
        base = {
            "domain": DOMAIN,
            "query": "",
            "filter_country": COUNTRY_IN_SCOPE,
            "filter_businessarea": area,
        }
        for position in _paginate(session, base, f"area:{area}"):
            _add(position, f"business_area={area}")
        time.sleep(REQUEST_DELAY_SECONDS)

    # Pass 2 — all France roles, kept when tech/quant/data by title/department.
    base = {"domain": DOMAIN, "query": "", "filter_country": COUNTRY_IN_SCOPE}
    france_all = _paginate(session, base, "country:France")
    if not france_all:
        # A clean 200 with zero France roles is possible (MS is US-centric) but
        # unusual — surface it. Returning [] is safe: the DB empty-guard keeps the
        # last good rows rather than closing them.
        print("  WARNING: 0 France roles returned by the board", flush=True)
    for position in france_all:
        if _keep_by_role(position):
            _add(position, "role_match")

    return kept


def _fetch_detail(session: requests.Session, pid: str) -> dict | None:
    """position_details for one job. Returns the detail dict, or None if the job
    404s (vanished between listing and detail — the one allowed drop; every other
    error keeps the listing-only row so a transient blip can't retire it)."""
    params = {"position_id": pid, "domain": DOMAIN, "hl": "en"}
    try:
        response = session.get(DETAIL_URL, params=params, timeout=REQUEST_TIMEOUT)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json().get("data")
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001 — keep the row on transient detail errors
        print(f"    detail {pid}: {type(exc).__name__}: {exc} — listing-only", flush=True)
        return {}


def _first(value) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    value = (str(value).strip() if value is not None else "")
    return value or None


def _posted_date(position: dict) -> str | None:
    ts = position.get("postedTs") or position.get("creationTs")
    if not isinstance(ts, (int, float)) or ts <= 0:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


def _description(detail: dict) -> str | None:
    html = detail.get("jobDescription")
    if not html:
        return None
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    return text or None


def _to_job(position: dict, detail: dict) -> Job:
    req_id = (position.get("displayJobId") or position.get("atsJobId") or "").strip()
    if not req_id:
        raise RuntimeError(f"position missing displayJobId (id={position.get('id')})")

    apply_url = (detail.get("publicUrl") or "").strip()
    if not apply_url:
        path = (position.get("positionUrl") or f"/careers/job/{position.get('id')}").strip()
        apply_url = f"{BASE}{path}"

    locations = position.get("locations") or []
    level = _first(detail.get("efcustomTextPcsPostingJobLevel"))

    raw = {**position}
    raw["_detail_level"] = level
    raw["_detail_time_type"] = detail.get("efcustomTextTextTimeType")

    return Job(
        native_job_id=req_id,
        title=(position.get("name") or "").strip(),
        apply_url=apply_url,
        location="; ".join(str(x).strip() for x in locations if str(x).strip()),
        category=(position.get("department") or "").strip() or None,
        employment_type=_first(detail.get("efcustomTextTextTimeType")),
        description=_description(detail),
        posted_date=_posted_date(position),
        identifier=req_id,
        raw_payload=raw,
    )


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Listing phase (Eightfold PCSX)...", flush=True)
    candidates = _collect_france(session)
    print(f"  kept {len(candidates)} France in-scope position(s)", flush=True)

    print("Detail phase...", flush=True)
    jobs: dict[str, Job] = {}
    for pid, position in candidates.items():
        detail = _fetch_detail(session, pid)
        if detail is None:
            print(f"  {pid}: 404 (vanished) -> DROP", flush=True)
            continue
        job = _to_job(position, detail)
        jobs[job.native_job_id] = job
        print(
            f"  {job.native_job_id} [{job.category} | {job.employment_type} | "
            f"{position.get('_keep_reason')}] {job.title!r}",
            flush=True,
        )
        time.sleep(REQUEST_DELAY_SECONDS)

    elapsed = time.time() - started
    print(f"\n  -> {len(jobs)} jobs in {elapsed:.1f}s\n", flush=True)
    return [asdict(j) for j in jobs.values()]


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
    print(f"=== {len(results)} jobs final (total runtime {elapsed:.1f}s) ===\n")

    for j in results:
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
