"""Probe 3: fetch a Product & Tech detail page and inspect its shape.

The listing card doesn't expose contract type (Permanent vs Internship),
so we have to enrich every job. Same dual-pass shape as Sanofi/BNP.

We need to find on the detail page:
- contract type (so we can keep only Permanent)
- description (long-form text)
- datePosted / publication date
- a stable native_job_id (the URL `jid` is already stable, but if there's
  a separate req-id we should grab it)
- whether there's a JobPosting JSON-LD block (would make parsing trivial)
"""
from __future__ import annotations

import json
import pathlib
import re
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
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.deezerjobs.com/en/jobs/",
    "From": "yannickarieldossa@gmail.com",
}

# iOS Team Manager – picked from probe 2 listing, Product & Tech.
URL = "https://www.deezerjobs.com/en/job-details/?jid=7630173"
OUT_DIR = pathlib.Path(__file__).parent
OUT_FILE = OUT_DIR / "detail_sample.html"


def main() -> None:
    r = requests.get(URL, headers=HEADERS, timeout=30, allow_redirects=True)
    print(f"STATUS: {r.status_code}")
    print(f"FINAL URL: {r.url}")
    print(f"LEN: {len(r.text)} chars")
    OUT_FILE.write_text(r.text, encoding="utf-8")
    print(f"SAVED: {OUT_FILE}\n")

    soup = BeautifulSoup(r.text, "html.parser")

    # 1. JSON-LD JobPosting?
    print("=== JSON-LD blocks ===")
    found_jp = False
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            t = item.get("@type")
            print(f"  @type: {t!r}  keys: {list(item.keys())[:12]}")
            if t == "JobPosting":
                found_jp = True
                print("  --- JobPosting full payload ---")
                print(json.dumps(item, indent=2, ensure_ascii=False)[:3000])
    if not found_jp:
        print("  no JobPosting JSON-LD")

    # 2. Visible contract-type / employment-type indicators on the page.
    print("\n=== Contract / employment type hints ===")
    text = soup.get_text(" ", strip=True)
    for kw in [
        "permanent", "cdi", "cdd", "internship", "intern",
        "apprentice", "apprenticeship", "fixed term", "fixed-term",
        "full-time", "full time", "part-time", "part time",
        "stage", "alternance",
    ]:
        if kw in text.lower():
            # show ~80 chars context
            idx = text.lower().find(kw)
            print(f"  '{kw}' @ {idx}: ...{text[max(0,idx-40):idx+60]!r}...")

    # 3. Look for Deezer-specific labels / card content blocks.
    print("\n=== Likely meta-info containers ===")
    for sel in [
        "div.jobdef", "div.jobloc", "span.jobloc", "div.jobdw",
        "div.job-meta", "ul.job-meta", "div.meta", "dl",
        "div.singjob", "div.singlejob", "div.job-detail", "article",
    ]:
        els = soup.select(sel)
        for el in els[:3]:
            snippet = " ".join(el.get_text(" ", strip=True).split())[:200]
            print(f"  {sel} -> {snippet!r}")

    # 4. Page title / h1
    print("\n=== Title / H1 ===")
    if soup.title:
        print(f"  <title>: {soup.title.get_text(strip=True)!r}")
    for h1 in soup.find_all("h1")[:3]:
        print(f"  <h1>: {h1.get_text(' ', strip=True)!r}")

    # 5. Look for a description container.
    print("\n=== Big content blocks (likely description) ===")
    for sel in [
        "div.jobdetails", "div.job-details", "div.singlejob",
        "div.entry-content", "div.start_guten", "main",
    ]:
        els = soup.select(sel)
        for el in els[:1]:
            txt = " ".join(el.get_text(" ", strip=True).split())
            print(f"  {sel} ({len(txt)} chars): {txt[:300]!r}")


if __name__ == "__main__":
    main()
