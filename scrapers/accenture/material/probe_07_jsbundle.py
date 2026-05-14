"""Probe 7: fetch the main JS bundle, search it for the real jobs URL.

The static HTML has 0 actual job rows (just empty container divs). The
JS at clientlib-site.lc-d6b6c788c5dfc793b676204a8942394c-lc.min.js is
what fetches and renders them. Minified, but URL strings stay as
string literals — we can grep them out.

Targets to look for:
- URL patterns containing "search", "jobs", "jobsearch", ".json"
- ajax/fetch call sites
- any reference to careers.accenture.com / careers.workday / similar
- the actual XHR path used by the search/filter components.
"""
from __future__ import annotations

import pathlib
import re
import sys
import time

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

HOST = "https://www.accenture.com"
JS_URL = HOST + "/etc.clientlibs/cio-sites/clientlibs/clientlib-site.lc-d6b6c788c5dfc793b676204a8942394c-lc.min.js"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Referer": HOST + "/fr-fr/careers/jobsearch",
}

OUT_DIR = pathlib.Path(__file__).parent


def main() -> None:
    print(f"GET {JS_URL}")
    r = requests.get(JS_URL, headers=HEADERS, timeout=60)
    print(f"  status={r.status_code}  len={len(r.text):,}")
    if r.status_code != 200:
        print(f"  body[:200]={r.text[:200]!r}")
        return

    js = r.text
    (OUT_DIR / "clientlib_site.min.js").write_text(js, encoding="utf-8")

    # Pull every URL-shaped literal
    url_lits = re.findall(r'["\'](/[^"\'<>\s]{6,160})["\']', js)
    print(f"\nURL-shaped string literals found: {len(url_lits)} (unique {len(set(url_lits))})")

    # Filter to job/career related
    relevant = sorted({u for u in url_lits if re.search(r"(job|career|search|api|services|.json|.html)", u, re.I)})
    print(f"\nJob/career-related URL literals ({len(relevant)}):")
    for u in relevant[:60]:
        print(f"  {u}")

    # Search for specific phrases used in code (often shows endpoint shape)
    print("\nKeyword hits in JS body:")
    for kw in [
        "jobsearch", "/jobs?", "joblisting", "joblist", ".json",
        "X-Requested-With", "fetchJobs", "loadJobs", "getJobs",
        "ajaxJobs", "fetch(", "axios", "$.ajax", "XMLHttpRequest",
        "searchKeywords", "categoryId", "employeeType",
    ]:
        n = js.lower().count(kw.lower())
        if n:
            print(f"  {kw!r:25s}: {n} occurrences")

    # Show context around 'employeeType' and 'jobsearch' specifically
    for kw in ["employeeType", "joblisting", "jobsearchresults"]:
        for m in re.finditer(re.escape(kw), js):
            ctx = js[max(0, m.start() - 80): m.start() + 200].replace("\n", " ")
            print(f"\nctx for {kw!r}:\n  ...{ctx}...")
            break


if __name__ == "__main__":
    main()
