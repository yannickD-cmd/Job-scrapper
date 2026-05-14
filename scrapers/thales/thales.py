"""Thales careers scraper — France, Permanent, three engineering categories.

Site runs on Phenom People (tenant `TGPTGWGLOBAL`). The widgets XHR API
(`/widgets`, `/api/jobs`) is closed to us — returns
`{"status":"failure"}` / "Tenant not identified" even with CSRF + cookies.
The search-results URL does server-side embed `phApp.ddo` with the first
10 jobs, but URL query params (`Country=France`, `Category=Software`,
`from=10`, …) are ignored at SSR — filtering only happens after JS hydration.

So this scraper goes through the sitemap instead:

1. INVENTORY. Pull all 8 `sitemap{1..8}.xml` files (~3569 `/job/<reqId>/...`
   URLs total). Sitemap entries have no facet metadata — country / city /
   category are not exposed here.

2. ENRICH + FILTER. Fetch each job detail page in parallel. Each page
   embeds `phApp.ddo.jobDetail.data.job` as a JSON object. We brace-balance
   the `phApp.ddo = {…}` assignment out of the script tag (it's serialised
   as plain JSON, no JS-only syntax) and read the fields we care about
   straight off the dict. Filter client-side:
     - country == "France"
     - workerSubType == "Regular Employee"  (the Permanent vs Apprenticeship
       discriminator — JSON-LD only exposes `employmentType: "Full time"`,
       which is the *worker* type, not the *hiring* type)
     - any multi_category.category in SCOPE_CATEGORIES
     - city in SCOPE_CITIES

Parallelism: 6 workers with a small per-request jitter. The detail pages
are static HTML behind Cloudflare — no Akamai-style bot wall like BNP.

To change scope: edit SCOPE_CATEGORIES / SCOPE_CITIES below.
"""
from __future__ import annotations

import json
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

HOST = "https://careers.thalesgroup.com"
SITEMAP_TEMPLATE = f"{HOST}/global/en/sitemap{{n}}.xml"
NUM_SITEMAPS = 8  # observed 2026-05 — sitemap.xml lists 8 child sitemaps

JOB_URL_RE = re.compile(rf"{re.escape(HOST)}/global/en/job/[^<\s]+")

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
    "Sec-Fetch-Site": "same-origin",
    "From": "yannickarieldossa@gmail.com",
}

REQUEST_TIMEOUT = 30
MAX_WORKERS = 6
JITTER_MIN = 0.15
JITTER_MAX = 0.45

SCOPE_COUNTRY = "France"
SCOPE_EMPLOYMENT_TYPE = "Permanent"  # surfaced as workerSubType="Regular Employee"
SCOPE_WORKER_SUBTYPE = "Regular Employee"

# Phenom's authoritative `multi_category[*].category` values — these are
# the strings the facet UI shows and the strings the JSON DDO emits.
SCOPE_CATEGORIES = {
    "Software",
    "Information Systems / Information Technology",
    "Engineering & Technical Specialities",
}

# DDO `city` field is the canonical Phenom city name. Thales France posts
# under Paris arrondissement names (not "Paris"), so match those exactly.
SCOPE_CITIES = {
    "Issy-les-Moulineaux",
    "Paris 9e Arrondissement",
    "Rungis",
}


@dataclass
class Job:
    native_job_id: str           # Thales req-id, e.g. "R0308833"
    title: str
    location: str
    category: str
    apply_url: str
    employment_type: str         # always SCOPE_EMPLOYMENT_TYPE here
    description: str | None = None
    posted_date: str | None = None
    identifier: str | None = None
    raw_payload: dict | None = None


def _extract_phapp_ddo(html: str) -> dict | None:
    """Brace-balance `phApp.ddo = {...};` out of the page's inline script.

    Phenom serialises the DDO as plain JSON (double-quoted keys/strings,
    no functions, no trailing commas), so once we have the substring we
    can hand it straight to `json.loads`. Returns None if the marker
    isn't present (e.g. job got delisted between sitemap pull and fetch).
    """
    m = re.search(r"phApp\.ddo\s*=\s*\{", html)
    if not m:
        return None

    start = m.end() - 1  # the opening '{'
    depth = 0
    i = start
    n = len(html)
    while i < n:
        c = html[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start:i + 1])
                except json.JSONDecodeError:
                    return None
        elif c == '"':
            # Skip the string literal — '{' and '}' inside strings would
            # otherwise corrupt the depth counter.
            i += 1
            while i < n:
                ch = html[i]
                if ch == "\\":
                    i += 2
                    continue
                if ch == '"':
                    break
                i += 1
        i += 1
    return None


def _pick_category(multi_category) -> str | None:
    """`multi_category` is a list of `{category, internalCategoryId, ...}`
    dicts. We use the first entry's `category` for the canonical name.
    """
    if not isinstance(multi_category, list):
        return None
    for entry in multi_category:
        if not isinstance(entry, dict):
            continue
        for key in ("category", "primaryLocaleCategory"):
            v = entry.get(key)
            if isinstance(v, str) and v:
                return v
    return None


