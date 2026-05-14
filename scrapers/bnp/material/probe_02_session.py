"""Probe 2: warm a session via the homepage, then hit /en/careers.

Akamai 403'd probe_01 even with browser headers. Common reason: their
Bot Manager hands out a challenge cookie (bm_sv / _abck / bm_sz) on the
first hit and only lets through clients that present it on subsequent
hits. A real browser visits group.bnpparibas/ first, gets the cookies,
then navigates to /en/careers carrying them.
"""
from __future__ import annotations

import pathlib
import sys
import time

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
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "From": "yannickarieldossa@gmail.com",
}

URL_HOME = "https://group.bnpparibas/"
URL_CAREERS = "https://group.bnpparibas/en/careers"
OUT_DIR = pathlib.Path(__file__).parent


def hit(session: requests.Session, url: str, *, referer: str | None = None) -> requests.Response:
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = referer
        headers["Sec-Fetch-Site"] = "same-origin"
    r = session.get(url, headers=headers, timeout=30, allow_redirects=True)
    print(f"\n>>> GET {url}")
    print(f"    status={r.status_code}  final={r.url}  len={len(r.text)}")
    print(f"    cookies after: {[c.name for c in session.cookies]}")
    return r


def main() -> None:
    s = requests.Session()

    home = hit(s, URL_HOME)
    (OUT_DIR / "home.html").write_text(home.text, encoding="utf-8")

    time.sleep(2.0)

    careers = hit(s, URL_CAREERS, referer=URL_HOME)
    (OUT_DIR / "landing.html").write_text(careers.text, encoding="utf-8")

    print(f"\nSAVED: {OUT_DIR / 'home.html'}")
    print(f"SAVED: {OUT_DIR / 'landing.html'}")
    if careers.status_code == 200:
        print("\nOK — got 200 on careers page, ready to parse")
    else:
        print(f"\nSTILL BLOCKED: {careers.status_code} — first 300 chars of body:")
        print(careers.text[:300])


if __name__ == "__main__":
    main()
