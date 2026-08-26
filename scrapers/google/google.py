"""Google job scraper — France, engineering + technical-solutions categories.

Google's careers board is a server-rendered Wiz app at

    https://www.google.com/about/careers/applications/jobs/results/

The old public JSON API (`careers.google.com/api/v3/search`) is gone — it 404s,
as does `…/applications/api/v3/search`. There is no JSON-LD on detail pages and
no `batchexecute` worth reverse-engineering: the listing HTML is fully rendered
(title, org, location, experience level, minimum qualifications all present), so
we parse HTML. Plain `requests` works; no Playwright, so this is CI-safe.

Three things had to be probed to get this right (artifacts in `material/`):

  * **The `category` facet is real but undiscoverable from the page.** The
    filter panel is rendered client-side, so no option list exists in the HTML —
    but `?category=SOFTWARE_ENGINEERING` *is* honoured server-side. An invalid
    value returns 0 matches, which makes the vocabulary enumerable by probing.
    `CATEGORIES` below is the resulting complete list: 17 values summing to 3334
    of the 3341 board-wide postings.
  * **`?location=France` is honest, and must NOT be second-guessed.** Several
    France-matching cards render a non-French primary city (Munich, London,
    Frankfurt). Those are multi-location reqs — the Munich "Customer Engineer,
    Google Distributed Cloud" really does say *"Munich, Germany; Paris, France;
    London, UK; Madrid, Spain; Copenhagen, Denmark"*. Re-filtering on the card's
    displayed location would silently drop every multi-location France role, so
    the facet is the country gate and the card location is never consulted.
  * **Detail-page location must not be read off `span.r0wTof`.** That class also
    wraps a boilerplate office list (Bengaluru / Dublin / Kirkland / Mexico City
    / Mountain View) present on every page. The job's real locations come from
    the "…share your preferred working location from the following:" sentence,
    falling back to the `place` marker block.

Scope
-----
France, all employment types (intern / fixed-term / full-time), scoped on
Google's own `category` facet — see `feedback_prefer_platform_category_over_is_tech_role`.

  KEEP wholesale   SOFTWARE_ENGINEERING, HARDWARE_ENGINEERING, NETWORK_ENGINEERING,
                   TECHNICAL_SOLUTIONS (Google's forward-deployed-engineer bucket:
                   Technical Account Manager, Customer Engineer, Technical
                   Solutions Engineer), DEVELOPER_RELATIONS, USER_EXPERIENCE.
  BLOCK outright   DATA_CENTER_OPERATIONS plus the commercial/back-office
                   categories. Blocked *before* the content gate on purpose — a
                   datacentre-compliance JD does mention "infrastructure".
  CONTENT gate     PROGRAM_MANAGEMENT / PRODUCT_MANAGEMENT (both genuinely mixed:
                   "Technical Program Manager" shares the bucket with business
                   programme managers) and anything uncategorised or in a
                   category Google adds later. Read off the FULL job description,
                   never the title.

`posted_date` is always None — Google publishes no posting date anywhere in the
markup. Same as the Air France and OVHcloud scrapers; dedup is by
`native_job_id`, and the dashboard falls back to `first_seen_at`.
"""
from __future__ import annotations

import html as html_mod
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests

HOST = "https://www.google.com"
LIST_URL = f"{HOST}/about/careers/applications/jobs/results/"
JOB_URL = f"{LIST_URL}{{job_id}}-{{slug}}"

LOCATION_FACET = "France"

