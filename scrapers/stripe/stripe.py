"""Stripe job scraper — UK / Ireland / France / Luxembourg / Switzerland (+ remote
in those countries), Data/AI/ML + Software Engineering, full-time + intern/apprentice.

Two-pass scrape (Sanofi-style):

1. LISTING. Walks every page of stripe.com/jobs/search (?skip=0,100,200,...).
   Each <tr class="TableRow"> carries everything we need to FILTER: the role
   title + numeric listing id, the team(s), the location display name, and a
   country flag whose alt is the ISO country code. We filter on the listing
   itself — geo (country code in scope) AND a title-keyword filter — before any
   detail-page fetches, so we only enrich the handful that survive.

2. ENRICHMENT. For each surviving listing, fetch the detail page and read the
   JobDetailCard properties (office locations / remote locations / team / job
   type) plus the JD body. There is NO JSON-LD and NO posted date anywhere on
   Stripe pages, so posted_date stays None — harmless, dedup is by
   native_job_id. The job-type ("Full time" / "Internship" / ...) gives the
   employment-type filter.

Why a title filter and not a team filter: Stripe's "teams" are product areas
(Payments, Money Movement, ...), each mixing engineers, PMs, designers and
sales. Team alone can't separate Software/Data from GTM, so the precise axis is
the title keyword. See filters.md.

To change scope, edit COUNTRY_CODES_IN_SCOPE / TITLE_INCLUDE / TITLE_EXCLUDE /
JOB_TYPES_IN_SCOPE.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

HOST = "https://stripe.com"
LISTING_URL = "https://stripe.com/jobs/search?skip={skip}"
PAGE_SIZE = 100

# Geo: ISO codes from the flag alt on each listing row. Captures both office
# roles (London/Dublin/Paris/Luxembourg/Swiss cities) and "Remote in <country>"
# roles for the same countries.
COUNTRY_CODES_IN_SCOPE: set[str] = {"GB", "IE", "FR", "LU", "CH"}

# Title filter — Software Engineering + Data/AI/ML. Keep when TITLE_INCLUDE
# matches and TITLE_EXCLUDE does not (the exclude kills GTM/pre-sales roles that
# still contain "engineer", e.g. Sales Engineer / Solutions Architect).
TITLE_INCLUDE = re.compile(
    r"engineer|engineering|software|developer|back[\s-]?end|front[\s-]?end|"
    r"full[\s-]?stack|\bSRE\b|site reliability|devops|"
    r"data scien|data engineer|data analyst|analytics|"
    r"machine learning|\bML\b|\bAI\b|artificial intelligence|applied scien",
    re.I,
)
TITLE_EXCLUDE = re.compile(
    r"sales|account executive|\bAE\b|account manager|"
    r"solutions? engineer|sales engineer|solutions? architect|"
    r"solutions? consultant|customer engineer|pre[\s-]?sales|"
    r"developer advocate|developer relations|evangelist|"
    r"program manager|product manager|professional services|"
    r"marketing|recruit|partner|success|"
    r"support engineer|product support|customer support|technical support",
    re.I,
)

# Employment types in scope. Stripe job types are "Full time", "Internship",
# "Apprenticeship", etc. Unknown/None types are KEPT (logged), never silently
# dropped — only an explicitly out-of-scope type is dropped.
JOB_TYPES_IN_SCOPE: set[str] = {"Full time", "Internship", "Apprenticeship"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_DELAY_SECONDS = 2.0
REQUEST_TIMEOUT = 30
MAX_PAGES = 20  # defensive cap: 20 * 100 = 2000 rows


@dataclass
class Job:
    native_job_id: str
    title: str
    location: str
    category: str  # Stripe "team(s)" from the listing
    apply_url: str
    country_code: str | None = None
    # Filled by detail-page enrichment:
    description: str | None = None
    posted_date: str | None = None  # Stripe never publishes one
    employment_type: str | None = None
    office_locations: str | None = None
    remote_locations: str | None = None
    identifier: str | None = None
    raw_payload: dict | None = None


def _job_id_from_href(href: str) -> str:
    # /jobs/listing/account-executive-enterprise-germany/7825578 -> 7825578
    return href.rstrip("/").rsplit("/", 1)[-1]


def _parse_listing_page(html: str) -> list[Job]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[Job] = []

    for row in soup.select("tr.TableRow"):
        anchor = row.select_one("a.JobsListings__link")
        if not anchor:
            continue  # header row / spacer

        href = anchor.get("href") or ""
        if "/jobs/listing/" not in href:
            continue

        title = anchor.get_text(strip=True)
        job_id = _job_id_from_href(href)
        apply_url = HOST + href if href.startswith("/") else href

        teams = [
            li.get_text(" ", strip=True)
            for li in row.select(".JobsListings__departmentsListItem")
        ]
        category = ", ".join(t for t in teams if t)

        loc_el = row.select_one(".JobsListings__locationDisplayName")
        location = loc_el.get_text(" ", strip=True) if loc_el else ""

        country_code = None
        flag = row.select_one("img.Flag")
        if flag:
            m = re.search(r"Flag--country([A-Z]{2})", " ".join(flag.get("class", [])))
            if m:
                country_code = m.group(1)

        jobs.append(Job(
            native_job_id=job_id,
            title=title,
            location=location,
            category=category,
            apply_url=apply_url,
            country_code=country_code,
        ))

    return jobs


def _in_title_scope(title: str) -> bool:
    return bool(TITLE_INCLUDE.search(title)) and not TITLE_EXCLUDE.search(title)


def _card_property(soup: BeautifulSoup, kind: str) -> str | None:
    """Read one JobDetailCard property by its icon kind (office/remote/team/time)."""
    for prop in soup.select(".JobDetailCardProperty"):
        icon = prop.select_one("svg.BasicIcon")
        kinds = [
            c.replace("BasicIcon--", "")
            for c in (icon.get("class", []) if icon else [])
            if c.startswith("BasicIcon--")
        ]
        if kind not in kinds:
            continue
        title_el = prop.select_one(".JobDetailCardProperty__title")
        if title_el:
            title_el.extract()  # drop the label, keep the value
        value = prop.get_text(" ", strip=True)
        return value or None
    return None


def _enrich(session: requests.Session, job: Job) -> bool:
    """Fetch detail page; fill description / job type / office+remote. True on success."""
    response = session.get(job.apply_url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    job.office_locations = _card_property(soup, "office")
    job.remote_locations = _card_property(soup, "remote")
    job.employment_type = _card_property(soup, "time")
    team = _card_property(soup, "team")
    if team:
        job.category = team  # detail team is authoritative over the listing cell

    body = soup.select_one("section.JobsBodySection")
    if body:
        for junk in body.select(
            ".JobDetailCardProperty, .JobDetailCard__buttonContainer, .JobDetailCard"
        ):
            junk.extract()
        job.description = body.get_text("\n", strip=True) or None

    job.identifier = job.native_job_id
    job.raw_payload = {
        "office_locations": job.office_locations,
        "remote_locations": job.remote_locations,
        "team": job.category,
        "job_type": job.employment_type,
        "country_code": job.country_code,
    }
    return body is not None


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    print("Listing phase...", flush=True)
    all_listings: dict[str, Job] = {}  # dedup by native_job_id
    started = time.time()

    for page in range(MAX_PAGES):
        skip = page * PAGE_SIZE
        url = LISTING_URL.format(skip=skip)
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        page_jobs = _parse_listing_page(response.text)
        if not page_jobs:
            break

        for j in page_jobs:
            all_listings.setdefault(j.native_job_id, j)

        print(
            f"  skip={skip}: {len(page_jobs)} rows "
            f"({len(all_listings)} unique so far)",
            flush=True,
        )

        if len(page_jobs) < PAGE_SIZE:
            break  # last (partial) page
        time.sleep(REQUEST_DELAY_SECONDS)
    else:
        print(f"  hit MAX_PAGES={MAX_PAGES} cap", flush=True)

    print(
        f"  → {len(all_listings)} unique jobs in {time.time() - started:.1f}s\n",
        flush=True,
    )

    # Filter on listing data: geo first, then title.
    geo_ok = [
        j for j in all_listings.values()
        if j.country_code in COUNTRY_CODES_IN_SCOPE
    ]
    print(
        f"Geo filter {sorted(COUNTRY_CODES_IN_SCOPE)}: "
        f"{len(geo_ok)}/{len(all_listings)} kept",
        flush=True,
    )

    in_scope = [j for j in geo_ok if _in_title_scope(j.title)]
    print(
        f"Title filter (Software/Data): {len(in_scope)}/{len(geo_ok)} kept\n",
        flush=True,
    )

    # Enrich survivors + employment-type filter.
    print(
        f"Enrichment phase: fetching {len(in_scope)} detail pages "
        f"(~{int(len(in_scope) * REQUEST_DELAY_SECONDS)}s)...",
        flush=True,
    )

    kept: list[Job] = []
    dropped_by_type: dict[str | None, int] = {}
    failed = 0

    for i, job in enumerate(in_scope, 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            ok = _enrich(session, job)
        except Exception as exc:
            print(f"  [{i}/{len(in_scope)}] {job.native_job_id} FAILED: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            failed += 1
            continue

        if not ok:
            print(f"  [{i}/{len(in_scope)}] {job.native_job_id} no JD body found",
                  flush=True)

        et = job.employment_type
        if et is None or et in JOB_TYPES_IN_SCOPE:
            kept.append(job)
            marker = "KEEP" + ("" if et else " (type?)")
        else:
            dropped_by_type[et] = dropped_by_type.get(et, 0) + 1
            marker = f"drop ({et})"

        print(f"  [{i}/{len(in_scope)}] {job.native_job_id} {job.title!r} "
              f"[{job.location}] → {marker}", flush=True)

    print(flush=True)
    print(f"Job-type filter {sorted(JOB_TYPES_IN_SCOPE)}:", flush=True)
    print(f"  kept    : {len(kept)}", flush=True)
    print(f"  dropped : {sum(dropped_by_type.values())} "
          f"(by type: {dict(dropped_by_type)})", flush=True)
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

    print(f"\n=== {len(jobs)} jobs final (total runtime {time.time() - started:.1f}s) ===\n")
    for j in jobs:
        desc = (j["description"] or "").strip().replace("\n", " ")
        desc = desc[:200] + ("…" if len(desc) > 200 else "")
        print(f"[{j['native_job_id']}] {j['title']}")
        print(f"  Team       : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']} ({j['country_code']})")
        print(f"  Office/Rem : {j['office_locations']} | {j['remote_locations']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
