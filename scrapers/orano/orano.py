"""Orano job scraper — France, Data/IT/Digital, Permanent/Regular (CDI).

Orano's career portal at `jobs.orano.group` is an Avature instance (same ATS
family as IBM, so [scrapers/ibm/ibm.py](../ibm/ibm.py) is the closest template).

Listing (paginated, page-size locked at 6 by the server, ~1000-1200 worldwide):
  GET https://jobs.orano.group/en_US/jobs/SearchJobs/?jobOffset=N

Each card is an `<article class="article--result">` exposing:
  - `.article__header__text__title a`       → title + JobDetail URL (with id)
  - `.list-item-ref`                        → "Ref #14562"             (id)
  - `.list-item-location`                   → "Drôme, France"          (Region, Country)
  - `.list-item-posted`                     → "Posted 01-Sep-2026"
  - `.list-item-jobTypeOfEmployment`        → "Permanent/Regular"

URL params do NOT filter the listing — Avature stores filter state in a
server-side session keyed off cookies. We walk every page and filter
client-side. The card surfaces location + employment type but NOT the Business
Unit / Job-family, so to identify Data/IT/Digital roles we regex the title at
the card level (otherwise we would need to fetch the detail page for every
France posting, which costs ~10× more requests). Detail pages do expose
"Business Unit (BU)" and "Job" (job family) fields and we capture them into
`raw_payload` for forensics.

Detail (per kept job, for description):
  GET https://jobs.orano.group/en_US/jobs/JobDetail?jobId={id}

Both endpoints are served as static HTML with a Googlebot UA — a browser UA
also works here (unlike IBM/awselb) but we keep the Googlebot UA for symmetry
with the IBM scraper.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime

import requests
from bs4 import BeautifulSoup

LISTING_URL_TEMPLATE = (
    "https://jobs.orano.group/en_US/jobs/SearchJobs/?jobOffset={offset}"
)
DETAIL_URL_TEMPLATE = "https://jobs.orano.group/en_US/jobs/JobDetail?jobId={job_id}"
APPLY_URL_TEMPLATE = DETAIL_URL_TEMPLATE

PAGE_SIZE = 6           # server-enforced; the `jobRecordsPerPage` param is ignored
MAX_OFFSET = 3_000      # safety cap — real total is ~1100

SCOPE_COUNTRY = "France"
SCOPE_EMPLOYMENT_TYPE_RE = re.compile(r"permanent", re.IGNORECASE)

# Data / IT / Digital keyword regex applied to the job title (Orano's card does
# not surface category / Business Unit; this is the only client-side signal).
# Mix of English + French because postings are in either language.
TECH_TITLE_RE = re.compile(
    r"\b("
    r"data|big[\s\-]data|analytics?|datawarehouse"
    r"|informatique|informaticien|système[s]?\s+d['’]information|si\s+industriel"
    r"|digital|numérique"
    r"|développeur|developpeur|developer|devops|sre\b"
    r"|software|logiciel"
    r"|cyber|cybersécurité|cybersecurity|sécurité\s+(des\s+)?(si|systèmes?\s+d['’]information|informatique)"
    r"|cloud|aws|azure|gcp|kubernetes"
    r"|réseaux?|network"
    r"|machine\s+learning|deep\s+learning|intelligence\s+artificielle|\bia\b|\bai\b|llm|nlp|mlops|genai"
    r"|web|backend|frontend|fullstack|full[\-\s]stack"
    r"|sap\b|erp\b"
    r"|sysadmin|administrat(eur|rice)\s+(système|réseau|sécurité|bdd|base)"
    r"|chef\s+de\s+projet\s+(it|si|informatique|digital|données|data)"
    r"|architect[e]?\s+(it|si|informatique|système|données|data|cloud|solutions?)"
    r"|ingénieur(e)?\s+(informatique|logiciel|développement|cyber|données|data|si|cloud|devops|test\s+logiciel)"
    r"|technicien(ne)?\s+(informatique|réseau|sup(port)?\s+inform|d[ée]ploiement)"
    r"|product\s+(owner|manager)"
    r"|consultant(e)?\s+(si|informatique|digital|data|cyber)"
    r")\b",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; Job-scrapper/1.0; "
        "+mailto:yannickarieldossa@gmail.com)"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    "From": "yannickarieldossa@gmail.com",
}

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 2.0

JOB_ID_FROM_HREF_RE = re.compile(r"/JobDetail/[^/]+/(\d+)")
JOB_ID_FROM_REF_RE = re.compile(r"#(\d+)")

# Orano card date format: "Posted 01-Sep-2026"
DATE_MONTHS = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}
POSTED_RE = re.compile(r"Posted\s+(\d{2})-([A-Za-z]{3})-(\d{4})")


@dataclass
class Job:
    native_job_id: str          # Avature jobId (5-digit integer as string)
    title: str
    location: str               # "Region, Country" as shown on the card
    apply_url: str
    employment_type: str        # e.g. "Permanent/Regular"
    description: str | None = None
    category: str | None = None  # Orano "Job" (job family) from detail page
    posted_date: str | None = None    # ISO "YYYY-MM-DD"
    identifier: str | None = None
    raw_payload: dict | None = None


def _parse_posted_date(raw: str) -> str | None:
    """'Posted 01-Sep-2026' -> '2026-09-01'."""
    m = POSTED_RE.search(raw or "")
    if not m:
        return None
    day, mon_abbr, year = m.group(1), m.group(2).title(), m.group(3)
    mm = DATE_MONTHS.get(mon_abbr)
    return f"{year}-{mm}-{day}" if mm else None


def _parse_card(article) -> dict | None:
    """Extract the fields we want from one `<article class="article--result">`."""
    title_link = article.select_one(".article__header__text__title a")
    if not title_link:
        return None
    title = title_link.get_text(strip=True)
    href = title_link.get("href", "")

    ref_el = article.select_one(".list-item-ref")
    job_id = None
    if ref_el:
        m = JOB_ID_FROM_REF_RE.search(ref_el.get_text(strip=True))
        if m:
            job_id = m.group(1)
    if not job_id:
        m = JOB_ID_FROM_HREF_RE.search(href)
        if m:
            job_id = m.group(1)
    if not job_id:
        return None

    loc_el = article.select_one(".list-item-location")
    posted_el = article.select_one(".list-item-posted")
    type_el = article.select_one(".list-item-jobTypeOfEmployment")

    return {
        "native_job_id": job_id,
        "title": title,
        "location": loc_el.get_text(strip=True) if loc_el else "",
        "employment_type": type_el.get_text(strip=True) if type_el else "",
        "posted_date_raw": posted_el.get_text(strip=True) if posted_el else "",
        "posted_date": _parse_posted_date(posted_el.get_text(strip=True)) if posted_el else None,
        "detail_url": href,
    }


def _fetch_listing_page(session: requests.Session, offset: int) -> list[dict]:
    url = LISTING_URL_TEMPLATE.format(offset=offset)
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    cards = []
    seen: set[str] = set()
    for art in soup.select("article.article--result"):
        card = _parse_card(art)
        if not card:
            continue
        if card["native_job_id"] in seen:
            continue
        seen.add(card["native_job_id"])
        cards.append(card)
    return cards


def _crawl_listing(session: requests.Session) -> list[dict]:
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
            f"{len(cards):>1} on page, {len(new):>1} new, {len(all_cards):>4} total",
            flush=True,
        )
        if not cards:
            break
        offset += PAGE_SIZE
        page_no += 1
        time.sleep(REQUEST_DELAY_SECONDS)
    return all_cards


def _in_scope_card(card: dict) -> bool:
    """Country (trailing token) + employment type + tech-title match."""
    loc = card["location"] or ""
    # Country is the trailing token after the last comma: "Drôme, France".
    country = loc.rsplit(",", 1)[-1].strip().lower() if loc else ""
    if country != SCOPE_COUNTRY.lower():
        return False
    if not SCOPE_EMPLOYMENT_TYPE_RE.search(card["employment_type"] or ""):
        return False
    if not TECH_TITLE_RE.search(card["title"] or ""):
        return False
    return True


def _extract_sidebar_fields(soup: BeautifulSoup) -> dict:
    """Pull every `<label> -> <value>` pair from the JobDetail sidebar.

    Avature renders each field as `.article__content__view__field` with a
    `__label` and `__value` child. We return a label-keyed dict so callers
    can pick out 'Country', 'Business Unit (BU)', 'Job', etc. without caring
    about field order.
    """
    out: dict[str, str] = {}
    for field in soup.select(".article__content__view__field"):
        label_el = field.select_one(".article__content__view__field__label")
        value_el = field.select_one(".article__content__view__field__value")
        if not label_el or not value_el:
            continue
        label = label_el.get_text(strip=True)
        if not label:
            continue
        # Preserve first occurrence — Description appears once, but a few labels
        # (e.g. empty-string body sections) repeat in the page.
        if label in out:
            continue
        out[label] = value_el.get_text(" ", strip=True)
    return out


def _extract_description(soup: BeautifulSoup) -> str | None:
    """Concatenate the labelled body sections into one plaintext blob."""
    main = soup.select_one("article.article--details") or soup
    chunks: list[str] = []
    for field in main.select(".article__content__view__field"):
        label_el = field.select_one(".article__content__view__field__label")
        value_el = field.select_one(".article__content__view__field__value")
        if not value_el:
            continue
        label = label_el.get_text(strip=True) if label_el else ""
        value = value_el.get_text("\n", strip=True)
        if not value:
            continue
        # Skip the sidebar metadata fields we already capture structurally.
        if label in {
            "Name", "Ref #", "Posting Date", "Country", "Region", "City",
            "Business Unit (BU)", "Type of Employment", "Desired Starting Date",
            "Work Schedule", "Remote Work", "Job",
        }:
            continue
        chunks.append(f"{label}\n{value}" if label else value)
    desc = "\n\n".join(chunks).strip()
    return desc or None


def _fetch_detail(session: requests.Session, job_id: str) -> tuple[str | None, dict]:
    url = DETAIL_URL_TEMPLATE.format(job_id=job_id)
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    return _extract_description(soup), _extract_sidebar_fields(soup)


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
        f"Filter phase: country={SCOPE_COUNTRY!r}, employment~='Permanent', "
        f"title=Data/IT/Digital regex...",
        flush=True,
    )
    candidates = [c for c in cards if _in_scope_card(c)]
    print(
        f"  kept {len(candidates)} candidates "
        f"(dropped {len(cards) - len(candidates)} out-of-scope)\n",
        flush=True,
    )

    print(
        f"Enrichment phase: fetching {len(candidates)} detail pages "
        f"(~{int(len(candidates) * REQUEST_DELAY_SECONDS / 60) + 1} min)...",
        flush=True,
    )

    kept: dict[str, Job] = {}
    dropped_by_country_verify = 0
    failed = 0

    for i, card in enumerate(candidates, 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        job_id = card["native_job_id"]
        try:
            description, sidebar = _fetch_detail(session, job_id)
        except Exception as exc:
            print(
                f"  [{i}/{len(candidates)}] {job_id} detail fetch FAILED: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            description, sidebar = None, {}
            failed += 1

        # Belt-and-suspenders: drop if the sidebar Country contradicts the card.
        country_sb = (sidebar.get("Country") or "").strip()
        if country_sb and country_sb.lower() != SCOPE_COUNTRY.lower():
            dropped_by_country_verify += 1
            print(
                f"  [{i}/{len(candidates)}] {job_id} {card['title']!r} "
                f"-> DROP (sidebar Country={country_sb!r})",
                flush=True,
            )
            continue

        raw = {
            **card,
            "sidebar": sidebar,
        }

        job = Job(
            native_job_id=job_id,
            title=card["title"],
            location=card["location"],
            apply_url=APPLY_URL_TEMPLATE.format(job_id=job_id),
            employment_type=card["employment_type"],
            description=description,
            category=sidebar.get("Job"),  # Orano "Job" = job family (e.g. "IS - ...")
            posted_date=card["posted_date"],
            identifier=sidebar.get("Business Unit (BU)"),
            raw_payload=raw,
        )
        kept[job_id] = job
        print(
            f"  [{i}/{len(candidates)}] {job_id} {job.title!r} "
            f"[BU={job.identifier!r}, Job={job.category!r}] -> KEEP",
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
        print(f"  BU         : {j['identifier']}")
        print(f"  Job-family : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
