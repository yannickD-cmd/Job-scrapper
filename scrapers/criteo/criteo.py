"""Criteo job scraper — France, tech families (R&D / Analytics / IT / Product),
Regular hires only (no interns, no FTC).

Two-pass HTML scrape against careers.criteo.com (the Happydance front-end
that wraps the criteo.wd3.myworkdayjobs.com Workday ATS):

1. LISTING. Paginate `?page=N` over the full board (the `?country=France`
   query param is purely cosmetic — Happydance filters client-side, the
   HTML returns all 138 regardless). Each `<div class="card card-job">`
   exposes data-id (the Workday jobReqId, e.g. `r20323`), title, the
   detail-page slug, a working style (Hybrid/On Site/Remote), one or more
   "City, Country" lines, a team name, and "Posted N days ago". We filter
   France + tech-team here, before any detail-page fetches.

2. DETAIL. For each survivor we fetch the Happydance detail page once to
   recover hiring type ("Permanent - Full Time" / "Intern - …" / "Fixed
   Term - …"), the long description, and the Workday apply URL. Anything
   that isn't a Permanent / Regular role is dropped.

Why not the Workday JSON API: the empty-facets POST works, but every
filtered POST we tried (`Country`, `locationCountry`, `country` plus
`Hiring_Type`, `Job_Category`) returns HTTP 400 with an empty body — the
tenant uses a non-standard facet key we couldn't guess from the response.
See scrapers/criteo/filters.md.
"""
from __future__ import annotations

import html
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

HOST = "https://careers.criteo.com"
LISTING_URL = f"{HOST}/en/jobs/"

# Team / department labels Happydance shows next to the briefcase icon on a
# card. These differ from the Workday Job_Category facet (R&D / IT) — we use
# the card labels because that's what the listing parser actually sees.
# Anything else (Commercial, Operations, Finance, People, Corporate Services,
# Legal & Compliance) is dropped at the listing stage.
TEAMS_IN_SCOPE: set[str] = {"Engineering", "Internal IT", "Analytics", "Product"}

# Drop anything whose detail-page hiring-type line contains one of these
# tokens (case-insensitive). Whatever is left is treated as Regular.
HIRING_TYPE_DROP_TOKENS = ("intern", "trainee", "fixed term", "ftc", "apprentice")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_DELAY_SECONDS = 2.0
REQUEST_TIMEOUT = 30
MAX_PAGES = 30  # defensive: at ~10 jobs/page we hit ~14 today


@dataclass
class Job:
    native_job_id: str        # Workday jobReqId, e.g. "r20323"
    title: str
    location: str             # "Paris, France" (or "Paris, France; Grenoble, France")
    category: str             # team label from the card ("R&D" / "Analytics" / ...)
    apply_url: str            # Workday apply URL extracted from detail HTML
    employment_type: str = "CDI"
    description: str | None = None
    posted_date: str | None = None  # YYYY-MM-DD, derived from "Posted N days ago"
    identifier: str | None = None   # detail-page slug, e.g. "senior-ai-engineer"
    raw_payload: dict | None = None


# ---------------------------------------------------------------------------
# Listing pass
# ---------------------------------------------------------------------------

POSTED_TODAY_RE = re.compile(r"posted\s+today", re.IGNORECASE)
POSTED_YESTERDAY_RE = re.compile(r"posted\s+yesterday", re.IGNORECASE)
# Matches "Posted 2 days ago" and "Posted 30+ days ago".
POSTED_DAYS_RE = re.compile(r"posted\s+(\d+)\+?\s+day", re.IGNORECASE)
# Happydance falls back to "Posted Over 30 days ago" for stale rows.
POSTED_OVER_DAYS_RE = re.compile(r"posted\s+over\s+(\d+)\s+day", re.IGNORECASE)


