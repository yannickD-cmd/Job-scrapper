"""Probe 7: figure out the real pagination parameter.

probe_06 always got the same 20 rows back, so `s=21`, `s=41`, ... were
silently ignored. Try the common Avature variants and report which one
moves the result set.

We collect the first 3 native_job_ids per fetch — if they change vs.
the s=1 baseline, that variant is the right param.
"""
from __future__ import annotations

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
TARGET_FIRST = HOST + "/en_US/jobs/SearchJobs?3_110_3=18022"

OUT_DIR = pathlib.Path(__file__).parent


def ids_from(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    ids = []
    for art in soup.select("article.article--result"):
        actions = art.select_one("[id^='jobId']")
        if actions:
            ids.append(actions.get("id", "").replace("jobId", ""))
    return ids


def fetch(s, url: str) -> str:
    r = s.get(
        url,
        headers={
            "Referer": TARGET_FIRST,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "text/html, */*; q=0.01",
        },
        timeout=30,
    )
    return r.text


def main() -> None:
    s = cffi_requests.Session(impersonate="chrome131")
    s.get(SEARCH, timeout=30)
    time.sleep(2.0)

    baseline = fetch(s, AJAX + "?s=1&3_110_3=18022")
    base_ids = ids_from(baseline)
    print(f"baseline (?s=1) first 5 ids: {base_ids[:5]}   total: {len(base_ids)}")

    variants = [
        # different param names for offset/start
        AJAX + "?s=21&3_110_3=18022",
        AJAX + "?s=41&3_110_3=18022",
        AJAX + "?from=20&3_110_3=18022",
        AJAX + "?offset=20&3_110_3=18022",
        AJAX + "?start=20&3_110_3=18022",
        AJAX + "?page=2&3_110_3=18022",
        AJAX + "?3_110_3=18022&s=21",
        # different param case
        AJAX + "?S=21&3_110_3=18022",
        # maybe pagination params come from the search-page URL itself
        SEARCH + "?3_110_3=18022&s=21",
        SEARCH + "?3_110_3=18022&page=2",
    ]
    for url in variants:
        time.sleep(2.0)
        html = fetch(s, url)
        first = ids_from(html)[:5]
        same = first == base_ids[:5]
        marker = "SAME as baseline" if same else "DIFFERENT — pagination works!"
        print(f"  {url}")
        print(f"    first 5 ids: {first}   → {marker}")


if __name__ == "__main__":
    main()