def _format_location(job: dict) -> str:
    """Build a human "City, State, Country" string from the DDO fields.
    Falls back to the prebuilt `address` when components are missing.
    """
    parts = [job.get("city"), job.get("state"), job.get("country")]
    parts = [p for p in parts if isinstance(p, str) and p.strip()]
    if parts:
        return ", ".join(parts)
    addr = job.get("address")
    return addr if isinstance(addr, str) else ""


def _parse_detail(html: str, url: str) -> Job | None:
    """Return a `Job` if the page passes the filter, else None.

    None means: page had no DDO (delisted / 404), or fields missing, or
    the row didn't match country/permanent/category/city.
    """
    ddo = _extract_phapp_ddo(html)
    if not ddo:
        return None

    job = ddo.get("jobDetail", {}).get("data", {}).get("job")
    if not isinstance(job, dict):
        return None

    if job.get("country") != SCOPE_COUNTRY:
        return None
    if job.get("workerSubType") != SCOPE_WORKER_SUBTYPE:
        return None

    category = _pick_category(job.get("multi_category"))
    if category not in SCOPE_CATEGORIES:
        return None

    city = job.get("city")
    if city not in SCOPE_CITIES:
        return None

    req_id = job.get("reqId") or ""
    title = job.get("title") or ""
    posted = job.get("postedDate") or job.get("datePosted")

    return Job(
        native_job_id=req_id,
        title=title,
        location=_format_location(job),
        category=category,
        apply_url=url,
        employment_type=SCOPE_EMPLOYMENT_TYPE,
        description=job.get("description"),
        posted_date=posted,
        identifier=req_id,
        raw_payload={
            "city": city,
            "state": job.get("state"),
            "country": job.get("country"),
            "workerSubType": job.get("workerSubType"),
            "type": job.get("type"),
            "applyUrl": job.get("applyUrl"),  # Workday direct-apply link
            "ml_skills": job.get("ml_skills"),
        },
    )


def _fetch_sitemap(session: requests.Session, url: str) -> list[str]:
    """GET a sitemap; return every `/job/...` URL it lists."""
    response = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return JOB_URL_RE.findall(response.text)


def _collect_job_urls(session: requests.Session) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for n in range(1, NUM_SITEMAPS + 1):
        sm_url = SITEMAP_TEMPLATE.format(n=n)
        urls = _fetch_sitemap(session, sm_url)
        new = 0
        for u in urls:
            if u not in seen:
                seen.add(u)
                ordered.append(u)
                new += 1
        print(f"  sitemap{n}: {len(urls)} entries ({new} new, {len(seen)} cumulative)", flush=True)
    return ordered


def _fetch_and_filter(url: str) -> Job | None:
    """Worker-pool task. Owns its own Session so retries/keep-alive don't
    cross threads. Politeness jitter spreads the load over the run.
    """
    time.sleep(random.uniform(JITTER_MIN, JITTER_MAX))
    session = requests.Session()
    response = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return _parse_detail(response.text, url)


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    print("Inventory phase (sitemap walk)...", flush=True)
    started = time.time()
    urls = _collect_job_urls(session)
    print(f"  → {len(urls)} unique job URLs in {time.time() - started:.1f}s\n", flush=True)

    print(
        f"Enrichment phase: {len(urls)} detail pages across {MAX_WORKERS} workers...",
        flush=True,
    )
    enrich_started = time.time()

    kept: list[Job] = []
    failed = 0
    processed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_to_url = {pool.submit(_fetch_and_filter, u): u for u in urls}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            processed += 1
            try:
                job = future.result()
            except Exception as exc:
                failed += 1
                if failed <= 10:  # avoid log spam — surface the first few only
                    print(
                        f"  [{processed}/{len(urls)}] FAILED {url}: "
                        f"{type(exc).__name__}: {exc}",
                        flush=True,
                    )
                continue
            if job is None:
                continue
            kept.append(job)
            print(
                f"  [{processed}/{len(urls)}] {job.identifier} "
                f"{job.title!r} ({job.category}, {job.location}) → KEEP",
                flush=True,
            )

    print(flush=True)
    print(f"Enrichment summary:", flush=True)
    print(f"  processed : {processed}", flush=True)
    print(f"  kept      : {len(kept)}", flush=True)
    print(f"  failed    : {failed}", flush=True)
    print(f"  elapsed   : {time.time() - enrich_started:.1f}s", flush=True)

    return [asdict(j) for j in kept]


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    run_started = time.time()
    try:
        jobs = scrape()
    except Exception as exc:
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise

    elapsed = time.time() - run_started
    print(f"\n=== {len(jobs)} jobs final (total runtime {elapsed:.1f}s) ===\n")

    for j in jobs:
        desc_preview = (j["description"] or "").strip()
        desc_preview = BeautifulSoup(desc_preview, "html.parser").get_text(" ", strip=True)
        desc_preview = desc_preview[:200] + ("…" if len(desc_preview) > 200 else "")

        print(f"[{j['identifier'] or j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc_preview}")
        print()
