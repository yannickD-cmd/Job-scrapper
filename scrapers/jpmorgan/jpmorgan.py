"""JPMorganChase job scraper — France, Data/AI/tech, all requisition types.

careers.jpmorgan.com redirects to a marketing site; the ATS of record is
Oracle Recruiting Cloud (ORC), tenant `jpmc`, site `CX_1001` — the same engine
as scrapers/hermes/hermes.py:

  LISTING  /hcmRestApi/resources/latest/recruitingCEJobRequisitions
           ?finder=findReqs;siteNumber=CX_1001,limit=...,offset=...
           -> requisitionList[]: Id, Title, PrimaryLocation,
              PrimaryLocationCountry, JobFamily, JobFunction, PostedDate,
              ShortDescriptionStr.  NOTE: RequisitionType / JobSchedule /
              BusinessUnit are all null in the listing — they exist only on the
              detail record, as does the full JD.

  DETAIL   /hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails
           ?finder=ById;Id=<Id>,siteNumber=CX_1001&expand=all
           -> ExternalDescriptionStr (full HTML JD), Category, JobFunction,
              RequisitionType, JobSchedule, ExternalPostedStartDate.

=============================================================================
WHY THIS SCRAPER HAS NO TITLE GATE
=============================================================================
Scope is decided on the ATS CATEGORY first and on the ACTUAL JOB CONTENT
second. Titles are never consulted. This is not a stylistic choice — the
France slice of this board breaks title-matching completely:

  * Measured 2026-08-23: 23 France requisitions, and NOT ONE of them carries a
    technology JobFunction. The tech families exist board-wide (Software
    Engineering 1054, Predictive Science 164, Risk Analytics/Modeling 43, …)
    but have zero France rows today.

  * The single best-matching France role is titled `Quant Model Risk Auditor`,
    filed under JobFunction "Internal Audit" / JobFamily "Auditing". Its JD:
    "Review complex models and build AI/ML tools…". A category-only gate drops
    it. A title gate drops it. Only the content finds it.

  * Two more, both titled `Asset & Liability Management (ALM) Risk Analyst`,
    are Python / Alteryx / Tableau / AI roles inside the "Risk" function.

So the gate is: KEEP when the JobFamily is a wholesale tech family, OR when
the full description carries a real data/AI signal. See _is_in_scope.

Country gate is `PrimaryLocationCountry == "FR"`, applied client-side after a
full-board crawl. The server-side location facet was measured to be lossy in
both directions (it returned 24 rows including a Frankfurt requisition whose
secondary location is Paris, while the true France count is 23), and a facet
loop that silently under-returns would retire live rows via a non-empty
partial result — see feedback_partial_scrape_false_close.

Requisition types are all kept (Professional + Campus): per the standing
"err inclusive on data-adjacent roles" rule, a New Grad / off-cycle AI role is
worth more than the noise it brings.
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests

HOST = "https://jpmc.fa.oraclecloud.com"
LIST_URL = f"{HOST}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
DETAIL_URL = f"{HOST}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
VANITY_JOB_URL = f"{HOST}/hcmUI/CandidateExperience/en/sites/CX_1001/job/{{job_id}}"

SITE_NUMBER = "CX_1001"
COUNTRY_IN_SCOPE = "FR"

# --- gate 1: wholesale tech job families (from the board-wide CATEGORIES facet;
# snapshot in material/probe_categories_facet.json). Every requisition in these
# is in scope regardless of what its description says. Deliberately excluded:
# "Product Management" / "Product Development" / "Analysts" / "Associates" —
# at a bank those are mostly banking-product and generalist-banking roles, and
# the content gate below rescues the technical ones anyway.
TECH_JOB_FAMILIES: frozenset[str] = frozenset({
    "Software Engineering",
    "Predictive Science",          # JPMC's data-science family
    "Risk Analytics/Modeling",     # quant modelling
    "Analytics Solutions & Delivery",
    "Data Management",
    "Infrastructure Engineering",
    "Architecture",
    "Technical Program Delivery",
    "Technology Support",
    "User Experience Design",
})

# JobFunction is coarser than JobFamily but is populated on rows where the
# family is a generic bucket, so it gets the same wholesale treatment.
TECH_JOB_FUNCTIONS: frozenset[str] = frozenset({
    "Technology",
    "Software Engineering",
    "Data & Analytics",
})

# --- gate 2: content signal, read off the FULL description ------------------
# STRONG: one occurrence is enough — these phrases do not appear in a private
# banker or FX sales JD.
_STRONG_PATTERNS: tuple[str, ...] = (
    r"machine learning", r"deep learning", r"artificial intelligence",
    r"\bai/ml\b", r"\bai\b", r"\bml\b", r"\bmlops\b", r"\bnlp\b", r"\bllms?\b",
    r"generative ai", r"large language model",
    r"data scien(?:ce|tist)", r"data engineer", r"big data", r"data pipeline",
    r"model risk", r"model validation", r"quantitative model",
    r"statistical model", r"econometric",
)
# SUPPORTING: common in technical JDs but also as one-line boilerplate in
# banking JDs ("strong quantitative skills"), so TWO DISTINCT ones are needed.
_SUPPORTING_PATTERNS: tuple[str, ...] = (
    r"\bpython\b", r"\bsql\b", r"\bscala\b", r"\bspark\b", r"\bhadoop\b",
    r"databricks", r"snowflake", r"\btableau\b", r"alteryx", r"power ?bi",
    r"\bsas\b", r"\bapi\b", r"microservice", r"\baws\b", r"\bazure\b",
    r"quantitative", r"analytics", r"automation", r"algorithm",
    r"statistical", r"modell?ing", r"software engineer",
)
_STRONG = re.compile("|".join(_STRONG_PATTERNS), re.I)
_SUPPORTING = re.compile("|".join(_SUPPORTING_PATTERNS), re.I)
MIN_SUPPORTING = 2

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
}

PAGE_SIZE = 200
MAX_PAGES = 60                  # defensive: 12k rows vs a ~7.3k board today
# A 37-page crawl of a live 7.3k-row board takes ~40s, during which requisitions
# genuinely open and close — the first run came back 7338/7340 and a strict
# equality guard aborted it. Tolerate churn, but still catch a real pagination
# bug: those lose a whole page (PAGE_SIZE rows) at a time, never single rows.
CRAWL_SHORTFALL_TOLERANCE = PAGE_SIZE // 4      # 50 rows
MAX_DETAIL_FETCHES = 400        # defensive: France is ~23; a blow-up means a bug
REQUEST_DELAY_SECONDS = 0.6     # JSON API
DETAIL_DELAY_SECONDS = 0.8
REQUEST_TIMEOUT = 60
MAX_ATTEMPTS = 4


@dataclass
class Job:
    native_job_id: str
    title: str
    apply_url: str
    description: str | None = None
    location: str | None = None
    category: str | None = None
    posted_date: str | None = None
    employment_type: str | None = None
    identifier: str | None = None
    raw_payload: dict | None = None


class PlannedOutage(RuntimeError):
    """Oracle Fusion pods take scheduled maintenance windows and answer every
    endpoint with a 503 'Planned Outage' HTML page. That is NOT an empty board —
    it must abort the run, never return [] (which would look like a silent-zero
    scrape) and never return a partial list."""


def _request(session: requests.Session, url: str) -> dict:
    """GET with linear-backoff retry. Fails closed after MAX_ATTEMPTS."""
    last: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT)
            if response.status_code == 503 and "Planned Outage" in response.text:
                raise PlannedOutage(
                    "jpmc Oracle pod is in a planned maintenance window"
                )
            if response.status_code >= 500:
                raise requests.HTTPError(f"{response.status_code} from ORC")
            response.raise_for_status()
            return response.json()
        except PlannedOutage:
            raise                                   # never retry an outage
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError,
                requests.exceptions.ChunkedEncodingError,
                json.JSONDecodeError) as exc:
            last = exc
            if attempt == MAX_ATTEMPTS:
                break
            wait = REQUEST_DELAY_SECONDS * attempt * 3
            print(f"    attempt {attempt}/{MAX_ATTEMPTS} failed "
                  f"({type(exc).__name__}: {exc}); retrying in {wait:.0f}s",
                  flush=True)
            time.sleep(wait)
    raise RuntimeError(f"ORC request failed after {MAX_ATTEMPTS} attempts: {url}") from last


def _list_page(session: requests.Session, offset: int) -> dict:
    url = (f"{LIST_URL}?onlyData=true"
           f"&expand=requisitionList.secondaryLocations"
           f"&finder=findReqs;siteNumber={SITE_NUMBER},"
           f"limit={PAGE_SIZE},offset={offset}")
    payload = _request(session, url)
    items = payload.get("items") or []
    if not items:
        raise RuntimeError(f"no items envelope at offset={offset}")
    return items[0]


def _fetch_detail(session: requests.Session, job_id: str) -> dict | None:
    url = (f"{DETAIL_URL}?expand=all&onlyData=true"
           f"&finder=ById;Id={job_id},siteNumber={SITE_NUMBER}")
    payload = _request(session, url)
    items = payload.get("items") or []
    return items[0] if items else None


def _crawl_france(session: requests.Session) -> list[dict]:
    """Whole-board crawl + client-side country filter (see module docstring)."""
    france: list[dict] = []
    seen = 0
    total: int | None = None

    for page in range(MAX_PAGES):
        offset = page * PAGE_SIZE
        if page:
            time.sleep(REQUEST_DELAY_SECONDS)

        item = _list_page(session, offset)
        if total is None:
            total = int(item.get("TotalJobsCount") or 0)
            print(f"  board total: {total}", flush=True)

        batch = item.get("requisitionList") or []
        if not batch:
            break
        seen += len(batch)
        france += [r for r in batch
                   if (r.get("PrimaryLocationCountry") or "").upper() == COUNTRY_IN_SCOPE]
        if page % 10 == 0 or seen >= (total or 0):
            print(f"    crawled {seen}/{total} — {len(france)} France so far",
                  flush=True)
        if total and seen >= total:
            break
    else:
        raise RuntimeError(f"MAX_PAGES={MAX_PAGES} hit — pagination bug?")

    shortfall = (total - seen) if total else 0
    if shortfall > CRAWL_SHORTFALL_TOLERANCE:
        raise RuntimeError(
            f"crawled {seen} of {total} rows (short by {shortfall}, tolerance "
            f"{CRAWL_SHORTFALL_TOLERANCE}) - aborting rather than returning a "
            f"partial list"
        )
    if shortfall:
        print(f"  note: {shortfall} row(s) closed mid-crawl (within tolerance)",
              flush=True)
    return france


def _plain_text(*html_fragments: str | None) -> str:
    joined = " ".join(f or "" for f in html_fragments)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", joined))).strip()


def _content_signals(text: str) -> tuple[list[str], list[str]]:
    strong = sorted({m.group(0).lower() for m in _STRONG.finditer(text)})
    supporting = sorted({m.group(0).lower() for m in _SUPPORTING.finditer(text)})
    return strong, supporting


def _is_in_scope(detail: dict, body: str) -> tuple[bool, str]:
    """Category first, content second. The title is never consulted."""
    family = (detail.get("JobFamily") or "").strip()
    function = (detail.get("JobFunction") or "").strip()
    category = (detail.get("Category") or "").strip()

    if family in TECH_JOB_FAMILIES:
        return True, f"family={family!r}"
    if function in TECH_JOB_FUNCTIONS or category in TECH_JOB_FAMILIES:
        return True, f"function={function!r}"

    strong, supporting = _content_signals(body)
    if strong:
        return True, f"content:strong={','.join(strong[:4])}"
    if len(supporting) >= MIN_SUPPORTING:
        return True, f"content:supporting={','.join(supporting[:4])}"

    weak = f" (only {','.join(supporting)})" if supporting else ""
    return False, f"no signal{weak}"


def _build_job(listing: dict, detail: dict, body: str, reason: str) -> Job:
    job_id = str(detail.get("Id") or listing.get("Id"))
    posted = (detail.get("ExternalPostedStartDate")
              or listing.get("PostedDate") or "")
    posted_date = posted[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", posted) else None

    family = (detail.get("JobFamily") or listing.get("JobFamily") or "").strip()
    function = (detail.get("JobFunction") or listing.get("JobFunction") or "").strip()
    category = " — ".join(dict.fromkeys(p for p in (function, family) if p)) or None

    schedule = (detail.get("JobSchedule") or "").strip()
    req_type = (detail.get("RequisitionType") or "").strip()
    employment_type = " / ".join(p for p in (req_type, schedule) if p) or None

    strong, supporting = _content_signals(body)

    return Job(
        native_job_id=job_id,
        title=html.unescape((detail.get("Title") or listing.get("Title") or "").strip()),
        apply_url=VANITY_JOB_URL.format(job_id=job_id),
        description=detail.get("ExternalDescriptionStr") or None,
        location=(detail.get("PrimaryLocation")
                  or listing.get("PrimaryLocation") or None),
        category=category,
        posted_date=posted_date,
        employment_type=employment_type,
        identifier=str(detail.get("RequisitionId") or job_id),
        raw_payload={
            "Id": job_id,
            "RequisitionId": detail.get("RequisitionId"),
            "JobFamily": family,
            "JobFunction": function,
            "Category": detail.get("Category"),
            "BusinessUnit": detail.get("BusinessUnit"),
            "Organization": detail.get("Organization"),
            "RequisitionType": req_type,
            "JobSchedule": schedule,
            "WorkplaceType": detail.get("WorkplaceType"),
            "PrimaryLocation": detail.get("PrimaryLocation"),
            "PrimaryLocationCountry": detail.get("PrimaryLocationCountry"),
            "ShortDescriptionStr": detail.get("ShortDescriptionStr"),
            "kept_because": reason,
            "content_strong": strong,
            "content_supporting": supporting,
        },
    )


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)
    started = time.time()

    print("Listing phase (full-board crawl, client-side France filter)...",
          flush=True)
    france = _crawl_france(session)
    print(f"  {len(france)} France requisitions\n", flush=True)

    if len(france) > MAX_DETAIL_FETCHES:
        raise RuntimeError(
            f"{len(france)} France rows exceeds MAX_DETAIL_FETCHES="
            f"{MAX_DETAIL_FETCHES} — refusing to hammer the ATS"
        )

    print(f"Detail phase: {len(france)} fetches "
          f"(~{int(len(france) * DETAIL_DELAY_SECONDS)}s)...", flush=True)

    kept: list[Job] = []
    rejected: dict[str, int] = {}
    missing = 0

    for i, listing in enumerate(france, 1):
        time.sleep(DETAIL_DELAY_SECONDS)
        job_id = str(listing.get("Id"))
        detail = _fetch_detail(session, job_id)
        if detail is None:
            print(f"  [{i}/{len(france)}] {job_id} detail 404 — skipped", flush=True)
            missing += 1
            continue

        body = _plain_text(
            detail.get("ExternalDescriptionStr"),
            detail.get("ExternalQualificationsStr"),
            detail.get("ExternalResponsibilitiesStr"),
            detail.get("ShortDescriptionStr"),
        )
        keep, reason = _is_in_scope(detail, body)
        title = (detail.get("Title") or "")[:52]

        if keep:
            kept.append(_build_job(listing, detail, body, reason))
            print(f"  [{i}/{len(france)}] KEEP {title!r} — {reason}", flush=True)
        else:
            rejected[reason] = rejected.get(reason, 0) + 1
            print(f"  [{i}/{len(france)}] drop {title!r} — {reason}", flush=True)

    elapsed = time.time() - started
    print(flush=True)
    print("Gate: country=FR × (tech JobFamily wholesale OR data/AI content). "
          "No title matching.", flush=True)
    print(f"  kept    : {len(kept)}", flush=True)
    print(f"  dropped : {sum(rejected.values())}", flush=True)
    print(f"  missing : {missing}", flush=True)
    print(f"  runtime : {elapsed:.1f}s", flush=True)

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
        desc = _plain_text(j.get("description"))
        desc = desc[:200] + ("…" if len(desc) > 200 else "")
        raw = j["raw_payload"]
        print(f"[{j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Kept because: {raw['kept_because']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
