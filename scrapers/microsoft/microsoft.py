"""Microsoft job scraper — France, engineering + technical-delivery disciplines.

Microsoft retired the old `gcsservices.careers.microsoft.com/search/api/v1/*`
API. `jobs.careers.microsoft.com` now 302s to **https://apply.careers.microsoft.com**,
an **Eightfold "PCSX"** board — the same stack as the Kering ATS surface. As
there, `GET /api/pcsx/search` and `GET /api/pcsx/position_details` are open (no
key, no browser fingerprinting); the older `/api/apply/v2/jobs` on the same host
answers `{"message": "Not authorized for PCSX"}` — do not go back to it.

Two API quirks worth writing down, both found by probing (material/):

  * **`num` is ignored.** The page size is hard-wired to 10 whatever you ask
    for, and `num=100` makes the endpoint return an empty body. Hence 200+ pages
    for a ~2k board.
  * **`employment_type` is ignored too.** Passing `employment_type=internship`
    returns the identical unfiltered count. Internship/apprenticeship therefore
    has to be filtered client-side off the detail payload, never server-side.

`location=France` (the geo filter) IS honoured, but we do NOT rely on it: a full
unfiltered crawl cross-checked against it returned exactly the same 21 France
positions, so the filter is honest today — yet a geo radius is a silent-loss
risk the day Eightfold retunes it. We crawl the whole board and gate on the
position's own location strings instead. ~207 pages, ~4 minutes.

Scope
-----
France, all employment types (Full-Time, Internship, Temp/Contract — stage and
alternance are in scope, they simply do not exist on this board today).

The category axis is the **Discipline** (`department` on the listing,
`efcustomTextTaDisciplineName` on the detail), NOT the Profession
(`efcustomTextCurrentProfession`). Profession is too coarse and actively
misfiles technical work:

    Cloud Solution Architecture      -> Profession "Customer Success"
    Customer Experience Engineering  -> Profession "Program Management"
    Solution Engineering             -> Profession "Customer Success"

Gating on Profession would drop every one of those. Discipline is free at
listing level and is the honest axis.

Three-way gate, in order:

  1. `BLOCKED_DISCIPLINES` — explicitly out of scope by scope decision (data
     centre / critical-environment / logistics technicians, technical pre-sales
     and account management, plus the plainly non-technical functions). Nothing
     rescues these, not even the content gate: they are excluded on purpose, and
     a datacentre technician JD does mention "automation" and "PowerShell".
  2. `TECH_DISCIPLINES` — kept wholesale, whatever the title says.
  3. Anything else (an ambiguous discipline, or one Microsoft invents next
     quarter) falls through to a **content gate read off the full job
     description** — never the title. This is the `is_tech_role` alternative
     used by the JPMorgan scraper: the mission decides, not the label.

The content gate is deliberately stricter than JPMorgan's because Microsoft
boilerplate mentions "AI" and "Azure" in essentially every JD including pure
sales ones, so neither is a signal here.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any

import requests
from bs4 import BeautifulSoup

HOST = "https://apply.careers.microsoft.com"
SEARCH_URL = f"{HOST}/api/pcsx/search"
DETAIL_URL = f"{HOST}/api/pcsx/position_details"
JOB_URL = f"{HOST}/careers/job/{{position_id}}"

DOMAIN = "microsoft.com"
SORT_BY = "timestamp"

# --- gate 1: disciplines that are out of scope by decision -------------------
# Data centre / critical-environment / logistics: real infrastructure work and
# the bulk of Microsoft's French headcount (13 of 21 open roles), but hands-on
# hardware and warehouse rather than software. Technical pre-sales and account
# management: quota-carrying, customer-facing. Neither may be rescued by the
# content gate — that is the whole point of blocking them explicitly.
BLOCKED_DISCIPLINES: frozenset[str] = frozenset({
    # data centre & physical infrastructure ops
    "Data Center Technicians",
    "Data Center Operations Management",
    "Data Center Program Management",
    "Critical Environment Ops",
    "Logistics Technician",
    "Materials Handling",
    "Construction Project Management",
    "Real Estate Portfolio Management",
    "Environmental Health & Safety",
    # physical-product engineering (Surface/Xbox devices, not software)
    "Mechanical Engineering",
    "Manufacturing Engineering",
    "Manufacturing Test Engineering",
    "NPI Product Engineering",
    "NPI Program Management",
    "Sourcing Engineering",
    "Model Making",
    # technical pre-sales & account management
    "Strategic Account Technology",
    "Account Technology",
    "Strategic Account Management",
    "Account Management",
    "Digital Account Management",
    "Services Account Management",
    "Consulting Account Management",
    "Partner Account Management",
    "Advertising Account Management",
    "Customer Success Account Mgmt",
    "Customer Success Management",
    "Solution Area Specialists",
    "Digital Solution Area Specialists",
    "Solution Sales Advisory",
    "Partner Solution Sales",
    "Specialist Sales Management",
    "Industry Advisory",
})

# --- gate 2: disciplines kept wholesale --------------------------------------
# Software, data/science, security and silicon, plus the customer-facing
# *engineering* disciplines (Microsoft's forward-deployed-engineer analogues):
# cloud/solution architecture, solution engineering, technical support
# engineering and Industry Solutions Delivery ("Technology Consulting").
TECH_DISCIPLINES: frozenset[str] = frozenset({
    # software & platform
    "Software Engineering",
    "Digital Software Engineering",
    "Quantum Software Engineering",
    "Service Engineering",
    "Site Reliability Engineering",
    "Reliability Engineering",
    "Cloud Network Engineering",
    "UX Engineering",
    "Product Design",
    # data & science
    "Data Engineering",
    "Data Science",
    "Data Analytics",
    "Business Analytics",
    "Applied Sciences",
    "Research Sciences",
    # security
    "Security",
    "Security Engineering",
    "Security Operations Engineering",
    "Security Research",
    "Security Assurance",
    "Penetration Testing",
    # hardware & silicon
    "Hardware Engineering",
    "Silicon Engineering",
    "Firmware Engineering",
    "Electrical Engineering",
    "Quantum Engineering",
    # cloud & solution engineering (technical, customer-facing)
    "Cloud Solution Architecture",
    "Digital Cloud Solution Architecture",
    "Solution Architecture",
    "Solution Engineering",
    "Digital Solution Engineering",
    "Customer Experience Engineering",
    "Technical Support Engineering",
    "Technical Support Advisory",
    "Support Escalation Management",
    "Technical Solution Management",
    "Technology Consulting",
    "Corporate Technology Support",
})

# --- gate 3: content signal, read off the FULL description -------------------
# STRONG: one hit is enough. Chosen so Microsoft's ubiquitous mission boilerplate
# does NOT trip them — note that a bare "AI", "Azure", "Copilot" or "cloud" is
# absent from both lists on purpose; they appear in every Microsoft JD, sales
# roles included, and would make the gate meaningless.
_STRONG_PATTERNS: tuple[str, ...] = (
    r"machine learning", r"deep learning", r"\bmlops\b", r"\bnlp\b",
    r"large language model", r"\bllms?\b", r"generative ai (?:engineer|develop)",
    r"data scien(?:ce|tist)", r"data engineer", r"analytics engineer",
    r"data pipeline", r"\betl\b", r"big data", r"data warehouse", r"data platform",
    r"software (?:engineer|develop)", r"\bsoftware development\b",
    r"distributed systems", r"microservice", r"\bdevops\b",
    r"site reliability", r"\bsre\b", r"infrastructure as code",
    r"\bkubernetes\b", r"\bterraform\b", r"ci/cd", r"\bapi design\b",
    r"solution architect", r"cloud architect", r"security engineer",
    r"penetration test", r"reverse engineer",
)
# SUPPORTING: normal in a technical JD but also as a single throwaway line in a
# commercial one, so TWO DISTINCT hits are required.
_SUPPORTING_PATTERNS: tuple[str, ...] = (
    r"\bpython\b", r"\bjava\b", r"\bc\+\+\b", r"\bc#\b", r"\.net\b",
    r"\bgolang\b", r"\brust\b", r"\btypescript\b", r"\bjavascript\b",
    r"\bsql\b", r"\bspark\b", r"\bkafka\b", r"\bdatabricks\b", r"\bsnowflake\b",
    r"\bpowershell\b", r"\bbash\b", r"\blinux\b", r"\bdocker\b", r"\bgit\b",
    r"\brest api\b", r"\bgraphql\b", r"\bpytorch\b", r"\btensorflow\b",
    r"azure devops", r"\bcosmos ?db\b", r"\bsql server\b",
    r"\balgorithm", r"\bdebugging\b", r"\bcodebase\b", r"\bproduction code\b",
    r"computer science", r"\bengineering degree\b",
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

PAGE_SIZE = 10                  # hard-wired server-side; `num` is ignored
MAX_PAGES = 400                 # defensive: 4k rows vs a ~2.1k board today
MAX_DETAIL_FETCHES = 300        # defensive: France is ~21; a blow-up means a bug
# The crawl takes ~4 minutes, during which requisitions genuinely open and close,
# and multi-location reqs share one displayJobId — so unique-ids < advertised is
# normal. Guard against a real pagination bug, which loses whole pages at a time.
CRAWL_SHORTFALL_TOLERANCE = 0.10        # 10% of the advertised row count

REQUEST_DELAY_SECONDS = 1.1     # JSON API; the host 429s on faster bursts
REQUEST_TIMEOUT = 45
MAX_ATTEMPTS = 5
RATE_LIMIT_BACKOFF_SECONDS = 6  # linear: 6s, 12s, 18s, ...


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


def _request(session: requests.Session, url: str, params: dict) -> dict:
    """GET with linear-backoff retry on 429/5xx/transport errors. Fails closed.

    The PCSX host enforces a short-window burst limit and answers 429 without a
    Retry-After header; it clears within a few seconds. A 207-page crawl will hit
    it, so retrying here is required, not optional — and raising after
    MAX_ATTEMPTS is deliberate: aborting beats returning a partial board, which
    would false-close every France row that happened to be past the cut.
    """
    last: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if response.status_code == 429 or response.status_code >= 500:
                raise requests.HTTPError(
                    f"HTTP {response.status_code}", response=response
                )
            response.raise_for_status()
            return response.json() or {}
        except (requests.Timeout, requests.ConnectionError,
                requests.HTTPError, ValueError) as exc:
            last = exc
            if attempt == MAX_ATTEMPTS:
                break
            wait = RATE_LIMIT_BACKOFF_SECONDS * attempt
            print(f"    {type(exc).__name__}: {exc} — retry {attempt}/"
                  f"{MAX_ATTEMPTS - 1} in {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"Microsoft: {url} failed after {MAX_ATTEMPTS} attempts: {last}")


def _is_france(position: dict[str, Any]) -> bool:
    """France by the position's own location strings.

    `locations` is free text with the **country first** ("France, Paris, Paris")
    — note this is the opposite order from Kering's board, so the Kering
    `endswith("France")` test would silently match nothing here.
    `standardizedLocations` is normalised with an ISO suffix ("Paris, IDF, FR").
    A multi-country req ("United Kingdom, ...", "France, Paris, Paris") counts.
    """
    for loc in position.get("locations") or []:
        if isinstance(loc, str) and loc.strip().lower().startswith("france"):
            return True
    for loc in position.get("standardizedLocations") or []:
        if isinstance(loc, str) and loc.strip().upper().endswith(", FR"):
            return True
    return False


def _crawl(session: requests.Session) -> dict[str, dict]:
    """Page the whole board. Returns {displayJobId: position}."""
    by_req: dict[str, dict] = {}
    start = 0
    pages = 0
    rows_seen = 0
    advertised: int | None = None

    print("Crawl phase: apply.careers.microsoft.com PCSX (10/page)...", flush=True)
    started = time.time()

    while pages < MAX_PAGES:
        data = _request(session, SEARCH_URL, {
            "domain": DOMAIN,
            "query": "",
            "location": "",
            "start": start,
            "sort_by": SORT_BY,
        }).get("data") or {}

        positions = data.get("positions") or []
        if advertised is None:
            advertised = data.get("count")
            print(f"  advertised: {advertised} positions "
                  f"(~{(advertised or 0) // PAGE_SIZE + 1} pages, "
                  f"~{int((advertised or 0) / PAGE_SIZE * REQUEST_DELAY_SECONDS)}s)",
                  flush=True)

        before = len(by_req)
        for position in positions:
            req_id = str(position.get("displayJobId")
                         or position.get("atsJobId")
                         or position.get("id") or "").strip()
            if req_id:
                by_req.setdefault(req_id, position)

        pages += 1
        rows_seen += len(positions)
        start += len(positions)

        if pages % 25 == 0:
            print(f"  page {pages}: {rows_seen}/{advertised} rows, "
                  f"{len(by_req)} unique", flush=True)

        # Stop on an empty page, on a page that added nothing new (the API
        # repeating itself), or once the advertised total is covered.
        if not positions or len(by_req) == before or start >= (advertised or 0):
            break
        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"  -> {len(by_req)} unique requisitions from {rows_seen} rows in "
          f"{pages} pages ({time.time() - started:.0f}s)", flush=True)

    # A pagination bug loses whole pages at a time. Aborting is mandatory: a
    # partial board is non-empty, so it slips past db.persist_run_results'
    # empty-guard and retires every France row that fell past the cut.
    if advertised and rows_seen < advertised * (1 - CRAWL_SHORTFALL_TOLERANCE):
        raise RuntimeError(
            f"Microsoft: crawled only {rows_seen} of {advertised} advertised rows "
            f"({pages} pages) — aborting rather than persisting a partial board."
        )
    print(flush=True)
    return by_req


def _fetch_detail(session: requests.Session, position_id: Any) -> dict | None:
    data = _request(session, DETAIL_URL, {
        "position_id": str(position_id),
        "domain": DOMAIN,
        "hl": "en",
    })
    return data.get("data") or None


def _first(value: Any) -> Any:
    """Eightfold custom text fields come back as single-element lists."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _plain_text(html: str | None) -> str:
    if not html:
        return ""
    return BeautifulSoup(html, "html.parser").get_text("\n", strip=True)


