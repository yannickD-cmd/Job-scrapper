"""Publicis Groupe job scraper — France, Data / Software / Tech, CDI only.

The corporate site at publicisgroupe.com/.../job-opportunities is a landing
page that funnels into the iCIMS-hosted board at careers.publicisgroupe.com
— which itself is a Jibe (iCIMS) SPA. The SPA renders nothing server-side;
all listings come from a single JSON endpoint:

    GET https://careers.publicisgroupe.com/api/jobs?country=France&page=N

Response shape (per page, fixed 10 jobs):
    { "jobs": [ { "data": {...} } ],
      "totalCount": <int across whole country>,
      "count": <int in current language locale — DO NOT trust>,
      "filter": { "facetList": {...}, "locations": {...} } }

`size`/`from`/`offset` are silently ignored; `page` is the only pagination
knob. Pages past the last one return an empty `jobs` list (not 404).

Each job's `data` block carries:
- `req_id`           → native_job_id  (e.g. "149906")
- `title`, `apply_url` (per-agency icims subdomain, e.g.
                       epsilon-publicisgroupe.icims.com)
- `country`/`country_code`, `full_location`
- `tags1`            → job family    (Data Sciences / Engineering /
                       Technology — or their French translations
                       "Science des données" / "Ingénieurie" /
                       "Technologies" — Publicis tags France jobs in
                       French, not in the API's response language)
- `tags3`            → contract type (Régulier à temps plein / Regular /
                       Alternance / Interne / Temporary — Fixed Term
                       Contract, …)
- `description`, `responsibilities`, `qualifications`
- `posted_date`      → ISO datetime, normalised to YYYY-MM-DD on output

Filters are applied client-side after fetch because the public API has no
server-side filter for `tags1` that respects multi-language tag values (a
?tags1=Engineering query misses the 4/50-sampled French "Technologies"
jobs we want to keep). With ~133 France postings total (≈14 pages), one
full scan is cheap.

To change scope, edit KEEP_FAMILIES / CDI_TAGS3 / COUNTRY.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass

import requests

HOST = "https://careers.publicisgroupe.com"
API_URL = f"{HOST}/api/jobs"
COUNTRY = "France"

# Job-family axis — Publicis stores `tags1` in the posting's own locale, so
# a France posting is tagged "Science des données" / "Ingénieurie" /
# "Technologies"; an English-locale posting at the same agency uses
# "Data Sciences" / "Engineering" / "Technology". We need both spellings.
# (Strings reproduced exactly as the API emits them — "Ingénieurie" is the
# spelling Publicis uses, not the standard "Ingénierie".)
KEEP_FAMILIES: set[str] = {
    "Data Sciences",
    "Engineering",
    "Technology",
    "Science des données",
    "Ingénieurie",
    "Technologies",
}

# Contract axis — `tags3` carries the localized label. Keep permanent only.
# Observed non-CDI values for France: "Alternance", "Interne" (Stage),
# "Temporary - Fixed Term Contract" (CDD).
CDI_TAGS3: set[str] = {
    "Régulier à temps plein",
    "Regular",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

REQUEST_DELAY_SECONDS = 1.0
REQUEST_TIMEOUT = 30
MAX_PAGES = 50  # safety cap — France currently has ~14 pages


@dataclass
class Job:
    native_job_id: str
    title: str
    apply_url: str
    description: str | None = None
    location: str | None = None
    category: str | None = None
    posted_date: str | None = None
    employment_type: str | None = None
    identifier: str | None = None
    raw_payload: dict | None = None


def _normalize_date(raw: str | None) -> str | None:
    """Publicis emits ISO datetimes like '2026-05-13T11:50:00+0000'. Date only."""
    if not raw:
        return None
    return raw.split("T", 1)[0]


def _build_job(data: dict) -> Job | None:
    req_id = (data.get("req_id") or data.get("slug") or "").strip()
    if not req_id:
        return None

    title = (data.get("title") or "").strip()
    apply_url = (data.get("apply_url") or "").strip()
    if not apply_url:
        # Fallback if the per-agency icims URL is missing for some reason —
        # the careers.publicisgroupe.com canonical page also works.
        apply_url = f"{HOST}/jobs/{req_id}"

    tags1 = data.get("tags1") or []
    tags3 = data.get("tags3") or []

    return Job(
        native_job_id=req_id,
        title=title,
        apply_url=apply_url,
        description=data.get("description") or None,
        location=data.get("full_location") or data.get("short_location") or None,
        category=", ".join(tags1) if tags1 else None,
        posted_date=_normalize_date(data.get("posted_date")),
        employment_type=", ".join(tags3) if tags3 else None,
        identifier=req_id,
        raw_payload={
            "req_id": req_id,
            "country": data.get("country"),
            "country_code": data.get("country_code"),
            "city": data.get("city"),
            "tags1": tags1,
            "tags2": data.get("tags2"),  # agency/brand (Epsilon, Zenith, …)
            "tags3": tags3,
            "tags5": data.get("tags5"),  # seniority
            "tags6": data.get("tags6"),  # on-site/hybrid/remote
            "ats_code": data.get("ats_code"),
            "create_date": data.get("create_date"),
            "update_date": data.get("update_date"),
        },
    )


def _is_in_scope(job: Job, data: dict) -> tuple[bool, str]:
    """Returns (keep, reason). reason describes the rejection axis."""
    tags1 = set(data.get("tags1") or [])
    tags3 = set(data.get("tags3") or [])

    if not (tags1 & KEEP_FAMILIES):
        return False, f"family={sorted(tags1) or [None]!r}"
    if not (tags3 & CDI_TAGS3):
        return False, f"contract={sorted(tags3) or [None]!r}"
    return True, ""


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print(f"Listing phase (country={COUNTRY})...", flush=True)

    seen: dict[str, tuple[Job, dict]] = {}
    total_count: int | None = None

    for page in range(1, MAX_PAGES + 1):
        if page > 1:
            time.sleep(REQUEST_DELAY_SECONDS)

        params = {"country": COUNTRY, "page": page}
        response = session.get(API_URL, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()

        page_jobs = payload.get("jobs") or []
        total_count = payload.get("totalCount", total_count)

        if not page_jobs:
            print(f"  page {page}: empty — stop", flush=True)
            break

        added = 0
        for entry in page_jobs:
            data = entry.get("data") or {}
            job = _build_job(data)
            if job is None:
                continue
            if job.native_job_id in seen:
                continue
            seen[job.native_job_id] = (job, data)
            added += 1

        print(
            f"  page {page}: {len(page_jobs)} jobs, {added} new "
            f"({len(seen)} unique so far, totalCount={total_count})",
            flush=True,
        )

        if total_count is not None and len(seen) >= total_count:
            break

    print(
        f"  → {len(seen)} unique {COUNTRY} jobs fetched "
        f"in {time.time() - started:.1f}s\n",
        flush=True,
    )

    # Filter phase
    kept: list[Job] = []
    rejected: dict[str, int] = {}

    for job, data in seen.values():
        keep, reason = _is_in_scope(job, data)
        if keep:
            kept.append(job)
            marker = "KEEP"
        else:
            rejected[reason] = rejected.get(reason, 0) + 1
            marker = f"drop ({reason})"
        print(f"  {job.identifier} {job.title!r} → {marker}", flush=True)

    elapsed = time.time() - started
    print(flush=True)
    print(
        f"Filters country={COUNTRY!r} × family={sorted(KEEP_FAMILIES)} "
        f"× contract={sorted(CDI_TAGS3)}:",
        flush=True,
    )
    print(f"  kept    : {len(kept)}", flush=True)
    print(f"  dropped : {sum(rejected.values())}", flush=True)
    print(f"  runtime : {elapsed:.1f}s", flush=True)

    if rejected:
        top = sorted(rejected.items(), key=lambda kv: -kv[1])[:10]
        print("  top reject reasons:", flush=True)
        for r, n in top:
            print(f"    {n:3d}  {r}", flush=True)

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
        desc = (j.get("description") or "")
        desc = re.sub(r"<[^>]+>", " ", desc)
        desc = re.sub(r"\s+", " ", desc).strip()
        desc = desc[:200] + ("…" if len(desc) > 200 else "")

        print(f"[{j['identifier']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
