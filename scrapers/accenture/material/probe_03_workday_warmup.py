"""Probe 3: warm a Workday session, then hit the JSON API properly.

probe_02 got 406 on GET site config and 422 on POST jobs. Workday tenants
require:
- a session warmed via the careers HTML page (sets cookies including
  CSRF / session-id needed by the API)
- exact `Accept: application/json` (no `*/*` fallback)
- some tenants also require an `X-CALYPSO-CSRF-TOKEN` header echoing
  the value of the `CALYPSO_CSRF_TOKEN` cookie set on warmup.

We try the well-known payload shapes documented in the Workday public
career-site API and see which one returns jobs.
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
CAREERS_HTML_URL = f"{ORIGIN}/en-US/{SITE}"

OUT_DIR = pathlib.Path(__file__).parent

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def warm(s: std_requests.Session) -> str:
    """Hit the careers HTML page to seed cookies. Returns the page HTML
    so we can also extract a CSRF token if present in the markup."""
    r = s.get(
        CAREERS_HTML_URL,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=30,
    )
    print(f"warmup GET {CAREERS_HTML_URL}")
    print(f"  status={r.status_code}  len={len(r.text)}")
    print(f"  cookies seeded: {[c.name for c in s.cookies]}")
    return r.text


def json_headers(s: std_requests.Session) -> dict:
    """Build the JSON-API headers, echoing CSRF if Workday set one."""
    h = {
        "User-Agent": UA,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": ORIGIN,
        "Referer": CAREERS_HTML_URL,
        "X-Requested-With": "XMLHttpRequest",
    }
    # Common Workday CSRF cookie names
    for name in ("CALYPSO_CSRF_TOKEN", "PLAY_SESSION", "WDAY_CSRF_TOKEN"):
        val = s.cookies.get(name)
        if val:
            h["X-CALYPSO-CSRF-TOKEN"] = val
            break
    return h


def try_endpoint(label: str, s: std_requests.Session, method: str, url: str, *, payload=None) -> None:
    print(f"\n>>> {label}")
    print(f"    {method} {url}")
    h = json_headers(s)
    if method == "GET":
        r = s.get(url, headers=h, timeout=30)
    else:
        r = s.post(url, headers=h, json=payload, timeout=30)
    ct = r.headers.get("content-type", "")
    print(f"    status={r.status_code}  len={len(r.text)}  ct={ct}")
    if "json" in ct and r.status_code == 200:
        data = r.json()
        if isinstance(data, dict):
            print(f"    top keys: {list(data.keys())[:20]}")
            # Common job-list fields
            for k in ("total", "jobPostings", "jobs", "items", "facets", "searchText"):
                if k in data:
                    v = data[k]
                    if isinstance(v, list):
                        print(f"      {k!r}: list len={len(v)}")
                    else:
                        print(f"      {k!r}: {str(v)[:120]}")
        safe = label.lower().replace(" ", "_").replace("/", "_")[:80]
        (OUT_DIR / f"wd_{safe}.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"    saved wd_{safe}.json")
    else:
        print(f"    body[:400]: {r.text[:400]!r}")


def main() -> None:
    s = std_requests.Session()
    warm(s)
    time.sleep(2.0)

    # Try the site-config endpoint (returns facet schema for the site)
    try_endpoint(
        "site_config",
        s,
        "GET",
        f"{ORIGIN}/wday/cxs/{TENANT}/{SITE}",
    )

    # Variant: trailing slash / variant site names
    try_endpoint(
        "site_config_trailing",
        s,
        "GET",
        f"{ORIGIN}/wday/cxs/{TENANT}/{SITE}/",
    )

    # Empty-search POST (minimal valid payload per Workday public API)
    try_endpoint(
        "jobs_minimal",
        s,
        "POST",
        f"{ORIGIN}/wday/cxs/{TENANT}/{SITE}/jobs",
        payload={"limit": 20, "offset": 0, "searchText": ""},
    )

    # With explicit appliedFacets {}
    try_endpoint(
        "jobs_with_facets",
        s,
        "POST",
        f"{ORIGIN}/wday/cxs/{TENANT}/{SITE}/jobs",
        payload={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""},
    )


if __name__ == "__main__":
    main()
