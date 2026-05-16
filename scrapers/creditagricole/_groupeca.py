"""Shared groupecreditagricole.jobs scraper for CA brands not on Talentsoft.

The group careers portal at `groupecreditagricole.jobs` is a WordPress site
that aggregates jobs across CA Group subsidiaries. Each brand has its own
SEO-friendly listing page:

  /fr/nos-marques/<brand-slug>/nos-offres/
  /fr/nos-marques/<brand-slug>/nos-offres/page/<N>/

The HTML embeds rich per-card metadata via `data-gtm-*` attributes — title,
brand (`jobEntity`), contract type, publication date, location, category —
so a single listing fetch yields everything we need. No detail-page enrichment
required.

Used by sofinco.py (CA Personal Finance & Mobility), bforbank.py and
assurances.py (Crédit Agricole Assurances). The bigger CA subsidiaries
(Amundi/LCL/CACEIS/CACIB/Indosuez) also appear on this portal but are scraped
from their dedicated Talentsoft tenants — see _talentsoft.py.
"""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

from . import _talentsoft  # only for the TECH_KEYWORDS_RE reuse

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 0.7
MAX_PAGES = 60  # safety cap; per-brand totals seen so far: BforBank ~19, Sofinco/CAA ~30-100

# Offer URL pattern on groupecreditagricole.jobs:
#   /fr/nos-offres-emploi/<brandId>-<officeId>-<familyId>-<slug>-reference--<YEAR>-<jobId>--/
REFERENCE_RE = re.compile(r"reference--([\d\-]+)--/?$", re.IGNORECASE)
DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")


@dataclass
class BrandConfig:
    company: str                 # display name for COMPANY_NAMES
    brand_slug: str              # part of the brand URL (e.g. "bforbank")
    # Substring (case-insensitive) the card's data-gtm-jobEntity must contain
    # for us to accept the row. groupeca occasionally surfaces cross-brand rows
    # on a brand page, so we double-check the entity tag.
    entity_match: str
    scope_country: str = "France"
    keywords_re: re.Pattern[str] = _talentsoft.TECH_KEYWORDS_RE


@dataclass
class Job:
    native_job_id: str           # the YEAR-NNNNNN reference from data-reference
    title: str
    location: str
    category: str | None         # the offer-job tag, e.g. "IT, Digital et Data"
    apply_url: str
    employment_type: str
    description: str | None = None
    posted_date: str | None = None
    identifier: str | None = None
    raw_payload: dict | None = None


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "From": "yannickarieldossa@gmail.com",
}


def _listing_url(cfg: BrandConfig, page: int) -> str:
    base = f"https://groupecreditagricole.jobs/fr/nos-marques/{cfg.brand_slug}/nos-offres/"
    return base if page == 1 else f"{base}page/{page}/"


def _parse_card(article) -> dict | None:
    """Pull title/url/location/category/contract/date/brand from one <article>."""
    title_link = article.select_one("h3.offer-title a")
    if not title_link:
        return None
    href = (title_link.get("href") or "").strip()
    if not href:
        return None
    title = title_link.get_text(strip=True)

    # data-reference holds "YEAR-NNNNNN" — same value also embedded in href.
    ref_input = article.select_one("input[data-reference]")
    reference = (ref_input.get("data-reference") if ref_input else "") or ""
    if not reference:
        m = REFERENCE_RE.search(href)
        if m:
            reference = m.group(1)
    if not reference:
        return None

    # data-gtm-* attributes hang off the outer article element. BeautifulSoup's
    # html.parser lower-cases attribute names, so we read the lowercased forms.
    entity = (article.get("data-gtm-jobentity") or "").strip()
    contract = (article.get("data-gtm-jobcontract") or "").strip()
    publish_raw = (article.get("data-gtm-jobpublishdate") or "").strip()
    publish = None
    dm = DATE_RE.search(publish_raw)
    if dm:
        publish = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"

    loc_el = article.select_one("li.offer-location")
    location = loc_el.get_text(" ", strip=True) if loc_el else ""

    cat_el = article.select_one("li.offer-job")
    category = cat_el.get_text(" ", strip=True) if cat_el else None

    return {
        "native_job_id": reference,
        "title": title,
        "apply_url": href,
        "entity": entity,
        "employment_type": contract,
        "posted_date": publish,
        "location": location,
        "category": category,
    }