def _posted_to_iso(text: str, today: date) -> str | None:
    """Convert Happydance's relative date string to ISO YYYY-MM-DD.

    Approximate by design: Criteo only surfaces "Posted N days ago" on the
    card. "Over 30 days ago" is treated as exactly 30 days — these rows are
    old enough that the precise date doesn't add value, and the
    native_job_id is the dedup key so any drift is harmless.
    """
    if not text:
        return None
    text = text.strip()
    if POSTED_TODAY_RE.search(text):
        return today.isoformat()
    if POSTED_YESTERDAY_RE.search(text):
        return (today - timedelta(days=1)).isoformat()
    m = POSTED_OVER_DAYS_RE.search(text)
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    m = POSTED_DAYS_RE.search(text)
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    return None


def _parse_card(card_html, today: date) -> dict | None:
    """Extract the listing-card fields. Returns None if the card is malformed."""
    actions = card_html.select_one(".card-job-actions.js-job")
    if not actions or not actions.get("data-id"):
        return None
    job_req_id = actions["data-id"].strip()

    title_a = card_html.select_one("h2.card-title a")
    if not title_a or not title_a.get("href"):
        return None
    title = title_a.get_text(strip=True)
    detail_href = title_a["href"].strip()
    if detail_href.startswith("/"):
        detail_href = HOST + detail_href

    # job-meta = [<li with map-marker + working style + locations>, <li with briefcase + team>]
    meta_lis = card_html.select("ul.list-inline.job-meta > li.list-inline-item")
    working_style = ""
    locations: list[str] = []
    team = ""
    for li in meta_lis:
        if li.select_one("ul.job-meta-locations"):
            strong = li.find("strong")
            if strong:
                working_style = strong.get_text(strip=True)
            for loc_li in li.select("ul.job-meta-locations > li.list-inline-item"):
                locations.append(loc_li.get_text(strip=True))
        else:
            # The briefcase li is just "<svg .../>Team Name". Stripping the
            # svg via get_text gives us the label.
            team = li.get_text(strip=True)

    posted_text = ""
    posted_el = card_html.select_one("p.card-date")
    if posted_el:
        posted_text = posted_el.get_text(strip=True)

    return {
        "native_job_id": job_req_id,
        "title": title,
        "detail_url": detail_href,
        "team": team,
        "working_style": working_style,
        "locations": locations,
        "posted_date": _posted_to_iso(posted_text, today),
        "posted_text": posted_text,
    }


