"""Probe 7: confirm count + enumerate every job in our locked scope.

Scope: Permanent + Digital transformation and data + France
URL:   /en/careers/all-job-offers/permanent/digital-transformation-and-data/france

We walk pagination (?page=N), parse each `article.card-offer` row, and
print one line per job (slug, title, location, brand). This is what
the real scraper will see.
"""
from __future__ import annotations

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
BASE = HOST + "/en/careers/all-job-offers/permanent/digital-transformation-and-data/france"
OUT_DIR = pathlib.Path(__file__).parent


def warmup() -> requests.Session:
    s = requests.Session()
    s.get(HOST + "/", headers=HEADERS, timeout=30)
    return s


def fetch(s: requests.Session, url: str, *, referer: str) -> requests.Response:
    h = dict(HEADERS)
    h["Referer"] = referer
    h["Sec-Fetch-Site"] = "same-origin"
    return s.get(url, headers=h, timeout=30, allow_redirects=True)


def parse_jobs_from_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for card in soup.select("article.card-offer"):
        a = card.select_one("a.card-link")
        href = a.get("href") if a else ""
        title_el = card.select_one("h3.title-4")
        title = title_el.get_text(" ", strip=True) if title_el else ""

        loc_el = card.select_one(".offer-location")
        if loc_el:
            # strip the icon child and collapse whitespace
            for icon in loc_el.select(".icon"):
                icon.extract()
            location = re.sub(r"\s+", " ", loc_el.get_text(" ", strip=True))
        else:
            location = ""

        type_el = card.select_one(".offer-type")
        offer_type = type_el.get_text(" ", strip=True) if type_el else ""

        logo_img = card.select_one(".offer-logo img")
        brand = logo_img.get("alt") if logo_img else ""

        # native_job_id = slug after /job-offer/
        slug = ""
        m = re.search(r"/job-offer/([^/?#]+)", href or "")
        if m:
            slug = m.group(1)

        out.append({
            "native_job_id": slug,
            "title": title,
            "location": location,
            "offer_type": offer_type,
            "brand": brand,
            "apply_url": HOST + href if href and href.startswith("/") else href,
        })
    return out


def main() -> None:
    s = warmup()
    time.sleep(2.0)

    # Page 1
    r1 = fetch(s, BASE, referer=HOST + "/en/careers/all-job-offers")
    print(f">>> {BASE}")
    print(f"    status={r1.status_code}  len={len(r1.text)}")
    if r1.status_code != 200:
        print(r1.text[:300])
        return

    (OUT_DIR / "scope_p1.html").write_text(r1.text, encoding="utf-8")
    soup = BeautifulSoup(r1.text, "html.parser")
    total = int(soup.select_one("span.nb-total").get_text(strip=True).replace(",", ""))
    pag = soup.select_one(".pagination")
    pag_txt = pag.get_text(" ", strip=True) if pag else ""
    print(f"    header total: {total}")
    print(f"    pagination: {pag_txt!r}")

    jobs = parse_jobs_from_page(r1.text)
    print(f"    page 1 rows: {len(jobs)}")

    # Pagination — BNP typically uses ?page=N. Walk until we have `total`.
    page = 2
    seen_slugs = {j["native_job_id"] for j in jobs if j["native_job_id"]}
    while len(jobs) < total and page < 20:
        time.sleep(2.0)
        url = f"{BASE}?page={page}"
        rN = fetch(s, url, referer=BASE)
        print(f"\n>>> page {page}: {url}")
        print(f"    status={rN.status_code}  len={len(rN.text)}")
        if rN.status_code != 200:
            break
        page_jobs = parse_jobs_from_page(rN.text)
        new = [j for j in page_jobs if j["native_job_id"] not in seen_slugs]
        print(f"    rows on page: {len(page_jobs)}  new: {len(new)}")
        if not new:
            # different pagination scheme — bail
            break
        for j in new:
            seen_slugs.add(j["native_job_id"])
            jobs.append(j)
        page += 1

    print(f"\n=== TOTAL COLLECTED: {len(jobs)} (header said {total}) ===\n")
    for i, j in enumerate(jobs, 1):
        print(f"  [{i:2d}] {j['native_job_id']}")
        print(f"       title    : {j['title']}")
        print(f"       type     : {j['offer_type']}")
        print(f"       location : {j['location']}")
        print(f"       brand    : {j['brand']}")
        print(f"       apply    : {j['apply_url']}")
        print()


if __name__ == "__main__":
    main()
