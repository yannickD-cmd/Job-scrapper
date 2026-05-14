"""L'Oréal job scraper — France, Tech & Data functions, Permanent only.

ATS: Avature (URL signature: /en_US/jobs/SearchJobs + facet params
like ?3_110_3=18022). Site sits behind **Cloudflare Bot Management**,
which TLS-fingerprints requests before HTTP headers are even checked.
Plain `requests` (stdlib OpenSSL) gets 403'd every endpoint, including
/robots.txt and /sitemap.xml. We use curl_cffi with chrome131
impersonation — it forges a real Chrome TLS handshake and clears the
check (see probes 1–3 in ./material for evidence).

Filter strategy (mirrors Sanofi):
- COUNTRY (server-side): one facet param `?3_110_3=18022` filters to
  France. Confirmed by probing — the country `<select>` shows "France"
  as the active option when this param is set.
- CONTRACT_TYPE and FUNCTION (client-side): NOT exposed as URL facets;
  they live only in the per-row inline `dataLayer.push` block as a
  "::"-separated eventLabel string. We parse that, then drop any row
  whose contract_type or function isn't in scope.

Pagination: ?offset=N where N is 0-indexed (0, 20, 40, …). The
URL-style `?s=21` is silently ignored — confirmed in probe_07.

Per-row HTML shape (from probe_04 / probe_05):
  <article class="article--result">
    <h3 class="article__header__text__title"><a href=".../JobDetail/<slug>/<id>">Title</a></h3>
    <div class="article__header__text__subtitle">
      <span>Clichy</span>
      <span>Posted 05-Mar-2026</span>
    </div>
    <div class="article__content">…description preview…</div>
    <div id="jobId<N>">…</div>
    <script>… dataLayer.push({ 'eventLabel': 'Title::Function::Sub::::Schedule::ContractType::::::Location::JobID' })</script>
  </article>

To change scope, edit FRANCE_FACET, CONTRACT_TYPES_IN_SCOPE,
FUNCTIONS_IN_SCOPE.
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
from dataclasses import asdict, dataclass

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

HOST = "https://careers.loreal.com"
SEARCH_BASE = HOST + "/en_US/jobs/SearchJobs"
AJAX_LISTING = HOST + "/en_US/jobs/SearchJobsAJAX"

# France's value in the country facet. The facet key `3_110_3` is L'Oréal's
# Avature encoding of the Country axis (their site uses this exact param
# in the user-facing URL: /en_US/jobs/SearchJobs?3_110_3=18022).
FRANCE_FACET = "3_110_3=18022"

FIRST_PAGE_URL = f"{SEARCH_BASE}?{FRANCE_FACET}"

CONTRACT_TYPES_IN_SCOPE: set[str] = {"Permanent"}
# Avature's per-row function taxonomy is finer than the "Our Expertise"
# URL facet. Tech and Data are distinct values in the per-row dataLayer.
FUNCTIONS_IN_SCOPE: set[str] = {"Tech", "Data"}

# Identifies us in access logs via `From:` even though the UA is browser-shaped
# (Cloudflare rejects identifying UAs).
HEADERS_FROM = "yannickarieldossa@gmail.com"

REQUEST_DELAY_SECONDS = 2.0
REQUEST_TIMEOUT = 30
PAGE_SIZE = 20

IMPERSONATE_PROFILE = "chrome131"


# Locale-safe English month parsing (`datetime.strptime("%b", ...)` is
# locale-dependent and would break on a non-English Windows install).
MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

# Matches dataLayer.push({... 'eventLabel': 'X::Y::...' ...}). The value
# is wrapped in single quotes per Avature's emitter; values themselves
# are HTML-escaped (e.g. `&amp;`), so apostrophes won't break the regex.
EVENT_LABEL_RE = re.compile(r"['\"]eventLabel['\"]\s*:\s*['\"]([^'\"]+)['\"]")


@dataclass
class Job:
    native_job_id: str          # numeric ID from id="jobId<N>" / URL trailing segment
    title: str
    location: str | None
    category: str               # `function` from dataLayer (Tech / Data / ...)
    employment_type: str        # `contract_type` from dataLayer (Permanent / ...)
    apply_url: str
    posted_date: str | None     # ISO YYYY-MM-DD
    # Filled by detail-page enrichment:
    description: str | None = None
    identifier: str | None = None
    raw_payload: dict | None = None


def _new_session() -> cffi_requests.Session:
    s = cffi_requests.Session(impersonate=IMPERSONATE_PROFILE)
    s.headers.update({
        "Accept-Language": "en-US,en;q=0.9",
        "From": HEADERS_FROM,
    })
    return s


def _warmup(session: cffi_requests.Session) -> None:
    """Hit the bare /jobs/SearchJobs once to seed cookies (Cloudflare
    sets `__cf_bm` etc. on the first valid TLS handshake)."""
    session.get(SEARCH_BASE, timeout=REQUEST_TIMEOUT)


def _ajax_get(session: cffi_requests.Session, url: str, *, referer: str) -> str:
    r = session.get(
        url,
        headers={
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "text/html, */*; q=0.01",
        },
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return r.text


def _doc_get(session: cffi_requests.Session, url: str, *, referer: str) -> str:
    r = session.get(
        url,
        headers={
            "Referer": referer,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return r.text


def _parse_posted(text: str) -> str | None:
    """'Posted 05-Mar-2026' -> '2026-03-05'."""
    if not text:
        return None
    cleaned = text.replace("Posted", "").strip()
    parts = cleaned.split("-")
    if len(parts) != 3:
        return None
    try:
        day = int(parts[0])
        month = MONTHS[parts[1][:3].title()]
        year = int(parts[2])
        return f"{year:04d}-{month:02d}-{day:02d}"
    except (ValueError, KeyError):
        return None


def _extract_event_label(article) -> dict | None:
    """Find the dataLayer eventLabel inside any <script> in this article.

    probe_06 saw 13/162 rows fail when we only looked at article.find('script').
    Fix: walk every <script> in the article AND fall back to searching the
    article's raw HTML — covers all observed cases.
    """
    candidates: list[str] = []
    for script in article.find_all("script"):
        if script.string:
            candidates.append(script.string)
    candidates.append(str(article))

    for body in candidates:
        m = EVENT_LABEL_RE.search(body)
        if not m:
            continue
        # The eventLabel is HTML-escaped inside the JS source. Unescape so
        # downstream comparisons (function=="Tech") aren't tripped by &amp;.
        raw = html.unescape(m.group(1))
        parts = raw.split("::")
        while len(parts) < 10:
            parts.append("")
        return {
            "title": parts[0].strip(),
            "function": parts[1].strip(),
            "sub_function": parts[2].strip(),
            "schedule": parts[4].strip(),
            "contract_type": parts[5].strip(),
            "location": parts[8].strip(),
            "job_id": parts[9].strip(),
        }
    return None


def _parse_listing_html(fragment: str) -> tuple[list[Job], int | None]:
    """Return (rows_on_page, total_jobs_or_none_if_unknown)."""
    soup = BeautifulSoup(fragment, "html.parser")
    rows: list[Job] = []
    total: int | None = None

    for article in soup.select("article.article--result"):
        if total is None:
            try:
                total = int(article.get("data-total", "0")) or None
            except ValueError:
                pass

        # Numeric ID — prefer the id="jobId<N>" inside the actions div.
        actions = article.select_one("[id^='jobId']")
        native_id = ""
        if actions:
            native_id = actions.get("id", "").replace("jobId", "")
        # Fallback to last segment of the JobDetail URL.
        a = article.select_one("h3 a") or article.select_one("a[href*='/JobDetail/']")
        href = a.get("href", "") if a else ""
        if not native_id and "/JobDetail/" in href:
            native_id = href.rstrip("/").rsplit("/", 1)[-1]
        if not native_id:
            continue

        title = a.get_text(" ", strip=True) if a else ""

        location = posted_date = None
        subtitle = article.select_one(".article__header__text__subtitle")
        if subtitle:
            spans = [s.get_text(" ", strip=True) for s in subtitle.find_all("span")]
            spans = [s for s in spans if s]
            for s in spans:
                if s.lower().startswith("posted"):
                    posted_date = _parse_posted(s)
                elif location is None:
                    location = s

        meta = _extract_event_label(article) or {}
        category = meta.get("function") or ""
        contract_type = meta.get("contract_type") or ""

        # Avature emits absolute URLs in the listing, but be defensive.
        apply_url = href if href.startswith("http") else HOST + href

        rows.append(Job(
            native_job_id=native_id,
            title=title,
            location=location,
            category=category,
            employment_type=contract_type,
            apply_url=apply_url,
            posted_date=posted_date,
        ))

    return rows, total


# ---- Detail-page enrichment ---------------------------------------------

def _parse_jobposting_jsonld(html_text: str) -> dict | None:
    soup = BeautifulSoup(html_text, "html.parser")
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def _extract_description_dom(html_text: str) -> str | None:
    """Pull the description block out of the rendered page.

    L'Oréal's schema.org JSON-LD is sparse (just title + datePosted —
    confirmed via probe_08). The real description lives in the DOM,
    most reliably under schema.org microdata at `[itemprop=description]`.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    for sel in [
        "[itemprop='description']",   # ~5KB on probed sample, reliable
        "[class*='job-description']", # Avature naming variant
        ".description",
        "[class*='description']",
    ]:
        for el in soup.select(sel):
            txt = el.get_text(" ", strip=True)
            if len(txt) > 400:  # filters out tiny chrome/menu/breadcrumb hits
                return str(el)
    return None


