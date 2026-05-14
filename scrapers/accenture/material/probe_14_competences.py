"""Probe 14: enumerate competences from the actual 211-job dataset.

We have 211 France jobs accessible via the API. Each row's data
contains:
  title, jobId, jobCityState, postedDate, skill, requisitionId,
  jobDetailUrl, jobDescription, jobRemoteType, regionDescription, ...

We walk all 211 (5 pages of 50), and tally:
- skill (competences)
- jobRemoteType
- requisitionId pattern
- regionDescription / jobCityState

That tells us the universe of competences and where Full-Time lives.
Also dumps the Sw aggregations constant from the JS bundle so we know
what facet name the API expects.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import time
from collections import Counter

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

HOST = "https://www.accenture.com"
SEARCH_PAGE = HOST + "/fr-fr/careers/jobsearch"
API_URL = HOST + "/api/accenture/jobsearch/result"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "From": "yannickarieldossa@gmail.com",
    "Referer": SEARCH_PAGE,
}

OUT_DIR = pathlib.Path(__file__).parent


def find_sw_constant() -> None:
    js_path = OUT_DIR / "clientlib_site.min.js"
    if not js_path.exists():
        return
    js = js_path.read_text(encoding="utf-8")
    for m in re.finditer(r"\bSw=", js):
        ctx = js[m.start(): m.start() + 1500]
        # Find the matching close — heuristic: until the first `,XX=`
        print(f"\n=== Sw constant @ offset {m.start()} ===")
        print(ctx[:1200])
        print("...")
        break


def get_page(s: requests.Session, start: int) -> dict | None:
    fields = {
        "startIndex": str(start),
        "maxResultSize": "50",
        "jobKeyword": "",
        "jobLanguage": "fr-fr",
        "countrySite": "fr-fr",
        "jobCountry": "France",
        "jobFilters": "[]",
        "aggregations": "[]",
        "sortBy": "0",
        "componentId": "",
    }
    r = s.post(
        API_URL,
        headers={**HEADERS, "Accept": "*/*", "X-Requested-With": "XMLHttpRequest",
                 "Origin": HOST},
        files={k: (None, v) for k, v in fields.items()},
        timeout=30,
    )
    if r.status_code != 200 or not r.text:
        print(f"  fail at startIndex={start}: status={r.status_code} len={len(r.text)}")
        return None
    return r.json()


def main() -> None:
    print("--- Sw constant search ---")
    find_sw_constant()

    s = requests.Session()
    s.get(SEARCH_PAGE, timeout=30)
    time.sleep(1.0)

    all_jobs = []
    # The JS uses Lw(f) = max(0, f-1) — so startIndex sent is one less than
    # the page number. The minimal_france call with startIndex=0 returned
    # 50 rows. So startIndex is 0-indexed.
    for page in range(0, 5):
        start = page * 50
        time.sleep(1.5)
        data = get_page(s, start)
        if not data:
            break
        rows = data.get("data") or []
        all_jobs.extend(rows)
        print(f"page {page} (startIndex={start}): {len(rows)} rows, "
              f"cumulative={len(all_jobs)}/{data.get('total')}")
        if len(rows) < 50:
            break

    print(f"\nTOTAL collected: {len(all_jobs)}")

    # Per-row inspection
    if all_jobs:
        print("\n=== sample row keys & first 3 jobs ===")
        print(f"all keys: {sorted(set(k for j in all_jobs for k in j.keys()))[:40]}")
        for j in all_jobs[:3]:
            print("---")
            for k in ("title", "jobId", "jobCityState", "regionDescription",
                     "postedDate", "postedDateText", "jobDetailUrl",
                     "skill", "businessArea", "employeeType",
                     "jobTypeDescription", "remoteType", "jobRemoteType",
                     "requisitionId"):
                if k in j:
                    print(f"  {k}: {str(j[k])[:120]!r}")

    # Tally categorical fields that exist in the data
    fields_to_tally = ["skill", "businessArea", "jobTypeDescription",
                       "employeeType", "remoteType", "jobRemoteType",
                       "regionDescription"]
    for f in fields_to_tally:
        vals = [j.get(f) for j in all_jobs if j.get(f) is not None]
        if not vals:
            continue
        # flatten if lists
        flat = []
        for v in vals:
            if isinstance(v, list):
                flat.extend(str(x) for x in v if x)
            else:
                flat.append(str(v))
        c = Counter(flat)
        print(f"\n=== {f} distribution ({len(c)} distinct, {len(flat)} totals) ===")
        for k, n in c.most_common(30):
            print(f"  {n:4d}  {k!r}")


if __name__ == "__main__":
    main()
