"""Shared Talentsoft scraper for Crédit Agricole subsidiaries.

Five CA subsidiaries (Amundi, LCL, CACIB, CACEIS, Indosuez) run their careers
sites on Talentsoft, a classic ASP.NET WebForms ATS. Pagination is `?page=N`;
LCID picks the locale (1036=fr, 2057=en).

Two URL conventions coexist in the wild:
  - /offre-de-emploi/liste-offres.aspx     (Amundi, LCL, CACEIS)
  - /Pages/Offre/listeoffre.aspx           (CACIB, Indosuez)
Detail pages are absolute paths returned by the card's anchor href.

Two listing-card themes also coexist and we parse both:
  - legacy `<li class="ts-offer-list-item">`  (Amundi, LCL, CACEIS, Indosuez)
  - newer  `<div class="ts-offer-card">`      (CACIB)

Country scoping is the subtle part. The listing card only shows a bare city or
département ("Paris", "94 - Val-De-Marne") with no country token, so we CANNOT
decide France from it. The authoritative signal is the detail page's
`#fldlocation_location_geographicalareacollection` hierarchy, which spells out
"Europe, France, Ile-de-France, 75 - Paris" for French roles and the foreign
country otherwise ("Europe, Germany", "Europa, Italia, ..."). We gate on that.
(A previous version gated on the bare card location, so once the old sidebar
selector drifted to None every French role was silently dropped — that is the
bug this module now avoids.)

This module factors out the crawl/filter/enrich loop so each subsidiary file
only specifies its tenant URL and keeps the COMPANY_NAMES key. Kept separate
from scrapers/dassault/aviation.py, which is older and self-contained.
"""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.0
MAX_PAGES = 60  # safety cap; real maxes seen: LCL 24, Amundi 9, CACIB 24

# Broad IT/Tech keyword filter — French + English. Matches against the card
# title attribute (Talentsoft puts the JobDescription/category there) and the
# rendered title text. Word-boundary, case-insensitive.
TECH_KEYWORDS_RE = re.compile(
    r"\b("
    # AI / Data
    r"AI|IA|ML|MLOps|NLP|LLM|LLMs|GenAI"
    r"|Machine\s+Learning|Deep\s+Learning|Generative\s+AI"
    r"|Data\s+(?:Scientist|Engineer|Analyst|Architect|Science|Engineering|Analytics|Steward|Officer)"
    r"|Données|Donnees|Intelligence\s+Artificielle"
    r"|Analytics|Analytique|Statisticien|Statisticienne"
    # Software / Dev
    r"|Développeu(?:r|se)|Developpeu(?:r|se)|Developer|Programmeu(?:r|se)|Programmer"
    r"|Software\s+(?:Engineer|Developer|Architect)|Ingénieur\s+(?:logiciel|d[ée]veloppement)"
    r"|Full[\s\-]?Stack|Back[\s\-]?End|Front[\s\-]?End|Backend|Frontend"
    r"|Java|Python|Scala|\.NET|Kotlin|Swift|Angular|React|Node"
    r"|API|Microservices?|Web\s+Services?"
    # Infra / Cloud / Ops / Cyber
    r"|DevOps|SRE|Site\s+Reliability|Cloud|AWS|Azure|GCP|Kubernetes|Docker"
    r"|Infrastructure|Réseau|Reseau|Network\s+Engineer|Système|Systeme|Sysadmin"
    r"|Cybersécurité|Cybersecurite|Cyber[\s\-]?Sécurit[ée]|Cybersecurity|Security\s+(?:Engineer|Analyst|Officer)"
    r"|SOC|SIEM|RSSI|CISO|Pentest|Pentester"
    # IT operational
    r"|IT|SI(?:\W|$)|Informatique|Digital|Tech(?:nique|nology)?"
    r"|Architect[ue]?|Architecte|Tech\s+Lead|Lead\s+Tech"
    r"|Product\s+Owner|Product\s+Manager|Scrum\s+Master|Agile\s+Coach"
    r"|Chef\s+de\s+Projet\s+(?:SI|Informatique|Digital|IT)"
    # QA / Tests / Support
    r"|QA|Test(?:eur|euse)?|Testing|Automation\s+Engineer"
    r"|Support\s+(?:N\d|technique|applicatif|informatique)"
    # Misc tech
    r"|Robotic|Automation|RPA|UX|UI|Designer\s+UX"
    r")\b",
    re.IGNORECASE,
)

