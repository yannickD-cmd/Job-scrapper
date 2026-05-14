"""Probe 6: can we add a domain (job family) filter to Permanent + France?

Target domain: "Digital transformation and data" (data-id=168, slug
`digital-transformation-and-data`). The Sanofi-equivalent picks one
job-family. We know type+country in the URL path works as
`/permanent/france`. Question: does a third segment work, and if so
which order? Fallback is the Drupal-style query-string form encoding.

We try every plausible URL shape and read off the "<N> job offers in
<K> locations" header so we can compare counts.
"""
from __future__ import annotations

import pathlib
import re
import sys
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "From": "yannickarieldossa@gmail.com",
}

HOST = "https://group.bnpparibas"
OUT_DIR = pathlib.Path(__file__).parent

# from earlier probes:
#   domain "Digital transformation and data" -> id=168, slug=digital-transformation-and-data
DOMAIN_ID = "168"
DOMAIN_SLUG = "digital-transformation-and-data"


def warmup() -> requests.Session:
    s = requests.Session()
    s.get(HOST + "/", headers=HEADERS, timeout=30)
    return s


def hit(s: requests.Session, url: str, *, referer: str) -> requests.Response:
    h = dict(HEADERS)
    h["Referer"] = referer
    h["Sec-Fetch-Site"] = "same-origin"
    return s.get(url, headers=h, timeout=30, allow_redirects=True)


def summarize(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    total = locations = None
    for span in soup.select("span.nb-total"):
        try:
            total = int(span.get_text(strip=True).replace(",", "").replace(".", ""))
            break
        except ValueError:
            pass
    for span in soup.select("span.nb-countries"):
        try:
            locations = int(span.get_text(strip=True).replace(",", "").replace(".", ""))
            break
        except ValueError:
            pass
    cards = soup.select("article.card-offer")
    pag = soup.select_one(".pagination")
    pag_txt = pag.get_text(" ", strip=True) if pag else ""
    # Also grab country names visible on results to sanity-check the filter
    locs_seen = set()
    for el in soup.select("article.card-offer .offer-location"):
        txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
        # last comma-separated part is typically the country
        country = txt.rsplit(",", 1)[-1].strip() if "," in txt else txt
        if country:
            locs_seen.add(country)
    return {
        "total": total,
        "locations_in_header": locations,
        "cards_on_page": len(cards),
        "countries_in_first_page": sorted(locs_seen),
        "pagination": pag_txt[:120],
    }


def run(s: requests.Session, label: str, url: str) -> None:
    print(f"\n>>> {label}")
    print(f"    URL: {url}")
    r = hit(s, url, referer=HOST + "/en/careers/all-job-offers")
    print(f"    status={r.status_code}  final={r.url}  len={len(r.text)}")
    if r.status_code != 200:
        return
    info = summarize(r.text)
    print(f"    total={info['total']!r}  locations_hdr={info['locations_in_header']!r}  "
          f"cards={info['cards_on_page']}")
    print(f"    countries on p1: {info['countries_in_first_page']}")
    print(f"    pagination: {info['pagination']!r}")


def main() -> None:
    s = warmup()
    time.sleep(2.0)

    # The known-good 2-segment baseline:
    run(s, "baseline: permanent+france",
        f"{HOST}/en/careers/all-job-offers/permanent/france")
    time.sleep(2.0)

    # 3-segment URL variants — try every order
    paths = [
        f"permanent/france/{DOMAIN_SLUG}",
        f"permanent/{DOMAIN_SLUG}/france",
        f"{DOMAIN_SLUG}/permanent/france",
        f"france/permanent/{DOMAIN_SLUG}",
        f"france/{DOMAIN_SLUG}/permanent",
        f"{DOMAIN_SLUG}/france/permanent",
    ]
    for p in paths:
        run(s, f"3-seg: /{p}", f"{HOST}/en/careers/all-job-offers/{p}")
        time.sleep(2.0)

    # Query-string approach — Drupal form-array encoding
    # form[domain][]=168 layered on top of the 2-segment path
    qs_url = (
        f"{HOST}/en/careers/all-job-offers/permanent/france?"
        f"form%5Bdomain%5D%5B%5D={DOMAIN_ID}"
    )
    run(s, "qs: permanent+france + ?form[domain][]=168", qs_url)
    time.sleep(2.0)

    # Sanity bound: domain alone (no type, no country) — should be a big number
    run(s, "sanity: domain alone",
        f"{HOST}/en/careers/all-job-offers/{DOMAIN_SLUG}")


if __name__ == "__main__":
    main()
