"""Dassault Aviation job scraper — France, broad Data/AI scope.

Dassault Aviation runs its careers site on Talentsoft (the
`carriere.dassault-aviation.com` domain redirects to a
`dassault-aviation-cand.talent-soft.com` Talentsoft tenant). The site is a
classic ASP.NET WebForms front-end with server-rendered HTML listings.

Listing (paginated, 10 offers per page, ~77 offers total → ~8 pages):
  GET https://carriere.dassault-aviation.com/offre-de-emploi/liste-toutes-offres.aspx?page=<N>&LCID=1036

IT-family pre-fetch (used to bypass title-only matching for SI roles):
  GET https://carriere.dassault-aviation.com/offre-de-emploi/liste-toutes-offres.aspx?changefacet=1&facet_JobFamily=5817

Each `<li class="ts-offer-list-item">` exposes:
  - onclick / a[href]            : detail page URL (offerId is the trailing _NNNNN)
  - h3 > a@title                 : "Title (Réf. : YEAR-OFFERID) - JobDescription"
  - span[data-reference]         : "YEAR-OFFERID" (Talentsoft posting reference)
  - ul > li[1..4]                : Réf, date (DD/MM/YYYY), contract type, location

Detail page (per kept offer, for full description + canonical Localisation):
  GET https://carriere.dassault-aviation.com/offre-de-emploi/<slug>_<offerId>.aspx

LCID=1036 is the French locale; the site has no full English variant, so all
job content is in French. All offers are France-located (Dassault Aviation
operates exclusively from French sites) but we still verify via the detail
sidebar's "Localisation" field.

To widen scope, edit ALWAYS_KEEP_JOB_FAMILY_IDS or AI_KEYWORDS_RE.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

BASE = "https://carriere.dassault-aviation.com"
LISTING_URL_TEMPLATE = (
    BASE + "/offre-de-emploi/liste-toutes-offres.aspx?page={page}&LCID=1036"
)
FAMILY_FILTER_URL_TEMPLATE = (
    BASE
    + "/offre-de-emploi/liste-toutes-offres.aspx"
    + "?changefacet=1&facet_JobFamily={family_id}"
)
DETAIL_URL_TEMPLATE = BASE + "{path}"  # path comes from the listing card href

MAX_PAGES = 30  # safety cap; real total ≈ 8 pages

SCOPE_COUNTRY = "France"

# Talentsoft JobFamily ids whose entire output we keep regardless of title
# (broad scope = include software engineering adjacent). 5817 = "SYSTÈME
# D'INFORMATION" — Dassault Aviation's IT department, where data, AI, infra,
# security and dev roles live.
ALWAYS_KEEP_JOB_FAMILY_IDS: tuple[int, ...] = (5817,)

# Title / job-description keywords promoting non-IT-family postings into scope.
# French + English, with accent-insensitive matching where it matters (IA,
# données, machine learning, intelligence artificielle, etc.).
AI_KEYWORDS_RE = re.compile(
    r"\b("
    r"AI|IA|ML|MLOps|NLP|LLM|LLMs|GenAI"
    r"|Machine\s+Learning|Deep\s+Learning|Generative\s+AI|Foundation\s+Models?"
    r"|Data\s+(?:Scientist|Engineer|Analyst|Architect|Science|Engineering|Analytics)"
    r"|Données|Donnees"
    r"|Intelligence\s+Artificielle"
    r"|Applied\s+Scientist|Research\s+Scientist"
    r"|Analytics|Analytique"
    r"|Sciences?\s+de\s+(?:la\s+)?donn[ée]es"
    r")\b",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.7,en;q=0.5",
    "Referer": BASE + "/accueil.aspx?LCID=1036",
    "From": "yannickarieldossa@gmail.com",
}

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.0

# Talentsoft URL pattern: /offre-de-emploi/<slug>_<offerId>.aspx
OFFER_ID_RE = re.compile(r"_(\d+)\.aspx", re.IGNORECASE)
# Title attribute format: "Title (Réf. : 2026-15347) - JobDescription"
TITLE_ATTR_RE = re.compile(
    r"^(?P<title>.+?)\s*\(R[ée]f\.\s*:\s*(?P<ref>[\d\-]+)\)\s*-\s*(?P<job>.+)$",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


@dataclass
class Job:
    native_job_id: str          # Talentsoft offerId (integer string, e.g. "15347")
    title: str
    location: str               # "France, <region>, <site>" from the detail sidebar
    category: str | None        # Talentsoft JobDescription (e.g. "Architecture", "Cybersécurité")
    apply_url: str              # public detail page (which carries the "Je postule" form)
    employment_type: str        # raw contract type ("CDI" / "C. apprentissage" / "Stage" / "CDD" / ...)
    description: str | None = None
    posted_date: str | None = None   # YYYY-MM-DD
    identifier: str | None = None    # full reference "YEAR-OFFERID" (Talentsoft display ref)
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

    raw_title_attr = title_link.get("title", "").strip()
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
            reference = fav.get("data-reference", "").strip() or None

    desc_lis = [el.get_text(strip=True) for el in li.select("ul.ts-offer-list-item__description > li")]
    # Conventional ordering: [Réf, date, contract, city] — but stay defensive.
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

    return {
        "native_job_id": offer_id,
        "title": title_clean,
        "category_card": category,
        "reference": reference,
        "employment_type": contract,
        "location_card": location_card,
        "posted_date": posted,
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


def _fetch_family_offer_ids(family_id: int) -> set[str]:
    """Single-shot fetch of a JobFamily-filtered listing — returns offerIds.

    Talentsoft's `?changefacet=1` writes the facet into the server-side session
    (it's a stateful WebForms postback dressed up as a GET). Reusing that same
    session for the unfiltered listing would then return the filtered set on
    every page. To keep this fetch hermetic we spin up a fresh Session that we
    discard on the way out — its cookies never touch the listing crawl.
    """
    fresh = requests.Session()
    fresh.headers.update(HEADERS)
    url = FAMILY_FILTER_URL_TEMPLATE.format(family_id=family_id)
    response = fresh.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    ids: set[str] = set()
    for li in soup.select("li.ts-offer-list-item"):
        link = li.select_one("a.ts-offer-list-item__title-link")
        if not link:
            continue
        m = OFFER_ID_RE.search(link.get("href", ""))
        if m:
            ids.add(m.group(1))
    return ids


def _extract_sidebar_localisation(soup: BeautifulSoup) -> str | None:
    """Find the offer-page sidebar div labelled 'Localisation' and return its value.

    The detail page contains TWO 'Localisation' labels — one in the global
    search form (left rail, with the placeholder "Veuillez sélectionner…")
    and one in the offer's own info sidebar (right column, inside
    `#contenu-ficheoffre`). We must scope to the latter.
    """
    body = soup.select_one("#contenu-ficheoffre")
    if not body:
        return None
    for label in body.find_all(string=lambda s: isinstance(s, str) and s.strip() == "Localisation"):
        parent = label.parent
        if not parent:
            continue
        sibling = parent.find_next_sibling("div") or parent.find_next("div")
        if sibling:
            txt = sibling.get_text(" ", strip=True)
            if txt:
                return txt
    return None


def _extract_description(soup: BeautifulSoup) -> str | None:
    body = soup.select_one("#contenu-ficheoffre")
    if not body:
        return None
    # Drop the right-hand sidebar (the <td> with bg #edf2f9) so the body text
    # doesn't include Localisation/Contract metadata twice. CSS-selector match
    # avoids the find_all(td) approach, which breaks when decompose() mutates
    # the iteration order.
    for td in body.select('td[style*="edf2f9"]'):
        td.decompose()
    text = body.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip() or None


def _fetch_detail(session: requests.Session, path: str) -> tuple[str | None, str | None]:
    """Return (description, localisation_from_sidebar)."""
    url = DETAIL_URL_TEMPLATE.format(path=path)
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    # Localisation lives in the right-hand sidebar (#edf2f9 td), which
    # _extract_description decomposes — so read it first.
    localisation = _extract_sidebar_localisation(soup)
    description = _extract_description(soup)
    return description, localisation


def _in_scope_preliminary(card: dict, it_offer_ids: set[str]) -> bool:
    """Pre-detail filter: keep if in IT family OR title matches AI/Data regex."""
    if card["native_job_id"] in it_offer_ids:
        return True
    title = card.get("title") or ""
    cat = card.get("category_card") or ""
    if AI_KEYWORDS_RE.search(title) or AI_KEYWORDS_RE.search(cat):
        return True
    return False


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()

    print(
        f"Pre-fetch: JobFamily IDs to always keep: "
        f"{list(ALWAYS_KEEP_JOB_FAMILY_IDS)} (Talentsoft facet_JobFamily)...",
        flush=True,
    )
    it_offer_ids: set[str] = set()
    for fid in ALWAYS_KEEP_JOB_FAMILY_IDS:
        ids = _fetch_family_offer_ids(fid)
        print(f"  family {fid}: {len(ids)} offers", flush=True)
        it_offer_ids |= ids
        time.sleep(REQUEST_DELAY_SECONDS)

    print("\nListing phase...", flush=True)
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
        # Stop when an empty page comes back OR when a page yields zero new
        # offers — Talentsoft just clamps `?page=N` past the end to the last
        # page and would otherwise loop forever.
        if not cards or not new:
            break
        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"\nFilter phase: country={SCOPE_COUNTRY!r}, "
          f"always-keep family ids={list(ALWAYS_KEEP_JOB_FAMILY_IDS)}, "
          f"title-fallback=AI/Data keywords...", flush=True)
    candidates = [c for c in all_cards.values() if _in_scope_preliminary(c, it_offer_ids)]
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
        try:
            description, localisation = _fetch_detail(session, card["detail_path"])
        except Exception as exc:
            print(
                f"  [{i}/{len(candidates)}] {card['native_job_id']} detail fetch FAILED: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            description, localisation = None, None
            failed += 1

        location = localisation or card.get("location_card") or ""
        if location and SCOPE_COUNTRY.lower() not in location.lower():
            dropped_country += 1
            print(
                f"  [{i}/{len(candidates)}] {card['native_job_id']} "
                f"{card['title']!r} -> DROP (location={location!r})",
                flush=True,
            )
            continue

        job = Job(
            native_job_id=card["native_job_id"],
            title=card["title"],
            location=location,
            category=card.get("category_card"),
            apply_url=DETAIL_URL_TEMPLATE.format(path=card["detail_path"]),
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
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
