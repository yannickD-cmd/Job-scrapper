"""Probe 11: GET csrf token, then POST jobsearch/result with the full payload.

Per probe_10, the JS does:

  fetch("/libs/granite/csrf/token.json")  -> { token: "..." }
  then fetch("/api/accenture/jobsearch/result", {
    method: "POST",
    headers: { "CSRF-Token": token },
    body: FormData {
      startIndex:    0-indexed (Lw(f) where f is e.f)
      maxResultSize: number (e.s)  — default "0"
      jobKeyword:    text     (e.query)
      jobLanguage:   "fr-fr"  (e.lang from data-countrycode->lang)
      countrySite:   "fr-fr"  (e.cs)
      jobFilters:    JSON-string of [{metadatafieldname, items:[...]}]
                     items=[] entries are filtered out by `ww()`
      aggregations:  JSON-string (default Sw constant, unknown — try [])
      jobCountry:    "France" (e.c)
      sortBy:        "0"
      componentId:   "..." (an AEM component id — try empty)
    }
  })

This probe:
1. GET the search page to seed any session cookies.
2. GET /libs/granite/csrf/token.json -> grab token.
3. POST the minimal payload first (no filter).
4. POST with the employeeType=Full-Time filter.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import time

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

HOST = "https://www.accenture.com"
SEARCH_PAGE = HOST + "/fr-fr/careers/jobsearch"
CSRF_URL = HOST + "/libs/granite/csrf/token.json"
API_URL = HOST + "/api/accenture/jobsearch/result"

HEADERS_DOC = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "From": "yannickarieldossa@gmail.com",
    "Referer": SEARCH_PAGE,
}

OUT_DIR = pathlib.Path(__file__).parent


def post_api(s: requests.Session, csrf: str, label: str, fields: dict) -> None:
    print(f"\n>>> {label}")
    print(f"    csrf={csrf!r}")
    print(f"    fields:")
    for k, v in fields.items():
        print(f"      {k}: {v[:80]!r}")

    r = s.post(
        API_URL,
        headers={
            **HEADERS_DOC,
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": HOST,
            "CSRF-Token": csrf,
        },
        # FormData on requests = `files=...` (multipart) OR `data=...` (urlencoded).
        # JS's FormData defaults to multipart/form-data; mimic by passing tuples.
        files={k: (None, v) for k, v in fields.items()},
        timeout=30,
    )
    ct = r.headers.get("content-type", "")
    print(f"    status={r.status_code}  len={len(r.text)}  ct={ct}")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label.lower())[:80]
    (OUT_DIR / f"api_call_{safe}.txt").write_text(r.text[:200000], encoding="utf-8")
    if "json" in ct:
        try:
            data = r.json()
            if isinstance(data, dict):
                print(f"    JSON keys: {list(data.keys())[:25]}")
                for k in (
                    "total", "totalJobs", "totalCount", "totalRecord",
                    "totalRecordsCount", "jobs", "jobList", "results",
                    "jobPostings", "data", "acnfacets", "facets",
                ):
                    if k in data:
                        v = data[k]
                        if isinstance(v, list):
                            print(f"      {k!r}: list len={len(v)}")
                            if v and isinstance(v[0], dict):
                                print(f"        first item keys: {list(v[0].keys())[:18]}")
                        else:
                            print(f"      {k!r}: {str(v)[:120]}")
        except Exception as e:
            print(f"    json parse failed: {e}  body[:200]={r.text[:200]!r}")
    else:
        print(f"    body[:300]={r.text[:300]!r}")


def main() -> None:
    s = requests.Session()
    s.headers.update(HEADERS_DOC)

    # Warm the session by hitting the careers page
    r0 = s.get(SEARCH_PAGE, timeout=30)
    print(f"warmup GET {SEARCH_PAGE} -> {r0.status_code}")
    print(f"  cookies after warmup: {[c.name for c in s.cookies]}")
    time.sleep(1.5)

    # Grab CSRF token
    r1 = s.get(CSRF_URL, headers={**HEADERS_DOC, "Accept": "application/json"}, timeout=30)
    print(f"\nGET {CSRF_URL} -> {r1.status_code}  ct={r1.headers.get('content-type')}")
    print(f"  body: {r1.text[:200]!r}")
    csrf = ""
    try:
        csrf = r1.json().get("token", "")
    except Exception:
        pass
    print(f"  parsed token: {csrf!r}")

    # POST 1: minimal — France, no other filters
    post_api(s, csrf, "minimal_france", {
        "startIndex": "0",
        "maxResultSize": "50",
        "jobKeyword": "",
        "jobLanguage": "fr-fr",
        "countrySite": "fr-fr",
        "jobCountry": "France",
        "jobFilters": "[]",
        "aggregations": "[]",
        "sortBy": "0",
        "componentId": "",
    })
    time.sleep(1.0)

    # POST 2: with the employeeType=Full-Time filter via jobFilters
    post_api(s, csrf, "france_employeetype_fulltime_lower", {
        "startIndex": "0",
        "maxResultSize": "50",
        "jobKeyword": "",
        "jobLanguage": "fr-fr",
        "countrySite": "fr-fr",
        "jobCountry": "France",
        "jobFilters": json.dumps([
            {"metadatafieldname": "employeeType", "items": ["Full-Time"]}
        ]),
        "aggregations": "[]",
        "sortBy": "0",
        "componentId": "",
    })


if __name__ == "__main__":
    main()
