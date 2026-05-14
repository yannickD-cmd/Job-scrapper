"""Probe 6: replay the page's XHR call to the AEM include URL exactly.

From the page's inline JS at line 2358:
    url = "/content/acom/fr-fr/careers/jobsearch/_jcr_content/.../joblistingblock.nocache.html"
    xhr.open('GET', url + window.location.search)
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest')

In probe_05 we fetched the URL but WITHOUT the X-Requested-With header,
and got back a 33-byte empty stub. AEM dispatchers commonly gate the
SDI-include path on that header. Retrying with it now.

Also testing whether `?employeeType=Full-Time` (and various param shapes)
actually narrows the result set.
"""
from __future__ import annotations

import pathlib
import re
import sys
import time
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

HEADERS_DOC = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "From": "yannickarieldossa@gmail.com",
}
HEADERS_XHR = {
    **HEADERS_DOC,
    "Accept": "*/*",
    "X-Requested-With": "XMLHttpRequest",
}

HOST = "https://www.accenture.com"
BASE = HOST + "/fr-fr/careers/jobsearch"
INCLUDE = (
    HOST
    + "/content/acom/fr-fr/careers/jobsearch/_jcr_content/root/container_main/"
    + "container/jobsearchhero/jobsearchresultscontainer/joblistingblock.nocache.html"
)

OUT_DIR = pathlib.Path(__file__).parent


def snapshot(label: str, r: requests.Response) -> None:
    print(f"\n>>> {label}")
    print(f"    status={r.status_code}  len={len(r.text)}  ct={r.headers.get('content-type')}")
    if r.status_code != 200:
        print(f"    body[:200]: {r.text[:200]!r}")
        return
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label.lower())[:80]
    (OUT_DIR / f"xhr_{safe}.html").write_text(r.text, encoding="utf-8")
    soup = BeautifulSoup(r.text, "html.parser")
    # Look for job-row markers in this fragment
    for sel in [
        ".cmp-jobs-list__item", ".cmp-job-listing__item", ".cmp-job-card",
        "[class*=cmp-jobs-result__item]", "[class*=joblistingblock] li",
        "[class*=joblist__item]", "[class*=cmp-job-list-item]",
        "article", "li",
    ]:
        n = len(soup.select(sel))
        if n:
            print(f"    sel {sel!r:35s} → {n}")
    # find any jobdetails anchors
    detail_links = re.findall(r"/[a-z]+-[a-z]+/careers/jobdetails[^\"\'\s]*", r.text)
    print(f"    jobdetails anchors: {len(set(detail_links))} (sample {list(set(detail_links))[:3]})")
    # find total count phrasing
    for m in re.finditer(r"(\d[\d\s,]*)\s*(emplois|offres|jobs?|résultats|results)", r.text, re.I):
        print(f"    count phrase: {m.group(0)!r}")
        break


def main() -> None:
    s = requests.Session()
    s.headers.update(HEADERS_DOC)

    # warm up by hitting the base page (sets dispatcher session cookies)
    s.get(BASE, timeout=30)
    time.sleep(1.5)

    # A) include URL with XHR header, no params
    r = s.get(INCLUDE, headers=HEADERS_XHR, timeout=30)
    snapshot("a_include_xhr_noparams", r)
    time.sleep(1.5)

    # B) include URL with XHR header + employeeType=Full-Time
    r = s.get(
        INCLUDE + "?" + urlencode({"employeeType": "Full-Time"}),
        headers=HEADERS_XHR, timeout=30,
    )
    snapshot("b_include_xhr_employeeType_FullTime", r)
    time.sleep(1.5)

    # C) include URL with XHR header + various employeeType spellings
    for val in ["Full Time", "FullTime", "full-time", "full_time"]:
        r = s.get(
            INCLUDE + "?" + urlencode({"employeeType": val}),
            headers=HEADERS_XHR, timeout=30,
        )
        snapshot(f"c_include_xhr_employeeType_{val}", r)
        time.sleep(1.0)

    # D) URL with pagination params commonly used by Accenture
    r = s.get(
        INCLUDE + "?" + urlencode({"pageSize": 50, "pg": 1}),
        headers=HEADERS_XHR, timeout=30,
    )
    snapshot("d_include_xhr_paginated", r)


if __name__ == "__main__":
    main()
