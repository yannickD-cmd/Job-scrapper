"""Probe 5: find the live results endpoint on accenture.com itself.

From the saved HTML we know:
- The page is Adobe AEM (Sling resource model).
- An SDI include points at:
    /content/acom/fr-fr/careers/jobsearch/_jcr_content/root/container_main/
    container/jobsearchhero/jobsearchresultscontainer/joblistingblock.nocache.html
- Available filter IDs (enabled in fr-fr):
    skill, location, jobTypeDescription, businessArea, employeeType, remoteType
- Country is locked to France by virtue of the /fr-fr/ URL prefix.

We try several URL shapes for the same logical fetch, starting cheapest:
A) The base page with query-string filters: ?employeeType=Full-Time
B) AEM Sling include URL paths variants
C) Both with filter params

For each we report status, length, whether the response looks like a
"job listing fragment" (presence of job-row markers) vs the full page.
"""
from __future__ import annotations

import pathlib
import re
import sys
import time
from urllib.parse import quote

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
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "From": "yannickarieldossa@gmail.com",
}

HOST = "https://www.accenture.com"
BASE = HOST + "/fr-fr/careers/jobsearch"
INCLUDE_PATH = (
    "/content/acom/fr-fr/careers/jobsearch/_jcr_content/root/container_main/"
    "container/jobsearchhero/jobsearchresultscontainer/joblistingblock.nocache.html"
)

OUT_DIR = pathlib.Path(__file__).parent


def snapshot(label: str, r) -> None:
    print(f"\n>>> {label}")
    print(f"    status={r.status_code}  len={len(r.text)}  ct={r.headers.get('content-type')}")
    if r.status_code != 200 or len(r.text) < 500:
        print(f"    body[:300]: {r.text[:300]!r}")
        return
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label.lower())[:80]
    out = OUT_DIR / f"endpoint_{safe}.html"
    out.write_text(r.text, encoding="utf-8")
    print(f"    saved {out.name}")

    # Quick signal: does it contain job-row markup? Look for the markers
    # we saw in pass_a_requests_bare.html (cmp-jobs-results, joblistingblock).
    soup = BeautifulSoup(r.text, "html.parser")
    print(f"    has <title>?       {'yes (' + soup.title.get_text(strip=True)[:40] + '...)' if soup.title else 'no'}")
    print(f"    cmp-jobs-results selectors: {len(soup.select('[class*=cmp-jobs-results]'))}")
    print(f"    cmp-jobs-result/list items: {len(soup.select('[class*=cmp-jobs-result__item], [class*=cmp-jobs-result-item], [class*=joblistingblock] li, [class*=joblisting] li'))}")
    job_links = re.findall(r"/[a-z]+-[a-z]+/careers/jobdetails\?id=\w+", r.text)
    job_links += re.findall(r"/fr-fr/careers/jobdetails[^\"\'\s]*", r.text)
    job_links += re.findall(r"/careers/jobdetails/[^\"\'\s]+", r.text)
    print(f"    jobdetails-shaped links: {len(set(job_links))}")
    # total-jobs count phrasing
    m = re.search(r"(\d[\d\s,]*)\s+(emplois|offres|jobs?|résultats|results)", r.text, re.I)
    if m:
        print(f"    count phrase: {m.group(0)!r}")


def main() -> None:
    s = requests.Session()
    s.headers.update(HEADERS)

    # A) Base URL, no filters
    r = s.get(BASE, timeout=30)
    snapshot("a_base_no_filters", r)
    time.sleep(1.5)

    # B) Base URL, with the most common Accenture employeeType values
    for val in ["Full-Time", "Full Time", "full-time"]:
        r = s.get(BASE, params={"employeeType": val}, timeout=30)
        snapshot(f"b_base_employeeType={val}", r)
        time.sleep(1.5)

    # C) AEM include URL — full path on www.accenture.com
    r = s.get(HOST + INCLUDE_PATH, timeout=30)
    snapshot("c_include_raw", r)
    time.sleep(1.5)

    # D) Include URL with filter query params
    r = s.get(
        HOST + INCLUDE_PATH,
        params={"employeeType": "Full-Time"},
        timeout=30,
    )
    snapshot("d_include_employeeType_FullTime", r)
    time.sleep(1.5)

    # E) AEM resource model variants — sometimes the include is served at
    #    a sling selector on the page URL itself (e.g. .acmodels.json).
    for ext in [".html", ".joblistingblock.html", ".joblist.json", ".json"]:
        r = s.get(BASE + ext, timeout=30)
        snapshot(f"e_base_ext_{ext}", r)
        time.sleep(1.5)


if __name__ == "__main__":
    main()