def _fetch_listing_page(session: requests.Session, page: int) -> str:
    params = {"page": page} if page > 1 else None
    response = session.get(
        LISTING_URL,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    # Happydance serves UTF-8 but Python sometimes guesses latin-1 from the
    # absent charset header — force it (same pattern as Sopra Steria).
    response.encoding = "utf-8"
    return response.text


def _collect_cards(session: requests.Session, today: date) -> list[dict]:
    cards: list[dict] = []
    seen_ids: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        html_text = _fetch_listing_page(session, page)
        soup = BeautifulSoup(html_text, "html.parser")
        page_cards = soup.select("div.card.card-job")
        if not page_cards:
            break
        new_on_page = 0
        for c in page_cards:
            parsed = _parse_card(c, today)
            if not parsed:
                continue
            if parsed["native_job_id"] in seen_ids:
                continue
            seen_ids.add(parsed["native_job_id"])
            cards.append(parsed)
            new_on_page += 1
        # When pagination loops past the end (Happydance keeps rendering
        # page 1) we stop on the first page that adds zero new rows.
        if new_on_page == 0:
            break
        time.sleep(REQUEST_DELAY_SECONDS)
    return cards


def _in_france(card: dict) -> bool:
    return any(loc.endswith(", France") for loc in card.get("locations", []))


def _in_tech_team(card: dict) -> bool:
    return card.get("team", "") in TEAMS_IN_SCOPE


# ---------------------------------------------------------------------------
# Detail pass
# ---------------------------------------------------------------------------

# The hiring-type / time-type line on the detail page sits in the same
# <ul class="list-inline job-meta"> as locations and team. We identify it
# by the #clock sprite reference on its <svg><use> — both team (briefcase)
# and hiring-type (clock) lis carry an svg, so a generic "li has any svg"
# heuristic mis-tags the team label.
# Word-bounded — `Intern` without \b would substring-match "Internal IT".
HIRING_TYPE_HINT_RE = re.compile(
    r"\b(Permanent|Intern(?:ship)?|Trainee|Fixed Term|FTC|Apprentice)\b",
    re.IGNORECASE,
)


def _li_is_hiring_type(li) -> bool:
    use = li.find("use")
    if not use:
        return False
    href = use.get("xlink:href") or use.get("href") or ""
    return href.endswith("#clock")


def _extract_hiring_type(soup: BeautifulSoup) -> str:
    for li in soup.select("section.hero-job ul.list-inline.job-meta > li.list-inline-item"):
        if _li_is_hiring_type(li):
            return li.get_text(" ", strip=True)
    # Fallback: scan the whole hero block for a known hiring-type token.
    hero = soup.select_one("section.hero-job")
    if hero:
        m = HIRING_TYPE_HINT_RE.search(hero.get_text(" ", strip=True))
        if m:
            return m.group(0)
    return ""


def _extract_apply_url(soup: BeautifulSoup) -> str:
    a = soup.select_one("a#js-apply-external[href]")
    if a:
        href = a["href"].strip()
        if href:
            return href
    # Fallback to any apply-now link.
    a = soup.select_one("a.js-apply-now[href]")
    return a["href"].strip() if a else ""


def _extract_description(soup: BeautifulSoup) -> str | None:
    article = soup.select_one("article.cms-content")
    if not article:
        return None
    text = article.get_text(" ", strip=True)
    text = html.unescape(text)
    return text or None


def _extract_detail_slug(detail_url: str) -> str | None:
    # /en/jobs/r20323/senior-ai-engineer/ → "senior-ai-engineer"
    m = re.search(r"/en/jobs/[^/]+/([^/]+)/?$", detail_url)
    return m.group(1) if m else None


def _is_droppable_hiring_type(hiring_type: str) -> bool:
    low = hiring_type.lower()
    return any(tok in low for tok in HIRING_TYPE_DROP_TOKENS)


def _fetch_detail(session: requests.Session, url: str) -> BeautifulSoup:
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    response.encoding = "utf-8"
    return BeautifulSoup(response.text, "html.parser")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def scrape() -> list[dict]:
    today = date.today()

    session = requests.Session()
    session.headers.update(HEADERS)

    print("Fetch phase (Happydance listing pages)...", flush=True)
    started = time.time()
    cards = _collect_cards(session, today)
    print(f"  {len(cards)} cards collected", flush=True)

    fr_tech = [c for c in cards if _in_france(c) and _in_tech_team(c)]
    print(
        f"  -> {len(fr_tech)} France + tech-team "
        f"(R&D/Analytics/IT/Product) listings",
        flush=True,
    )

    print("\nDetail phase...", flush=True)
    jobs: list[Job] = []
    for card in fr_tech:
        try:
            soup = _fetch_detail(session, card["detail_url"])
        except requests.HTTPError as exc:
            print(
                f"  detail failed for {card['native_job_id']} "
                f"({card['title']!r}): {exc}",
                flush=True,
            )
            continue

        hiring_type = _extract_hiring_type(soup)
        if _is_droppable_hiring_type(hiring_type):
            print(
                f"  skip {card['native_job_id']}: hiring_type={hiring_type!r}",
                flush=True,
            )
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        apply_url = _extract_apply_url(soup)
        if not apply_url:
            print(
                f"  skip {card['native_job_id']}: no Workday apply URL on detail page",
                flush=True,
            )
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        jobs.append(
            Job(
                native_job_id=card["native_job_id"],
                title=card["title"],
                location="; ".join(card["locations"]),
                category=card["team"],
                apply_url=apply_url,
                employment_type="CDI",
                description=_extract_description(soup),
                posted_date=card["posted_date"],
                identifier=_extract_detail_slug(card["detail_url"]),
                raw_payload={
                    "card": {
                        "team": card["team"],
                        "working_style": card["working_style"],
                        "locations": card["locations"],
                        "posted_text": card["posted_text"],
                    },
                    "hiring_type": hiring_type,
                },
            )
        )
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
        desc = (j["description"] or "")[:200]
        if j["description"] and len(j["description"]) > 200:
            desc += "..."
        print(f"[{j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
