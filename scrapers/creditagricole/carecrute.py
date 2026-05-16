"""ca-recrute.fr scraper — Crédit Agricole Recrute aggregator (Teamtailor).

www.ca-recrute.fr is the Teamtailor-powered career hub that fronts ~30 regional
caisses (Crédit Agricole Lorraine, Centre-Est, Alpes Provence, Anjou Maine,
Atlantique Vendée, Aquitaine, Brie Picardie, Centre France, Côtes d'Armor,
Nord-Est, …) plus group companies including CAMCA, CA Business Digital, CA
Titres, Doxallia, IFCAM, FNCA and CA Technologies. ~800 jobs total.

Crawl strategy:
  1. LISTING. Paginated `/fr/jobs?page=N`, 20 cards per page, ~40 pages.
     Each card links to the brand's own Teamtailor tenant
     (`https://recrutement.ca-<brand>.fr/jobs/<id>-<slug>`). The card exposes
     title, department label, and city — enough for a cheap tech-keyword
     pre-filter so we don't fetch ~700 non-tech detail pages.

  2. ENRICHMENT. For each surviving card, fetch the detail page on its brand
     subdomain and parse the embedded JSON-LD `JobPosting` block (Teamtailor
     emits one per offer) for description + structured location.

The /jobs.rss feed only returns the 100 most-recent items (Teamtailor cap), so
HTML crawl is required to reach the full catalogue.

Scope: All IT/tech roles, France only.
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from . import _talentsoft  # only for the TECH_KEYWORDS_RE pattern reuse

LISTING_URL = "https://www.ca-recrute.fr/fr/jobs?page={page}"
MAX_PAGES = 50  # safety cap; real count ≈ 40
REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.5
LISTING_RETRY_BACKOFFS = (5, 15, 45)  # seconds between attempts on 5xx/429

SCOPE_COUNTRY = "France"
TECH_KEYWORDS_RE = _talentsoft.TECH_KEYWORDS_RE

# Card href format on www.ca-recrute.fr is
# https://recrutement.ca-<brand>.fr/jobs/<id>-<slug>
# The numeric id is the stable Teamtailor job id we use as native_job_id.
JOB_ID_RE = re.compile(r"/jobs/(\d+)(?:[-/]|$)")

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


@dataclass
class Job:
    native_job_id: str           # numeric Teamtailor job id (stable per brand tenant)
    title: str
    location: str
    category: str | None         # "department · role" from the listing card
    apply_url: str
    employment_type: str         # parsed from JSON-LD if present, else ""
    description: str | None = None
    posted_date: str | None = None
    identifier: str | None = None   # "<brand_subdomain>:<job_id>" to disambiguate
    raw_payload: dict | None = None


def _brand_from_url(url: str) -> str:
    """Pull the brand subdomain ('ca-lorraine', 'ca-centrest', …) from a card href."""
    host = urlparse(url).hostname or ""
    # recrutement.ca-lorraine.fr -> ca-lorraine
    parts = host.split(".")
    if len(parts) >= 2 and parts[0] in ("recrutement", "recrute", "jobs"):
        return parts[1]
    return host


def _parse_listing(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    cards: list[dict] = []
    seen: set[str] = set()
    for a in soup.select('a[href*="/jobs/"]'):
        href = (a.get("href") or "").strip()
        m = JOB_ID_RE.search(href)
        if not m or not href.startswith("http"):
            continue
        job_id = m.group(1)
        # Same job sometimes appears twice (the figure link + the title link).
        key = f"{_brand_from_url(href)}:{job_id}"
        if key in seen:
            continue
        seen.add(key)

        title_span = a.select_one('span[title]')
        title = (title_span.get("title").strip() if title_span and title_span.get("title")
                 else a.get_text(" ", strip=True))

        # Department + city sit in sibling <span>s under the card text container.
        meta_spans = [s.get_text(strip=True) for s in a.select("div.mt-1 span")]
        # Pattern: ["<Department>", "·", "<CITY>"] (· may be absent).
        meta_spans = [s for s in meta_spans if s and s != "·" and s != "·"]
        department = meta_spans[0] if meta_spans else ""
        city = meta_spans[1] if len(meta_spans) > 1 else ""

        cards.append({
            "native_job_id": job_id,
            "title": title,
            "department": department,
            "city": city,
            "brand": _brand_from_url(href),
            "apply_url": href,
        })
    return cards


def _fetch_listing_page(session: requests.Session, page: int) -> list[dict]:
    url = LISTING_URL.format(page=page)
    last_exc: Exception | None = None
    for attempt, backoff in enumerate((0, *LISTING_RETRY_BACKOFFS), start=1):
        if backoff:
            print(
                f"  page {page}: retry {attempt - 1}/{len(LISTING_RETRY_BACKOFFS)} "
                f"after {backoff}s ({type(last_exc).__name__})",
                flush=True,
            )
            time.sleep(backoff)
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT)
            if response.status_code in (429, 500, 502, 503, 504):
                response.raise_for_status()
            response.raise_for_status()
            return _parse_listing(response.text)
        except requests.RequestException as exc:
            last_exc = exc
    assert last_exc is not None
    raise last_exc


def _parse_json_ld(html: str) -> dict | None:
    """Return the first JSON-LD JobPosting found in the detail page, if any."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "{}")
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def _format_location_from_jobposting(jp: dict) -> tuple[str, str]:
    """Return (location_text, country) from JSON-LD jobLocation."""
    loc = jp.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if not isinstance(loc, dict):
        return "", ""
    addr = loc.get("address") or {}
    parts = [addr.get("addressCountry"), addr.get("addressRegion"),
             addr.get("addressLocality"), addr.get("streetAddress")]
    parts = [p for p in parts if p]
    return ", ".join(parts), (addr.get("addressCountry") or "")