# Talentsoft URL pattern: /<dir>/<slug>_<offerId>.aspx
OFFER_ID_RE = re.compile(r"_(\d+)\.aspx", re.IGNORECASE)
# Title attribute is sometimes "Title (Réf. : YEAR-OFFERID) - JobDescription"
# (Dassault Aviation), sometimes just "YEAR-OFFERID" (Amundi/LCL). We handle
# both: parse if structured, otherwise fall back to data-reference.
TITLE_ATTR_RE = re.compile(
    r"^(?P<title>.+?)\s*\(R[ée]f\.\s*:\s*(?P<ref>[\d\-]+)\)\s*-\s*(?P<job>.+)$",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


@dataclass
class TenantConfig:
    company: str                  # display name passed to run.py / Supabase
    base: str                     # https://jobs.amundi.com
    listing_path: str             # /offre-de-emploi/liste-offres.aspx
    lcid: int = 1036              # 1036 = fr-FR, 2057 = en-US
    scope_country: str = "France"
    keywords_re: re.Pattern[str] = TECH_KEYWORDS_RE


@dataclass
class Job:
    native_job_id: str            # Talentsoft offerId
    title: str
    location: str
    category: str | None
    apply_url: str
    employment_type: str
    description: str | None = None
    posted_date: str | None = None
    identifier: str | None = None
    raw_payload: dict | None = None


def _ua_headers(cfg: TenantConfig) -> dict:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.7,en;q=0.5",
        "Referer": cfg.base + "/",
        "From": "yannickarieldossa@gmail.com",
    }


def _parse_card(item, base: str) -> dict | None:
    """Dispatch on theme: legacy ts-offer-list-item vs newer ts-offer-card."""
    if item.select_one("a.ts-offer-card__title-link"):
        return _parse_card_fs(item, base)
    return _parse_card_legacy(item, base)


def _parse_card_fs(card, base: str) -> dict | None:
    """Newer Talentsoft theme (CACIB): <div class="ts-offer-card">.

    The card carries less metadata than the legacy one: title link whose
    `title` attribute is the reference (e.g. "2026-109444"), and a content
    <ul> of [contract, country, city]. No publish date on the card.
    """
    title_link = card.select_one("a.ts-offer-card__title-link")
    if not title_link:
        return None
    href = title_link.get("href", "")
    m = OFFER_ID_RE.search(href)
    if not m:
        return None
    offer_id = m.group(1)

    title_clean = title_link.get_text(strip=True)
    reference = (title_link.get("title") or "").strip() or None
    if not reference:
        fav = card.select_one("[data-reference]")
        if fav:
            reference = (fav.get("data-reference") or "").strip() or None

    # Content list is [contract, country, city] (country/city optional). The
    # country word is a useful fallback for the scope gate when the detail
    # page can't be fetched.
    items = [el.get_text(strip=True) for el in card.select("ul.ts-offer-card-content__list > li")]
    items = [s for s in items if s]
    contract = items[0] if items else ""
    location_card = ", ".join(items[1:]) if len(items) > 1 else ""

    detail_url = href if href.startswith("http") else base + href
    return {
        "native_job_id": offer_id,
        "title": title_clean,
        "category_card": None,
        "reference": reference,
        "employment_type": contract,
        "location_card": location_card,
        "posted_date": None,
        "detail_url": detail_url,
    }


def _parse_card_legacy(li, base: str) -> dict | None:
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

    desc_lis = [el.get_text(strip=True) for el in li.select("ul.ts-offer-list-item__description > li")]
    date_str = next((s for s in desc_lis if DATE_RE.match(s)), None)
    contract = ""
    location_card = ""
    for s in desc_lis:
        if DATE_RE.match(s) or s.lower().startswith("réf") or s.lower().startswith("ref"):
            continue
        if not contract:
            contract = s
        else:
            location_card = s

    posted = None
    if date_str:
        dm = DATE_RE.match(date_str)
        if dm:
            posted = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"

    detail_url = href if href.startswith("http") else base + href
    return {
        "native_job_id": offer_id,
        "title": title_clean,
        "category_card": category,
        "reference": reference,
        "employment_type": contract,
        "location_card": location_card,
        "posted_date": posted,
        "detail_url": detail_url,
    }


def _fetch_listing_page(session: requests.Session, cfg: TenantConfig, page: int) -> list[dict]:
    url = f"{cfg.base}{cfg.listing_path}?page={page}&LCID={cfg.lcid}"
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    cards = []
    # `.ts-offer-card` matches only the newer container <div> (its children use
    # distinct `ts-offer-card__*` tokens), so the two selectors don't overlap.
    for item in soup.select("li.ts-offer-list-item, .ts-offer-card"):
        card = _parse_card(item, cfg.base)
        if card:
            cards.append(card)
    return cards


