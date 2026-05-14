"""Probe 4: fetch the real listings page and figure out how filters work.

Goals:
1. Fetch /en/careers/all-job-offers (no filters) — see if rows are
   server-rendered, identify facet UI (country dropdown? checkboxes?
   URL-path filters? query params?).
2. Fetch /en/careers/all-job-offers/permanent — confirm contract type
   maps to a URL segment.
3. Look for a France country filter and figure out its URL shape.

We re-use the Akamai warm-up pattern from probe_02: hit homepage first
to seed cookies, then navigate within the session with a Referer.
"""
from __future__ import annotations

import json
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
OUT_DIR = pathlib.Path(__file__).parent


def warmup_session() -> requests.Session:
    """One throwaway hit to the homepage to seed Akamai cookies."""
    s = requests.Session()
    s.get(HOST + "/", headers=HEADERS, timeout=30)
    return s


def hit(s: requests.Session, url: str, *, referer: str) -> requests.Response:
    headers = dict(HEADERS)
    headers["Referer"] = referer
    headers["Sec-Fetch-Site"] = "same-origin"
    r = s.get(url, headers=headers, timeout=30, allow_redirects=True)
    print(f"\n>>> GET {url}")
    print(f"    status={r.status_code}  final={r.url}  len={len(r.text)}")
    return r


def analyze(html: str, label: str) -> None:
    soup = BeautifulSoup(html, "html.parser")
    print(f"\n--- {label} ---")

    # Likely facet/filter elements: <select>, <input type=checkbox>, <details>
    selects = soup.find_all("select")
    print(f"  <select> count: {len(selects)}")
    for s in selects[:8]:
        name = s.get("name") or s.get("id") or "(unnamed)"
        opts = [o.get_text(strip=True) for o in s.find_all("option")][:6]
        print(f"    select[name={name!r}] sample options={opts}")

    checkboxes = soup.find_all("input", type="checkbox")
    print(f"  checkbox count: {len(checkboxes)}")
    for c in checkboxes[:12]:
        name = c.get("name") or c.get("id")
        val = c.get("value")
        # try to grab the visible label
        label_text = ""
        if c.get("id"):
            lab = soup.find("label", attrs={"for": c["id"]})
            if lab:
                label_text = lab.get_text(" ", strip=True)
        print(f"    checkbox name={name!r} value={val!r} label={label_text!r}")

    # Look for any element mentioning France
    france_mentions = soup.find_all(string=re.compile(r"\bFrance\b", re.I))
    print(f"  'France' string matches: {len(france_mentions)}")
    for t in france_mentions[:5]:
        parent = t.parent
        ptag = parent.name if parent else "?"
        print(f"    in <{ptag}> -> {t.strip()[:80]!r}")

    # Look for job-shaped rows: anchors or articles with a stable id/href
    print("  candidate job rows:")
    for sel in [
        "article", "li.job", "div.job", "[data-job-id]", "[data-jobid]",
        "[class*=job-card]", "[class*=joboffer]", "[class*=offer-item]",
    ]:
        found = soup.select(sel)
        if found:
            print(f"    selector {sel!r}: {len(found)} matches")
            for el in found[:3]:
                txt = el.get_text(" ", strip=True)[:120]
                href = el.get("href") or (el.find("a", href=True) or {}).get("href", "")
                print(f"      → href={href!r} text={txt!r}")

    # Pagination indicators
    print("  pagination clues:")
    for sel in [
        ".pagination", "[class*=pagination]", "input.pagination-current",
        "[aria-label*=page]", "[aria-label*=Page]",
    ]:
        found = soup.select(sel)
        if found:
            for el in found[:3]:
                print(f"    {sel!r}: {el.name}[{el.get('class')}] text={el.get_text(' ', strip=True)[:80]!r}")
                break

    # Inline JSON state
    for script in soup.find_all("script"):
        sid = script.get("id") or ""
        src = script.get("src") or ""
        if sid or "data" in src.lower() or "jobs" in src.lower():
            txt = (script.string or "")[:80]
            print(f"  <script id={sid!r} src={src!r}> body[:80]={txt!r}")

    # Count number of links that look like /en/careers/all-job-offers/<slug>/<id>
    job_link_re = re.compile(r"/en/careers/all-job-offers/[^/]+/[^?#]+")
    matches = sorted(set(job_link_re.findall(html)))
    print(f"  job-detail-shaped links in html: {len(matches)}")
    for m in matches[:8]:
        print(f"    {m}")


def main() -> None:
    s = warmup_session()
    time.sleep(2.0)

    # Step 1: unfiltered listings page
    url1 = HOST + "/en/careers/all-job-offers"
    r1 = hit(s, url1, referer=HOST + "/en/careers")
    (OUT_DIR / "all_job_offers.html").write_text(r1.text, encoding="utf-8")
    if r1.status_code == 200:
        analyze(r1.text, "all_job_offers.html")

    time.sleep(2.0)

    # Step 2: permanent contract type
    url2 = HOST + "/en/careers/all-job-offers/permanent"
    r2 = hit(s, url2, referer=url1)
    (OUT_DIR / "all_job_offers_permanent.html").write_text(r2.text, encoding="utf-8")
    if r2.status_code == 200:
        analyze(r2.text, "all_job_offers_permanent.html")


if __name__ == "__main__":
    main()