def _fetch_detail(session: requests.Session, url: str) -> dict:
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    jp = _parse_json_ld(response.text) or {}
    description = jp.get("description") or ""
    if description:
        # JSON-LD description is HTML — strip tags, collapse whitespace.
        description = BeautifulSoup(description, "html.parser").get_text("\n", strip=True)
        description = re.sub(r"\n{3,}", "\n\n", description)
    location_text, country = _format_location_from_jobposting(jp)
    return {
        "description": description or None,
        "location_text": location_text,
        "country": country,
        "posted_date": (jp.get("datePosted") or "")[:10] or None,
        "employment_type": jp.get("employmentType") or "",
        "hiring_org": ((jp.get("hiringOrganization") or {}).get("name") or ""),
    }


def _in_scope(card: dict) -> bool:
    haystack = " ".join(filter(None, [card.get("title"), card.get("department")]))
    return bool(TECH_KEYWORDS_RE.search(haystack))


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("=== Crédit Agricole Recrute (ca-recrute.fr Teamtailor aggregator) ===", flush=True)
    print("Listing phase...", flush=True)

    all_cards: dict[str, dict] = {}
    for page in range(1, MAX_PAGES + 1):
        cards = _fetch_listing_page(session, page)
        new = [c for c in cards if f"{c['brand']}:{c['native_job_id']}" not in all_cards]
        for c in new:
            all_cards[f"{c['brand']}:{c['native_job_id']}"] = c
        print(
            f"  page {page:>2}: {len(cards)} on page, "
            f"{len(new)} new, {len(all_cards)} total",
            flush=True,
        )
        if not cards:
            break
        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"\nFilter phase: tech-keyword pre-filter on title + department...", flush=True)
    candidates = [c for c in all_cards.values() if _in_scope(c)]
    print(
        f"  {len(candidates)} candidates "
        f"(dropped {len(all_cards) - len(candidates)} non-tech before detail fetch)",
        flush=True,
    )

    print(
        f"\nEnrichment phase: fetching {len(candidates)} detail pages "
        f"(~{int(len(candidates) * REQUEST_DELAY_SECONDS)}s)...",
        flush=True,
    )

    kept: dict[str, Job] = {}
    dropped_country = 0
    failed = 0
    for i, card in enumerate(candidates, 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            detail = _fetch_detail(session, card["apply_url"])
        except Exception as exc:
            print(
                f"  [{i}/{len(candidates)}] {card['native_job_id']} detail FAILED: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            detail = {"description": None, "location_text": "", "country": "",
                      "posted_date": None, "employment_type": "", "hiring_org": ""}
            failed += 1

        country = detail.get("country") or ""
        # Country may be empty when Teamtailor doesn't populate JSON-LD; fall
        # back to "France" since ca-recrute.fr brands are all France-located.
        if country and country.lower() not in ("france", "fr"):
            dropped_country += 1
            print(
                f"  [{i}/{len(candidates)}] {card['native_job_id']} "
                f"{card['title']!r} -> DROP (country={country!r})",
                flush=True,
            )
            continue

        location = detail["location_text"] or (
            f"{card['city']}, France" if card.get("city") else "France"
        )
        category = card["department"] or None

        # Identifier: <brand>:<jobid> so two brands that happen to share the
        # same Teamtailor numeric id don't collide in Supabase. native_job_id
        # is the same composite string so dedupe works per-row.
        composite_id = f"{card['brand']}:{card['native_job_id']}"

        job = Job(
            native_job_id=composite_id,
            title=card["title"],
            location=location,
            category=category,
            apply_url=card["apply_url"],
            employment_type=detail.get("employment_type") or "",
            description=detail.get("description"),
            posted_date=detail.get("posted_date"),
            identifier=composite_id,
            raw_payload={**card, **{"hiring_org": detail.get("hiring_org") or ""}},
        )
        kept[composite_id] = job
        print(
            f"  [{i}/{len(candidates)}] {card['brand']}/{card['native_job_id']} "
            f"{job.title!r} -> KEEP",
            flush=True,
        )

    elapsed = time.time() - started
    print(flush=True)
    print("Summary (Crédit Agricole Recrute):", flush=True)
    print(f"  collected (listing) : {len(all_cards)}", flush=True)
    print(f"  candidates (tech)   : {len(candidates)}", flush=True)
    print(f"  kept                : {len(kept)}", flush=True)
    print(f"  dropped (country)   : {dropped_country}", flush=True)
    print(f"  detail fetch failed : {failed}", flush=True)
    print(f"  total runtime       : {elapsed:.1f}s", flush=True)

    return [asdict(j) for j in kept.values()]


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    jobs = scrape()
    print(f"\n=== {len(jobs)} jobs final ===\n")
    for j in jobs[:30]:
        print(f"[{j['native_job_id']}] {j['title']}")
        print(f"  Category : {j['category']}")
        print(f"  Location : {j['location']}")
        print(f"  Brand    : {(j.get('raw_payload') or {}).get('brand')}")
        print(f"  Apply    : {j['apply_url']}")
        print()
