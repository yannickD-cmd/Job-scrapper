"""Probe 8: POST to /api/accenture/jobsearch/result and see what's there.

probe_07 found the live API endpoint in the JS bundle:
    POST /api/accenture/jobsearch/result   (FormData)

Param names extracted from the minified code:
    startIndex, query, s, f, lang, cs, c, sb, endpoint, df, componentId
Field filter list (each pushed via FormData):
    keyword, location, postedDate, jobTypeDescription, businessArea,
    travelPercentage, yearsOfExperience, specialization, employeeType,
    remoteType (and probably skill / countryLabel based on earlier probes)

We start with a minimal POST to see what the response shape is, then
incrementally add params (employeeType=Full-Time, c=fr, etc.) to home
in on a working France + Full-Time query.
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
API_URL = HOST + "/api/accenture/jobsearch/result"

HEADERS_DOC = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Referer": SEARCH_PAGE,
    "From": "yannickarieldossa@gmail.com",
}
HEADERS_AJAX = {
    **HEADERS_DOC,
    "Accept": "*/*",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": HOST,
}

OUT_DIR = pathlib.Path(__file__).parent


def post(label: str, s: requests.Session, payload: dict) -> None:
    print(f"\n>>> {label}")
    print(f"    payload: {payload}")
    # FormData == multipart/form-data; requests does that automatically when
    # you pass `files=` or `data=` of strings. The minified JS uses
    # `FormData.append`, which results in multipart. We try both
    # encodings to see which works.
    r = s.post(API_URL, headers=HEADERS_AJAX, data=payload, timeout=30)
    ct = r.headers.get("content-type", "")
    print(f"    status={r.status_code}  len={len(r.text)}  ct={ct}")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label.lower())[:80]
    out = OUT_DIR / f"api_{safe}.txt"
    out.write_text(r.text[:50000], encoding="utf-8")  # cap to be safe
    print(f"    saved {out.name}")
    if "json" in ct:
        try:
            data = r.json()
            if isinstance(data, dict):
                print(f"    JSON keys: {list(data.keys())[:25]}")
                for k in ("total", "totalJobs", "totalCount", "count", "jobs", "results", "data"):
                    if k in data:
                        v = data[k]
                        if isinstance(v, list):
                            print(f"      {k!r}: list len={len(v)}")
                            if v and isinstance(v[0], dict):
                                print(f"      first row keys: {list(v[0].keys())[:18]}")
                        else:
                            print(f"      {k!r}: {str(v)[:120]}")
            elif isinstance(data, list):
                print(f"    JSON list len={len(data)}")
                if data and isinstance(data[0], dict):
                    print(f"      first row keys: {list(data[0].keys())[:18]}")
        except Exception as e:
            print(f"    json parse failed: {e}")
    else:
        # text/HTML — look for jobdetails-shaped strings
        links = re.findall(r"/[a-z]+-[a-z]+/careers/jobdetails[^\"\'\s]*", r.text)
        print(f"    jobdetails-shaped strings: {len(set(links))}")
        # show first 400 chars
        body = r.text.replace("\n", " ")
        print(f"    body[:400]: {body[:400]!r}")


def main() -> None:
    s = requests.Session()
    s.headers.update(HEADERS_DOC)
    # Warm the session
    s.get(SEARCH_PAGE, timeout=30)
    time.sleep(1.5)

    # Try with NO body first to see if it gives a "missing params" message
    post("empty", s, {})
    time.sleep(1.0)

    # Minimal startIndex
    post("startIndex_0", s, {"startIndex": "0"})
    time.sleep(1.0)

    # Add language + country hints
    post("with_lang_cs", s, {"startIndex": "0", "lang": "fr-fr", "cs": "fr-fr"})
    time.sleep(1.0)

    # Plus the employeeType filter
    post("plus_employeeType", s, {
        "startIndex": "0",
        "lang": "fr-fr", "cs": "fr-fr",
        "employeeType": "Full-Time",
    })
    time.sleep(1.0)

    # Sometimes Accenture's API uses `c` for country and `s` for page size
    post("c_fr_s_50", s, {
        "startIndex": "0",
        "lang": "fr-fr", "cs": "fr-fr",
        "c": "FR", "s": "50",
        "employeeType": "Full-Time",
    })


if __name__ == "__main__":
    main()
