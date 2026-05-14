"""Probe 6: Deezer's ATS is Teamtailor — try fetching the canonical page.

Probe 5 revealed that "Apply now" on a deezerjobs.com detail page links to
https://deezer.teamtailor.com/jobs/<jid>-<slug>/applications/new

Teamtailor's *job page* (without /applications/new) usually serves a clean
JSON-LD JobPosting block with datePosted, employmentType, description, etc.
If that's true here, we should enrich from Teamtailor instead of scraping
the WordPress description (which mixes in the title and boilerplate).

Try two URL shapes:
- https://deezer.teamtailor.com/jobs/<jid>
- https://deezer.teamtailor.com/jobs/<jid>-<slug>
"""
from __future__ import annotations

import json
import pathlib
import sys

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

OUT_DIR = pathlib.Path(__file__).parent

# Same iOS Team Manager job we used in probe 3.
CANDIDATES = [
    ("teamtailor_id_only.html",   "https://deezer.teamtailor.com/jobs/7630173"),
    ("teamtailor_id_slug.html",   "https://deezer.teamtailor.com/jobs/7630173-ios-team-manager-listeners-m-f-d"),
]


def main() -> None:
    for filename, url in CANDIDATES:
        print(f"\n=== {url} ===")
        try:
            r = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
        except Exception as e:
            print(f"  REQUEST FAILED: {type(e).__name__}: {e}")
            continue

        print(f"  STATUS: {r.status_code}")
        print(f"  FINAL URL: {r.url}")
        print(f"  LEN: {len(r.text)} chars")
        out = OUT_DIR / filename
        out.write_text(r.text, encoding="utf-8")
        print(f"  SAVED: {out.name}")

        if r.status_code != 200:
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        jp = None
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict) and item.get("@type") == "JobPosting":
                    jp = item
                    break
            if jp:
                break

        if jp:
            print("  JobPosting found! keys:", list(jp.keys()))
            print(f"    datePosted     : {jp.get('datePosted')}")
            print(f"    validThrough   : {jp.get('validThrough')}")
            print(f"    employmentType : {jp.get('employmentType')}")
            print(f"    title          : {jp.get('title')}")
            ident = jp.get("identifier")
            print(f"    identifier     : {ident}")
            desc = jp.get("description") or ""
            txt = BeautifulSoup(desc, "html.parser").get_text(" ", strip=True)
            print(f"    description    : ({len(txt)} chars) {txt[:200]!r}...")
        else:
            print("  no JobPosting JSON-LD")
            # Show <title> as a sanity check.
            if soup.title:
                print(f"  <title>: {soup.title.get_text(strip=True)!r}")


if __name__ == "__main__":
    main()
