"""Probe 1: hit the user's URL and see what defenses (if any) we face.

Target: https://careers.loreal.com/en_US/jobs/SearchJobs?3_110_3=18022

That `?3_110_3=18022` is Avature's encoded facet syntax — exact meaning
unknown until we read the page. The user suspects anti-bot.

We try two passes:
- Pass A: bare GET with browser headers, no warmup. Most ATSes accept this.
- Pass B: warm-up GET to the bare /en_US/jobs/SearchJobs first (no params),
  then the targeted URL with a Referer — same pattern that cleared
  BNP's Akamai gate.

For each: report status + length + a structural snapshot (title, number
of job-row-shaped elements, presence of inline JSON, ATS fingerprints).
"""
from __future__ import annotations

import pathlib
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "From": "yannickarieldossa@gmail.com",
}

HOST = "https://careers.loreal.com"
TARGET = HOST + "/en_US/jobs/SearchJobs?3_110_3=18022"
SEARCH_BASE = HOST + "/en_US/jobs/SearchJobs"
OUT_DIR = pathlib.Path(__file__).parent


def snapshot(html: str, label: str) -> None:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.find("title")
    print(f"  title: {title.get_text(strip=True) if title else '(none)'!r}")
    h1 = soup.find("h1")
    if h1:
        print(f"  h1   : {h1.get_text(' ', strip=True)!r}")

    lower = html.lower()
    fingerprints = [
        "avature", "challenge", "captcha", "perimeterx", "px-captcha",
        "cloudflare", "datadome", "incapsula", "akamai", "_abck",
        "distil", "kasada",
    ]
    hits = [k for k in fingerprints if k in lower]
    print(f"  fingerprints: {hits or 'none'}")

    # job-row candidates
    for sel in [
        "[data-job-id]", "[data-jobid]", "[data-req-id]",
        "li.job", "div.job", "tr.job", "article.job",
        "[class*=job-card]", "[class*=jobItem]", "[class*=joboffer]",
        "[class*=searchResult]", "tr.data-row",
    ]:
        found = soup.select(sel)
        if found:
            print(f"  row-candidate {sel!r}: {len(found)} matches")

    # inline JSON blobs?
    for tag in soup.find_all("script", id=True):
        sid = tag.get("id", "")
        if any(k in sid.lower() for k in ["next", "data", "state", "apollo", "nuxt", "init"]):
            body = (tag.string or "")
            print(f"  inline script id={sid!r} len={len(body)}")

    # mention of "France" anywhere
    france = len(re.findall(r"\bFrance\b", html, re.I))
    print(f"  'France' mentions: {france}")

    # how many anchors point to job-detail-shaped URLs
    job_links = len(re.findall(r"/en_US/jobs/JobDetail[/?]", html))
    print(f"  /en_US/jobs/JobDetail links: {job_links}")

    # any total-count text on the page?
    for m in re.finditer(r"(\d[\d\s,]*)\s+(jobs?|positions?|results?|offres?|opportunit)",
                         html, re.I):
        print(f"  count hint: {m.group(0)!r}")
        break


def fetch(s: requests.Session, url: str, *, referer: str | None) -> requests.Response:
    h = dict(HEADERS)
    if referer:
        h["Referer"] = referer
        h["Sec-Fetch-Site"] = "same-origin"
    r = s.get(url, headers=h, timeout=30, allow_redirects=True)
    return r


def main() -> None:
    # --- Pass A: bare GET, no warmup ---
    print("=== PASS A: bare GET, no session warmup ===")
    s_a = requests.Session()
    r_a = fetch(s_a, TARGET, referer=None)
    print(f"GET {TARGET}")
    print(f"  status={r_a.status_code}  final={r_a.url}  len={len(r_a.text)}")
    print(f"  cookies after: {[c.name for c in s_a.cookies]}")
    (OUT_DIR / "pass_a_target.html").write_text(r_a.text, encoding="utf-8")
    if r_a.status_code == 200:
        snapshot(r_a.text, "pass_a")

    # --- Pass B: warm via the search base, then target with Referer ---
    print("\n=== PASS B: warmup via search base, then target ===")
    s_b = requests.Session()

    r_b1 = fetch(s_b, SEARCH_BASE, referer=None)
    print(f"GET {SEARCH_BASE}")
    print(f"  status={r_b1.status_code}  final={r_b1.url}  len={len(r_b1.text)}")
    print(f"  cookies after: {[c.name for c in s_b.cookies]}")
    (OUT_DIR / "pass_b_search_base.html").write_text(r_b1.text, encoding="utf-8")

    time.sleep(2.0)

    r_b2 = fetch(s_b, TARGET, referer=SEARCH_BASE)
    print(f"\nGET {TARGET}")
    print(f"  status={r_b2.status_code}  final={r_b2.url}  len={len(r_b2.text)}")
    print(f"  cookies after: {[c.name for c in s_b.cookies]}")
    (OUT_DIR / "pass_b_target.html").write_text(r_b2.text, encoding="utf-8")
    if r_b2.status_code == 200:
        snapshot(r_b2.text, "pass_b")


if __name__ == "__main__":
    main()
