"""Accenture job scraper — France, Full-time, Tech-flavored competences.

Source: https://www.accenture.com/fr-fr/careers/jobsearch
We hit Accenture's own AEM-fronted JSON API directly — NOT the Workday
backend behind it. The accenture.com page itself loads jobs by POSTing
to /api/accenture/jobsearch/result; we replay the same call.

API call shape (reverse-engineered from clientlib_site.min.js — see
./material/probe_07..probe_14 for the discovery path):

  POST https://www.accenture.com/api/accenture/jobsearch/result
  Content-Type: multipart/form-data
  Headers: X-Requested-With: XMLHttpRequest, Referer: <search page>
  FormData:
    startIndex     0-indexed page offset (0, 50, 100, ...)
    maxResultSize  page size (50 max — confirmed)
    jobKeyword     free-text search (empty for browse)
    jobLanguage    "fr-fr"  (language of returned text)
    countrySite    "fr-fr"  (locale of /fr-fr/ URL space)
    jobCountry     "France" (server-side country filter — only knob)
    jobFilters     JSON array; empty "[]" = no extra filters
    aggregations   JSON array; "[]" = don't compute facets
    sortBy         "0" (default)
    componentId    "" (page-component id; empty works)

Response shape:
  { total: int, data: [{ title, jobId, jobCityState, postedDate,
                         postedDateText, jobDetailUrl, skill, businessArea,
                         employeeType, jobRemoteType, jobDescription,
                         requisitionId, ... }],
    aggregations, status, message }

Filtering strategy (mirrors Sanofi):
- COUNTRY (server-side): jobCountry="France" — 211 jobs at probe time.
- EMPLOYEE_TYPE + SKILL (client-side): the JSON API silently rejects
  ad-hoc jobFilters payloads (returns 0 bytes 200) and we couldn't
  reverse-engineer the exact accepted shape. Cheaper to just pull all
  211 rows (5 pages × 50) and filter `employeeType` + `skill` locally.

To change scope: edit EMPLOYEE_TYPES_IN_SCOPE / SKILLS_IN_SCOPE.
"""
from __future__ import annotations

import ast
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import requests

HOST = "https://www.accenture.com"
SEARCH_PAGE = HOST + "/fr-fr/careers/jobsearch"
API_URL = HOST + "/api/accenture/jobsearch/result"

# Server-side filters baked into the POST body.
COUNTRY = "France"
JOB_LANGUAGE = "fr-fr"
COUNTRY_SITE = "fr-fr"

# Client-side filters applied after collecting all France rows.
EMPLOYEE_TYPES_IN_SCOPE: set[str] = {"Full-time"}
SKILLS_IN_SCOPE: set[str] = {
    "Software Engineering",
    "AI & Data",
    "Security",
    "Engineering & Networks",
}
# Match if ANY of these cities appears in the per-row jobCityState list.
# Accenture's France data uses bare city names ("Paris", not "Paris 75001"),
# so plain string equality is enough.
CITIES_IN_SCOPE: set[str] = {"Paris"}

# Browser-shaped headers. From=email so anyone reading server logs can
# reach us — accenture.com does not refuse the requests on UA alone (we
# confirmed in probe_01), so this is courtesy, not bypass.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "From": "yannickarieldossa@gmail.com",
    "Referer": SEARCH_PAGE,
}

REQUEST_DELAY_SECONDS = 2.0
REQUEST_TIMEOUT = 30
PAGE_SIZE = 50


@dataclass
class Job:
    native_job_id: str          # API `jobId`, e.g. "R00300992_fr-fr"
    title: str
    location: str | None
    category: str               # mapped from `skill`
    employment_type: str        # `employeeType`
    apply_url: str
    posted_date: str | None     # ISO YYYY-MM-DD (from epoch-ms `postedDate`)
    description: str | None
    identifier: str | None      # `requisitionId` like "R00300992"
    raw_payload: dict | None


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _warmup(session: requests.Session) -> None:
    """Hit the careers HTML page once. Not strictly required but seeds
    any future session affinity cookies and keeps the request pattern
    looking like a real browser flow."""
    session.get(SEARCH_PAGE, timeout=REQUEST_TIMEOUT)


