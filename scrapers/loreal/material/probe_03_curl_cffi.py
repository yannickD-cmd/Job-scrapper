"""Probe 3: retry the L'Oréal URLs with curl_cffi (Chrome TLS impersonation).

probe_01 + probe_02 confirmed Cloudflare Bot Management blocks every
endpoint when we use the stdlib `requests` library, because the TLS
handshake fingerprint (JA3/JA4) flags as non-browser before we even
send our HTTP request.

curl_cffi wraps curl-impersonate, which performs a real Chrome TLS
handshake. Same API as `requests` so the test is a near-drop-in.

If this returns 200 with real HTML, L'Oréal is scrapable with a
trivial code swap. If it still 403s, we escalate to Playwright.
"""
from __future__ import annotations

import pathlib
import sys
import time

from curl_cffi import requests as cffi_requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

HOST = "https://careers.loreal.com"
TARGET = HOST + "/en_US/jobs/SearchJobs?3_110_3=18022"
SEARCH_BASE = HOST + "/en_US/jobs/SearchJobs"
OUT_DIR = pathlib.Path(__file__).parent


def fetch(s, url: str, *, referer: str | None) -> object:
    headers = {}
    if referer:
        headers["Referer"] = referer
    return s.get(url, headers=headers or None, timeout=30, allow_redirects=True)


def summarise(label: str, r) -> None:
    print(f"\n>>> {label}")
    print(f"    status={r.status_code}  len={len(r.text)}  ct={r.headers.get('content-type')}")
    cookies = []
    try:
        cookies = [c.name for c in r.cookies.jar]
    except Exception:
        try:
            cookies = list(r.cookies.keys())
        except Exception:
            pass
    print(f"    cookies set on this response: {cookies}")
    # Cloudflare challenge pages are usually tiny and HTML; real listings
    # are large. Use length as a first signal.
    if r.status_code == 200 and len(r.text) > 10000:
        # Save with a Windows-safe filename: strip /, :, ?, etc. — NTFS
        # treats `:` as an ADS stream separator, so a colon in the name
        # silently writes the content into a hidden stream.
        import re as _re
        safe = _re.sub(r"[^A-Za-z0-9_.-]+", "_", label.lower()).strip("_")[:80]
        out = OUT_DIR / f"cffi_{safe}.html"
        out.write_text(r.text, encoding="utf-8")
        head = r.text[:500].replace("\n", " ")
        print(f"    LOOKS-LIKE-REAL — saved {out.name} — head={head[:200]!r}")
    else:
        print(f"    body preview: {r.text[:200]!r}")


def main() -> None:
    # Each impersonation profile mimics a specific browser version's
    # TLS ClientHello + HTTP/2 settings. We start with current Chrome
    # since that's what most users present; if that fails we can try
    # safari/firefox profiles or older chrome versions.
    profile = "chrome131"
    print(f"=== curl_cffi impersonate={profile!r} ===")

    s = cffi_requests.Session(impersonate=profile)

    # Pass 1: hit the search base "warm-up" style first
    r1 = fetch(s, SEARCH_BASE, referer=None)
    summarise(f"warmup GET {SEARCH_BASE}", r1)

    time.sleep(2.0)

    # Pass 2: hit the user's exact pre-filtered URL with a Referer
    r2 = fetch(s, TARGET, referer=SEARCH_BASE)
    summarise(f"GET {TARGET}", r2)


if __name__ == "__main__":
    main()
