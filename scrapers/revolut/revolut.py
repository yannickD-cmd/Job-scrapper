"""Revolut job scraper — Europe (any country), Engineering + Data teams.

www.revolut.com/careers/ is a custom Next.js frontend (no public Greenhouse /
Lever board — boards-api.greenhouse.io/v1/boards/revolut is a 404). The full
position list (~585 jobs, all geos) ships embedded in the careers page's
Next.js data route:

    https://www.revolut.com/_next/data/{buildId}/en-GB/careers.json
        -> pageProps.positions: [{id, text, team, locations[], ...}]

Each position lists MULTIPLE locations ({name, type: office|remote, country}),
so the Europe filter keeps a job if ANY of its locations is in a European
country, and the stored `location` is the "; "-joined European subset.

buildId changes on every deploy, so it is discovered at runtime from the
/careers/ HTML page's __NEXT_DATA__. Fallback: nonexistent paths under /api/
fall through to the Next.js-rendered 404 page, which also carries buildId
(arbitrary 404 paths do NOT — they get a static edge 404 without it).

Descriptions are empty in the listing payload; one detail data-route call per
kept job fills them:

    https://www.revolut.com/_next/data/{buildId}/en-GB/careers/position/{id}.json
        -> pageProps.position.description (HTML)

WAF quirk: a full browser User-Agent is required — a bare "Mozilla/5.0" gets
403 on the data routes. Session is cookie-free (Pernod lesson: never seed
Cloudflare cookies).

The payload has no posted_date and no employment_type (Revolut lists only
full-time-style roles); both stay None.

To change scope, edit TEAMS_IN_SCOPE / EUROPEAN_COUNTRIES.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass

import requests

HOST = "https://www.revolut.com"
CAREERS_HTML_URL = f"{HOST}/careers/"
# Nonexistent /api/ paths render the Next.js 404 page (which embeds buildId);
# arbitrary 404 paths get a static edge 404 without it.
BUILD_ID_FALLBACK_URL = f"{HOST}/api/careers/positions/"
LISTING_URL_TEMPLATE = f"{HOST}/_next/data/{{build_id}}/en-GB/careers.json"
DETAIL_URL_TEMPLATE = (
    f"{HOST}/_next/data/{{build_id}}/en-GB/careers/position/{{job_id}}.json"
)
APPLY_URL_TEMPLATE = f"{HOST}/careers/position/{{job_id}}/"

TEAMS_IN_SCOPE: set[str] = {"Engineering", "Data"}

EUROPEAN_COUNTRIES: set[str] = {
    "Albania", "Austria", "Belgium", "Bosnia and Herzegovina", "Bulgaria",
    "Croatia", "Cyprus", "Czech Republic", "Czechia", "Denmark", "Estonia",
    "Finland", "France", "Germany", "Greece", "Hungary", "Iceland", "Ireland",
    "Italy", "Latvia", "Lithuania", "Luxembourg", "Malta", "Moldova",
    "Montenegro", "Netherlands", "North Macedonia", "Norway", "Poland",
    "Portugal", "Romania", "Serbia", "Slovakia", "Slovenia", "Spain",
    "Sweden", "Switzerland", "Ukraine", "United Kingdom",
}

HEADERS = {
    # Full browser UA is mandatory: Revolut's WAF 403s bare/bot UAs even on
    # the _next/data JSON routes. `From` keeps the request attributable.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html;q=0.9",
    "Accept-Language": "en-GB,en;q=0.9",
    "x-nextjs-data": "1",
    "From": "yannickarieldossa@gmail.com",
}

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.0

_BUILD_ID_RE = re.compile(r'"buildId":"([^"]+)"')


@dataclass
class Job:
    native_job_id: str          # Revolut position UUID
    title: str
    location: str               # "; "-joined European location names
    category: str               # team ("Engineering" / "Data")
    apply_url: str
    description: str | None = None
    posted_date: str | None = None      # not exposed by the payload
    employment_type: str | None = None  # not exposed by the payload
    identifier: str | None = None
    raw_payload: dict | None = None


def _discover_build_id(session: requests.Session) -> str:
    """Read the current buildId out of a Next.js-rendered page's __NEXT_DATA__."""
    for url, ok_statuses in (
        (CAREERS_HTML_URL, (200,)),
        (BUILD_ID_FALLBACK_URL, (200, 404)),  # 404 is the healthy outcome here
    ):
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        if response.status_code not in ok_statuses:
            print(
                f"  buildId probe {url} -> HTTP {response.status_code}, "
                f"trying fallback",
                flush=True,
            )
            time.sleep(REQUEST_DELAY_SECONDS)
            continue
        match = _BUILD_ID_RE.search(response.text)
        if match:
            return match.group(1)
        print(f"  no buildId in {url}, trying fallback", flush=True)
        time.sleep(REQUEST_DELAY_SECONDS)
    raise RuntimeError("could not discover Next.js buildId (WAF block?)")


