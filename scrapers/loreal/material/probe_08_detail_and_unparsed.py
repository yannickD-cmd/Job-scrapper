"""Probe 8: figure out two outstanding issues from the first smoke test.

Issue A — descriptions blank on all 8 kept jobs.
   Enrichment found JSON-LD JobPosting (so returned True) but
   payload.description was empty/None. Dump the full JSON-LD for one
   kept job to see what fields are actually populated and find the
   real description location.

Issue B — 13 listing rows had unparseable dataLayer.
   One of them ('Data Project Manager, HR Domain' / id 240207) looks
   like it could be a Permanent + Tech/Data role we're losing.
   Find that row in the listing HTML and dump its <script> block to
   see why our regex missed.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import time

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

HOST = "https://careers.loreal.com"
SEARCH = HOST + "/en_US/jobs/SearchJobs"
AJAX = HOST + "/en_US/jobs/SearchJobsAJAX"
OUT_DIR = pathlib.Path(__file__).parent


def warm(s):
    """Match the real scraper's warm-up: hit the bare search base AND
    fetch at least one listing page so Cloudflare sees real navigation
    pattern before we ask for a detail page. Single warm + immediate
    detail fetch was getting 403 in earlier runs."""
    s.get(SEARCH, timeout=30)
    time.sleep(2.0)
    s.get(SEARCH + "?3_110_3=18022", timeout=30)
    time.sleep(2.0)
    s.get(
        AJAX + "?offset=0&3_110_3=18022",
        headers={
            "Referer": SEARCH + "?3_110_3=18022",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "text/html, */*; q=0.01",
        },
        timeout=30,
    )
    time.sleep(2.0)


def main() -> None:
    s = cffi_requests.Session(impersonate="chrome131")
    warm(s)

    # ----- Issue A: dump JSON-LD for a kept job ----------------------------
    detail_url = HOST + "/en_US/jobs/JobDetail/Cyberdefense-Expert/236746"
    r = s.get(
        detail_url,
        headers={
            "Referer": SEARCH + "?3_110_3=18022",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        timeout=30,
    )
    print(f"--- DETAIL {detail_url} ---")
    print(f"    status={r.status_code}  len={len(r.text)}")
    (OUT_DIR / "detail_236746.html").write_text(r.text, encoding="utf-8")

    soup = BeautifulSoup(r.text, "html.parser")
    found = False
    for i, script in enumerate(soup.select('script[type="application/ld+json"]')):
        body = script.string or ""
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            print(f"    [ld+json #{i}] unparseable ({e})")
            continue
        items = data if isinstance(data, list) else [data]
        for j, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            t = item.get("@type")
            print(f"    [ld+json #{i}.{j}] @type={t!r}  keys={list(item.keys())}")
            if t == "JobPosting":
                found = True
                for k, v in item.items():
                    s_ = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
                    print(f"      {k!r}: {s_[:220]}")
                if "description" in item:
                    desc = item["description"] or ""
                    print(f"      >> description length: {len(desc)}")

    if not found:
        print("    NO JobPosting in JSON-LD — falling back to scanning the body")

    # Even if JSON-LD lacked description, the page itself has it somewhere.
    print("\n--- DESCRIPTION-CONTAINER SCAN (detail_236746.html) ---")
    for sel in [
        "[itemprop='description']",
        "#job-description", "div.job-description", ".job-description",
        "[class*=job-description]",
        ".jobDescriptionLine", "[class*=description]",
        ".article", "article",
        "#textDescription", "div.row.description",
    ]:
        elems = soup.select(sel)
        for el in elems[:1]:
            txt = el.get_text(" ", strip=True)
            if txt and len(txt) > 200:
                print(f"  hit: {sel!r}  text_len={len(txt)}  preview={txt[:160]!r}")
                break

    # ----- Issue B: walk pages, find the 240207 row, dump its script ------
    time.sleep(2.0)
    print("\n--- finding listing row for jobId240207 (Data Project Manager) ---")
    for offset in range(0, 180, 20):
        time.sleep(2.0)
        fragment = s.get(
            AJAX + f"?offset={offset}&3_110_3=18022",
            headers={
                "Referer": SEARCH + "?3_110_3=18022",
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "text/html, */*; q=0.01",
            },
            timeout=30,
        ).text
        soup2 = BeautifulSoup(fragment, "html.parser")
        articles = soup2.select("article.article--result")
        for art in articles:
            actions = art.select_one("[id^='jobId']")
            if actions and actions.get("id") == "jobId240207":
                print(f"  found at offset={offset}")
                # Print every script body inside this article
                scripts = art.find_all("script")
                print(f"  scripts in article: {len(scripts)}")
                for k, sc in enumerate(scripts):
                    body = sc.string or ""
                    print(f"  [script #{k}] string-attr len={len(body)}")
                    # check 'eventLabel' anywhere in the article
                if "eventLabel" in str(art):
                    idx = str(art).find("eventLabel")
                    snippet = str(art)[max(0, idx - 60):idx + 320]
                    print(f"  raw eventLabel context:")
                    print(f"    ...{snippet}...")
                else:
                    print("  no 'eventLabel' substring anywhere in article HTML")
                return


if __name__ == "__main__":
    main()
