"""Orange job scraper — France, Data & AI, Permanent (CDI) only.

Orange runs Phenom People as their ATS. The on-site job search is fully
client-side, and the `/api/jobs` endpoint that other Phenom installs
expose returns 500 here. Instead we read the public sitemap, which
links every job detail page, and parse each page's embedded
schema.org/JobPosting JSON-LD block (same pattern as Sanofi and BNP).

Two-pass scrape:

1. LISTING. Fetch the 3 sub-sitemaps under https://orange.jobs/gb/en/.
   Each `/job/<native_id>/<slug>` URL is one job. native_id is the
   stable Orange req-id (e.g. `IOS-2026-1935`, `ICM-585101`).
   A cheap slug-based pre-filter drops obvious non-permanent postings
   (Alternance, Stage, CDD) before we burn any detail-page fetches.

2. ENRICHMENT. For each surviving listing, fetch the detail page and
   read the JSON-LD JobPosting. We then drop anything whose:
     - addressCountry != FRANCE, or
     - occupationalCategory != "IT & Engineering", or
     - title matches the non-permanent regex (belt-and-suspenders;
       catches alternance postings whose slug doesn't tag itself).

   We don't filter on `employmentType` because Orange emits it
   inconsistently — same site shows "Full-time", "FULL_TIME", and
   "OTHER" for ostensibly similar roles.

To change scope, edit SCOPE_COUNTRY, SCOPE_CATEGORY, or NON_PERMANENT_RE.
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

HOST = "https://orange.jobs"
SITEMAPS = [
    f"{HOST}/gb/en/sitemap1.xml",
    f"{HOST}/gb/en/sitemap2.xml",
    f"{HOST}/gb/en/sitemap3.xml",
]

SCOPE_COUNTRY = "FRANCE"
SCOPE_CATEGORIES: set[str] = {"Data & AI"}
SCOPE_EMPLOYMENT_TYPE = "Permanent"  # documented, not enforced via JSON-LD

# Words that mark a posting as non-permanent. Applied to both the URL
# slug (cheap pre-filter) and the JobPosting title (final filter).
# Case-insensitive, word-boundary — \b also matches hyphens in slugs.
NON_PERMANENT_RE = re.compile(
    r"\b(?:"
    r"alternance|alternant|alternants|alternante|alternantes"
    r"|stage|stages|stagiaire|stagiaires|internship"
    r"|apprenti|apprentie|apprentissage"
    r"|cdd"
    r"|contrat[\s\-]de[\s\-]professionnalisation"
    r"|vie"
    r"|professional[\s\-]contract"
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

REQUEST_DELAY_SECONDS = 1.0
REQUEST_TIMEOUT = 30


@dataclass
class Job:
    native_job_id: str          # Orange req-id from URL path
    title: str
    location: str
    category: str
    apply_url: str
    employment_type: str        # always SCOPE_EMPLOYMENT_TYPE for this scraper
    # Filled by detail-page enrichment:
    description: str | None = None
    posted_date: str | None = None
    identifier: str | None = None
    raw_payload: dict | None = None


def _native_id_from_url(url: str) -> str | None:
    """Pull the Orange req-id out of `…/job/<id>/<slug>`."""
    m = re.search(r"/job/([^/]+)/", url)
    return m.group(1) if m else None


def _fetch_listing_urls(session: requests.Session) -> list[str]:
    urls: list[str] = []
    for sm in SITEMAPS:
        response = session.get(sm, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        urls.extend(
            re.findall(r"<loc>([^<]+/job/[^<]+)</loc>", response.text)
        )
        time.sleep(REQUEST_DELAY_SECONDS)
    # Dedupe while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _parse_detail_payload(html: str) -> dict | None:
    """Return the schema.org/JobPosting JSON-LD block, or None."""
    soup = BeautifulSoup(html, "html.parser")
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


def _first_dict(value) -> dict:
    """JSON-LD often emits `jobLocation` as either a dict or a list of dicts."""
    if isinstance(value, list):
        return value[0] if value and isinstance(value[0], dict) else {}
    return value if isinstance(value, dict) else {}


def _extract_country(payload: dict) -> str:
    loc = _first_dict(payload.get("jobLocation"))
    addr = _first_dict(loc.get("address"))
    return (addr.get("addressCountry") or "").strip()


def _extract_location_text(payload: dict) -> str:
    """City, region — for display in our DB."""
    loc = _first_dict(payload.get("jobLocation"))
    addr = _first_dict(loc.get("address"))
    parts = [
        addr.get("addressLocality"),
        addr.get("addressRegion"),
    ]
    return ", ".join(p for p in parts if p)


def _extract_identifier(payload: dict) -> str | None:
    """Orange (like BNP) emits identifier as a PropertyValue object."""
    ident = payload.get("identifier")
    if isinstance(ident, dict):
        v = ident.get("value")
        if isinstance(v, str) and v:
            return v
    if isinstance(ident, str) and ident:
        return ident
    return None


def _enrich(session: requests.Session, url: str) -> dict | None:
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return _parse_detail_payload(response.text)


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    # Listing phase
    print("Listing phase...", flush=True)
    started = time.time()
    urls = _fetch_listing_urls(session)
    print(f"  {len(urls)} job URLs from sitemap in {time.time()-started:.1f}s",
          flush=True)

    # Slug pre-filter — drop obvious non-permanent before any detail fetch
    candidates: list[str] = []
    dropped_by_slug = 0
    for u in urls:
        slug = u.rsplit("/", 1)[-1]
        if NON_PERMANENT_RE.search(slug):
            dropped_by_slug += 1
            continue
        candidates.append(u)
    print(f"  slug pre-filter: dropped {dropped_by_slug} non-permanent, "
          f"{len(candidates)} to enrich\n", flush=True)

    # Enrichment phase
    print(
        f"Enrichment phase: fetching {len(candidates)} detail pages "
        f"(~{int(len(candidates) * REQUEST_DELAY_SECONDS / 60)} min)...",
        flush=True,
    )

    kept: list[Job] = []
    drops = {"country": 0, "category": 0, "non_permanent": 0, "no_jsonld": 0}
    failed = 0

    for i, url in enumerate(candidates, 1):
        time.sleep(REQUEST_DELAY_SECONDS)

        native_id = _native_id_from_url(url) or url
        try:
            payload = _enrich(session, url)
        except Exception as exc:
            print(f"  [{i}/{len(candidates)}] {native_id} FAILED: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            failed += 1
            continue

        if not payload:
            drops["no_jsonld"] += 1
            continue

        country = _extract_country(payload)
        if country != SCOPE_COUNTRY:
            drops["country"] += 1
            continue

        category = payload.get("occupationalCategory") or ""
        if category not in SCOPE_CATEGORIES:
            drops["category"] += 1
            continue

        title = payload.get("title") or ""
        if NON_PERMANENT_RE.search(title):
            drops["non_permanent"] += 1
            continue

        kept.append(Job(
            native_job_id=native_id,
            title=title,
            location=_extract_location_text(payload),
            category=category,
            apply_url=url,
            employment_type=SCOPE_EMPLOYMENT_TYPE,
            description=payload.get("description"),
            posted_date=payload.get("datePosted"),
            identifier=_extract_identifier(payload),
            raw_payload=payload,
        ))
        print(f"  [{i}/{len(candidates)}] {kept[-1].identifier or native_id} "
              f"{title!r} -> KEEP", flush=True)

    print(flush=True)
    print("Enrichment summary:", flush=True)
    print(f"  kept              : {len(kept)}", flush=True)
    print(f"  dropped (country) : {drops['country']}", flush=True)
    print(f"  dropped (category): {drops['category']}", flush=True)
    print(f"  dropped (non-CDI) : {drops['non_permanent']}", flush=True)
    print(f"  no JSON-LD        : {drops['no_jsonld']}", flush=True)
    print(f"  failed            : {failed}", flush=True)

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
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc_preview}")
        print()
