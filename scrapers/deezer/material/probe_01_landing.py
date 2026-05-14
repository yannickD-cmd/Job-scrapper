"""Probe 1: fetch the Deezer jobs landing page and look for clues.

We don't yet know whether deezerjobs.com/en/jobs/ is:
- a marketing hub linking out to a real ATS (Workday, SmartRecruiters, etc.)
- a server-rendered listing (HTML cards we can parse directly)
- a JS app fetching listings over XHR (need to find the API)

Strategy: fetch raw HTML, scan for ATS vendor fingerprints and obvious
listing markers, dump links to spot candidate detail-page / API URLs.
Save the HTML so subsequent probes don't re-hit Deezer.
"""
from __future__ import annotations

import pathlib
import re
import sys

import requests

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
    # No `br` — requests doesn't decode brotli without the brotli package,
    # and the landing came back as raw binary on the first attempt.
    "Accept-Encoding": "gzip, deflate",
    "From": "yannickarieldossa@gmail.com",
}

URL = "https://www.deezerjobs.com/en/jobs/"
OUT_DIR = pathlib.Path(__file__).parent
OUT_FILE = OUT_DIR / "landing.html"


def main() -> None:
    r = requests.get(URL, headers=HEADERS, timeout=30, allow_redirects=True)
    print(f"STATUS: {r.status_code}")
    print(f"FINAL URL: {r.url}")
    print(f"CONTENT-TYPE: {r.headers.get('content-type')}")
    print(f"LEN: {len(r.text)} chars")

    OUT_FILE.write_text(r.text, encoding="utf-8")
    print(f"SAVED: {OUT_FILE}")

    lower = r.text.lower()

    print("\n--- ATS / careers-system fingerprints ---")
    ats_keywords = [
        "smartrecruiters", "workday", "myworkdayjobs", "taleo", "icims",
        "successfactors", "greenhouse", "lever.co", "avature", "phenom",
        "eightfold", "jobvite", "applytojob", "ashbyhq", "teamtailor",
        "workable", "recruitee", "personio", "softgarden", "pinpoint",
    ]
    for kw in ats_keywords:
        if kw in lower:
            print(f"  hit: {kw!r}")

    print("\n--- Listing-shape hints ---")
    listing_keywords = [
        "permanent", "internship", "apprentice", "fixed-term",
        "product & tech", "product and tech", "product-tech",
        "marketing", "revenue", "commercial", "corporate", "people",
        "job-offer", "job_offer", "joboffer", "/job/", "/jobs/",
        "categor", "department", "team", "filter",
        "__next_data__", "window.__", "apolloState", "initialprops",
    ]
    for kw in listing_keywords:
        if kw in lower:
            print(f"  hit: {kw!r}")

    hrefs = re.findall(r'href=["\']([^"\']+)["\']', r.text)
    job_links = sorted(set(
        h for h in hrefs
        if any(k in h.lower() for k in ["job", "career", "appl", "vacanc", "offre", "opening"])
    ))
    print(f"\n--- JOB-ISH LINKS ({len(job_links)} unique) ---")
    for h in job_links[:80]:
        print(f"  {h}")

    # Look for inline JSON dumps (Next.js / Nuxt / Apollo state).
    print("\n--- Inline JSON blobs ---")
    for marker in ["__NEXT_DATA__", "__NUXT__", "__APOLLO_STATE__", "window.__INITIAL_STATE__"]:
        if marker in r.text:
            idx = r.text.find(marker)
            print(f"  {marker} at offset {idx}")


if __name__ == "__main__":
    main()
