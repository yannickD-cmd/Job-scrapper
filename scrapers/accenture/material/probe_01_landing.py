"""Probe 1: hit the Accenture careers search page and figure out the stack.

Target URL: https://www.accenture.com/fr-fr/careers/jobsearch

Three passes, each more capable than the last — stop at the cheapest
that works:

A) bare requests.GET with browser-shaped headers
B) requests with a session warmup (homepage -> target with Referer)
C) curl_cffi (chrome131 TLS impersonation) — if (A)/(B) hit 403

For each pass that returns 200 we snapshot:
- title, first H1
- ATS / anti-bot fingerprints in the HTML (Workday, Cloudflare, etc.)
- row-shaped selectors, JobDetail-shaped links, inline JSON blobs
- mentions of 'France' / 'Full-Time' / 'Compétence'
"""
from __future__ import annotations

import pathlib
import re
import sys
import time

import requests as std_requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

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
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "From": "yannickarieldossa@gmail.com",
}

HOST = "https://www.accenture.com"
HOME = HOST + "/fr-fr/careers"
TARGET = HOST + "/fr-fr/careers/jobsearch"
OUT_DIR = pathlib.Path(__file__).parent


def snapshot(html: str) -> None:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.find("title")
    print(f"  title: {title.get_text(strip=True) if title else '(none)'!r}")
    h1 = soup.find("h1")
    if h1:
        print(f"  h1   : {h1.get_text(' ', strip=True)!r}")

    lower = html.lower()
    fingerprints = [
        "workday", "myworkdayjobs", "successfactors", "taleo", "icims",
        "phenom", "phenompeople", "smartrecruiters", "greenhouse",
        "avature", "eightfold", "challenge", "captcha", "cloudflare",
        "datadome", "incapsula", "akamai", "_abck", "perimeterx",
        "kasada", "distilnetworks",
    ]
    hits = [k for k in fingerprints if k in lower]
    print(f"  fingerprints: {hits or 'none'}")

    # row-candidates
    print("  row-candidate selectors:")
    for sel in [
        "[data-job-id]", "[data-jobid]", "[data-req-id]",
        "li.job", "li.job-card", "li.jobItem",
        "article.job", "article.job-card",
        "[class*=job-card]", "[class*=jobItem]", "[class*=joblist]",
        "[class*=jobs-result]", "[class*=cmp-jobcard]",
        "tr.data-row", "tr.job",
        "[class*=search-result]",
    ]:
        found = soup.select(sel)
        if found:
            print(f"    {sel!r:40s} → {len(found)} matches")

    # JobDetail / JobDescription-style links
    print("  job-detail-shaped URL patterns found:")
    for pat in [
        r"/jobsearch[/?][^\"\'\s]*",
        r"/jobdetails?/[^\"\'\s]*",
        r"/job/[^\"\'\s]*",
        r"/careers?/job/[^\"\'\s]*",
        r"j_id=[\w-]+",
        r"reqId=[\w-]+",
    ]:
        matches = re.findall(pat, html, flags=re.I)
        if matches:
            print(f"    {pat!r:35s} → {len(matches)} (unique {len(set(matches))})")

    # France / Full-Time / Compétence mentions
    print(f"  'France' mentions      : {len(re.findall(r'\\bFrance\\b', html, re.I))}")
    print(f"  'Full-Time' mentions   : {len(re.findall(r'Full[-\\s]?Time', html, re.I))}")
    print(f"  'Temps plein' mentions : {len(re.findall(r'Temps\\s+plein', html, re.I))}")
    print(f"  'Compétence' mentions  : {len(re.findall(r'Comp[ée]tences?', html, re.I))}")
    print(f"  'Skill' mentions       : {len(re.findall(r'\\bSkill[s]?\\b', html, re.I))}")

    # inline JSON blobs (Adobe AEM / Next.js / React state)
    for tag in soup.find_all("script", id=True):
        sid = tag.get("id", "")
        body = tag.string or ""
        if any(k in sid.lower() for k in ["next", "data", "state", "apollo", "nuxt", "init"]):
            print(f"  inline <script id={sid!r}> len={len(body)}")

    # try parse first article for context
    for art in soup.select("article")[:3]:
        cls = art.get("class")
        if cls and any("job" in c.lower() or "card" in c.lower() for c in cls):
            print(f"  sample <article class={cls}>: {art.get_text(' ', strip=True)[:150]!r}")


def run(label: str, fetch_fn) -> int:
    print(f"\n=== {label} ===")
    try:
        status, body = fetch_fn()
    except Exception as exc:
        print(f"  ERROR: {type(exc).__name__}: {exc}")
        return -1
    print(f"  status={status}  len={len(body)}")
    safe = label.lower().replace(" ", "_").replace("/", "_").replace(":", "")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", safe)[:80]
    if status == 200 and len(body) > 10000:
        (OUT_DIR / f"{safe}.html").write_text(body, encoding="utf-8")
        snapshot(body)
    else:
        print(f"  body[:200]={body[:200]!r}")
    return status


def main() -> None:
    # --- PASS A: bare requests.GET, no warmup -----------------------------
    def pass_a():
        r = std_requests.get(TARGET, headers=HEADERS, timeout=30, allow_redirects=True)
        return r.status_code, r.text
    run("pass_a_requests_bare", pass_a)

    # --- PASS B: requests session, hit /careers first, then target --------
    def pass_b():
        s = std_requests.Session()
        s.get(HOME, headers=HEADERS, timeout=30, allow_redirects=True)
        time.sleep(2.0)
        h2 = dict(HEADERS); h2["Referer"] = HOME; h2["Sec-Fetch-Site"] = "same-origin"
        r = s.get(TARGET, headers=h2, timeout=30, allow_redirects=True)
        return r.status_code, r.text
    run("pass_b_requests_warmup", pass_b)

    # --- PASS C: curl_cffi (chrome131 TLS) --------------------------------
    def pass_c():
        s = cffi_requests.Session(impersonate="chrome131")
        s.get(HOME, headers={"Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"}, timeout=30)
        time.sleep(2.0)
        r = s.get(
            TARGET,
            headers={"Referer": HOME, "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"},
            timeout=30,
        )
        return r.status_code, r.text
    run("pass_c_curlcffi", pass_c)


if __name__ == "__main__":
    main()
