"""Sopra Steria job scraper — France, IDF cities, Data/AI/ML/Cloud, Standard only.

The careers portal runs on Attrax (custom CMS, Azure Front Door). Filter
application is a POST guarded by an ASP.NET antiforgery token, so we don't
narrow server-side. Instead:

1. LISTING. Walk all pages of `?size=48&page=N` and parse every
   `<div class="attrax-vacancy-tile">`. Each tile carries class modifiers
   for country/city/job-type and a structured `__option-department` text
   block, which is enough to apply scope without a detail-page fetch.

2. ENRICHMENT. For each tile that passes the scope filter, fetch the
   detail page and read the `<script type="application/ld+json">`
   JobPosting block: description, datePosted, employmentType, identifier
   UUID, locality.

`?size=48` is the largest page size the portal honours; ~25 pages total.

Scope rule (hybrid — Sopra Steria's department tagging is loose, most
Data/Cloud roles in France sit under "Engineering, Development,
Applications" rather than their own dept):

  Country = France (tile class `--france`)
  Type    = Standard (tile class `--standard`)
  City    = freetext-location prefix in ALLOWED_CITIES
            (Courbevoie / Paris / Le Plessis-Robinson / Montreuil).
            We can't use the `--<city>` class because a "Toutes Régions"
            job carries every French city's class — see jid-5433.
  Department:
    - in DATA_CLOUD_DEPARTMENTS                       → keep
    - in TITLE_FALLBACK_DEPARTMENTS  AND title regex  → keep
    - else                                            → drop

Native ID: numeric `jid-<id>` from the apply URL (also `data-jobid` on the
tile). The other surface — JSON-LD `identifier.value` — is a UUID; we
expose that as `identifier`.
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

HOST = "https://careers.soprasteria.com"
LISTING_URL = HOST + "/jobs?size=48&page={page}"

COUNTRY_CLASSES: set[str] = {"attrax-vacancy-tile--france"}
JOB_TYPE_CLASSES: set[str] = {"attrax-vacancy-tile--standard"}

# City filter is text-based against the tile's freetext "Location" field
# (e.g. "Courbevoie, France"). Class-based filtering is unreliable: a
# "Toutes Régions" job carries every French city's `--<city>` modifier,
# so it would slip through any class-based city allowlist.
ALLOWED_CITIES: set[str] = {
    "courbevoie",
    "paris",
    "le plessis-robinson",
    "le plessis robinson",
    "montreuil",
}

# Departments that are unambiguously data/cloud — always keep.
DATA_CLOUD_DEPARTMENTS: set[str] = {
    "Data",
    "Data Analytics",
    "Infrastructure & Cloud",
    "Infrastructure & Cloud Services",
}

# Catch-all departments where data/cloud/AI/ML roles also live but get
# mixed in with everything else — keep only if the title matches the
# keyword regex.
TITLE_FALLBACK_DEPARTMENTS: set[str] = {
    "Engineering, Development, Applications",
    "Tech",
    "Software",
}

TITLE_KEYWORD_RE = re.compile(
    r"\b("
    r"data|big[\s-]?data|datalake|data[\s-]?ops|datahub|"
    r"ai|ia|gen[\s-]?ai|gen[\s-]?ia|"  # IA = Intelligence Artificielle (French)
    r"intelligence[\s-]artificielle|"
    r"ml|ml[\s-]?ops|"
    r"machine[\s-]?learning|deep[\s-]?learning|nlp|llm|"
    r"cloud|aws|azure|gcp|"
    r"kubernetes|k8s|devops|sre|"
    r"databricks|snowflake|"
    r"pyspark|spark|hadoop|kafka|airflow|dbt|"
    r"analytics|analyste\s+data|data[\s-]?(?:engineer|analyst|scientist|architect)"
    r")\b",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_DELAY_SECONDS = 2.0
REQUEST_TIMEOUT = 30
MAX_PAGES = 80  # safety cap; real value is ~25

JID_RE = re.compile(r"-jid-(\d+)$")


@dataclass
class Job:
    native_job_id: str
    title: str
    location: str
    category: str
    apply_url: str
    brand: str | None = None
    description: str | None = None
    posted_date: str | None = None
    employment_type: str | None = None
    identifier: str | None = None
    raw_payload: dict | None = None


def _tile_text(tile, option_class: str) -> str | None:
    block = tile.select_one(f".attrax-vacancy-tile__option-{option_class}")
    if not block:
        return None
    val = block.select_one(".attrax-vacancy-tile__item-value")
    return val.get_text(strip=True) if val else None


def _is_in_scope(department: str, title: str) -> bool:
    if department in DATA_CLOUD_DEPARTMENTS:
        return True
    if department in TITLE_FALLBACK_DEPARTMENTS and TITLE_KEYWORD_RE.search(title):
        return True
    return False


def _city_match(location: str) -> bool:
    """Match the city token before the first comma in the freetext location."""
    if not location:
        return False
    head = location.split(",", 1)[0].strip().lower()
    return head in ALLOWED_CITIES


def _parse_listing_page(html: str) -> tuple[list[Job], int]:
    soup = BeautifulSoup(html, "html.parser")

    jobs: list[Job] = []
    for tile in soup.select("div.attrax-vacancy-tile"):
        classes = set(tile.get("class") or [])

        if not (COUNTRY_CLASSES & classes):
            continue
        if not (JOB_TYPE_CLASSES & classes):
            continue

        loc_block = tile.select_one(
            ".attrax-vacancy-tile__location-freetext .attrax-vacancy-tile__item-value"
        )
        location = loc_block.get_text(strip=True) if loc_block else ""
        if not _city_match(location):
            continue

        title_a = tile.select_one("a.attrax-vacancy-tile__title")
        if not title_a:
            continue
        title = title_a.get_text(strip=True)
        department = _tile_text(tile, "department") or ""

        if not _is_in_scope(department, title):
            continue

        href = title_a.get("href") or ""
        m = JID_RE.search(href)
        if not m:
            continue
        native_job_id = m.group(1)

        jobs.append(Job(
            native_job_id=native_job_id,
            title=title,
            location=location,
            category=department,
            apply_url=HOST + href if href.startswith("/") else href,
            brand=_tile_text(tile, "brand"),
        ))

    # Pagination: prefer the "Last" link's pagination(N).
    total_pages = 1
    last_a = soup.select_one(".attrax-pagination__last a")
    if last_a:
        m = re.search(r"pagination\((\d+)\)", last_a.get("href") or "")
        if m:
            total_pages = int(m.group(1))
    if total_pages == 1:
        page_nums = [
            int(m.group(1))
            for a in soup.select(".attrax-pagination__page-item a")
            for m in [re.search(r"pagination\((\d+)\)", a.get("href") or "")]
            if m
        ]
        if page_nums:
            total_pages = max(page_nums)

    return jobs, total_pages


def _parse_detail_payload(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    return None


def _normalize_date(raw: str | None) -> str | None:
    if not raw:
        return None
    return raw.split("T", 1)[0]


def _coerce_employment_type(raw) -> str | None:
    if not raw:
        return None
    if isinstance(raw, list):
        return raw[0] if raw else None
    return str(raw)


def _coerce_location(payload: dict) -> str | None:
    loc = payload.get("jobLocation")
    if isinstance(loc, list) and loc:
        loc = loc[0]
    if isinstance(loc, dict):
        addr = loc.get("address") or {}
        if isinstance(addr, dict):
            return addr.get("addressLocality") or None
    return None


def _coerce_identifier(payload: dict) -> str | None:
    ident = payload.get("identifier")
    if isinstance(ident, dict):
        return ident.get("value") or None
    return ident or None


def _enrich(session: requests.Session, job: Job) -> bool:
    response = session.get(job.apply_url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    payload = _parse_detail_payload(response.text)
    if not payload:
        return False

    job.description = payload.get("description")
    job.posted_date = _normalize_date(payload.get("datePosted"))
    job.employment_type = _coerce_employment_type(payload.get("employmentType"))
    job.identifier = _coerce_identifier(payload)
    if not job.location:
        job.location = _coerce_location(payload) or ""
    job.raw_payload = payload
    return True


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    print("Listing phase...", flush=True)
    in_scope: dict[str, Job] = {}
    page = 1
    total_pages = 1
    started = time.time()

    while page <= total_pages and page <= MAX_PAGES:
        url = LISTING_URL.format(page=page)
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        page_jobs, total_pages = _parse_listing_page(response.text)
        for j in page_jobs:
            in_scope.setdefault(j.native_job_id, j)

        print(
            f"  page {page}/{total_pages}: "
            f"{len(page_jobs)} in-scope tiles "
            f"({len(in_scope)} unique so far)",
            flush=True,
        )

        page += 1
        if page <= total_pages and page <= MAX_PAGES:
            time.sleep(REQUEST_DELAY_SECONDS)

    listing_elapsed = time.time() - started
    print(
        f"  → {len(in_scope)} unique in-scope jobs after "
        f"{page - 1} pages in {listing_elapsed:.1f}s\n",
        flush=True,
    )

    print(
        f"Enrichment phase: fetching {len(in_scope)} detail pages "
        f"(~{int(len(in_scope) * REQUEST_DELAY_SECONDS)}s)...",
        flush=True,
    )

    kept: list[Job] = []
    failed = 0
    for i, job in enumerate(in_scope.values(), 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            ok = _enrich(session, job)
        except Exception as exc:
            print(
                f"  [{i}/{len(in_scope)}] {job.native_job_id} FAILED: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            failed += 1
            continue

        if not ok:
            print(
                f"  [{i}/{len(in_scope)}] {job.native_job_id} no JSON-LD found",
                flush=True,
            )
            failed += 1
            continue

        kept.append(job)
        print(
            f"  [{i}/{len(in_scope)}] {job.identifier or job.native_job_id} "
            f"[{job.category}] {job.title!r} → KEEP",
            flush=True,
        )

    print(flush=True)
    print(f"Final: kept={len(kept)} failed={failed}", flush=True)

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
        desc_preview = desc_preview[:160] + ("…" if len(desc_preview) > 160 else "")

        print(f"[{j['identifier'] or j['native_job_id']}] {j['title']}")
        print(f"  Brand      : {j['brand']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc_preview}")
        print()
