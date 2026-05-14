"""Probe 5: nail down the description block + check for any date field.

Probe 3 found 6423 chars in `div.start_guten` but that block also
contained the page title at the top. Need to find the cleanest sub-block
that holds *only* the job description so we don't duplicate the title.

Also: search the detail HTML for any date-shaped text (ISO, dd/mm/yyyy,
"posted", "published", etc.) — Deezer may not expose one, in which case
the scraper leaves posted_date null and we lean on the DB's last_seen_at.
"""
from __future__ import annotations

import pathlib
import re
import sys

from bs4 import BeautifulSoup

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

HTML_FILE = pathlib.Path(__file__).parent / "detail_sample.html"


def main() -> None:
    html = HTML_FILE.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    # 1. Look at the full start_guten block tree.
    block = soup.select_one("div.start_guten")
    if not block:
        print("no start_guten block")
        return

    print("=== start_guten direct children ===")
    for child in block.find_all(recursive=False):
        snippet = " ".join(child.get_text(" ", strip=True).split())[:140]
        print(f"  <{child.name} class={child.get('class')}>: {snippet!r}")

    # 2. Try common candidate selectors for the description body.
    print("\n=== Candidate description selectors ===")
    for sel in [
        "div.singjobdesc", "div.jobdesc", "div.singlejob",
        "div.job-description", "div.description", "article",
        "div.entry-content", "div.start_guten > div",
    ]:
        els = soup.select(sel)
        for el in els[:1]:
            txt = " ".join(el.get_text(" ", strip=True).split())
            print(f"  {sel} ({len(txt)} chars): {txt[:160]!r}")

    # 3. Date-shaped patterns.
    print("\n=== Date-shaped tokens ===")
    patterns = [
        r"\d{4}-\d{2}-\d{2}",
        r"\d{2}/\d{2}/\d{4}",
        r"\d{1,2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}",
        r"datePosted",
        r"posted",
        r"published",
        r"date_posted",
        r"date-posted",
    ]
    for p in patterns:
        for m in re.finditer(p, html, re.IGNORECASE):
            ctx = html[max(0, m.start() - 40):m.end() + 40]
            ctx = ctx.replace("\n", " ")
            print(f"  /{p}/ -> {ctx!r}")
            # cap output per pattern
            break

    # 4. Look for an "Apply now" link/iframe (sometimes embeds an ATS URL
    # that has the canonical req-id).
    print("\n=== Apply links / iframes ===")
    for a in soup.find_all("a"):
        text = (a.get_text(" ", strip=True) or "").lower()
        if "apply" in text:
            print(f"  <a> text={text!r} href={a.get('href')!r}")
    for ifr in soup.find_all("iframe")[:5]:
        print(f"  <iframe src={ifr.get('src')!r}>")


if __name__ == "__main__":
    main()
