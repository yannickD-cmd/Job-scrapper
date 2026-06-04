"""Deloitte France job scraper — Consulting, tech/digital advisory subset, CDI only.

Single-pass JSON API. The board at deloitte.com/fr/fr/careers/content/job/
results.html is a SPA ("intconv", built by KMB Labs) whose results come from one
search endpoint:

    POST https://f6nv82mofd.execute-api.eu-west-1.amazonaws.com/prod/offres_v2
    header: x-api-key: <token baked into the public app.js>
    body  : {"size": N, "from": OFFSET}   (empty body = first page, all geos)

Every field we need is in that one response — description, city, contract type,
specialty, the Deloitte offer page (offer_url) and the underlying Workday apply
link (link). No detail-page enrichment, so this is a one-call scraper (paginated
defensively in case the board grows past one page).

SCOPE (locked with the user):
  * country == "France"                 (the API returns Maroc / UK / Monaco / ...
                                          African firms too — we keep France only)
  * contract_type == "CDI"
  * job_specialty_id in the Consulting tech/digital-advisory subset:
        Stratégies IT, Transformation ERP, IA/Data/Cloud, Cyber,
        Marketing digital, Information Technology
    (the full Consulting family also has pure-strategy / finance / M&A roles —
     those are dropped; see filters.md)

Taxonomy lives in two fields:
    activity_title  -> family   ("Consulting", "Audit", "Tax & Legal", ...)
    job_specialty_id-> specialty (stable, accent-free id we filter on)

Dedup key: `reference` (Workday req "R-####"), unique and also what offer_url
keys on. `date_publication` is refreshed to "today" for every offer (SEO), so it
is stored but not meaningful — dedup is by reference, so the noise is harmless
(same call as Orange).

To change scope, edit COUNTRY_IN_SCOPE / CONTRACT_TYPES_IN_SCOPE /
SPECIALTY_IDS_IN_SCOPE.
"""
from __future__ import annotations

import sys
import time
from dataclasses import asdict, dataclass

import requests

API_URL = "https://f6nv82mofd.execute-api.eu-west-1.amazonaws.com/prod/offres_v2"
# Public token shipped in the board's app.js (preprod.deloitte.intconv.kmblabs.com).
API_KEY = "JKT2pdDzG35s3MoPXwmy3TjLcCALbuj9SP6bTPt1"

OFFER_PAGE = "https://www.deloitte.com/fr/fr/careers/content/job/results/offer.html?ref={ref}"

COUNTRY_IN_SCOPE: set[str] = {"France"}
CONTRACT_TYPES_IN_SCOPE: set[str] = {"CDI"}

# Consulting tech/digital-advisory specialties. We filter on job_specialty_id
# (accent-free, stable) rather than the display title. The comment is the title.
SPECIALTY_IDS_IN_SCOPE: dict[str, str] = {
    "stratGiesIt": "Stratégies IT",
    "transformationErpSapOracleEmergingErp": "Transformation ERP (SAP, Oracle, Emerging ERP)",
    "iaDataCloud": "IA, Data & Cloud",
    "cyber": "Cyber",
    "marketingDigital": "Marketing digital",
    "InformationTechnology": "Information Technology",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
    "x-api-key": API_KEY,
    "Content-Type": "application/json",
}

REQUEST_DELAY_SECONDS = 1.0
REQUEST_TIMEOUT = 30
PAGE_SIZE = 200
MAX_PAGES = 20  # defensive cap: 20 * 200 = 4000 offers


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


def _fetch_page(session: requests.Session, offset: int, size: int) -> tuple[list[dict], int]:
    """One POST. Returns (results, total)."""
    response = session.post(
        API_URL,
        json={"size": size, "from": offset},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("error"):
        raise RuntimeError(f"offres_v2 returned error=true: {data}")
    return data.get("results", []), int(data.get("total", 0))


def _location(rec: dict) -> str | None:
    # `city` is the display name ("Paris La Défense"); fall back to the raw commune.
    return rec.get("city") or rec.get("city_name") or rec.get("country") or None


def _description(rec: dict) -> str | None:
    parts = [rec.get("description"), rec.get("additional_description")]
    joined = "\n\n".join(p for p in parts if p)
    return joined or None


def _posted_date(rec: dict) -> str | None:
    raw = rec.get("date_publication")  # ISO "2026-06-03T15:31:00.176Z" (SEO-refreshed)
    return raw[:10] if raw else None


def _to_job(rec: dict) -> Job:
    ref = rec.get("reference") or rec.get("id")
    return Job(
        native_job_id=ref,
        title=rec.get("jobname") or rec.get("exactjobname") or "",
        # offer_url is the canonical Deloitte posting; rebuild from ref if absent.
        apply_url=rec.get("offer_url") or OFFER_PAGE.format(ref=ref),
        description=_description(rec),
        location=_location(rec),
        category=rec.get("job_specialty") or rec.get("activity_title"),
        posted_date=_posted_date(rec),
        employment_type=rec.get("contract_type"),
        identifier=rec.get("id"),
        raw_payload=rec,
    )


def _in_scope(rec: dict) -> bool:
    return (
        rec.get("country") in COUNTRY_IN_SCOPE
        and rec.get("contract_type") in CONTRACT_TYPES_IN_SCOPE
        and rec.get("job_specialty_id") in SPECIALTY_IDS_IN_SCOPE
    )


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    print("Fetch phase (offres_v2)...", flush=True)
    raw: dict[str, dict] = {}  # dedup by reference across pages
    total = None
    started = time.time()

    for page in range(MAX_PAGES):
        offset = page * PAGE_SIZE
        results, total = _fetch_page(session, offset, PAGE_SIZE)
        if not results:
            break

        for rec in results:
            ref = rec.get("reference") or rec.get("id")
            if ref:
                raw.setdefault(ref, rec)

        print(
            f"  from={offset}: {len(results)} offers "
            f"({len(raw)}/{total} unique so far)",
            flush=True,
        )

        if offset + len(results) >= total:
            break  # collected everything the API reports
        time.sleep(REQUEST_DELAY_SECONDS)
    else:
        print(f"  hit MAX_PAGES={MAX_PAGES} cap", flush=True)

    print(
        f"  → {len(raw)} unique offers (all geos) in {time.time() - started:.1f}s\n",
        flush=True,
    )

    # Scope filter: France + CDI + tech-advisory specialty.
    kept = [_to_job(rec) for rec in raw.values() if _in_scope(rec)]

    by_specialty: dict[str | None, int] = {}
    for rec in raw.values():
        if rec.get("country") in COUNTRY_IN_SCOPE and rec.get("contract_type") in CONTRACT_TYPES_IN_SCOPE:
            sid = rec.get("job_specialty_id")
            if sid in SPECIALTY_IDS_IN_SCOPE:
                by_specialty[SPECIALTY_IDS_IN_SCOPE[sid]] = by_specialty.get(
                    SPECIALTY_IDS_IN_SCOPE[sid], 0
                ) + 1

    print(
        f"Scope filter (France · CDI · tech advisory): {len(kept)}/{len(raw)} kept",
        flush=True,
    )
    for title, n in sorted(by_specialty.items(), key=lambda kv: -kv[1]):
        print(f"    {n:3d}  {title}", flush=True)

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
        # crude tag-strip for the console preview only
        while "<" in desc and ">" in desc:
            a, b = desc.find("<"), desc.find(">")
            if a < b:
                desc = desc[:a] + " " + desc[b + 1:]
            else:
                break
        desc = " ".join(desc.split())[:200]
        print(f"[{j['native_job_id']}] {j['title']}")
        print(f"  Specialty  : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
