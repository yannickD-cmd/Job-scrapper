"""Probe 5: find how to filter by France + count Permanent + France jobs.

The HTML form exposes a `Location` facet (#form_country) but the option
list is empty in the server response — JS fetches it after page load.
BNP uses numeric country IDs (Europe block: 7|8|810|43|46|48|51|...).

Approaches to try, cheapest first:
1. URL-path shortcut: /en/careers/all-job-offers/france (BNP uses path
   slugs for every other facet — type, domain, brand — so worth a try).
2. URL-path shortcut + permanent combo: /permanent/france and /france/permanent.
3. Find the XHR endpoint that populates the country dropdown by searching
   the JS bundle / inline scripts for "country" API paths.
4. Worst case: enumerate Europe country IDs as `?form[country][]=N` and
   read the title to find which one gives "France" results.

For each try, we capture the title element that shows
"<NN> job offers in <K> locations" and the count of `card-offer` rows.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import time

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
    # parse pagination "1 2 ... 493 494 Next See more job offers (4,922 left)"
    pag = soup.select_one(".pagination")
    pag_txt = pag.get_text(" ", strip=True) if pag else ""
    return {
        "status": "ok",
        "title_total": total,
        "title_locations": locations,
        "cards_on_page": len(cards),
        "pagination_text": pag_txt[:120],
    }


def run_one(s: requests.Session, label: str, url: str, referer: str) -> None:
    print(f"\n>>> {label}")
    print(f"    URL: {url}")
    r = hit(s, url, referer=referer)
    print(f"    status={r.status_code}  final={r.url}  len={len(r.text)}")
    if r.status_code != 200:
        print(f"    body[:200]={r.text[:200]!r}")
        return
    info = summarize(r.text)
    print(f"    {info}")
    # save for later inspection
    safe = label.lower().replace(" ", "_").replace("/", "_")
    path = OUT_DIR / f"q_{safe}.html"
    path.write_text(r.text, encoding="utf-8")


def scan_for_country_api(html: str) -> None:
    """Look in inline JS for API endpoints that load countries."""
    print("\n--- searching scripts for country/location API hints ---")
    patterns = [
        r'["\']([^"\']*(?:countr|location|geoloc|facet|filter|search)[^"\']*)["\']',
    ]
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    for script in soup.find_all("script"):
        body = (script.string or "") + " " + (script.get("src") or "")
        for pat in patterns:
            for m in re.findall(pat, body):
                # only path-ish or url-ish things
                if ("/" in m or m.endswith(".json")) and m not in seen and len(m) < 200:
                    seen.add(m)
                    if any(k in m.lower() for k in ["country", "location", "geoloc", "/api/", ".json", "facet", "filter"]):
                        print(f"  hit: {m}")


def main() -> None:
    s = warmup()
    time.sleep(2.0)

    # Step A: prime by hitting /en/careers/all-job-offers (already known to work)
    run_one(s, "baseline_all", HOST + "/en/careers/all-job-offers",
            referer=HOST + "/en/careers")
    time.sleep(2.0)

    # Step B: scan the saved page for any country-loading JS endpoint hint
    base_html = (OUT_DIR / "all_job_offers.html").read_text(encoding="utf-8")
    scan_for_country_api(base_html)

    # Step C: try URL-path shortcut for France
    run_one(s, "path_france", HOST + "/en/careers/all-job-offers/france",
            referer=HOST + "/en/careers/all-job-offers")
    time.sleep(2.0)

    # Step D: try query-string variants (Drupal form-array encoding)
    # We don't know France's ID yet — try a tracer like q=France and the
    # known shortcuts to see if any move the count.
    run_one(s, "q_France",
            HOST + "/en/careers/all-job-offers?q=France",
            referer=HOST + "/en/careers/all-job-offers")
    time.sleep(2.0)

    # Step E: permanent + France path combos
    run_one(s, "path_permanent_france",
            HOST + "/en/careers/all-job-offers/permanent/france",
            referer=HOST + "/en/careers/all-job-offers")
    time.sleep(2.0)

    run_one(s, "path_france_permanent",
            HOST + "/en/careers/all-job-offers/france/permanent",
            referer=HOST + "/en/careers/all-job-offers")


if __name__ == "__main__":
    main()