def _fetch_positions(session: requests.Session, build_id: str) -> list[dict]:
    url = LISTING_URL_TEMPLATE.format(build_id=build_id)
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    positions = (response.json().get("pageProps") or {}).get("positions")
    if not positions:
        raise RuntimeError(f"careers.json has no pageProps.positions ({url})")
    return positions


def _european_locations(doc: dict) -> list[str]:
    return [
        (loc.get("name") or "").strip()
        for loc in doc.get("locations") or []
        if (loc.get("country") or "").strip() in EUROPEAN_COUNTRIES
    ]


def _fetch_description(
    session: requests.Session, build_id: str, job_id: str
) -> str | None:
    url = DETAIL_URL_TEMPLATE.format(build_id=build_id, job_id=job_id)
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    position = (response.json().get("pageProps") or {}).get("position") or {}
    desc = position.get("description")
    return desc if isinstance(desc, str) and desc else None


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Discovering Next.js buildId...", flush=True)
    build_id = _discover_build_id(session)
    print(f"  buildId = {build_id}", flush=True)

    time.sleep(REQUEST_DELAY_SECONDS)
    print("Listing phase...", flush=True)
    docs = _fetch_positions(session, build_id)
    print(f"  {len(docs)} positions total (all geos, all teams)", flush=True)

    print("Filter phase...", flush=True)
    candidates: list[tuple[dict, list[str]]] = []
    dropped_team = 0
    dropped_geo = 0
    unknown_countries: set[str] = set()

    for doc in docs:
        if (doc.get("team") or "").strip() not in TEAMS_IN_SCOPE:
            dropped_team += 1
            continue
        euro_locs = _european_locations(doc)
        if not euro_locs:
            dropped_geo += 1
            unknown_countries.update(
                (loc.get("country") or "").strip()
                for loc in doc.get("locations") or []
            )
            continue
        candidates.append((doc, euro_locs))

    print(
        f"  kept {len(candidates)} "
        f"(dropped {dropped_team} off-team, {dropped_geo} outside Europe)",
        flush=True,
    )
    non_euro = unknown_countries - EUROPEAN_COUNTRIES
    if non_euro:
        print(f"  geo-dropped countries: {sorted(non_euro)}", flush=True)

    print(
        f"Enrichment phase: fetching {len(candidates)} detail payloads "
        f"(~{int(len(candidates) * REQUEST_DELAY_SECONDS)}s)...",
        flush=True,
    )

    kept: dict[str, Job] = {}
    failed = 0
    for i, (doc, euro_locs) in enumerate(candidates, 1):
        job_id = (doc.get("id") or "").strip()
        if not job_id:
            raise RuntimeError(f"position missing id (title={doc.get('text')!r})")
        if job_id in kept:
            continue

        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            desc = _fetch_description(session, build_id, job_id)
        except Exception as exc:
            # Description is non-essential — keep the row with desc=None.
            print(
                f"  [{i}/{len(candidates)}] {job_id[:8]} description fetch "
                f"FAILED: {type(exc).__name__}: {exc}",
                flush=True,
            )
            desc = None
            failed += 1

        kept[job_id] = Job(
            native_job_id=job_id,
            title=(doc.get("text") or "").strip(),
            location="; ".join(euro_locs),
            category=(doc.get("team") or "").strip(),
            apply_url=APPLY_URL_TEMPLATE.format(job_id=job_id),
            description=desc,
            raw_payload=doc,
        )
        print(
            f"  [{i}/{len(candidates)}] {job_id[:8]} "
            f"{kept[job_id].title!r} -> KEEP",
            flush=True,
        )

    elapsed = time.time() - started
    print(flush=True)
    print(
        f"  -> {len(kept)} jobs in {elapsed:.1f}s "
        f"({failed} description fetches failed)\n",
        flush=True,
    )
    return [asdict(j) for j in kept.values()]


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
    print(f"=== {len(jobs)} jobs final (total runtime {elapsed:.1f}s) ===\n")

    for j in jobs:
        desc = re.sub(r"<[^>]+>", " ", j["description"] or "")
        desc = re.sub(r"\s+", " ", desc).strip()
        desc = desc[:200] + ("..." if len(desc) > 200 else "")
        print(f"[{j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Location   : {j['location']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
