"""Decisive test: is the HTTP_400 a rate throttle, or the two-facet request body?

A token bucket 400s the Nth request REGARDLESS of body. So we order the requests
worst-first: the scraper's exact two-facet body FIRST (freshest bucket), then a
single-facet body, then bare. If the FIRST (two-facet) 400s while a LATER, simpler
request 200s, the bucket theory is REFUTED (a bucket can't let a later request
through after failing an earlier one) — the two-facet combination is the culprit.

    .venv\\Scripts\\python.exe scrapers\\pernodricard\\material\\diag_facets.py

Verdicts:
  two-facet 200                      -> scraper body is fine right now; failures are pure throttle/timing.
  two-facet 400, single 200, bare 200 -> NOT throttle: the jobFamilyGroup+workerSubType COMBO is rejected.
                                          Fix: POST one facet, filter the other client-side / at detail.
  two-facet 400, single 400, bare 200 -> only unfaceted works -> any facet rejected (or live ban).
  all 400                            -> hard ban / IP block right now. Inconclusive; retry after cooldown.
"""
from __future__ import annotations

import time

import requests

HOST = "https://pernodricard.wd3.myworkdayjobs.com"
SITE = "pernod-ricard"
TENANT = "pernodricard"
LIST_URL = f"{HOST}/wday/cxs/{TENANT}/{SITE}/jobs"
TECH_FAMILY = "5c4276c36b5a1001e317a08d36940000"
REGULAR = "371688745b5701d8d14db11fa6174024"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": HOST,
    "Referer": f"{HOST}/en-US/{SITE}",
    "X-Calypso-Selected-Locale": "en-US",
}

TWO_FACET = {
    "appliedFacets": {"jobFamilyGroup": [TECH_FAMILY], "workerSubType": [REGULAR]},
    "limit": 20, "offset": 0, "searchText": "",
}
ONE_FACET = {
    "appliedFacets": {"jobFamilyGroup": [TECH_FAMILY]},
    "limit": 20, "offset": 0, "searchText": "",
}
BARE = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}


def _post(label: str, body: dict) -> None:
    s = requests.Session()
    s.headers.update(HEADERS)
    s.cookies.clear()  # cookie-free, like the scraper
    r = s.post(LIST_URL, json=body, timeout=30)
    ok = r.status_code == 200 and '"errorCode"' not in r.text[:120]
    total = ""
    if ok:
        try:
            total = f"  total={r.json().get('total')}"
        except Exception:
            pass
    print(f"[{label:9}] HTTP {r.status_code}  ok={ok}{total}")
    print(f"           {r.text[:150].replace(chr(10), ' ')}")


def main() -> None:
    # worst-first: two-facet on the FRESHEST bucket state
    _post("two-facet", TWO_FACET)
    time.sleep(6)
    _post("one-facet", ONE_FACET)
    time.sleep(6)
    _post("bare", BARE)


if __name__ == "__main__":
    main()
