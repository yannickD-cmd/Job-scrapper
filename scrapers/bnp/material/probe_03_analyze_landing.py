"""Probe 3: analyze the saved landing.html — is this listings or marketing?

We have 145KB of HTML from /en/careers. Need to find out:
- Are job listings rendered server-side on this page?
- If not, where does the "search jobs" CTA link to?
- What ATS/system is BNP using behind the scenes?
- Are there inline JSON blobs / API endpoints hinted in the markup?
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

from bs4 import BeautifulSoup

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

HTML_PATH = pathlib.Path(__file__).parent / "landing.html"


def main() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    title = soup.find("title")
    print("TITLE:", title.get_text(strip=True) if title else "(none)")

    h1s = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
    h2s = [h.get_text(" ", strip=True) for h in soup.find_all("h2")]
    print(f"\nH1 ({len(h1s)}):")
    for t in h1s[:10]:
        print(f"  {t!r}")
    print(f"\nH2 ({len(h2s)}):")
    for t in h2s[:20]:
        print(f"  {t!r}")

    # Look for ATS fingerprints in the body
    lower = html.lower()
    print("\n--- ATS / external careers-system hits ---")
    for kw in [
        "smartrecruiters", "myworkdayjobs", "workday", "taleo", "icims",
        "successfactors", "greenhouse", "lever.co", "avature", "phenompeople",
        "phenom", "eightfold", "jobvite", "ashbyhq", "applytojob",
        "jobs.bnpparibas", "careers.bnpparibas", "rejoignez", "bnppjobs",
    ]:
        if kw in lower:
            # find first occurrence with surrounding context
            idx = lower.find(kw)
            ctx = html[max(0, idx - 60): idx + 120].replace("\n", " ")
            print(f"  {kw!r} → ...{ctx}...")

    # All outbound links from this page
    hrefs = [a.get("href") for a in soup.find_all("a", href=True)]
    print(f"\n--- ALL UNIQUE HREFS ({len(set(hrefs))}) ---")
    interesting = sorted({
        h for h in hrefs
        if any(k in h.lower() for k in [
            "job", "career", "appl", "vacanc", "offre", "opening",
            "search", "smartrecruiters", "workday", "taleo",
        ])
    })
    print(f"interesting subset ({len(interesting)}):")
    for h in interesting[:80]:
        print(f"  {h}")

    # JSON-LD blocks (Sanofi uses these — does BNP?)
    print("\n--- JSON-LD scripts ---")
    for i, script in enumerate(soup.select('script[type="application/ld+json"]')):
        raw = script.string or ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print(f"  [{i}] (unparseable, {len(raw)} chars)")
            continue
        kind = data.get("@type") if isinstance(data, dict) else type(data).__name__
        print(f"  [{i}] @type={kind!r}  len={len(raw)}")

    # Embedded data blobs (Next.js __NEXT_DATA__, etc.)
    print("\n--- inline data blobs ---")
    for tag in soup.find_all("script", id=True):
        sid = tag.get("id")
        if any(k in sid.lower() for k in ["next", "data", "state", "apollo", "nuxt"]):
            print(f"  <script id={sid!r}> len={len(tag.string or '')}")

    # Forms (search forms?)
    print("\n--- forms ---")
    for f in soup.find_all("form"):
        action = f.get("action")
        method = f.get("method")
        inputs = [i.get("name") for i in f.find_all(["input", "select"]) if i.get("name")]
        print(f"  action={action!r} method={method!r} fields={inputs}")

    # Likely "see all jobs" / "search jobs" CTAs by anchor text
    print("\n--- CTA-shaped anchors (text mentions jobs/careers/apply) ---")
    seen = set()
    for a in soup.find_all("a", href=True):
        txt = a.get_text(" ", strip=True).lower()
        if any(k in txt for k in ["job", "vacanc", "offer", "apply", "join", "search", "opportunit"]):
            href = a["href"]
            key = (txt[:60], href)
            if key in seen:
                continue
            seen.add(key)
            print(f"  [{txt[:60]!r}] → {href}")
            if len(seen) > 40:
                break


if __name__ == "__main__":
    main()
