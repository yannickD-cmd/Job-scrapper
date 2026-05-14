"""Adobe job scraper — France only, no category filter.

Source: https://careers.adobe.com/us/en/search-results?qcountry=France
Adobe runs on the Phenom People SaaS career platform; tenant `ADOBUS`.
We hit Phenom's content API directly instead of scraping the SPA HTML
(the search page is a Vue/Aurelia shell that loads jobs via XHR).

API call shape (discovered via ./material/probe_01..probe_06 and the
ph-common JS bundle that builds the URL as
  `${cdnUrl}/api/${refNum}/${ddoKey}?locale=...&siteType=...&...`):

  GET https://content-us.phenompeople.com/api/ADOBUS/eagerLoadRefineSearch
      ?locale=en_us
      &siteType=external
      &deviceType=desktop
      &pageName=search-results
      &qcountry=France        # any of the page's `q*` filter params
      &from=0
      &size=100               # max page size — France fits in one page

Response shape (relevant subset):
  { eagerLoadRefineSearch: {
      status: 200, totalHits: int, hits: int,
      data: { jobs: [{ jobSeqNo, jobId, reqId, title, category, type,
                       city, state, country, location, cityStateCountry,
                       applyUrl, postedDate, dateCreated,
                       descriptionTeaser, experienceLevel, ... }],
              aggregations: [{ field, buckets:[] }, ... ] } } }

A second call to ddo `jobDetail` (`?jobSeqNo=<id>`) returns the full
HTML `description` (~8KB) — the listing only gives a teaser, so we
enrich each kept row with one detail call.

Filtering strategy:
- COUNTRY (server-side): `qcountry=France` cuts ~1153 -> 18 rows.
  Phenom treats this as a *radius* (sliderRadius=305mi in the response).
- COUNTRY (client-side): keep a row if its primary `country == "France"`
  OR it is `isMultiLocation` with at least one entry in `multi_location`
  ending in ", France". Adobe routinely tags pan-EMEA roles with a non-FR
  primary city (London / Reading / Amsterdam) plus Paris as an option;
  those count. All 18 rows currently pass.
- No category/skill filter — see filters.md for why.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import requests

HOST = "https://careers.adobe.com"
SEARCH_PAGE = HOST + "/us/en/search-results?qcountry=France"

# Phenom content API. `ADOBUS` is Adobe's Phenom tenant `refNum`.
API_HOST = "https://content-us.phenompeople.com"
TENANT = "ADOBUS"
LIST_URL = f"{API_HOST}/api/{TENANT}/eagerLoadRefineSearch"
DETAIL_URL = f"{API_HOST}/api/{TENANT}/jobDetail"

# Common DDO query params — every Phenom call carries these.
COMMON_PARAMS = {
    "locale": "en_us",
    "siteType": "external",
    "deviceType": "desktop",
}

# Server-side filter — soft, radius-based.
COUNTRY = "France"

# Client-side filter — primary `country` must be France, OR (multi-location and
# any of its locations is in France). Adobe tags pan-EMEA roles with a single
# primary city (London / Reading / Amsterdam) but lists Paris as one of the
# acceptable work sites in `multi_location`; those count for us.
COUNTRY_STRICT: set[str] = {"France"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "From": "yannickarieldossa@gmail.com",
    "Referer": SEARCH_PAGE,
    "Origin": HOST,
}

REQUEST_DELAY_SECONDS = 1.0
REQUEST_TIMEOUT = 30
PAGE_SIZE = 100  # France fits well under this; we never paginate in practice


@dataclass
class Job:
    native_job_id: str          # `jobSeqNo`, e.g. "ADOBUSR166821EXTERNALENUS"
    title: str
    location: str | None        # `location` field, e.g. "Paris, Paris, France"
    category: str               # `category`, e.g. "Sales" / "Design"
    employment_type: str        # `type`, e.g. "Full time"
    apply_url: str              # Workday URL — where users actually apply
    posted_date: str | None     # ISO YYYY-MM-DD
    description: str | None     # Full HTML description from `jobDetail`
    identifier: str | None      # `reqId`, e.g. "R166821"
    raw_payload: dict | None


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _fetch_listing(session: requests.Session, start: int, size: int) -> dict:
    params = {
        **COMMON_PARAMS,
        "pageName": "search-results",
        "qcountry": COUNTRY,
        "from": str(start),
        "size": str(size),
    }
    r = session.get(LIST_URL, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()["eagerLoadRefineSearch"]


def _fetch_detail(session: requests.Session, job_seq_no: str) -> dict | None:
    params = {
        **COMMON_PARAMS,
        "pageName": "jobDetail",
        "jobSeqNo": job_seq_no,
    }
    r = session.get(DETAIL_URL, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    try:
        return r.json()["jobDetail"]["data"]["job"]
    except (KeyError, ValueError):
        return None


def _parse_posted_date(raw) -> str | None:
    """`postedDate` arrives as ISO-8601 like '2026-03-19T00:00:00.000+0000'."""
    if not raw:
        return None
    s = str(raw).strip()
    # Strip the .000+0000 suffix Python's fromisoformat can't take pre-3.11
    s = re.sub(r"\.\d+([+-]\d{4})$", r"\1", s)
    s = re.sub(r"([+-])(\d{2})(\d{2})$", r"\1\2:\3", s)
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc).strftime("%Y-%m-%d")
    except ValueError:
        return s[:10] if len(s) >= 10 else None


def _job_country_match(row: dict, allowed: set[str]) -> bool:
    """Keep a job if its primary country matches, OR if it's a multi-location
    role that lists an allowed country among its alternates. Adobe routinely
    posts pan-EMEA roles with a non-FR primary city plus Paris as an option."""
    if (row.get("country") or "").strip() in allowed:
        return True
    if row.get("isMultiLocation"):
        locs = row.get("multi_location") or []
        # multi_location entries look like "Paris, Paris, France"
        for loc in locs:
            if not isinstance(loc, str):
                continue
            tail = loc.rsplit(",", 1)[-1].strip() if "," in loc else ""
            if tail in allowed:
                return True
    return False


def _location_for_job(row: dict) -> str | None:
    """Use the primary location for single-site roles; join the full set with
    ' | ' for multi-location, so the row keeps the full picture."""
    if row.get("isMultiLocation"):
        locs = [l for l in (row.get("multi_location") or []) if isinstance(l, str) and l.strip()]
        if locs:
            return " | ".join(locs)
    primary = (row.get("location") or row.get("cityStateCountry") or "").strip()
    return primary or None


def _row_to_job(row: dict, description: str | None) -> Job | None:
    job_seq = (row.get("jobSeqNo") or "").strip()
    if not job_seq:
        return None
    apply_url = (row.get("applyUrl") or "").strip()
    return Job(
        native_job_id=job_seq,
        title=(row.get("title") or "").strip(),
        location=_location_for_job(row),
        category=(row.get("category") or "").strip(),
        employment_type=(row.get("type") or "").strip(),
        apply_url=apply_url,
        posted_date=_parse_posted_date(row.get("postedDate") or row.get("dateCreated")),
        description=description,
        identifier=(row.get("reqId") or row.get("jobId") or None) or None,
        raw_payload=row,
    )


def scrape() -> list[dict]:
    session = _new_session()

    print("Listing phase...", flush=True)
    start = 0
    all_rows: dict[str, dict] = {}
    total: int | None = None

    while True:
        data = _fetch_listing(session, start, PAGE_SIZE)
        if total is None:
            total = int(data.get("totalHits") or 0)
        rows = data.get("data", {}).get("jobs") or []
        new_count = 0
        for r in rows:
            seq = r.get("jobSeqNo")
            if seq and seq not in all_rows:
                all_rows[seq] = r
                new_count += 1
        print(
            f"  from={start:3d}: {len(rows)} rows ({new_count} new, "
            f"{len(all_rows)}/{total} cumulative)",
            flush=True,
        )
        if len(rows) < PAGE_SIZE or len(all_rows) >= (total or 0):
            break
        start += PAGE_SIZE
        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"  -> {len(all_rows)} listings\n", flush=True)

    # Country filter — primary OR any alternate location in COUNTRY_STRICT.
    in_scope = [r for r in all_rows.values() if _job_country_match(r, COUNTRY_STRICT)]
    multi_city = sum(1 for r in in_scope if len(r.get("multi_location") or []) > 1)
    dropped = len(all_rows) - len(in_scope)
    print("Filter pass:", flush=True)
    print(f"  country in {sorted(COUNTRY_STRICT)} (primary or any multi-location)", flush=True)
    print(f"  kept           : {len(in_scope)} ({multi_city} multi-city, "
          f"{len(in_scope) - multi_city} single-city)", flush=True)
    print(f"  dropped        : {dropped} (no FR site at all)", flush=True)
    print(flush=True)

    # Enrich each kept row with the full description.
    jobs: list[Job] = []
    print("Detail phase...", flush=True)
    for i, row in enumerate(in_scope, 1):
        seq = row["jobSeqNo"]
        print(f"  [{i}/{len(in_scope)}] {seq} ...", flush=True)
        detail = _fetch_detail(session, seq)
        desc = (detail or {}).get("description") if detail else None
        job = _row_to_job(row, desc)
        if job is not None:
            jobs.append(job)
        time.sleep(REQUEST_DELAY_SECONDS)

    print(flush=True)
    for j in jobs:
        print(
            f"  KEEP [{j.identifier or j.native_job_id}] "
            f"{j.title!r} -> {j.category} ({j.location})",
            flush=True,
        )

    return [asdict(j) for j in jobs]


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
        desc_preview = re.sub(r"<[^>]+>", " ", desc_preview)
        desc_preview = re.sub(r"\s+", " ", desc_preview).strip()
        desc_preview = desc_preview[:200] + ("..." if len(desc_preview) > 200 else "")

        print(f"[{j['identifier'] or j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc_preview}")
        print()
