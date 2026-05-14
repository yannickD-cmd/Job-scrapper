"""Probe 4: analyze the saved Cloudflare-cleared HTML.

Goals:
- Confirm the page contains real job rows.
- Identify the row selector & what `3_110_3=18022` filters by.
- Find the country (France) filter, total count, pagination, and any
  inline JSON state Avature exposes.
"""
from __future__ import annotations

import pathlib
import re
import sys

from bs4 import BeautifulSoup

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

HERE = pathlib.Path(__file__).parent
TARGET_HTML = HERE / "cffi_get_https_careers.loreal.com_en_us_jobs_searchjobs_3_110_3_18022.html"
WARMUP_HTML = HERE / "cffi_warmup_get_https_careers.loreal.com_en_us_jobs_searchjobs.html"


def analyze(path: pathlib.Path, label: str) -> None:
    html = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    print(f"\n{'=' * 70}\n=== {label} ({len(html):,} chars) ===\n{'=' * 70}")

    title = soup.find("title")
    print(f"title: {title.get_text(strip=True) if title else '(none)'!r}")

    # Avature usually renders job rows as tables OR <li> items inside a
    # results container. Try common selectors.
    print("\n--- row-candidate selectors ---")
    for sel in [
        "tr.data-row", "tr[data-job-id]", "li.job-item", "li.jobListItem",
        "[data-job-id]", "[data-jobid]", "[data-req-id]",
        "[class*=job-item]", "[class*=jobItem]", "[class*=job-card]",
        "[class*=jobCard]", "[class*=search-result]", "[class*=result-item]",
        "article.job", "article.result",
    ]:
        found = soup.select(sel)
        if found:
            print(f"  {sel!r:45s} → {len(found)} matches")
            for el in found[:2]:
                href = el.get("href") or (el.find("a", href=True) or {}).get("href", "")
                txt = el.get_text(" ", strip=True)[:100]
                print(f"      sample: <{el.name}> href={href!r:60s} text={txt!r}")

    # JobDetail-shaped links anywhere in the doc
    jd = re.findall(r"/en_US/jobs/JobDetail/([^\s\"'<>?#]+)", html)
    print(f"\nJobDetail-shaped links: {len(jd)} (unique slugs: {len(set(jd))})")
    for slug in list(set(jd))[:6]:
        print(f"  {slug}")

    # Form / select / facet markup
    print("\n--- facet markup ---")
    selects = soup.find_all("select")
    print(f"  <select> count: {len(selects)}")
    for s in selects[:12]:
        name = s.get("name") or s.get("id") or "(unnamed)"
        opt_count = len(s.find_all("option"))
        sample = [o.get_text(strip=True) for o in s.find_all("option")[:5]]
        print(f"    select[{name!r}] options={opt_count} sample={sample}")

    # Inputs with 3_NNN_N-style names (Avature facet encoding) and France hits
    facet_inputs = soup.find_all("input", attrs={"name": re.compile(r"^\d+_\d+_\d+$")})
    print(f"  facet-shaped <input> (name=3_110_3 etc): {len(facet_inputs)}")
    for i in facet_inputs[:10]:
        # The label is usually the next sibling or referenced by id
        label = ""
        if i.get("id"):
            lab = soup.find("label", attrs={"for": i["id"]})
            if lab:
                label = lab.get_text(" ", strip=True)
        print(f"    name={i.get('name')!r:14s} value={i.get('value')!r:8s} "
              f"checked={i.get('checked') is not None} label={label[:60]!r}")

    # Show all anchors that link to filtered SearchJobs URLs (facets the
    # site exposes as clickable links). These often reveal what every
    # facet ID means.
    print("\n--- facet-link anchors (SearchJobs?key=value) ---")
    facet_links = re.findall(r"/en_US/jobs/SearchJobs\?(\d+_\d+_\d+)=(\d+)", html)
    by_facet: dict[str, set[str]] = {}
    for k, v in facet_links:
        by_facet.setdefault(k, set()).add(v)
    for k, vs in sorted(by_facet.items()):
        sample = sorted(vs, key=int)[:8]
        print(f"  {k}: {len(vs)} distinct values, sample={sample}")

    # France mentions
    france = re.findall(r"\bFrance\b", html, re.I)
    print(f"\n'France' mentions: {len(france)}")
    # Look for context — anchor texts or option labels referencing France
    for m in re.finditer(r">([^<]{0,80}France[^<]{0,40})<", html, re.I):
        print(f"  ctx: {m.group(1).strip()!r}")
        break

    # Count hints
    print("\n--- count-shaped phrases ---")
    for m in re.finditer(
        r"([\d,]+)\s*(jobs?|positions?|results?|offres?|opportunities|matches?)",
        html, re.I,
    ):
        print(f"  {m.group(0)!r}")
        if m.start() > 200000:  # don't keep printing
            break

    # Inline JSON state
    print("\n--- inline JSON-ish blobs ---")
    for tag in soup.find_all("script"):
        sid = tag.get("id") or ""
        body = tag.string or ""
        if not body or len(body) < 200:
            continue
        # Avature sometimes embeds a window.__PRELOADED_STATE__ or similar
        if any(k in (sid + body[:200]).lower() for k in
               ["preload", "initial", "state", "window.", "search_results", "jobs"]):
            print(f"  <script id={sid!r}> len={len(body)} head={body[:120]!r}")


def main() -> None:
    if not TARGET_HTML.exists():
        print(f"missing: {TARGET_HTML}")
        return
    analyze(WARMUP_HTML, "WARMUP (no filter)")
    analyze(TARGET_HTML, "TARGET (?3_110_3=18022)")


if __name__ == "__main__":
    main()
