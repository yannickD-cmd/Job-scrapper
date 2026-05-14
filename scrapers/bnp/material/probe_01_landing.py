"""Probe 1: fetch the BNP careers landing page and look for clues.

We don't yet know whether group.bnpparibas/en/careers is:
- a marketing hub that links out to a real job-search system, or
- the actual listing itself (server-rendered HTML), or
- a JS app fetching listings over XHR

Strategy: fetch it, dump raw HTML, scan for hints (known ATS vendors
like Workday/SmartRecruiters/Taleo, "search jobs" CTAs, careers
subdomains). Save the HTML so we can re-inspect without re-hitting BNP.
"""
from __future__ import annotations

import pathlib
import re
import sys

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# BNP is behind Akamai, which 403s anything that doesn't send a full
# browser-shaped header set. We still identify ourselves via `From:`
# so we're not pretending to be anonymous — just not advertising "bot".
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
    "From": "yannickarieldossa@gmail.com",
}

URL = "https://group.bnpparibas/en/careers"
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
        "eightfold", "jobvite", "applytojob", "ashbyhq",
    ]
    for kw in ats_keywords:
        if kw in lower:
            print(f"  hit: {kw!r}")

    print("\n--- BNP-specific hints ---")
    bnp_keywords = [
        "search jobs", "search job", "vacancies", "join us",
        "careers.bnpparibas", "jobs.bnpparibas", "bnppjobs",
        "rejoignez-nous", "nos-offres", "offers", "openings",
    ]
    for kw in bnp_keywords:
        if kw in lower:
            print(f"  hit: {kw!r}")

    hrefs = re.findall(r'href=["\']([^"\']+)["\']', r.text)
    career_links = sorted(set(
        h for h in hrefs
        if any(k in h.lower() for k in ["job", "career", "appl", "vacanc", "offre", "opening"])
    ))
    print(f"\n--- CAREER-ISH LINKS ({len(career_links)} unique) ---")
    for h in career_links[:60]:
        print(f"  {h}")


if __name__ == "__main__":
    main()
