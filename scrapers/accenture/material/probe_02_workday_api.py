"""Probe 2: hit the Accenture Workday tenant's JSON API directly.

From probe_01 we know:
  Workday tenant URL: https://accenture.wd3.myworkdayjobs.com/AccentureCareers

Workday's career-site backend exposes a public JSON API consumed by
their own frontend:

  GET  /wday/cxs/<host>/<site>                 -> site config (facets etc)
  POST /wday/cxs/<host>/<site>/jobs            -> paginated job search
  GET  /wday/cxs/<host>/<site>/job/<jobId>     -> single job details

`<site>` here is "AccentureCareers". `<host>` is sometimes the tenant
name (`accenture`), sometimes something else — we'll try both.

This probe:
1. Probes the site-config endpoint to discover the facet schema (which
   keys does Workday use for country / employment type / function?).
2. POSTs an empty job search to confirm pagination/count shape.
3. POSTs a search filtered by France (once we identify the France ID
   in the facet schema).
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

import requests as std_requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

TENANT = "accenture"
WD = "wd3"
SITE = "AccentureCareers"
ORIGIN = f"https://{TENANT}.{WD}.myworkdayjobs.com"
CAREERS_URL = f"{ORIGIN}/{SITE}"

OUT_DIR = pathlib.Path(__file__).parent

HEADERS_HTML = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}
HEADERS_JSON = {
    **HEADERS_HTML,
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": ORIGIN,
    "Referer": CAREERS_URL,
}


def try_url(label: str, method: str, url: str, *, payload=None) -> None:
    print(f"\n>>> {label}\n    {method} {url}")
    try:
        if method == "GET":
            r = std_requests.get(url, headers=HEADERS_JSON, timeout=30)
        else:
            r = std_requests.post(url, headers=HEADERS_JSON, json=payload, timeout=30)
    except Exception as exc:
        print(f"    ERROR: {type(exc).__name__}: {exc}")
        return
    ct = r.headers.get("content-type", "")
    print(f"    status={r.status_code}  len={len(r.text)}  ct={ct}")
    if r.status_code == 200 and "json" in ct:
        try:
            data = r.json()
        except Exception:
            data = None
        if isinstance(data, dict):
            print(f"    top-level keys: {list(data.keys())[:20]}")
        elif isinstance(data, list):
            print(f"    list of {len(data)} items, first keys: "
                  f"{list(data[0].keys())[:10] if data and isinstance(data[0], dict) else 'n/a'}")
        # save
        safe = label.lower().replace(" ", "_")
        (OUT_DIR / f"wd_{safe}.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"    saved wd_{safe}.json")
    else:
        print(f"    body[:300]: {r.text[:300]!r}")


def main() -> None:
    # --- Site config (facets schema) — try both host conventions ---------
    for host in [TENANT, SITE.lower(), SITE]:
        try_url(
            f"site_config_host={host}",
            "GET",
            f"{ORIGIN}/wday/cxs/{host}/{SITE}",
        )

    # --- Job search (empty payload) — same host candidates ---------------
    for host in [TENANT, SITE.lower(), SITE]:
        try_url(
            f"jobs_empty_host={host}",
            "POST",
            f"{ORIGIN}/wday/cxs/{host}/{SITE}/jobs",
            payload={"limit": 20, "offset": 0, "searchText": "", "appliedFacets": {}},
        )
        time.sleep(1.0)


if __name__ == "__main__":
    main()