def _fetch_page(session: requests.Session, start_index: int) -> dict:
    """POST one page of results. Returns the parsed JSON dict."""
    fields = {
        "startIndex": str(start_index),
        "maxResultSize": str(PAGE_SIZE),
        "jobKeyword": "",
        "jobLanguage": JOB_LANGUAGE,
        "countrySite": COUNTRY_SITE,
        "jobCountry": COUNTRY,
        "jobFilters": "[]",
        "aggregations": "[]",
        "sortBy": "0",
        "componentId": "",
    }
    r = session.post(
        API_URL,
        headers={
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": HOST,
        },
        files={k: (None, v) for k, v in fields.items()},
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def _parse_location(raw) -> str | None:
    """jobCityState arrives as a string repr of a list, e.g. "['Blagnac']"
    or sometimes "['Paris', 'France']". Be tolerant of either string-list
    or already-a-list shapes."""
    if raw is None:
        return None
    if isinstance(raw, list):
        cities = [str(x).strip() for x in raw if x]
        return ", ".join(cities) if cities else None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        # Try literal_eval first ('list-as-string'), then fall back to plain text.
        try:
            v = ast.literal_eval(s)
            if isinstance(v, list):
                cities = [str(x).strip() for x in v if x]
                return ", ".join(cities) if cities else None
            return str(v)
        except (ValueError, SyntaxError):
            return s
    return str(raw)


def _parse_posted_date(raw) -> str | None:
    """API returns epoch milliseconds as either int or numeric string."""
    if raw is None or raw == "":
        return None
    try:
        ms = int(raw)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


def _row_to_job(row: dict) -> Job | None:
    job_id = (row.get("jobId") or "").strip()
    if not job_id:
        return None
    apply_url = (row.get("jobDetailUrl") or "").strip()
    if apply_url and not apply_url.startswith("http"):
        apply_url = HOST + apply_url

    return Job(
        native_job_id=job_id,
        title=(row.get("title") or "").strip(),
        location=_parse_location(row.get("jobCityState")),
        category=(row.get("skill") or "").strip(),
        employment_type=(row.get("employeeType") or "").strip(),
        apply_url=apply_url,
        posted_date=_parse_posted_date(row.get("postedDate")),
        description=row.get("jobDescription"),
        identifier=(row.get("requisitionId") or None) or None,
        raw_payload=row,
    )


def scrape() -> list[dict]:
    session = _new_session()

    print("Warming session...", flush=True)
    _warmup(session)
    time.sleep(REQUEST_DELAY_SECONDS)

    # Listing phase — walk pages until we've consumed `total`.
    #
    # The API ranks results in an unstable order between sequential calls
    # (jobs on the boundary of a page can drift to the next page on the
    # next request). So a clean offset walk loses ~15-20% to overlap. We
    # work around it by continuing to paginate past `total`, wrapping the
    # offset modulo `total`, until we've collected all `total` unique IDs
    # or hit MAX_PAGES — whichever comes first.
    print("Listing phase...", flush=True)
    all_rows: dict[str, Job] = {}
    total: int | None = None
    started = time.time()
    MAX_PAGES = 20  # generous; usually converges in ~6-7 with 211 jobs
    consecutive_no_new = 0

    for page_idx in range(MAX_PAGES):
        # First pass at 0, PAGE_SIZE, 2*PAGE_SIZE, ... — once we exceed
        # `total`, wrap so we keep getting fresh shuffles of the deck.
        raw_start = page_idx * PAGE_SIZE
        start = raw_start if total is None else raw_start % max(total, 1)

        data = _fetch_page(session, start)
        if total is None:
            total = int(data.get("total") or 0)
        rows = data.get("data") or []

        new_count = 0
        for raw in rows:
            job = _row_to_job(raw)
            if job is None:
                continue
            if job.native_job_id not in all_rows:
                all_rows[job.native_job_id] = job
                new_count += 1

        print(
            f"  page {page_idx + 1:2d} (startIndex={start:3d}): {len(rows)} rows  "
            f"({new_count} new, {len(all_rows)}/{total} cumulative)",
            flush=True,
        )

        if total is not None and len(all_rows) >= total:
            break

        if new_count == 0:
            consecutive_no_new += 1
            if consecutive_no_new >= 3:
                # Three pages in a row with nothing new — we're done.
                break
        else:
            consecutive_no_new = 0

        time.sleep(REQUEST_DELAY_SECONDS)

    listing_elapsed = time.time() - started
    print(
        f"  → {len(all_rows)} listings in {listing_elapsed:.1f}s\n",
        flush=True,
    )

    # Client-side filter
    in_scope: list[Job] = []
    dropped_by_type: dict[str, int] = {}
    dropped_by_skill: dict[str, int] = {}
    dropped_by_city = 0
    for row in all_rows.values():
        if row.employment_type not in EMPLOYEE_TYPES_IN_SCOPE:
            dropped_by_type[row.employment_type or "(blank)"] = \
                dropped_by_type.get(row.employment_type or "(blank)", 0) + 1
            continue
        if row.category not in SKILLS_IN_SCOPE:
            dropped_by_skill[row.category or "(blank)"] = \
                dropped_by_skill.get(row.category or "(blank)", 0) + 1
            continue
        # City filter — `location` is already ", "-joined. Any in-scope
        # city listed for the job qualifies (Accenture often tags multi-city
        # roles like "Lyon, Paris"; that one keeps because Paris is in it).
        row_cities = [c.strip() for c in (row.location or "").split(",") if c.strip()]
        if not any(c in CITIES_IN_SCOPE for c in row_cities):
            dropped_by_city += 1
            continue
        in_scope.append(row)

    print("Filter pass:", flush=True)
    print(f"  employee_types={sorted(EMPLOYEE_TYPES_IN_SCOPE)}", flush=True)
    print(f"  skills        ={sorted(SKILLS_IN_SCOPE)}", flush=True)
    print(f"  cities        ={sorted(CITIES_IN_SCOPE)}", flush=True)
    print(f"  kept             : {len(in_scope)}", flush=True)
    print(f"  dropped by type  : {sum(dropped_by_type.values())} "
          f"({dict(dropped_by_type)})", flush=True)
    print(f"  dropped by skill : {sum(dropped_by_skill.values())} "
          f"({dict(dropped_by_skill)})", flush=True)
    print(f"  dropped by city  : {dropped_by_city}", flush=True)

    for j in in_scope:
        print(
            f"  KEEP [{j.identifier or j.native_job_id}] "
            f"{j.title!r} → {j.category} ({j.location})",
            flush=True,
        )

    return [asdict(j) for j in in_scope]


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
        # API jobDescription often carries HTML — strip tags for the preview only.
        desc_preview = re.sub(r"<[^>]+>", " ", desc_preview)
        desc_preview = re.sub(r"\s+", " ", desc_preview).strip()
        desc_preview = desc_preview[:200] + ("…" if len(desc_preview) > 200 else "")

        print(f"[{j['identifier'] or j['native_job_id']}] {j['title']}")
        print(f"  Skill      : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc_preview}")
        print()