def _extract_geo(soup: BeautifulSoup) -> str | None:
    """The detail page's authoritative location hierarchy.

    `<p id="fldlocation_location_geographicalareacollection">` holds e.g.
    "Europe, France, Ile-de-France, 75 - Paris" (French) or "Europe, Germany"
    (foreign). This is the only place that names the country, so it drives the
    scope gate. Returns None if the field is absent.
    """
    el = soup.select_one("#fldlocation_location_geographicalareacollection")
    if el:
        return el.get_text(" ", strip=True) or None
    return None


def _extract_description(soup: BeautifulSoup) -> str | None:
    body = soup.select_one("#contenu-ficheoffre")
    if not body:
        return None
    # Drop the right-hand sidebar to avoid Localisation/Contract metadata
    # leaking into the description text.
    for td in body.select('td[style*="edf2f9"]'):
        td.decompose()
    text = body.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip() or None


def _fetch_detail(session: requests.Session, url: str) -> tuple[str | None, str | None]:
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    geo = _extract_geo(soup)
    description = _extract_description(soup)
    return description, geo


def _in_scope_preliminary(card: dict, cfg: TenantConfig) -> bool:
    text = " ".join(filter(None, [card.get("title"), card.get("category_card")]))
    return bool(cfg.keywords_re.search(text))


def _passes_country(geo: str | None, card_loc: str | None, country: str) -> bool:
    """Decide France-scope. The detail-page geo hierarchy is authoritative; it
    names the country explicitly. Fall back to the card location only when geo
    is missing — the newer card theme carries the country word there too. With
    no location info at all, keep the row (the dashboard applies the region cut).
    """
    c = country.lower()
    if geo:
        return c in geo.lower()
    if card_loc:
        return c in card_loc.lower()
    return True


def scrape(cfg: TenantConfig) -> list[dict]:
    session = requests.Session()
    session.headers.update(_ua_headers(cfg))

    started = time.time()
    print(f"=== {cfg.company} (Talentsoft @ {cfg.base}) ===", flush=True)
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
        # Talentsoft clamps `?page=N` past the end to the last page, so stop
        # when a page returns zero new offers (or zero offers at all).
        if not cards or not new:
            break
        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"\nFilter phase: keep titles/categories matching tech keywords...", flush=True)
    candidates = [c for c in all_cards.values() if _in_scope_preliminary(c, cfg)]
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
            description, geo = _fetch_detail(session, card["detail_url"])
        except Exception as exc:
            print(
                f"  [{i}/{len(candidates)}] {card['native_job_id']} detail FAILED: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            description, geo = None, None
            failed += 1

        if not _passes_country(geo, card.get("location_card"), cfg.scope_country):
            dropped_country += 1
            print(
                f"  [{i}/{len(candidates)}] {card['native_job_id']} "
                f"{card['title']!r} -> DROP (geo={geo!r}, card={card.get('location_card')!r})",
                flush=True,
            )
            continue

        # Card location is cleaner for display/IDF detection than the verbose
        # geo hierarchy; fall back to geo when the card had none.
        location = card.get("location_card") or geo or ""
        job = Job(
            native_job_id=card["native_job_id"],
            title=card["title"],
            location=location,
            category=card.get("category_card"),
            apply_url=card["detail_url"],
            employment_type=card.get("employment_type") or "",
            description=description,
            posted_date=card.get("posted_date"),
            identifier=card.get("reference"),
            raw_payload=card,
        )
        kept[job.native_job_id] = job
        print(
            f"  [{i}/{len(candidates)}] {job.native_job_id} {job.title!r} -> KEEP",
            flush=True,
        )

    elapsed = time.time() - started
    print(flush=True)
    print(f"Summary ({cfg.company}):", flush=True)
    print(f"  collected (listing) : {len(all_cards)}", flush=True)
    print(f"  candidates (tech)   : {len(candidates)}", flush=True)
    print(f"  kept                : {len(kept)}", flush=True)
    print(f"  dropped (country)   : {dropped_country}", flush=True)
    print(f"  detail fetch failed : {failed}", flush=True)
    print(f"  total runtime       : {elapsed:.1f}s", flush=True)

    return [asdict(j) for j in kept.values()]
