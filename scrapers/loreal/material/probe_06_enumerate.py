"""Probe 6: walk all France jobs and tally the real categorical fields.

We confirmed:
- 162 jobs match ?3_110_3=18022 (France)
- 20 per page → 9 pages
- Pagination: /en_US/jobs/SearchJobsAJAX?offset=<N>&3_110_3=18022
  where N is the 0-indexed start position (0, 20, 40, ..., 160).
  Per probe_07: `?s=` is silently ignored, `?offset=` is what works.
- Per-row metadata is embedded inline as a dataLayer push:
    'eventLabel': 'Title::Category::Subcat::::Schedule::ContractType::::::Location::JobID'

So instead of trying to discover every facet ID from the JS-driven
filter panel, just scrape every listing row and tally what's there.
This is what we need for the scope decision anyway.

Output: counts by contract type, by function/category, and by location
slice, plus a sample of rows whose dataLayer string didn't parse so we
can spot edge cases.
"""
from __future__ import annotations

import pathlib
import re
import sys
import time
from collections import Counter

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

HOST = "https://careers.loreal.com"
SEARCH = HOST + "/en_US/jobs/SearchJobs"
AJAX = HOST + "/en_US/jobs/SearchJobsAJAX"
TARGET_FIRST = HOST + "/en_US/jobs/SearchJobs?3_110_3=18022"

OUT_DIR = pathlib.Path(__file__).parent

PAGE_SIZE = 20

# Match the inline dataLayer push:
#   'eventLabel': 'Title::Function::Subfunction::::Schedule::ContractType::::::Location::JobID'
# The pattern below is forgiving on whitespace + quoting.
EVENT_LABEL_RE = re.compile(
    r"['\"]eventLabel['\"]\s*:\s*['\"]([^'\"]+)['\"]"
)


def fetch_page(s, offset: int) -> str:
    url = AJAX + f"?offset={offset}&3_110_3=18022"
    referer = TARGET_FIRST if offset == 0 else (AJAX + f"?offset={max(0, offset - PAGE_SIZE)}&3_110_3=18022")
    r = s.get(
        url,
        headers={
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "text/html, */*; q=0.01",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.text


def parse_page(html: str) -> list[dict]:
    """Return one dict per <article.article--result> on the page."""
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict] = []
    for article in soup.select("article.article--result"):
        # Anchor for title + URL
        a = article.select_one("h3 a") or article.select_one("a")
        href = a.get("href") if a else ""
        title = a.get_text(" ", strip=True) if a else ""

        # Numeric ID: id="jobIdNNNNNN" on the actions div
        actions = article.select_one("[id^='jobId']")
        native_id = actions.get("id", "").replace("jobId", "") if actions else ""
        if not native_id and "/JobDetail/" in href:
            native_id = href.rstrip("/").rsplit("/", 1)[-1]

        # Subtitle has two <span>s: location + "Posted DD-MMM-YYYY"
        subtitle = article.select_one(".article__header__text__subtitle")
        location = posted = None
        if subtitle:
            spans = [s.get_text(" ", strip=True) for s in subtitle.find_all("span")]
            spans = [s for s in spans if s]
            for s in spans:
                if s.lower().startswith("posted"):
                    posted = s.replace("Posted", "").strip()
                elif location is None:
                    location = s

        # dataLayer eventLabel inside the inline <script>
        script = article.find("script")
        meta = {}
        if script and script.string:
            m = EVENT_LABEL_RE.search(script.string)
            if m:
                parts = m.group(1).split("::")
                # Title :: Function :: Subfunction :: ?? :: Schedule :: ContractType :: ?? :: ?? :: Location :: JobID
                # We've seen 10 parts in our sample. Be defensive.
                while len(parts) < 10:
                    parts.append("")
                meta = {
                    "dl_title": parts[0].strip(),
                    "function": parts[1].strip(),
                    "sub_function": parts[2].strip(),
                    "_p3": parts[3].strip(),
                    "schedule": parts[4].strip(),
                    "contract_type": parts[5].strip(),
                    "_p6": parts[6].strip(),
                    "_p7": parts[7].strip(),
                    "dl_location": parts[8].strip(),
                    "dl_job_id": parts[9].strip(),
                }

        rows.append({
            "native_job_id": native_id,
            "title": title,
            "apply_url": href,
            "location": location,
            "posted": posted,
            **meta,
        })
    return rows


def main() -> None:
    s = cffi_requests.Session(impersonate="chrome131")
    # warm the session
    s.get(SEARCH, timeout=30)
    time.sleep(2.0)

    all_rows: list[dict] = []
    seen_ids: set[str] = set()
    for page_idx in range(9):  # 162 / 20 → 9 pages
        offset = page_idx * PAGE_SIZE
        html = fetch_page(s, offset)
        rows = parse_page(html)
        # dedupe defensively in case the endpoint clamps offset > total
        new = [r for r in rows if r.get("native_job_id") not in seen_ids]
        for r in new:
            seen_ids.add(r.get("native_job_id"))
        print(f"page offset={offset:3d}: {len(rows)} rows  ({len(new)} new, {len(seen_ids)} cumulative)")
        all_rows.extend(new)
        if not new:
            break
        time.sleep(2.0)

    print(f"\nTOTAL ROWS COLLECTED: {len(all_rows)}\n")

    # Tally
    contracts = Counter(r.get("contract_type") or "(blank)" for r in all_rows)
    functions = Counter(r.get("function") or "(blank)" for r in all_rows)
    schedules = Counter(r.get("schedule") or "(blank)" for r in all_rows)
    sub_funcs = Counter(r.get("sub_function") or "(blank)" for r in all_rows)
    locations = Counter(r.get("location") or "(blank)" for r in all_rows)

    def show(label, counter):
        print(f"=== {label} ({len(counter)} distinct) ===")
        for k, n in counter.most_common():
            print(f"  {n:4d}  {k!r}")
        print()

    show("Contract type (per-row from dataLayer)", contracts)
    show("Function (per-row from dataLayer)", functions)
    show("Sub-function", sub_funcs)
    show("Schedule", schedules)
    show("Location (city)", locations)

    missing = [r for r in all_rows if not r.get("contract_type")]
    print(f"rows where dataLayer didn't parse a contract_type: {len(missing)}")
    for r in missing[:5]:
        print(f"  {r.get('native_job_id')} {r.get('title')!r}")


if __name__ == "__main__":
    main()