def _content_signals(text: str) -> tuple[list[str], list[str]]:
    strong = sorted({m.group(0).lower() for m in _STRONG.finditer(text)})
    supporting = sorted({m.group(0).lower() for m in _SUPPORTING.finditer(text)})
    return strong, supporting


def _iso_date(timestamp: Any) -> str | None:
    """PCSX `postedTs` / `creationTs` are epoch seconds."""
    try:
        seconds = int(timestamp)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).date().isoformat()


def _is_in_scope(discipline: str | None, body: str) -> tuple[bool, str]:
    """Discipline first, mission second. The title is never consulted."""
    name = (discipline or "").strip()
    if name in BLOCKED_DISCIPLINES:
        return False, f"blocked discipline: {name}"
    if name in TECH_DISCIPLINES:
        return True, f"discipline: {name}"

    strong, supporting = _content_signals(body)
    if strong:
        return True, f"content:strong={','.join(strong[:4])}"
    if len(supporting) >= MIN_SUPPORTING:
        return True, f"content:supporting={','.join(supporting[:4])}"
    return False, f"off-scope discipline ({name or 'unknown'}), no content signal"


def _build_job(position: dict, detail: dict, body: str, reason: str) -> Job:
    req_id = str(position.get("displayJobId") or position.get("atsJobId")
                 or position.get("id"))
    position_id = detail.get("id") or position.get("id")
    discipline = _first(detail.get("efcustomTextTaDisciplineName")) \
        or position.get("department")
    profession = _first(detail.get("efcustomTextCurrentProfession"))
    locations = detail.get("locations") or position.get("locations") or []
    strong, supporting = _content_signals(body)

    return Job(
        native_job_id=req_id,
        title=(detail.get("name") or position.get("name") or "").strip(),
        apply_url=detail.get("publicUrl") or JOB_URL.format(position_id=position_id),
        description=body or None,
        location=" | ".join(str(loc) for loc in locations) or None,
        # Both axes, Discipline first — it is the one the gate uses.
        category=" / ".join(x for x in (discipline, profession) if x) or None,
        posted_date=_iso_date(position.get("postedTs") or detail.get("postedTs")),
        employment_type=_first(detail.get("efcustomTextEmploymentType")),
        identifier=req_id,
        raw_payload={
            "position_id": position_id,
            "display_job_id": req_id,
            "discipline": discipline,
            "profession": profession,
            "role_type": _first(detail.get("efcustomTextRoletype")),
            "employment_type": _first(detail.get("efcustomTextEmploymentType")),
            "work_site": _first(detail.get("efcustomTextWorkSite")),
            "required_travel": _first(detail.get("efcustomTextRequiredTravel")),
            "work_location_option": position.get("workLocationOption"),
            "locations": locations,
            "standardized_locations": position.get("standardizedLocations"),
            "posted_ts": position.get("postedTs"),
            "creation_ts": position.get("creationTs"),
            "scope_reason": reason,
            "content_strong": strong,
            "content_supporting": supporting,
        },
    )


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    board = _crawl(session)

    france = {rid: pos for rid, pos in board.items() if _is_france(pos)}
    print(f"France gate: {len(france)} of {len(board)} requisitions\n", flush=True)
    if len(france) > MAX_DETAIL_FETCHES:
        raise RuntimeError(
            f"Microsoft: {len(france)} France positions exceeds the "
            f"{MAX_DETAIL_FETCHES} detail-fetch cap — refusing to hammer the API."
        )

    print(f"Detail phase: {len(france)} positions "
          f"(~{int(len(france) * REQUEST_DELAY_SECONDS)}s)...", flush=True)

    kept: list[Job] = []
    dropped: list[tuple[str, str]] = []
    missing_detail = 0

    for i, (req_id, position) in enumerate(sorted(france.items()), 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        detail = _fetch_detail(session, position.get("id"))
        if detail is None:
            # The detail carries the discipline the gate needs; keeping the row
            # would mean guessing at it. Counted, and guarded on below.
            print(f"  [{i}/{len(france)}] {req_id}: no detail payload", flush=True)
            missing_detail += 1
            continue

        body = _plain_text(detail.get("jobDescription"))
        discipline = _first(detail.get("efcustomTextTaDisciplineName")) \
            or position.get("department")
        in_scope, reason = _is_in_scope(discipline, body)

        title = detail.get("name") or position.get("name") or ""
        if in_scope:
            kept.append(_build_job(position, detail, body, reason))
            print(f"  [{discipline}] {title!r} -> KEEP ({reason})", flush=True)
        else:
            dropped.append((str(discipline), title))
            print(f"  [{discipline}] {title!r} -> drop ({reason})", flush=True)

    # Every detail failing means the payload shape changed or we are blocked —
    # that is not "France has no tech roles", so it must abort loudly.
    if france and missing_detail == len(france):
        raise RuntimeError(
            f"Microsoft: 0 of {len(france)} detail payloads parsed — aborting to "
            f"avoid false-closing DB rows."
        )

    print(flush=True)
    print(f"Gate: France x (tech Discipline wholesale OR technical mission in the "
          f"full JD). Blocked: datacentre/infra ops, technical pre-sales.",
          flush=True)
    print(f"  board          : {len(board)}", flush=True)
    print(f"  France         : {len(france)}", flush=True)
    print(f"  kept           : {len(kept)}", flush=True)
    print(f"  off-scope      : {len(dropped)}", flush=True)
    print(f"  detail missing : {missing_detail}", flush=True)

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
        print(f"[{j['identifier']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Why        : {j['raw_payload']['scope_reason']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
