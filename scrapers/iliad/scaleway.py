"""Scaleway job scraper — France, tech teams. iliad Group's cloud subsidiary.

Scaleway is the one iliad entity NOT on the group's SmartRecruiters tenant: it
runs its own Lever board, exposed through the standard public postings API:

    https://api.lever.co/v0/postings/scaleway?mode=json

One GET returns every public posting (~37 today) as a flat JSON array with the
full description already inline as `descriptionPlain` — no per-job detail call,
same as the Contentsquare / BlaBlaCar / Voodoo boards.

Free, Free Pro, Stancer, Opcore and Itrust live on SmartRecruiters and are
scraped by `scrapers/iliad/france.py`.

Scope
-----
France, all commitments. `country` is a reliable top-level ISO-2 on this board
(FR 33, IT 2, PL 1, SE 1), so it is the primary country gate, with the free-text
`categories.location` / `allLocations` as a fallback for any row that leaves it
blank. Scaleway posts multi-city French roles ("Paris, Bordeaux, Lille, Lyon,
Rennes, Rouen, Toulouse"), all captured by `allLocations`.

Commitment is NOT gated: the board carries 4 Internships and 1 Apprenticeship
(e.g. "Software Engineer IAM - Internship", "IT Workplace - Internship"), which
are explicitly wanted (`feedback_include_data_adjacent_ai_roles`).

Category axis is `categories.team`, which is clean on this board:

  KEEP wholesale   Engineering (10), GPU Cloud (4), IT (3), Products (2) — all
                   uniformly technical: SRE, Kubernetes Specialist, Managed
                   Database, Object Storage, DevOps Cybersecurity, Storage PM.
  CONTENT gate     Everything else (Sales 6, Operations 6, Marketing 4, Finance 1
                   and any null team) — Operations in particular is mixed.

posted_date caveat: Lever's only date field is `createdAt` — when the posting
RECORD was created, not when it was refreshed. Scaleway leaves reqs open for
long stretches, so an old date on a live row is normal. Do NOT read posted_date
as recency; dedup and closure are by `native_job_id`
(`project_lever_createdat_evergreen`).
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import requests

POSTINGS_URL = "https://api.lever.co/v0/postings/scaleway?mode=json"

COUNTRIES_IN_SCOPE = frozenset({"FR"})

# Wholesale tech teams; anything else reaches the content gate.
TECH_TEAMS: frozenset[str] = frozenset({
    "Engineering",
    "GPU Cloud",
    "IT",
    "Products",
})

# Fallback France detector for rows whose ISO-2 `country` is blank.
FRANCE_LOCATION_RE = re.compile(
    r"\b(france|paris|lyon|bordeaux|toulouse|nantes|lille|grenoble|marseille|"
    r"nice|rennes|rouen|strasbourg|montpellier|saint-ouen)\b",
    re.IGNORECASE,
)

_STRONG_PATTERNS: tuple[str, ...] = (
    r"software (?:engineer|develop)", r"d[ée]veloppeu?r", r"data scien(?:ce|tist)",
    r"data engineer", r"machine learning", r"\bmlops\b", r"\bllms?\b",
    r"\bdevops\b", r"\bsre\b", r"site reliability", r"\bkubernetes\b",
    r"\bterraform\b", r"\bansible\b", r"ci/cd", r"infrastructure as code",
    r"micro-?services?", r"distributed systems", r"cyber-?s(?:e|é)curit",
    r"cyber-?security", r"pentest", r"solutions? architect",
    r"\bapi design\b", r"backend|back-end", r"frontend|front-end",
)
_SUPPORTING_PATTERNS: tuple[str, ...] = (
    r"\bpython\b", r"\bgolang\b", r"\bjava\b", r"\bc\+\+\b", r"\brust\b",
    r"\btypescript\b", r"\bjavascript\b", r"\breact\b", r"\bsql\b",
    r"\blinux\b", r"\bdocker\b", r"\bgit\b", r"\bgrpc\b", r"\bgraphql\b",
    r"\brest api\b", r"\bprometheus\b", r"\bgrafana\b", r"\bpostgres",
    r"\bceph\b", r"\bopenstack\b", r"\bansible\b", r"\bhelm\b",
    r"\bscripting\b", r"\balgorithm", r"\bdebugging\b", r"\bcodebase\b",
    # Safe ONLY because the company pitch is stripped first (see _role_section):
    # every one of these also appears in the "OUR STORY" preamble.
    r"\bbare ?metal\b", r"\bstorage\b", r"\bcompute\b", r"\bnetworking\b",
    r"\barchitecture\b", r"\btechnical specification", r"\btroubleshoot",
    r"\bhypervisor\b", r"\bvirtualization\b", r"\bhpc\b",
)

# Every posting opens with the same "OUR STORY: 🇪🇺 Join Scaleway and shape the
# sovereign cloud of tomorrow … bare metal, containerization, serverless, AI …"
# company pitch. Judging that text means judging Scaleway, not the job: it kept
# an "Approvisionneur - Stage" (supply-chain intern) in the first smoke test.
# Cut to the first role-specific heading so the gate reads the MISSION only.
# Note a JD may be pitch-only ("Presales Solutions Engineer - HPC" is 1142 chars
# of pure boilerplate) — there is genuinely nothing to judge there, and it
# correctly drops.
_ROLE_SECTION_RE = re.compile(
    r"\b(WHY WE NEED YOU|YOUR FUTURE TEAM|YOUR DAILY ROUTINE|YOUR MISSION|"
    r"YOUR ROLE|ABOUT YOU|WHAT YOU WILL DO|HARD ?SKILLS|SOFT ?SKILLS)",
    re.IGNORECASE,
)


def _role_section(body: str) -> str:
    """Drop the shared company pitch; keep the role-specific remainder."""
    match = _ROLE_SECTION_RE.search(body or "")
    return body[match.start():] if match else (body or "")


_STRONG = re.compile("|".join(_STRONG_PATTERNS), re.I)
_SUPPORTING = re.compile("|".join(_SUPPORTING_PATTERNS), re.I)
MIN_SUPPORTING = 2

HEADERS = {
    "User-Agent": (
        "Job-scrapper/1.0 (+https://github.com/yannickD-cmd; "
        "yannickarieldossa@gmail.com) python-requests"
    ),
    "Accept": "application/json",
}

REQUEST_TIMEOUT = 45
MAX_ATTEMPTS = 4
RETRY_BACKOFF_SECONDS = 5


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


def _request(session: requests.Session, url: str):
    last: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT)
            if response.status_code == 429 or response.status_code >= 500:
                raise requests.HTTPError(f"HTTP {response.status_code}",
                                         response=response)
            response.raise_for_status()
            return response.json()
        except (requests.Timeout, requests.ConnectionError,
                requests.HTTPError, ValueError) as exc:
            last = exc
            if attempt == MAX_ATTEMPTS:
                break
            wait = RETRY_BACKOFF_SECONDS * attempt
            print(f"    {type(exc).__name__}: {exc} — retry {attempt}/"
                  f"{MAX_ATTEMPTS - 1} in {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"scaleway: {url} failed after {MAX_ATTEMPTS} attempts: {last}")


def _all_locations(posting: dict) -> list[str]:
    categories = posting.get("categories") or {}
    locations = list(categories.get("allLocations") or [])
    single = categories.get("location")
    if single and single not in locations:
        locations.append(single)
    return locations


def _is_france(posting: dict) -> bool:
    country = (posting.get("country") or "").upper()
    if country:
        return country in COUNTRIES_IN_SCOPE
    return any(FRANCE_LOCATION_RE.search(loc) for loc in _all_locations(posting))


def _content_signals(text: str) -> tuple[list[str], list[str]]:
    """Signals from the ROLE section only — never the shared company pitch."""
    role = _role_section(text)
    strong = sorted({m.group(0).lower() for m in _STRONG.finditer(role)})
    supporting = sorted({m.group(0).lower() for m in _SUPPORTING.finditer(role)})
    return strong, supporting


def _is_in_scope(team: str | None, body: str) -> tuple[bool, str]:
    """Team first, mission second. The title is never consulted."""
    if team in TECH_TEAMS:
        return True, f"team: {team}"
    strong, supporting = _content_signals(body)
    if strong:
        return True, f"content:strong={','.join(strong[:4])}"
    if len(supporting) >= MIN_SUPPORTING:
        return True, f"content:supporting={','.join(supporting[:4])}"
    return False, f"off-scope team ({team or 'none'}), no content signal"


def _iso_date(created_at) -> str | None:
    """Lever `createdAt` is epoch MILLISECONDS."""
    try:
        millis = int(created_at)
    except (TypeError, ValueError):
        return None
    if millis <= 0:
        return None
    return datetime.fromtimestamp(millis / 1000, timezone.utc).date().isoformat()


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    print("Fetching Lever board: scaleway...", flush=True)
    postings = _request(session, POSTINGS_URL)
    if not isinstance(postings, list):
        raise RuntimeError(
            f"scaleway: expected a JSON array, got {type(postings).__name__} "
            f"— aborting rather than persisting nothing."
        )
    print(f"  -> {len(postings)} postings\n", flush=True)

    france = [p for p in postings if _is_france(p)]
    print(f"France gate: {len(france)} of {len(postings)}\n", flush=True)

    kept: list[Job] = []
    dropped = 0

    for posting in france:
        categories = posting.get("categories") or {}
        team = categories.get("team")
        body = posting.get("descriptionPlain") or ""
        title = posting.get("text") or ""

        in_scope, reason = _is_in_scope(team, body)
        if not in_scope:
            dropped += 1
            print(f"  [{team}] {title[:52]!r} -> drop ({reason})", flush=True)
            continue

        strong, supporting = _content_signals(body)
        locations = _all_locations(posting)
        kept.append(Job(
            native_job_id=str(posting.get("id")),
            title=title,
            apply_url=posting.get("hostedUrl") or posting.get("applyUrl") or "",
            description=body or None,
            location=" | ".join(locations) or None,
            category=" / ".join(
                x for x in (categories.get("department"), team) if x) or None,
            posted_date=_iso_date(posting.get("createdAt")),
            employment_type=categories.get("commitment"),
            identifier=str(posting.get("id")),
            raw_payload={
                "id": posting.get("id"),
                "country": posting.get("country"),
                "team": team,
                "department": categories.get("department"),
                "commitment": categories.get("commitment"),
                "location": categories.get("location"),
                "all_locations": categories.get("allLocations"),
                "workplace_type": posting.get("workplaceType"),
                "created_at": posting.get("createdAt"),
                "scope_reason": reason,
                "content_strong": strong,
                "content_supporting": supporting,
            },
        ))
        print(f"  [{team}] {title[:52]!r} -> KEEP ({reason})", flush=True)

    print(flush=True)
    print("Gate: country=FR x (tech team wholesale OR technical mission in the "
          "full JD). Commitment not gated (internships wanted).", flush=True)
    print(f"  board     : {len(postings)}", flush=True)
    print(f"  France    : {len(france)}", flush=True)
    print(f"  kept      : {len(kept)}", flush=True)
    print(f"  off-scope : {dropped}", flush=True)

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
        desc = desc[:180] + ("…" if len(desc) > 180 else "")
        raw = j["raw_payload"]
        print(f"[{j['identifier']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Commitment : {j['employment_type']}   Workplace: {raw['workplace_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Why        : {raw['scope_reason']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