# --- Google's `category` facet, complete (probed 2026-08-25) -----------------
# Board-wide counts in comments; an invalid value returns 0, which is how this
# list was enumerated. To rescope, move a value between the three sets.
TECH_CATEGORIES: frozenset[str] = frozenset({
    "SOFTWARE_ENGINEERING",      # 1025
    "HARDWARE_ENGINEERING",      #  287
    "TECHNICAL_SOLUTIONS",       #  283  TAM / Customer Engineer / TSE
    "NETWORK_ENGINEERING",       #   39
    "USER_EXPERIENCE",           #   72
    "DEVELOPER_RELATIONS",       #    3
})
BLOCKED_CATEGORIES: frozenset[str] = frozenset({
    "DATA_CENTER_OPERATIONS",    #  202  scoped out: physical infra ops
    "SALES_OPERATIONS",          #  299
    "SALES",                     #  203
    "BUSINESS_STRATEGY",         #  134
    "MARKETING",                 #   64
    "FINANCE",                   #   60
    "PARTNERSHIPS",              #   59
    "LEGAL",                     #   42
    "ADMINISTRATIVE",            #   35
})
# Mixed buckets — decided by the description, not the label.
CONTENT_GATED_CATEGORIES: frozenset[str] = frozenset({
    "PROGRAM_MANAGEMENT",        #  376
    "PRODUCT_MANAGEMENT",        #  151
})
ALL_CATEGORIES = TECH_CATEGORIES | BLOCKED_CATEGORIES | CONTENT_GATED_CATEGORIES

# --- content gate, read off the FULL description -----------------------------
# One STRONG hit, or two distinct SUPPORTING hits, keeps the row. A bare "AI",
# "cloud" or "Google Cloud" is deliberately absent — Google puts those in
# marketing and sales JDs too, so they carry no signal here.
_STRONG_PATTERNS: tuple[str, ...] = (
    r"software (?:engineer|develop)", r"\bsoftware development\b",
    r"machine learning", r"deep learning", r"\bmlops\b", r"\bnlp\b",
    r"large language model", r"\bllms?\b", r"ml infrastructure",
    r"data scien(?:ce|tist)", r"data engineer", r"data pipeline",
    r"distributed (?:systems|computing)", r"data structures and algorithms",
    r"programming language", r"\bcodebase\b", r"code review",
    r"site reliability", r"\bsre\b", r"\bdevops\b", r"\bkubernetes\b",
    r"infrastructure as code", r"\bapi design\b", r"technical troubleshooting",
    r"solutions? architect", r"security engineer", r"penetration test",
    r"reverse engineer", r"\bcompiler\b",
)
_SUPPORTING_PATTERNS: tuple[str, ...] = (
    r"\bpython\b", r"\bjava\b", r"\bc\+\+\b", r"\bgolang\b", r"\bgo\b(?= programming)",
    r"\brust\b", r"\btypescript\b", r"\bjavascript\b", r"\bsql\b", r"\bspark\b",
    r"\bkafka\b", r"\bbigquery\b", r"\btensorflow\b", r"\bpytorch\b",
    r"\blinux\b", r"\bdocker\b", r"\bterraform\b", r"\bapis?\b",
    r"\balgorithm", r"\bdebugging\b", r"\bcomputer science\b",
    r"technical field", r"\bsystem design\b", r"\bdatabases?\b",
    r"\bmicroservice", r"\bnetworking\b",
)
_STRONG = re.compile("|".join(_STRONG_PATTERNS), re.I)
_SUPPORTING = re.compile("|".join(_SUPPORTING_PATTERNS), re.I)
MIN_SUPPORTING = 2

HEADERS = {
    # Google serves the rendered listing to a plain UA, but a browser-shaped one
    # avoids the trimmed no-JS variant.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 "
        "(personal-job-tracker/0.1; contact yannickarieldossa@gmail.com)"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

PAGE_SIZE = 20                  # cards per listing page
MAX_PAGES = 40                  # defensive: 800 rows vs 14 France today
MAX_DETAIL_FETCHES = 300        # defensive: a blow-up means a parsing bug
REQUEST_DELAY_SECONDS = 2.0     # HTML pages
REQUEST_TIMEOUT = 45
MAX_ATTEMPTS = 4
RETRY_BACKOFF_SECONDS = 5

_JOB_HREF_RE = re.compile(r'href="jobs/results/(\d+)-([^"?#]*)')
_CARD_SPLIT_RE = re.compile(r'(?=<li class="lLd3Je")')
_TITLE_RE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S)
_ORG_RE = re.compile(r'<p class="l103df">(.*?)\|', re.S)
_CARD_LOC_RE = re.compile(r'class="r0wTof\s*">(.*?)</span>', re.S)
_LEVEL_RE = re.compile(r'class="wVSTAb">(.*?)</span>', re.S)
_MATCHED_RE = re.compile(r"([\d,]+)\s+jobs? matched")
_PREFERRED_LOC_RE = re.compile(
    r"preferred working location from the following:\s*(.+?)\s*(?:\.|$)", re.S)

