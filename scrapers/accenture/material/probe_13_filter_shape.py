"""Probe 13: re-call API, inspect aggregations, derive correct filter shape.

probe_12 failed because the saved JSON was truncated. We just call
the API again and operate on the live response.

We also try a few filter-shape variants until we get a non-empty
response with the employeeType filter applied.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

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


BASE_FIELDS = {
    "startIndex": "0",
    "maxResultSize": "50",
    "jobKeyword": "",
    "jobLanguage": "fr-fr",
    "countrySite": "fr-fr",
    "jobCountry": "France",
    "aggregations": "[]",
    "sortBy": "0",
    "componentId": "",
}


def call(s: requests.Session, label: str, fields: dict) -> dict | None:
    r = s.post(
        API_URL,
        headers={
            **HEADERS,
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": HOST,
        },
        files={k: (None, v) for k, v in fields.items()},
        timeout=30,
    )
    print(f"\n>>> {label}")
    print(f"    status={r.status_code}  len={len(r.text)}  ct={r.headers.get('content-type')}")
    if r.status_code != 200 or not r.text:
        print(f"    body[:200]: {r.text[:200]!r}")
        return None
    try:
        data = r.json()
        print(f"    total={data.get('total')}  data len={len(data.get('data') or [])}  "
              f"aggregations len={len(data.get('aggregations') or [])}")
        return data
    except Exception as e:
        print(f"    json parse failed: {e}")
        return None


def main() -> None:
    s = requests.Session()
    s.get(SEARCH_PAGE, timeout=30)
    time.sleep(1.0)

    # 1) Baseline: France only, no filters
    baseline = call(s, "baseline_france", {**BASE_FIELDS, "jobFilters": "[]"})
    if not baseline:
        print("baseline failed; aborting")
        return

    # Save the FULL response — drop the previous 200k cap
    (OUT_DIR / "api_full_baseline.json").write_text(
        json.dumps(baseline, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Show aggregations summary
    print("\n=== aggregations (full) ===")
    for agg in baseline.get("aggregations") or []:
        fn = agg.get("fieldName") or agg.get("metadataFieldName") or agg.get("displayfacet")
        items = agg.get("items") or []
        print(f"  field={fn!r}  items_count={len(items)}")
        for it in items[:15]:
            term = it.get("term") or it.get("value") or it.get("name")
            count = it.get("count") or it.get("docCount")
            print(f"    {term!r:50s} count={count}")
        if len(items) > 15:
            print(f"    ...+{len(items) - 15}")

    # 2) Try filter shapes for employeeType=Full-Time
    # The aggregations should tell us the correct fieldName + the correct values.
    # Probe shapes:
    shapes = [
        ('lower_field', [{"metadatafieldname": "employeeType", "items": ["Full-Time"]}]),
        ('camel_field', [{"metadataFieldName": "employeeType", "items": ["Full-Time"]}]),
        ('lower_with_extras', [{"metadatafieldname": "employeeType",
                                "items": ["Full-Time"],
                                "displayfacet": "Type de poste",
                                "facetdisplayname": "employeeType"}]),
        # Value variants
        ('lower_full_time_space', [{"metadatafieldname": "employeeType", "items": ["Full Time"]}]),
        ('lower_fulltime_noseparator', [{"metadatafieldname": "employeeType", "items": ["FullTime"]}]),
        # Mirror what aggregations field is exactly named
        # (will be filled in dynamically below from baseline result)
    ]

    # Dynamic: if baseline aggregations include employeeType, use that exact term spelling
    for agg in baseline.get("aggregations") or []:
        fn = agg.get("fieldName") or agg.get("metadataFieldName") or ""
        if "employee" in (fn or "").lower():
            for it in (agg.get("items") or []):
                term = it.get("term") or it.get("value") or ""
                if "full" in term.lower():
                    shapes.append(
                        (f"dyn_term_{term}",
                         [{"metadatafieldname": fn, "items": [term]}])
                    )

    for label, jf in shapes:
        time.sleep(0.8)
        call(s, f"filter:{label}", {**BASE_FIELDS, "jobFilters": json.dumps(jf)})


if __name__ == "__main__":
    main()
