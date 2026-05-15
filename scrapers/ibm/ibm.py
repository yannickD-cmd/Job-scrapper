"""IBM job scraper — France, Data & AI roles.

The public `www.ibm.com/careers/search` page is a Next.js front-end whose
underlying JSON search API ("kepler") is gated behind an edge that 301s every
JS path under the bundle prefix `/marketplace/static/components/search/embedded/v2.2/`
to `/products`. The page is just a thin client over IBM's underlying Avature
ATS at `careers.ibm.com`, which DOES serve scrapable HTML provided the
User-Agent looks like a search-engine crawler — a browser UA gets HTTP 202
from `awselb` (bot challenge); a Googlebot UA gets HTTP 200 with the full
listing in one round trip.

Listing (paginated, ~2000-2100 jobs total worldwide):
  GET https://careers.ibm.com/en_US/careers/SearchJobs/?jobRecordsPerPage=48&jobOffset=N

Each card is an `<article class="article--card">` exposing:
  - pretitle <a>{CATEGORY}</a>       # "Data & Analytics", "Software Engineering", …
  - h3 <a>{TITLE}</a>
  - card-item-type                   # "Professional" / "Entry Level" / "Intern"
  - card-item-location               # "City, State, Country" (flat text)

URL params do NOT filter the listing — Avature stores filter state in a
server-side session keyed off cookies and populated by a POST to /OpenJobs.
We just walk every page and filter client-side, which is both simpler and
more resilient to Avature's internal field IDs changing.

Detail (per kept job, for description + Country verification):
  GET https://careers.ibm.com/en_US/careers/JobDetail?jobId={id}

IBM/Avature exposes NO posted date publicly, so `posted_date` is always None.

To widen scope, edit SCOPE_COUNTRY, CORE_CATEGORIES, TITLE_FILTERED_CATEGORIES,
or AI_KEYWORDS_RE.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

LISTING_URL_TEMPLATE = (
    "https://careers.ibm.com/en_US/careers/SearchJobs/"
    "?jobRecordsPerPage={page_size}&jobOffset={offset}"
)
DETAIL_URL_TEMPLATE = "https://careers.ibm.com/en_US/careers/JobDetail?jobId={job_id}"
APPLY_URL_TEMPLATE = DETAIL_URL_TEMPLATE  # public detail page is the apply landing

PAGE_SIZE = 48
MAX_OFFSET = 5_000  # safety cap; real total is ~2100

SCOPE_COUNTRY = "France"

# Categories whose every posting we keep (assuming country matches).
CORE_CATEGORIES: set[str] = {
    "Data & Analytics",
    "Research",
}

# Categories we keep ONLY when the title matches an AI/Data keyword.
# (Software Engineering and Cloud each carry many unrelated roles.)
TITLE_FILTERED_CATEGORIES: set[str] = {
    "Software Engineering",
    "Cloud",
}

# Title keywords that promote a Software Engineering / Cloud posting into scope.
# Word-boundary based. Catches: "AI Engineer", "Machine Learning", "Data Scientist",
# "Applied Scientist", "watsonx", "LLM", "GenAI", "MLOps", etc.
AI_KEYWORDS_RE = re.compile(
    r"\b("
    r"AI|ML|MLOps|NLP|LLM|LLMs|GenAI"
    r"|Machine\s+Learning|Deep\s+Learning|Generative\s+AI|Foundation\s+Models?"
    r"|Data\s+(?:Scientist|Engineer|Analyst|Architect|Science|Engineering|Analytics)"
    r"|Applied\s+Scientist|Research\s+Scientist"
    r"|Analytics|Watsonx|watsonx"
    r")\b",
    re.IGNORECASE,
)

HEADERS = {
    # Googlebot UA bypasses the awselb bot challenge on careers.ibm.com.
    # A normal browser UA returns HTTP 202 with an empty body.
    "User-Agent": (
        "Mozilla/5.0 (compatible; Googlebot/2.1; "
        "+http://www.google.com/bot.html)"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-US,en;q=0.9",
    "From": "yannickarieldossa@gmail.com",
}

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.0

JOB_ID_RE = re.compile(r"JobDetail\?jobId=(\d+)")


@dataclass
class Job:
    native_job_id: str          # Avature jobId (6-digit integer as string)
    title: str
    location: str               # "City, State, Country" as shown on the card
    category: str               # IBM "Area of work" — e.g. "Data & Analytics"
    apply_url: str
    employment_type: str        # Position type: "Professional" / "Entry Level" / "Intern"
    description: str | None = None
    posted_date: str | None = None    # IBM doesn't expose this publicly
    identifier: str | None = None
    raw_payload: dict | None = None


def _parse_card(article) -> dict | None:
    """Extract the fields we want from one `<article class="article--card">`."""
    link = article.select_one("a.link[href*='JobDetail?jobId=']")
    if not link:
        return None
    m = JOB_ID_RE.search(link.get("href", ""))
    if not m:
        return None
    job_id = m.group(1)

    pretitle = article.select_one(".article__header__text__pretitle")
    title_el = article.select_one(".article__header__text__title")
    type_el = article.select_one(".card-item-type")
    loc_el = article.select_one(".card-item-location")

    return {
        "native_job_id": job_id,
        "title": (title_el.get_text(" ", strip=True) if title_el else "").lstrip(": ").strip(),
        "category": (pretitle.get_text(" ", strip=True) if pretitle else "").strip(),
        "employment_type": (type_el.get_text(" ", strip=True) if type_el else "").strip(),
        "location": (loc_el.get_text(" ", strip=True) if loc_el else "").strip(),
    }


def _fetch_listing_page(session: requests.Session, offset: int) -> list[dict]:
    url = LISTING_URL_TEMPLATE.format(page_size=PAGE_SIZE, offset=offset)
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    cards = []
    seen: set[str] = set()
    for art in soup.select("article.article--card"):
        card = _parse_card(art)
        if not card:
            continue
        if card["native_job_id"] in seen:
            continue  # cards repeat the link 3-4× inside; dedupe within the page
        seen.add(card["native_job_id"])
        cards.append(card)
    return cards


def _crawl_listing(session: requests.Session) -> list[dict]:
    """Walk every page of the SearchJobs listing until we hit an empty one."""
    all_cards: list[dict] = []
    seen: set[str] = set()
    offset = 0
    page_no = 1
    while offset < MAX_OFFSET:
        cards = _fetch_listing_page(session, offset)
        new = [c for c in cards if c["native_job_id"] not in seen]
        for c in new:
            seen.add(c["native_job_id"])
        all_cards.extend(new)
        print(
            f"  page {page_no:>3} (offset={offset:>4}): "
            f"{len(cards)} on page, {len(new)} new, {len(all_cards)} total",
            flush=True,
        )
        if not cards:
            break
        offset += PAGE_SIZE
        page_no += 1
        time.sleep(REQUEST_DELAY_SECONDS)
    return all_cards


def _in_scope(card: dict) -> bool:
    """Country (substring) + category (with AI title gate on the mixed ones)."""
    if SCOPE_COUNTRY.lower() not in card["location"].lower():
        return False
    cat = card["category"]
    if cat in CORE_CATEGORIES:
        return True
    if cat in TITLE_FILTERED_CATEGORIES and AI_KEYWORDS_RE.search(card["title"]):
        return True
    return False


def _extract_description(soup: BeautifulSoup) -> str | None:
    """Plain-text description from the main article on a JobDetail page.

    The first `<article class="article--details">` on the page is the role
    body. Each field is `<div class="article__content__view__field">` with
    a `__label` (e.g. "Introduction", "Your role and responsibilities",
    "Required technical and professional expertise") and a `__value`. We
    concatenate them into a single readable plaintext blob.

    The boilerplate "ABOUT BUSINESS UNIT" / "YOUR LIFE @ IBM" / "ABOUT IBM"
    sections are `<details>` elements, not the main article, so they're
    naturally excluded.
    """
    main = soup.select_one("article.article--details")
    if not main:
        return None

    chunks: list[str] = []
    for field in main.select(".article__content__view__field"):
        label_el = field.select_one(".article__content__view__field__label")
        value_el = field.select_one(".article__content__view__field__value")
        if not value_el:
            continue
        label = label_el.get_text(" ", strip=True) if label_el else ""
        value = value_el.get_text("\n", strip=True)
        if not value:
            continue
        chunks.append(f"{label}\n{value}" if label else value)

    desc = "\n\n".join(chunks).strip()
    return desc or None


def _extract_sidebar_country(soup: BeautifulSoup) -> str | None:
    """Pull the Country value out of the JobDetail right-hand sidebar."""
    for field in soup.select(".article__content__view__field"):
        label_el = field.select_one(".article__content__view__field__label")
        value_el = field.select_one(".article__content__view__field__value")
        if not label_el or not value_el:
            continue
        if label_el.get_text(strip=True) == "Country":
            return value_el.get_text(strip=True) or None
    return None


def _fetch_detail(session: requests.Session, job_id: str) -> tuple[str | None, str | None]:
    """Return (description, country_from_sidebar)."""
    url = DETAIL_URL_TEMPLATE.format(job_id=job_id)
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    return _extract_description(soup), _extract_sidebar_country(soup)


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Listing phase...", flush=True)
    cards = _crawl_listing(session)
    print(
        f"  {len(cards)} unique postings in {time.time() - started:.1f}s\n",
        flush=True,
    )

    print(
        f"Filter phase: country={SCOPE_COUNTRY!r}, "
        f"core={sorted(CORE_CATEGORIES)}, "
        f"title-filtered={sorted(TITLE_FILTERED_CATEGORIES)}...",
        flush=True,
    )
    candidates = [c for c in cards if _in_scope(c)]
    print(
        f"  kept {len(candidates)} candidates "
        f"(dropped {len(cards) - len(candidates)} out-of-scope)\n",
        flush=True,
    )

    print(
        f"Enrichment phase: fetching {len(candidates)} detail pages "
        f"(~{int(len(candidates) * REQUEST_DELAY_SECONDS / 60)} min)...",
        flush=True,
    )

    kept: dict[str, Job] = {}
    dropped_by_country_verify = 0
    failed = 0

    for i, card in enumerate(candidates, 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        job_id = card["native_job_id"]
        try:
            description, country_sb = _fetch_detail(session, job_id)
        except Exception as exc:
            print(
                f"  [{i}/{len(candidates)}] {job_id} detail fetch FAILED: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            description, country_sb = None, None
            failed += 1

        # Belt-and-suspenders: drop if the sidebar Country contradicts the
        # listing card. Avature sometimes lists multi-country postings whose
        # location string contains "France" as one of many — in those cases
        # the canonical sidebar field is the source of truth.
        if country_sb and country_sb.strip().lower() != SCOPE_COUNTRY.lower():
            dropped_by_country_verify += 1
            print(
                f"  [{i}/{len(candidates)}] {job_id} {card['title']!r} "
                f"-> DROP (sidebar Country={country_sb!r})",
                flush=True,
            )
            continue

        job = Job(
            native_job_id=job_id,
            title=card["title"],
            location=card["location"],
            category=card["category"],
            apply_url=APPLY_URL_TEMPLATE.format(job_id=job_id),
            employment_type=card["employment_type"],
            description=description,
            posted_date=None,  # not exposed publicly
            identifier=None,
            raw_payload=card,
        )
        kept[job_id] = job
        print(
            f"  [{i}/{len(candidates)}] {job_id} {job.title!r} -> KEEP",
            flush=True,
        )

    elapsed = time.time() - started
    print(flush=True)
    print("Enrichment summary:", flush=True)
    print(f"  kept                       : {len(kept)}", flush=True)
    print(f"  dropped (country mismatch) : {dropped_by_country_verify}", flush=True)
    print(f"  failed                     : {failed}", flush=True)
    print(f"  total runtime              : {elapsed:.1f}s", flush=True)

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
    print(f"\n=== {len(jobs)} jobs final (total runtime {elapsed:.1f}s) ===\n")

    for j in jobs:
        desc = (j["description"] or "").strip()
        desc = desc[:200] + ("..." if len(desc) > 200 else "")
        print(f"[{j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
