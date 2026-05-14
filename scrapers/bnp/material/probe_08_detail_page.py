"""Probe 8: inspect a single job detail page to plan enrichment.

We want: description (text), posted_date (ISO), and ideally a stable
identifier (req-id) beyond the URL slug. Best case is a schema.org
JobPosting JSON-LD block (Sanofi's pattern). Fallback is to read
specific elements / meta tags.

Target: first job from probe_07, picked because it's the kind of role
the user actually cares about.
"""
from __future__ import annotations

import json
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

HOST = "https://group.bnpparibas"
DETAIL_URL = HOST + "/en/careers/job-offer/tech-lead-java-cloud-et-ebx-h-f"
OUT_DIR = pathlib.Path(__file__).parent


def warmup() -> requests.Session:
    s = requests.Session()
    s.get(HOST + "/", headers=HEADERS, timeout=30)
    return s


def main() -> None:
    s = warmup()
    time.sleep(2.0)

    h = dict(HEADERS)
    h["Referer"] = HOST + "/en/careers/all-job-offers/permanent/digital-transformation-and-data/france"
    h["Sec-Fetch-Site"] = "same-origin"

    r = s.get(DETAIL_URL, headers=h, timeout=30)
    print(f">>> {DETAIL_URL}")
    print(f"    status={r.status_code}  len={len(r.text)}")
    if r.status_code != 200:
        print(r.text[:400])
        return

    (OUT_DIR / "detail_sample.html").write_text(r.text, encoding="utf-8")
    soup = BeautifulSoup(r.text, "html.parser")

    print("\n--- TITLE ---")
    t = soup.find("title")
    print(f"  <title>: {t.get_text(strip=True) if t else None!r}")
    h1 = soup.find("h1")
    if h1:
        print(f"  <h1>: {h1.get_text(' ', strip=True)!r}")

    # 1) Best case: schema.org JSON-LD JobPosting (Sanofi pattern)
    print("\n--- JSON-LD scripts ---")
    found_jobposting = False
    for i, script in enumerate(soup.select('script[type="application/ld+json"]')):
        raw = script.string or ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"  [{i}] unparseable ({e})")
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            t = item.get("@type")
            print(f"  [{i}] @type={t!r}  keys={list(item.keys())[:12]}")
            if t == "JobPosting":
                found_jobposting = True
                print("    >>> JOB POSTING FIELDS <<<")
                for k in ["title", "datePosted", "validThrough", "employmentType",
                          "identifier", "hiringOrganization", "jobLocation",
                          "industry", "occupationalCategory"]:
                    if k in item:
                        v = item[k]
                        s = json.dumps(v, ensure_ascii=False)
                        print(f"      {k!r}: {s[:200]}")
                desc = item.get("description") or ""
                print(f"      description: len={len(desc)}  preview={desc[:200]!r}")

    # 2) Fallback hints: meta tags, time elements, common date attrs
    print("\n--- meta / time / date hints ---")
    for m in soup.find_all("meta"):
        name = m.get("name") or m.get("property") or ""
        if any(k in name.lower() for k in ["date", "posted", "publish", "modified", "valid"]):
            print(f"  <meta {name}={m.get('content')!r}>")
    for tag in soup.find_all("time"):
        print(f"  <time datetime={tag.get('datetime')!r}> text={tag.get_text(' ', strip=True)!r}")

    # 3) Description container candidates
    print("\n--- description-shaped containers ---")
    for sel in [
        ".job-description", "[class*=description]",
        "[class*=job-content]", "[class*=offer-content]",
        ".content", "article",
    ]:
        found = soup.select(sel)
        if found:
            for el in found[:2]:
                txt = el.get_text(" ", strip=True)
                if len(txt) > 200:
                    print(f"  {sel!r} → {el.name}.{el.get('class')} "
                          f"text_len={len(txt)} preview={txt[:150]!r}")
                    break

    # 4) Identifier / req-id hints
    print("\n--- identifier / req-id hints ---")
    for sel in [
        "[class*=reference]", "[class*=req]", "[id*=offer]", "[data-job]",
        "[data-id]",
    ]:
        for el in soup.select(sel)[:3]:
            print(f"  {sel}: <{el.name} class={el.get('class')} id={el.get('id')} "
                  f"data-id={el.get('data-id')}> text={el.get_text(' ', strip=True)[:80]!r}")

    print(f"\n>>> JSON-LD JobPosting found? {found_jobposting}")


if __name__ == "__main__":
    main()
