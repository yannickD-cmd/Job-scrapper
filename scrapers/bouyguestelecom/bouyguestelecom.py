"""Bouygues Telecom job scraper — France, SI + Sécurité filières, CDI only.

The board lives on the corporate WordPress site (server-rendered, no ATS
API exposed — the SuccessFactors-style filière IDs are baked into path
filters):

    https://www.corporate.bouyguestelecom.fr/travailler-ensemble/nos-offres
        /filtres/filieres/{filiere_id}/contrats/{contract_id}[/page/{n}/]

There is no "Data" filière — Data roles file under SYSTEMES D INFORMATION
(50000016), which is why the corporate Data métier page links to that ID.
Multi-value path filters 404, so we loop one request per filière and dedup
by reference number.

Two-pass scrape:

1. LISTING. One filtered listing walk per filière in FILIERES (CDI is
   applied server-side via /contrats/52170). Each `card-offer` div carries
   title, "Publié le DD/MM/YYYY", filière tag, contract/salary/region tags
   (distinguished by their svg icon anchor), and the detail URL ending in
   `reference-{id}`. The page announces "N annonces disponibles"; if we
   collect fewer than announced the scrape ABORTS rather than returning a
   partial list that would false-close DB rows.

2. ENRICHMENT. Detail pages have no JobPosting JSON-LD (only Yoast site
   boilerplate), so we read the HTML: `.offer-summary__content` +
   `.offer-summary__profile` for the description, `.offer-summary__adress`
   for the physical site (postcode + city refine the region-only card
   location). A 404 here means the offer vanished between passes → drop
   that job only; any other detail failure keeps the listing row with no
   description so the row is not lost.

France-only scope is enforced client-side by dropping the "International"
region (all other regions are French).

To change scope, edit FILIERES / CONTRACTS_IN_SCOPE.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.corporate.bouyguestelecom.fr/travailler-ensemble/nos-offres"

# Filière ids as used by the site's path filters (checkbox values on the board).
FILIERES: dict[str, str] = {
    "50000016": "SYSTEMES D INFORMATION",   # includes all Data roles
    "50000018": "SECURITE",
}
CONTRACTS_IN_SCOPE: dict[str, str] = {
    "52170": "CDI",
}
EXCLUDED_REGIONS: set[str] = {"International"}

PAGE_SIZE = 21  # cards per listing page, observed on the live board

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
}

REQUEST_DELAY_SECONDS = 2.0
REQUEST_TIMEOUT = 30
MAX_PAGES = 15  # defensive cap per filière (board is ~140 offers total)

_REFERENCE_RE = re.compile(r"reference-(\d+)/?$")
_TOTAL_RE = re.compile(r"(\d+)\s+annonces?\s+disponibles?")
_POSTCODE_CITY_RE = re.compile(r"\b\d{5}\s+(.+?)\s*$")


@dataclass
class Job:
    native_job_id: str
    title: str
    location: str
    category: str
    apply_url: str
    posted_date: str | None = None
    employment_type: str | None = None
    # Filled by detail-page enrichment:
    description: str | None = None
    identifier: str | None = None
    raw_payload: dict | None = None


def _listing_url(filiere_id: str, contract_id: str, page: int) -> str:
    url = f"{BASE_URL}/filtres/filieres/{filiere_id}/contrats/{contract_id}/"
    if page > 1:
        url += f"page/{page}/"
    return url


def _parse_date(raw: str) -> str | None:
    """'Publié le 24/06/2026' → '2026-06-24'."""
    m = re.search(r"(\d{2}/\d{2}/\d{4})", raw)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _card_tags(card) -> dict[str, str]:
    """Secondary tags are typed by their svg sprite anchor (#contract, #salary,
    #location); the primary tag is the filière."""
    tags: dict[str, str] = {}
    primary = card.select_one(".tag--primary")
    if primary:
        tags["filiere"] = primary.get_text(" ", strip=True)
    for tag in card.select(".tag--secondary"):
        use = tag.find("use")
        href = (use.get("xlink:href") or use.get("href") or "") if use else ""
        kind = href.rsplit("#", 1)[-1] if "#" in href else ""
        if kind:
            tags[kind] = tag.get_text(" ", strip=True)
    return tags


def _parse_listing_page(html: str) -> tuple[list[Job], int]:
    soup = BeautifulSoup(html, "html.parser")

    total = 0
    total_el = soup.find(string=_TOTAL_RE)
    if total_el:
        total = int(_TOTAL_RE.search(total_el).group(1))

    jobs: list[Job] = []
    for card in soup.select("div.card-offer"):
        link = card.select_one("a[href*='reference-']")
        title_el = card.select_one("h3")
        if not link or not title_el:
            continue

        apply_url = link["href"]
        ref_match = _REFERENCE_RE.search(apply_url)
        if not ref_match:
            continue

        tags = _card_tags(card)
        date_el = card.select_one(".card-offer__date")

        jobs.append(Job(
            native_job_id=ref_match.group(1),
            title=title_el.get_text(" ", strip=True),
            location=tags.get("location", ""),
            category=tags.get("filiere", ""),
            apply_url=apply_url,
            posted_date=_parse_date(date_el.get_text(strip=True)) if date_el else None,
            employment_type=tags.get("contract"),
            identifier=ref_match.group(1),
            raw_payload={"card": tags},
        ))

    return jobs, total


def _enrich(session: requests.Session, job: Job) -> None:
    """Fetch detail page, fill description/location. Raises on HTTP errors."""
    response = session.get(job.apply_url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    parts = []
    for selector in (".offer-summary__content", ".offer-summary__profile"):
        section = soup.select_one(selector)
        if section:
            parts.append(section.decode_contents().strip())
    if parts:
        job.description = "\n".join(parts)

    detail: dict[str, str] = {}
    address_el = soup.select_one(".offer-summary__adress p")
    if address_el:
        address = address_el.get_text(" ", strip=True)
        detail["address"] = address
        city_match = _POSTCODE_CITY_RE.search(address)
        if city_match:
            city = city_match.group(1).title()
            job.location = f"{city} ({job.location})" if job.location else city

    contact_el = soup.select_one(".offer-summary__contact p")
    if contact_el:
        detail["contact"] = contact_el.get_text(" ", strip=True)

    if detail:
        job.raw_payload = {**(job.raw_payload or {}), "detail": detail}


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    all_jobs: dict[str, Job] = {}  # dedup by native_job_id across filières
    dropped_regions: dict[str, int] = {}
    first_request = True

    for contract_id, contract_label in CONTRACTS_IN_SCOPE.items():
        for filiere_id, filiere_label in FILIERES.items():
            print(f"Listing {filiere_label} / {contract_label}...", flush=True)
            collected: dict[str, Job] = {}
            announced = 0
            page = 1

            while page <= MAX_PAGES:
                if not first_request:
                    time.sleep(REQUEST_DELAY_SECONDS)
                first_request = False

                url = _listing_url(filiere_id, contract_id, page)
                response = session.get(url, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()

                page_jobs, announced = _parse_listing_page(response.text)
                for j in page_jobs:
                    collected.setdefault(j.native_job_id, j)

                print(f"  page {page}: {len(page_jobs)} cards "
                      f"({len(collected)}/{announced} collected)", flush=True)

                if len(collected) >= announced or not page_jobs:
                    break
                page += 1

            # A partial listing must abort the run: returning a subset would
            # false-close every DB row in the missing slice.
            if len(collected) < announced:
                raise RuntimeError(
                    f"{filiere_label}: collected {len(collected)} of "
                    f"{announced} announced offers — aborting to avoid "
                    f"false-closing rows"
                )

            for job in collected.values():
                if job.location in EXCLUDED_REGIONS:
                    dropped_regions[job.location] = \
                        dropped_regions.get(job.location, 0) + 1
                    continue
                all_jobs.setdefault(job.native_job_id, job)

    print(f"\n→ {len(all_jobs)} unique in-scope jobs "
          f"(dropped by region: {dropped_regions or 'none'})\n", flush=True)

    print(f"Enrichment phase: fetching {len(all_jobs)} detail pages "
          f"(~{int(len(all_jobs) * REQUEST_DELAY_SECONDS)}s)...", flush=True)

    kept: list[Job] = []
    for i, job in enumerate(all_jobs.values(), 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            _enrich(session, job)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                print(f"  [{i}/{len(all_jobs)}] {job.native_job_id} "
                      f"404 — offer vanished, dropped", flush=True)
                continue
            print(f"  [{i}/{len(all_jobs)}] {job.native_job_id} detail failed "
                  f"({exc}) — kept without description", flush=True)
        except Exception as exc:
            print(f"  [{i}/{len(all_jobs)}] {job.native_job_id} detail failed "
                  f"({type(exc).__name__}: {exc}) — kept without description",
                  flush=True)

        kept.append(job)
        print(f"  [{i}/{len(all_jobs)}] {job.native_job_id} {job.title!r} "
              f"({job.location})", flush=True)

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

    elapsed = time.time() - started
    print(f"\n=== {len(jobs)} jobs final (total runtime {elapsed:.1f}s) ===\n")

    for j in jobs:
        desc_preview = BeautifulSoup(j["description"] or "", "html.parser") \
            .get_text(" ", strip=True)
        desc_preview = desc_preview[:200] + ("…" if len(desc_preview) > 200 else "")

        print(f"[{j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc_preview}")
        print()
