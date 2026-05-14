"""Probe 2: look for endpoints NOT behind Cloudflare Bot Management.

Avature-hosted careers sites often expose:
- /robots.txt (always public)
- /sitemap.xml (often has every job URL)
- An RSS feed (sometimes /en_US/jobs/SearchJobs/rss?...)
- A JSON API endpoint used by their own JS

Cloudflare typically protects HTML pages aggressively but leaves
machine-readable feeds (sitemap, RSS) less defended because Googlebot
needs them. Worth checking before we escalate to TLS-fingerprint
spoofing (curl_cffi) or a headless browser.
"""
from __future__ import annotations

import pathlib
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
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "From": "yannickarieldossa@gmail.com",
}

HOST = "https://careers.loreal.com"
OUT_DIR = pathlib.Path(__file__).parent

CANDIDATES = [
    "/robots.txt",
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/en_US/jobs/sitemap.xml",
    "/en_US/jobs/Sitemap",
    "/en_US/jobs/SearchJobs/rss",
    "/en_US/jobs/SearchJobs/rss?3_110_3=18022",
    "/en_US/jobs/JobFeed",
    "/api/jobs",
    "/api/v1/jobs",
    "/feed",
    "/jobs.json",
    "/en_US/jobs/JobsFeed",
]


def main() -> None:
    s = requests.Session()
    for path in CANDIDATES:
        url = HOST + path
        try:
            r = s.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        except Exception as exc:
            print(f"{path:48s}  ERROR  {type(exc).__name__}: {exc}")
            continue
        ct = r.headers.get("content-type", "")
        first = r.text[:80].replace("\n", " ").replace("\r", " ")
        print(f"{path:48s}  {r.status_code}  ({len(r.text)} bytes, {ct})  {first!r}")
        if r.status_code == 200 and len(r.text) > 200:
            safe = path.strip("/").replace("/", "_").replace("?", "_q_") or "root"
            ext = ".xml" if "xml" in ct else ".json" if "json" in ct else ".txt"
            (OUT_DIR / f"alt_{safe}{ext}").write_text(r.text, encoding="utf-8")


if __name__ == "__main__":
    main()