def _fetch_listing_page(session: requests.Session, cfg: BrandConfig, page: int) -> list[dict]:
    response = session.get(_listing_url(cfg, page), timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    cards: list[dict] = []
    # Each offer is wrapped in an <article> with data-gtm-* attrs.
    for art in soup.select('article[data-gtm-jobEntity], article[data-gtm-jobentity]'):
        c = _parse_card(art)
        if c:
            cards.append(c)
    # Fallback: some pages render bare div wrappers — accept any element with
    # data-gtm-jobEntity. Keep the strict article selector first since it's
    # accurate and avoids picking up nav/footer GTM tags.
    if not cards:
        for art in soup.select("[data-gtm-jobEntity], [data-gtm-jobentity]"):
            c = _parse_card(art)
            if c:
                cards.append(c)
    return cards


def _in_scope(card: dict, cfg: BrandConfig) -> bool:
    haystack = " ".join(filter(None, [card.get("title"), card.get("category")]))
    return bool(cfg.keywords_re.search(haystack))


def scrape(cfg: BrandConfig) -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print(f"=== {cfg.company} (groupecreditagricole.jobs/{cfg.brand_slug}) ===", flush=True)
    print("Listing phase...", flush=True)

    all_cards: dict[str, dict] = {}
    for page in range(1, MAX_PAGES + 1):
        cards = _fetch_listing_page(session, cfg, page)
        new = [c for c in cards if c["native_job_id"] not in all_cards]
        for c in new:
            all_cards[c["native_job_id"]] = c
        print(
            f"  page {page:>2}: {len(cards)} on page, "
            f"{len(new)} new, {len(all_cards)} total",
            flush=True,
        )
        # Stop when an empty page (no <article>s) comes back OR when a page
        # yields zero new rows — WordPress doesn't 404 past the last page.
        if not cards or not new:
            break
        time.sleep(REQUEST_DELAY_SECONDS)

    # Cross-brand filter: defensive guard in case the brand-offers page surfaces
    # other entities. entity_match is case-insensitive substring.
    em = cfg.entity_match.lower()
    branded = [c for c in all_cards.values() if em in (c.get("entity") or "").lower()]
    print(
        f"\nBrand filter: keep entity contains {cfg.entity_match!r} -> "
        f"{len(branded)}/{len(all_cards)} rows",
        flush=True,
    )

    print(f"\nTech-keyword filter on title + category...", flush=True)
    candidates = [c for c in branded if _in_scope(c, cfg)]
    print(
        f"  {len(candidates)} candidates "
        f"(dropped {len(branded) - len(candidates)} non-tech)",
        flush=True,
    )

    print(f"\nCountry filter: keep location contains {cfg.scope_country!r}...", flush=True)
    kept: dict[str, Job] = {}
    dropped_country = 0
    for card in candidates:
        location = card.get("location") or ""
        if location and cfg.scope_country.lower() not in location.lower():
            dropped_country += 1
            continue
        job = Job(
            native_job_id=card["native_job_id"],
            title=card["title"],
            location=location,
            category=card.get("category"),
            apply_url=card["apply_url"],
            employment_type=card.get("employment_type") or "",
            description=None,
            posted_date=card.get("posted_date"),
            identifier=card["native_job_id"],
            raw_payload=card,
        )
        kept[job.native_job_id] = job

    elapsed = time.time() - started
    print(flush=True)
    print(f"Summary ({cfg.company}):", flush=True)
    print(f"  collected (listing) : {len(all_cards)}", flush=True)
    print(f"  branded             : {len(branded)}", flush=True)
    print(f"  candidates (tech)   : {len(candidates)}", flush=True)
    print(f"  kept                : {len(kept)}", flush=True)
    print(f"  dropped (country)   : {dropped_country}", flush=True)
    print(f"  total runtime       : {elapsed:.1f}s", flush=True)

    return [asdict(j) for j in kept.values()]
