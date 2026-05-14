"""Probe 7: hit Teamtailor for all 4 in-scope jobs back-to-back.

Probe 6 confirmed a single Teamtailor page returns 200 with a clean
JobPosting JSON-LD (datePosted, employmentType, description). Before
wiring enrichment into the scraper we want to confirm:

- All 4 in-scope jids work (not just the one we sampled)
- Sequential requests don't trip rate-limiting / anti-bot
- The schema shape is consistent across jobs (same keys, same identifier
  shape) so the parser doesn't need per-job special cases
"""
from __future__ import annotations

import json
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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "From": "yannickarieldossa@gmail.com",
}

JIDS = ["7630173", "7629174", "7632008", "7637065"]
DELAY = 1.5


def fetch_jp(jid: str) -> dict | None:
    url = f"https://deezer.teamtailor.com/jobs/{jid}"
    r = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    print(f"  jid={jid}  status={r.status_code}  final={r.url}  len={len(r.text)}")
    if r.status_code != 200:
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def main() -> None:
    session = requests.Session()
    session.headers.update(HEADERS)

    for i, jid in enumerate(JIDS):
        if i:
            time.sleep(DELAY)
        jp = fetch_jp(jid)
        if not jp:
            print(f"    NO JobPosting for {jid}")
            continue

        ident = jp.get("identifier") or {}
        ident_value = ident.get("value") if isinstance(ident, dict) else ident
        desc_html = jp.get("description") or ""
        desc_text = BeautifulSoup(desc_html, "html.parser").get_text(" ", strip=True)
        print(f"    title          : {jp.get('title')}")
        print(f"    datePosted     : {jp.get('datePosted')}")
        print(f"    employmentType : {jp.get('employmentType')}")
        print(f"    identifier.val : {ident_value}")
        print(f"    description    : {len(desc_text)} chars")
        print(f"    keys           : {list(jp.keys())}")
        print()


if __name__ == "__main__":
    main()
