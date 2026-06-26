"""Diagnostic: why did the SG scraper miss the SGCIB 'Data Scientist' (La Défense)?

Reuses the production auth, but broadens the Quantum query to France + all
contracts + all job families, then reports what family/contract any
'Data Scientist' role actually carries vs. the scraper's scope.

Run: python -m scrapers.sg.material.probe_why_missed_datascientist
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter

import requests

from scrapers.sg.sg import (
    HEADERS,
    PROXY_URL,
    QUANTUM_SEARCH_URL,
    XHR_HEADERS,
    FIELD_TYPE,
    FIELD_LOCATION_FULL,
    FIELD_CONTRACT,
    FIELD_JOB_FAMILY,
    FIELD_LOCATION_LABEL,
    FIELD_POSTED_AT,
    PAGE_SIZE,
    REQUEST_DELAY_SECONDS,
    REQUEST_TIMEOUT,
    _grab_csrf,
    _get_bearer,
)

# Broadened query: France only, but NO contract filter and NO family filter.
def build_broad_query(skip_from: int) -> dict:
    return {
        "profile": "ces_profile_sgcareers",
        "query": {
            "advanced": [
                {"type": "simple", "name": FIELD_TYPE, "op": "eq", "value": "job"},
                {"type": "multi", "name": FIELD_LOCATION_FULL, "op": "eq", "values": ["FRA"]},
            ],
            "skipCount": PAGE_SIZE,
            "skipFrom": skip_from,
        },
        "lang": "fr",
        "responseType": "SearchResult",
    }


def search(session, bearer, skip_from):
    headers = {
        **HEADERS,
        **XHR_HEADERS,
        "Content-Type": "application/json",
        "Authorization-API": "Bearer " + bearer,
        "X-Proxy-URL": QUANTUM_SEARCH_URL,
    }
    r = session.post(PROXY_URL, headers=headers,
                     data=json.dumps(build_broad_query(skip_from)),
                     timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    session = requests.Session()
    session.headers.update(HEADERS)
    csrf = _grab_csrf(session)
    time.sleep(REQUEST_DELAY_SECONDS)
    bearer = _get_bearer(session, csrf)
    time.sleep(REQUEST_DELAY_SECONDS)

    family_counter = Counter()
    contract_counter = Counter()
    data_roles = []
    all_docs = []

    skip_from = 0
    total = None
    while True:
        payload = search(session, bearer, skip_from)
        if total is None:
            total = payload.get("TotalCount", 0)
            print(f"France TotalCount (all families/contracts): {total}")
        docs = (payload.get("Result") or {}).get("Docs") or []
        for d in docs:
            all_docs.append(d)
            fam = (d.get(FIELD_JOB_FAMILY) or "—").strip()
            con = (d.get(FIELD_CONTRACT) or "—").strip()
            family_counter[fam] += 1
            contract_counter[con] += 1
            title = (d.get("title") or d.get("resulttitle") or "").strip()
            if "data scien" in title.lower() or "data scientist" in title.lower():
                data_roles.append(d)
        if not docs or skip_from + len(docs) >= total:
            break
        skip_from += PAGE_SIZE
        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"\nCollected {len(all_docs)} France job docs.\n")

    print("=== Job families present in France (count) ===")
    for fam, n in family_counter.most_common():
        flag = "  <-- IN SCOPE" if fam.lower() in ("it", "innovation/digital/projet/organisation",
                                                    "innovation / digital / projet / organisation") else ""
        print(f"  {n:>4}  {fam}{flag}")

    print("\n=== Contract types present in France (count) ===")
    for con, n in contract_counter.most_common():
        print(f"  {n:>4}  {con}")

    print(f"\n=== 'Data Scien*' titled roles found: {len(data_roles)} ===")
    for d in data_roles:
        print(f"\n  Title    : {(d.get('title') or '').strip()}")
        print(f"  Family   : {(d.get(FIELD_JOB_FAMILY) or '').strip()}")
        print(f"  Contract : {(d.get(FIELD_CONTRACT) or '').strip()}")
        print(f"  Location : {(d.get(FIELD_LOCATION_LABEL) or '').strip()}")
        print(f"  Posted   : {(d.get(FIELD_POSTED_AT) or '').strip()}")
        print(f"  URL      : {(d.get('resulturl') or d.get('url1') or '').strip()}")


if __name__ == "__main__":
    main()