_DESC_START_MARKERS = ("Minimum qualifications", "About the job", "Responsibilities")
_DESC_END_MARKER = "Information collected and processed as part of your Google Careers profile"


@dataclass
class Job:
    native_job_id: str
    title: str
    apply_url: str
    description: str | None = None
    location: str | None = None
    category: str | None = None
    posted_date: str | None = None      # Google publishes none
    employment_type: str | None = None
    identifier: str | None = None
    raw_payload: dict | None = None


def _clean(fragment: str | None) -> str:
    if not fragment:
        return ""
    text = html_mod.unescape(re.sub(r"<[^>]+>", "", fragment))
    return re.sub(r"\s+", " ", text).strip()


def _get(session: requests.Session, url: str, params: dict | None = None) -> str:
    """GET with linear-backoff retry. Fails closed after MAX_ATTEMPTS."""
    last: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if response.status_code >= 500 or response.status_code == 429:
                raise requests.HTTPError(f"HTTP {response.status_code}",
                                         response=response)
            response.raise_for_status()
            return response.text
        except (requests.Timeout, requests.ConnectionError,
                requests.HTTPError) as exc:
            last = exc
            if attempt == MAX_ATTEMPTS:
                break
            wait = RETRY_BACKOFF_SECONDS * attempt
            print(f"    {type(exc).__name__}: {exc} — retry {attempt}/"
                  f"{MAX_ATTEMPTS - 1} in {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"Google: {url} failed after {MAX_ATTEMPTS} attempts: {last}")


def _page_lines(document: str) -> list[str]:
    """Visible text of a page, one entry per rendered block."""
    body = document[document.find("<body"):]
    stripped = re.sub(r"<script.*?</script>", " ", body, flags=re.S)
    stripped = re.sub(r"<style.*?</style>", " ", stripped, flags=re.S)
    text = html_mod.unescape(re.sub(r"<[^>]+>", "\n", stripped))
    return [line.strip() for line in text.split("\n") if line.strip()]


def _matched_total(document: str) -> int | None:
    match = _MATCHED_RE.search(" ".join(_page_lines(document)))
    return int(match.group(1).replace(",", "")) if match else None


def _parse_cards(document: str) -> list[dict]:
    """One dict per result card. Location here is only the PRIMARY city."""
    cards: list[dict] = []
    for chunk in _CARD_SPLIT_RE.split(document)[1:]:
        href = _JOB_HREF_RE.search(chunk)
        if not href:
            continue
        title = _TITLE_RE.search(chunk)
        org = _ORG_RE.search(chunk)
        locations = [_clean(x) for x in _CARD_LOC_RE.findall(chunk)]
        levels = [_clean(x) for x in _LEVEL_RE.findall(chunk)]
        cards.append({
            "job_id": href.group(1),
            "slug": href.group(2),
            "title": _clean(title.group(1)) if title else "",
            "org": _clean(org.group(1)) if org else None,
            "card_location": locations[0] if locations else None,
            "level": levels[0] if levels else None,
        })
    return cards


def _crawl_listing(session: requests.Session, params: dict, label: str) -> dict[str, dict]:
    """Paginate a listing query. Returns {job_id: card}."""
    found: dict[str, dict] = {}
    advertised: int | None = None

    for page in range(1, MAX_PAGES + 1):
        document = _get(session, LIST_URL, {**params, "page": page})
        if advertised is None:
            advertised = _matched_total(document)
        cards = _parse_cards(document)
        for card in cards:
            found.setdefault(card["job_id"], card)

        if len(cards) < PAGE_SIZE:
            break
        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"  {label:<34} matched={advertised if advertised is not None else '?':>5} "
          f"parsed={len(found)}", flush=True)

    # A parser break shows up as "the board says N, we parsed far fewer". That
    # must abort: a partial non-empty result slips past the empty-guard in
    # db.persist_run_results and retires every row that fell past the cut.
    if advertised and len(found) < advertised and advertised <= MAX_PAGES * PAGE_SIZE:
        raise RuntimeError(
            f"Google: {label} advertised {advertised} jobs but only {len(found)} "
            f"cards parsed — card markup changed? Aborting rather than "
            f"persisting a partial result."
        )
    return found


