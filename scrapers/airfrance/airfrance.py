"""Air France (AFKL group) job scraper — France, Data/AI + Software/IT, CDI.

Air France's careers site `recrutement.airfrance.com` is a Talentsoft (ASP.NET
WebForms) tenant, the same ATS family and CSS skin as Dassault Aviation
(`ts-offer-list-item*` cards, `?LCID=1036` locale, `_<offerId>.aspx` detail
URLs). The board is the group entity **"AFKL - GROUPE AIR FRANCE KLM"** — a
single France-only board that spans Air France, Air France-KLM (holding) and
HOP!. (KLM Netherlands lives on a separate board, out of scope.)

Listing (paginated, 50 offers per page, ~92 offers → 2 pages):
  GET https://recrutement.airfrance.com/job/list-of-jobs.aspx?LCID=1036&page=<N>

Each `<li class="ts-offer-list-item">` exposes:
  - a.ts-offer-list-item__title-link @href   : detail path (offerId = trailing _NNNNN)
  - a...@title                               : "Title (Réf. : YEAR-OFFERID) - JobDescription"
  - [data-reference]                         : "YEAR-OFFERID" posting reference
  - ul.ts-offer-list-item__description > li  : [contract, region]  (NO date, NO city)

Detail page (per kept offer, for description + canonical location):
  GET https://recrutement.airfrance.com<detail path>?LCID=1036
  Structured Talentsoft fields carry stable, locale-independent ids:
  - #fldlocation_location_geographicalareacollection : "France, <region>, <dept>"
  - #fldlocation_joblocation                         : street/site address
  - #fldjobdescription_longtext1 / _description1 / _description2 : the JD prose
  - #fldjobdescription_primaryprofile                : full category path

Scope decisions (locked with the user):
  - Country  : France only. The board is structurally France-only (JobCountry
               facet = France(92)); a soft France guard on the detail location
               catches any future non-FR AFKL role.
  - Families : Data/AI + Software/IT. There is NO clean IT family facet on the
               results page and Data leaks across families — the flagship
               "Senior Data Analyst" is filed under *Stratégie / Etudes et
               Performance*, "Data Analyst Commercial" under *e-Marketing*. So
               we crawl the whole board and gate on the shared is_tech_role()
               title/category predicate (like Schneider/Danone) rather than a
               facet. No `?changefacet=1` is used, so the Talentsoft facet /
               session-poisoning quirk is sidestepped entirely.
  - Contract : CDI only. Gated client-side on the card's contract field.

posted_date is intentionally None: the board exposes no publication date
(the card has none; the only detail date is "Wished start date", i.e. a start
date, not a posting date). Dedup is by native_job_id, so this is harmless.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

from scrapers._relevance import is_tech_role

BASE = "https://recrutement.airfrance.com"
LISTING_URL_TEMPLATE = BASE + "/job/list-of-jobs.aspx?LCID=1036&page={page}"

MAX_PAGES = 20          # safety cap; real total ≈ 2 pages (50/page)
SCOPE_COUNTRY = "France"
KEEP_CONTRACT = "CDI"   # exact card contract label to keep

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36 "
        "(+job-scrapper; yannickarieldossa@gmail.com)"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.7,en;q=0.5",
    "Referer": BASE + "/homepage.aspx?LCID=1036",
    "From": "yannickarieldossa@gmail.com",
}

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 2.0   # server-rendered HTML → be polite

# Talentsoft URL pattern: /offre-de-emploi/<slug>_<offerId>.aspx
OFFER_ID_RE = re.compile(r"_(\d+)\.aspx", re.IGNORECASE)
# Title attribute format: "Title (Réf. : 2026-24643) - JobDescription"
TITLE_ATTR_RE = re.compile(
    r"^(?P<title>.+?)\s*\(R[ée]f\.\s*:\s*(?P<ref>[\d\-]+)\)\s*-\s*(?P<job>.+)$",
    re.IGNORECASE,
)

# Detail-page description blocks (Talentsoft field ids), in reading order.
DESC_FIELD_IDS = (
    "fldjobdescription_longtext1",     # contextual information
    "fldjobdescription_description1",  # job description
    "fldjobdescription_description2",  # sought profile
)


@dataclass
class Job:
    native_job_id: str          # Talentsoft offerId (e.g. "24643")
    title: str
    location: str               # "France, <region>, <dept>" (detail) or card region
    category: str | None        # Talentsoft JobDescription (e.g. "Etudes et Performance")
    apply_url: str              # public detail page (carries the "Je postule" form)
    employment_type: str        # raw contract type from the card ("CDI")
    description: str | None = None
    posted_date: str | None = None   # always None — board has no publication date
    identifier: str | None = None    # full reference "YEAR-OFFERID"
    raw_payload: dict | None = None


def _parse_card(li) -> dict | None:
    title_link = li.select_one("a.ts-offer-list-item__title-link")
    if not title_link:
        return None
    href = title_link.get("href", "")
    m = OFFER_ID_RE.search(href)
    if not m:
        return None
    offer_id = m.group(1)

    raw_title_attr = (title_link.get("title") or "").strip()
    title_clean = title_link.get_text(strip=True)
    category = None
    reference = None
    tm = TITLE_ATTR_RE.match(raw_title_attr)
    if tm:
        reference = tm.group("ref").strip()
        category = tm.group("job").strip()
    if not reference:
        fav = li.select_one("[data-reference]")
        if fav:
            reference = (fav.get("data-reference") or "").strip() or None

    # Card description list is [contract, region] — stay defensive about order.
    desc_lis = [el.get_text(strip=True)
                for el in li.select("ul.ts-offer-list-item__description > li")]
    contract = desc_lis[0] if desc_lis else ""
    region_card = desc_lis[1] if len(desc_lis) > 1 else ""

    return {
        "native_job_id": offer_id,
        "title": title_clean,
        "category_card": category,
        "reference": reference,
        "employment_type": contract,
        "region_card": region_card,
        "detail_path": href,
    }


def _fetch_listing_page(session: requests.Session, page: int) -> list[dict]:
    url = LISTING_URL_TEMPLATE.format(page=page)
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    cards = []
    for li in soup.select("li.ts-offer-list-item"):
        card = _parse_card(li)
        if card:
            cards.append(card)
    return cards


def _clean_text(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _fetch_detail(session: requests.Session, path: str) -> tuple[str | None, str | None, str | None]:
    """Return (description, location, category_detail) from a detail page."""
    url = BASE + path
    if "LCID=" not in url:
        url += ("&" if "?" in url else "?") + "LCID=1036"
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    loc_el = soup.select_one("#fldlocation_location_geographicalareacollection")
    location = loc_el.get_text(" ", strip=True) if loc_el else None

    cat_el = soup.select_one("#fldjobdescription_primaryprofile")
    category_detail = cat_el.get_text(" ", strip=True) if cat_el else None

    parts = []
    for fid in DESC_FIELD_IDS:
        el = soup.select_one(f"#{fid}")
        if el:
            txt = el.get_text("\n", strip=True)
            if txt:
                parts.append(txt)
    description = _clean_text("\n\n".join(parts)) if parts else None

    return description, location, category_detail


def _in_scope(card: dict) -> bool:
    """CDI + tech/data title-or-category gate (category leaks, so check both)."""
    if (card.get("employment_type") or "").strip().upper() != KEEP_CONTRACT:
        return False
    title = card.get("title") or ""
    cat = card.get("category_card") or ""
    return is_tech_role(title, cat) or is_tech_role(cat)


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)
    started = time.time()

    print("Listing phase...", flush=True)
    all_cards: dict[str, dict] = {}
    for page in range(1, MAX_PAGES + 1):
        cards = _fetch_listing_page(session, page)
        new = [c for c in cards if c["native_job_id"] not in all_cards]
        for c in new:
            all_cards[c["native_job_id"]] = c
        print(
            f"  page {page:>2}: {len(cards)} on page, "
            f"{len(new)} new, {len(all_cards)} total",
            flush=True,
        )
        # Talentsoft clamps ?page=N past the end to the last page, so stop on an
        # empty page OR a page that yields nothing new (would otherwise loop).
        if not cards or not new:
            break
        time.sleep(REQUEST_DELAY_SECONDS)

    print(
        f"\nFilter phase: contract={KEEP_CONTRACT!r}, "
        f"gate=is_tech_role(title|category)...",
        flush=True,
    )
    candidates = [c for c in all_cards.values() if _in_scope(c)]
    print(
        f"  {len(candidates)} candidates "
        f"(dropped {len(all_cards) - len(candidates)} out-of-scope before detail fetch)",
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
        description = location = category_detail = None
        try:
            description, location, category_detail = _fetch_detail(
                session, card["detail_path"]
            )
        except Exception as exc:
            print(
                f"  [{i}/{len(candidates)}] {card['native_job_id']} detail FAILED: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            failed += 1

        # France guard: the board is France-only, but if the detail location is
        # present and clearly not France, drop it. Missing detail → keep (the
        # card region is always a French region on this board).
        if location and SCOPE_COUNTRY.lower() not in location.lower():
            dropped_country += 1
            print(
                f"  [{i}/{len(candidates)}] {card['native_job_id']} "
                f"{card['title']!r} -> DROP (location={location!r})",
                flush=True,
            )
            continue

        resolved_location = location or card.get("region_card") or ""
        job = Job(
            native_job_id=card["native_job_id"],
            title=card["title"],
            location=resolved_location,
            category=card.get("category_card") or category_detail,
            apply_url=BASE + card["detail_path"],
            employment_type=card.get("employment_type") or "",
            description=description,
            posted_date=None,
            identifier=card.get("reference"),
            raw_payload={**card, "category_detail": category_detail},
        )
        kept[job.native_job_id] = job
        print(
            f"  [{i}/{len(candidates)}] {job.native_job_id} {job.title!r} -> KEEP "
            f"({resolved_location})",
            flush=True,
        )

    elapsed = time.time() - started
    print(flush=True)
    print("Summary:", flush=True)
    print(f"  collected (listing) : {len(all_cards)}", flush=True)
    print(f"  candidates (scope)  : {len(candidates)}", flush=True)
    print(f"  kept                : {len(kept)}", flush=True)
    print(f"  dropped (country)   : {dropped_country}", flush=True)
    print(f"  detail fetch failed : {failed}", flush=True)
    print(f"  total runtime       : {elapsed:.1f}s", flush=True)

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
        desc = (j["description"] or "")[:200]
        desc = desc + ("..." if len(j["description"] or "") > 200 else "")
        print(f"[{j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Reference  : {j['identifier']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