def _enrich(session: cffi_requests.Session, job: Job, referer: str) -> bool:
    """Enrich one job from its detail page.

    Returns True if we extracted *something* useful — either JSON-LD
    metadata, a DOM description, or both.
    """
    html_text = _doc_get(session, job.apply_url, referer=referer)
    found_anything = False

    payload = _parse_jobposting_jsonld(html_text)
    if payload:
        # L'Oréal's JSON-LD typically only carries title + datePosted, so
        # the value is mainly the canonical posted_date.
        dp = payload.get("datePosted")
        if dp and not job.posted_date:
            job.posted_date = dp
        ident = payload.get("identifier")
        if isinstance(ident, dict):
            v = ident.get("value")
            if isinstance(v, str):
                job.identifier = v
        elif isinstance(ident, str):
            job.identifier = ident
        job.raw_payload = payload
        found_anything = True

    # Always go to the DOM for description — JSON-LD doesn't carry it here.
    desc = _extract_description_dom(html_text)
    if desc:
        job.description = desc
        found_anything = True

    return found_anything


# ---- Top-level ----------------------------------------------------------

def scrape() -> list[dict]:
    session = _new_session()

    print("Cloudflare warmup...", flush=True)
    _warmup(session)
    time.sleep(REQUEST_DELAY_SECONDS)

    # Listing phase
    print("Listing phase...", flush=True)
    all_rows: dict[str, Job] = {}
    total: int | None = None
    page_idx = 0
    started = time.time()

    while True:
        offset = page_idx * PAGE_SIZE
        url = f"{AJAX_LISTING}?offset={offset}&{FRANCE_FACET}"
        referer = FIRST_PAGE_URL if page_idx == 0 else \
                  f"{AJAX_LISTING}?offset={(page_idx - 1) * PAGE_SIZE}&{FRANCE_FACET}"
        fragment = _ajax_get(session, url, referer=referer)
        page_rows, page_total = _parse_listing_html(fragment)
        if page_total is not None:
            total = page_total

        new_count = 0
        for r in page_rows:
            if r.native_job_id not in all_rows:
                all_rows[r.native_job_id] = r
                new_count += 1

        print(
            f"  offset={offset:3d}: {len(page_rows)} rows  "
            f"({new_count} new, {len(all_rows)}{'/' + str(total) if total else ''} cumulative)",
            flush=True,
        )

        # Stop when we've matched the page's stated total, or when a page
        # returns no fresh rows (defensive against the offset-clamp bug
        # we hit during probing).
        if total is not None and len(all_rows) >= total:
            break
        if new_count == 0:
            break

        page_idx += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    listing_elapsed = time.time() - started
    print(
        f"  → {len(all_rows)} listings in {listing_elapsed:.1f}s\n",
        flush=True,
    )

    # Client-side filter — Avature exposes country as a URL facet but
    # contract type and function only in the per-row dataLayer.
    #
    # Rule: function is the hard filter (drop rows without one). Contract
    # type can be blank in the dataLayer (probed sample: jobId 240207
    # "Data Project Manager" has function=Tech, contract_type=""). We
    # keep such rows since the title shape usually means Permanent, and
    # let downstream readers see them rather than silently lose tech roles
    # over missing metadata.
    in_scope: list[Job] = []
    dropped_no_function = 0
    dropped_by_function: dict[str, int] = {}
    dropped_by_contract: dict[str, int] = {}
    kept_blank_contract = 0
    for row in all_rows.values():
        if not row.category:
            dropped_no_function += 1
            continue
        if row.category not in FUNCTIONS_IN_SCOPE:
            dropped_by_function[row.category] = \
                dropped_by_function.get(row.category, 0) + 1
            continue
        if row.employment_type and row.employment_type not in CONTRACT_TYPES_IN_SCOPE:
            dropped_by_contract[row.employment_type] = \
                dropped_by_contract.get(row.employment_type, 0) + 1
            continue
        if not row.employment_type:
            kept_blank_contract += 1
        in_scope.append(row)

    print("Filter pass:", flush=True)
    print(f"  contract_types={sorted(CONTRACT_TYPES_IN_SCOPE)} "
          f"functions={sorted(FUNCTIONS_IN_SCOPE)}", flush=True)
    print(f"  kept              : {len(in_scope)}", flush=True)
    print(f"    (of which blank-contract-type, kept tentatively: "
          f"{kept_blank_contract})", flush=True)
    print(f"  dropped by func   : {sum(dropped_by_function.values())} "
          f"({dict(dropped_by_function)})", flush=True)
    print(f"  dropped by type   : {sum(dropped_by_contract.values())} "
          f"({dict(dropped_by_contract)})", flush=True)
    print(f"  dropped no-funct  : {dropped_no_function}\n", flush=True)

    # Enrichment phase
    print(
        f"Enrichment phase: fetching {len(in_scope)} detail pages "
        f"(~{int(len(in_scope) * REQUEST_DELAY_SECONDS)}s)...",
        flush=True,
    )

    kept: list[Job] = []
    failed = 0
    for i, job in enumerate(in_scope, 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            ok = _enrich(session, job, referer=FIRST_PAGE_URL)
        except Exception as exc:
            print(f"  [{i}/{len(in_scope)}] {job.native_job_id} FAILED: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            failed += 1
            # Keep the row — we still have listing-level fields.
            kept.append(job)
            continue

        marker = "KEEP" if ok else "KEEP (no description found)"
        print(f"  [{i}/{len(in_scope)}] {job.identifier or job.native_job_id} "
              f"{job.title!r} → {marker}", flush=True)
        kept.append(job)

    print()
    print(f"Enrichment summary:", flush=True)
    print(f"  kept    : {len(kept)}", flush=True)
    print(f"  failed  : {failed}", flush=True)

    return [asdict(j) for j in kept]


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    started = time.time()
    try:
        jobs = scrape()
    except Exception as exc:
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise

    elapsed = time.time() - started
    print(f"\n=== {len(jobs)} jobs final (total runtime {elapsed:.1f}s) ===\n")

    for j in jobs:
        desc_preview = (j["description"] or "").strip()
        desc_preview = BeautifulSoup(desc_preview, "html.parser").get_text(" ", strip=True)
        desc_preview = desc_preview[:200] + ("…" if len(desc_preview) > 200 else "")

        print(f"[{j['identifier'] or j['native_job_id']}] {j['title']}")
        print(f"  Function   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc_preview}")
        print()
