"""Probe 4: use curl_cffi for the Workday tenant (CF-protected) and
find the actual API host slug from the HTML config.

probe_03 got 500 on warmup with plain requests + 406 on the API
(Cloudflare cookies were present, suggesting CF challenge).

Plan:
1. With curl_cffi (chrome131): fetch the careers landing page at the
   bare path /AccentureCareers — same trick that worked for L'Oréal.
2. Save the HTML and inspect for:
     - any inline JSON/JS config that names the API host slug
     - any /wday/cxs/... URLs that the page references directly
3. Retry the JSON jobs POST using the host slug we find.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import time

from curl_cffi import requests as cffi_requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

TENANT = "accenture"
WD = "wd3"
SITE = "AccentureCareers"
ORIGIN = f"https://{TENANT}.{WD}.myworkdayjobs.com"
OUT_DIR = pathlib.Path(__file__).parent


def fetch_landing(s, label: str, url: str) -> str | None:
    r = s.get(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=30,
    )
    print(f"\n>>> {label}\n    GET {url}")
    print(f"    status={r.status_code}  len={len(r.text)}  ct={r.headers.get('content-type')}")
    print(f"    cookies: {[c.name for c in r.cookies.jar]}")
    if r.status_code == 200 and len(r.text) > 1000:
        safe = label.lower().replace(" ", "_").replace("/", "_")[:80]
        out = OUT_DIR / f"landing_{safe}.html"
        out.write_text(r.text, encoding="utf-8")
        print(f"    saved {out.name}")
        return r.text
    else:
        print(f"    body[:300]: {r.text[:300]!r}")
        return None


def hunt_api_clues(html: str) -> None:
    print("\n--- hunting API clues in HTML ---")
    # Workday's React bundle config usually has `_csrf`, `host`, `siteId`, etc.
    # Their search-results page also references `/wday/cxs/<host>/<site>/jobs`
    # directly in inline JS for SSR hydration.
    patterns = [
        (r"/wday/cxs/[^\"'\s<>]+", "/wday/cxs/ URLs"),
        (r"\"host\"\s*:\s*\"[^\"]+\"", "JSON 'host' fields"),
        (r"\"site\"\s*:\s*\"[^\"]+\"", "JSON 'site' fields"),
        (r"\"siteId\"\s*:\s*\"[^\"]+\"", "JSON 'siteId' fields"),
        (r"\"jobs\"\s*:\s*\"/[^\"]+\"", "JSON 'jobs' URL fields"),
        (r"jobs/[a-zA-Z0-9_-]+", "jobs/<X> references"),
        (r"CALYPSO_CSRF_TOKEN", "CSRF token mentions"),
    ]
    for pat, label in patterns:
        matches = re.findall(pat, html, flags=re.I)
        if matches:
            uniq = list(dict.fromkeys(matches))[:10]
            print(f"  {label}: {len(matches)} (unique {len(set(matches))})")
            for m in uniq[:8]:
                print(f"    {m}")


def try_api(s, host_slug: str, payload: dict) -> None:
    url = f"{ORIGIN}/wday/cxs/{host_slug}/{SITE}/jobs"
    h = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": ORIGIN,
        "Referer": f"{ORIGIN}/{SITE}",
        "X-Requested-With": "XMLHttpRequest",
    }
    csrf = s.cookies.get("CALYPSO_CSRF_TOKEN")
    if csrf:
        h["X-CALYPSO-CSRF-TOKEN"] = csrf
    r = s.post(url, headers=h, json=payload, timeout=30)
    print(f"\n>>> POST jobs (host={host_slug!r})")
    print(f"    url: {url}")
    print(f"    status={r.status_code}  len={len(r.text)}  ct={r.headers.get('content-type')}")
    if r.status_code == 200:
        data = r.json()
        if isinstance(data, dict):
            print(f"    top keys: {list(data.keys())[:15]}")
            for k in ("total", "jobPostings", "facets"):
                if k in data:
                    v = data[k]
                    if isinstance(v, list):
                        print(f"    {k!r}: list len={len(v)}")
                    else:
                        print(f"    {k!r}: {str(v)[:120]}")
            (OUT_DIR / f"wd_jobs_host_{host_slug}.json").write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
    else:
        print(f"    body[:300]: {r.text[:300]!r}")


def main() -> None:
    s = cffi_requests.Session(impersonate="chrome131")

    # Try several URL shapes for the Workday careers landing page
    candidates = [
        ("root", f"{ORIGIN}/{SITE}"),
        ("user_home", f"{ORIGIN}/{SITE}/userHome"),
        ("us_locale", f"{ORIGIN}/en-US/{SITE}"),
        ("fr_locale", f"{ORIGIN}/fr-FR/{SITE}"),
    ]
    last_html = None
    for label, url in candidates:
        html = fetch_landing(s, label, url)
        if html:
            last_html = html
        time.sleep(2.0)

    if last_html:
        hunt_api_clues(last_html)

    # Try a few API host slugs with a minimal payload
    payload = {"limit": 20, "offset": 0, "searchText": "", "appliedFacets": {}}
    for host_slug in ["accenture", "AccentureCareers", "wd3", "us"]:
        try_api(s, host_slug, payload)
        time.sleep(1.0)


if __name__ == "__main__":
    main()