def _label_categories(session: requests.Session) -> dict[str, list[str]]:
    """Map job_id -> ALL its categories, by re-running France per category.

    Google exposes no category on the card or the detail page, but the facet is
    honoured server-side, so one cheap query per category labels the whole set.

    A requisition can sit in more than one category — "Founder Advocate, EMEA
    Startup, Google Cloud" is in both SALES_OPERATIONS and TECHNICAL_SOLUTIONS —
    so every label is collected. Keeping only the first would let a blocked
    category beat a tech one by nothing more than alphabetical order.
    """
    labels: dict[str, list[str]] = {}
    print(f"Category phase: {len(ALL_CATEGORIES)} facet queries...", flush=True)
    for category in sorted(ALL_CATEGORIES):
        time.sleep(REQUEST_DELAY_SECONDS)
        found = _crawl_listing(
            session,
            {"location": LOCATION_FACET, "category": category},
            f"  {category}",
        )
        for job_id in found:
            labels.setdefault(job_id, []).append(category)
    print(flush=True)
    return labels


def _fetch_detail(session: requests.Session, card: dict) -> dict:
    """Description + the job's real location list."""
    url = JOB_URL.format(job_id=card["job_id"], slug=card["slug"])
    document = _get(session, url)
    lines = _page_lines(document)

    start = next((i for i, line in enumerate(lines)
                  if line.startswith(_DESC_START_MARKERS)), None)
    end = next((i for i, line in enumerate(lines)
                if line.startswith(_DESC_END_MARKER)), len(lines))
    description = "\n".join(lines[start:end]) if start is not None else ""

    # Full location list: the "preferred working location" sentence is the only
    # reliable source (span.r0wTof also wraps a boilerplate office list).
    locations: list[str] = []
    preferred = _PREFERRED_LOC_RE.search("\n".join(lines))
    if preferred:
        locations = [part.strip() for part in
                     re.split(r";|\n", preferred.group(1)) if part.strip()]
    if not locations:
        # After the `place` marker the first line is the primary location and any
        # further ones are prefixed "; ". The block ends at the next icon token
        # (`bar_chart`, `laptop_windows`, …) — anything not starting with ";" is
        # no longer a location, so stop rather than skip, or badge text ("Mid",
        # "Remote eligible") and icon names leak into the field.
        marker = next((i for i, line in enumerate(lines) if line == "place"), None)
        if marker is not None:
            for line in lines[marker + 1: marker + 12]:
                if not locations:
                    locations.append(line.strip())
                    continue
                if not line.startswith(";"):
                    break
                candidate = line.lstrip("; ").strip()
                # "; +4 more" is an expander, not a location.
                if candidate and not candidate.startswith("+"):
                    locations.append(candidate)
    if not locations and card.get("card_location"):
        locations = [card["card_location"]]

    return {"url": url, "description": description, "locations": locations}


def _content_signals(text: str) -> tuple[list[str], list[str]]:
    strong = sorted({m.group(0).lower() for m in _STRONG.finditer(text)})
    supporting = sorted({m.group(0).lower() for m in _SUPPORTING.finditer(text)})
    return strong, supporting


def _is_in_scope(categories: list[str], body: str) -> tuple[bool, str]:
    """Category first, mission second. The title is never consulted.

    Precedence across a multi-category requisition: a tech category wins, then a
    content-gated one, and only an all-blocked row is dropped outright.
    """
    tech = sorted(set(categories) & TECH_CATEGORIES)
    if tech:
        return True, f"category: {','.join(tech)}"
    if categories and set(categories) <= BLOCKED_CATEGORIES:
        return False, f"blocked category: {','.join(sorted(set(categories)))}"

    strong, supporting = _content_signals(body)
    if strong:
        return True, f"content:strong={','.join(strong[:4])}"
    if len(supporting) >= MIN_SUPPORTING:
        return True, f"content:supporting={','.join(supporting[:4])}"
    label = ",".join(sorted(set(categories))) if categories else "uncategorised"
    return False, f"off-scope category ({label}), no content signal"


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    print(f"Listing phase: location={LOCATION_FACET}", flush=True)
    france = _crawl_listing(session, {"location": LOCATION_FACET}, "France (all)")
    print(flush=True)
    if not france:
        return []
    if len(france) > MAX_DETAIL_FETCHES:
        raise RuntimeError(
            f"Google: {len(france)} France postings exceeds the "
            f"{MAX_DETAIL_FETCHES} detail-fetch cap — refusing to hammer the site."
        )

    labels = _label_categories(session)
    unlabelled = [j for j in france if j not in labels]
    if unlabelled:
        print(f"  {len(unlabelled)} France jobs carry no category facet "
              f"-> content gate\n", flush=True)

    print(f"Detail phase: {len(france)} pages "
          f"(~{int(len(france) * REQUEST_DELAY_SECONDS)}s)...", flush=True)

    kept: list[Job] = []
    dropped = 0
    failed = 0

    for i, (job_id, card) in enumerate(sorted(france.items()), 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            detail = _fetch_detail(session, card)
        except Exception as exc:
            print(f"  [{i}/{len(france)}] {job_id} FAILED: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            failed += 1
            continue

        categories = labels.get(job_id, [])
        category = ",".join(sorted(set(categories))) or None
        body = detail["description"]
        in_scope, reason = _is_in_scope(categories, body)
        strong, supporting = _content_signals(body)

        if in_scope:
            kept.append(Job(
                native_job_id=job_id,
                title=card["title"],
                apply_url=detail["url"],
                description=body or None,
                location=" | ".join(detail["locations"]) or None,
                category=category,
                posted_date=None,
                employment_type=None,
                identifier=job_id,
                raw_payload={
                    "job_id": job_id,
                    "slug": card["slug"],
                    "organization": card["org"],
                    "experience_level": card["level"],
                    "categories": categories,
                    "card_location": card["card_location"],
                    "locations": detail["locations"],
                    "scope_reason": reason,
                    "content_strong": strong,
                    "content_supporting": supporting,
                },
            ))
            print(f"  [{category}] {card['title']!r} -> KEEP ({reason})", flush=True)
        else:
            dropped += 1
            print(f"  [{category}] {card['title']!r} -> drop ({reason})", flush=True)

    # Every detail failing means the layout changed or we are blocked — not
    # "France has no tech roles". Abort loudly.
    if france and failed == len(france):
        raise RuntimeError(
            f"Google: 0 of {len(france)} detail pages parsed — aborting to avoid "
            f"false-closing DB rows."
        )

    print(flush=True)
    print("Gate: France facet x (tech category wholesale OR technical mission in "
          "the full JD). Blocked: datacentre ops, commercial, back-office.",
          flush=True)
    print(f"  France postings : {len(france)}", flush=True)
    print(f"  kept            : {len(kept)}", flush=True)
    print(f"  off-scope       : {dropped}", flush=True)
    print(f"  failed          : {failed}", flush=True)

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
        raw = j["raw_payload"]
        print(f"[{j['identifier']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Org / level: {raw['organization']} / {raw['experience_level']}")
        print(f"  Location   : {j['location']}")
        print(f"  Why        : {raw['scope_reason']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
