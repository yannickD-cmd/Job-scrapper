"""AXA job scraper — France, Data & AI / ML, CDI only.

Stack: careers.axa.com is a Jibe front-end (assets.jibecdn.com/prod/axa/) that
serves a public listing JSON at `/api/jobs`, fronting an iCIMS apply backend at
careers-en-axa.icims.com. The Jibe API returns one record per (req_id, language)
rendition with the full description embedded — no detail-page fetch needed.

Strategy:

1. For each Data/AI category name (3 variants — same role family translated into
   the AXA taxonomy in different languages), query `?country=France&categories=
   <name>&limit=100` and follow `?page=N` pagination.
2. Dedup by req_id, preferring the fr-fr rendition (better titles/descriptions
   for the French scope) and falling back to en-us / first-seen.
3. Filter the surviving renditions to tags2 in CDI_TAGS — both
   "Permanent contract" (en-us) and "Contrat permanent" (fr-fr) variants.

The server-side AND of multiple Jibe facets only matches when both facet values
are present on the SAME language rendition, which gives 0 results for cross-
language combos. That's why CDI is filtered client-side instead of via tags2=.

Categories deliberately exclude "INFORMATION TECHNOLOGY" (150 jobs total) even
though some ML/data roles may be filed there — the user's scope is the AXA-
declared Data/AI taxonomy, not a title-keyword fan-out.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

API_URL = "https://careers.axa.com/api/jobs"
JD_URL = "https://careers.axa.com/jobs/{req_id}"

# Same Data/AI family, three language renditions of the category name as it
# appears in AXA's taxonomy. Each must be queried separately — the API filters
# categories within a single language rendition.
DATA_CATEGORIES: list[str] = [
    "DATA AND AI",
    "DATA ET IA",
    "DONNÉES ET INTELLIGENCE ARTIFICIELLE",
]

# CDI variants. "Permanent contract" is the en-us label; "Contrat permanent" is
# the fr-fr label. Both map to "CDI" in AXA's France HR taxonomy.
CDI_TAGS: set[str] = {"Permanent contract", "Contrat permanent"}

PAGE_LIMIT = 100
MAX_PAGES = 20  # defensive cap; Data/AI subset is tiny (<30 rows total)

# robots.txt requests crawl-delay: 5
REQUEST_DELAY_SECONDS = 5.0
REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
}

LANG_PREFERENCE = ("fr-fr", "en-us")


@dataclass
class Job:
    native_job_id: str
    title: str
    location: str
    category: str
    apply_url: str
    description: str | None = None
    posted_date: str | None = None
    employment_type: str | None = None
    identifier: str | None = None
    raw_payload: dict | None = None


def _normalize_date(raw: str | None) -> str | None:
    """Jibe emits ISO timestamps like '2026-05-15T01:38:00+0000'. Keep date."""
    if not raw:
        return None
    return raw[:10] if len(raw) >= 10 else raw


def _strip_html(html: str | None) -> str:
    if not html:
        return ""
    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)


def _fetch_category(
    session: requests.Session, category: str
) -> list[dict]:
    """Return all raw job records for one (France, category) pair, all pages."""
    records: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        params = {
            "country": "France",
            "categories": category,
            "limit": PAGE_LIMIT,
            "page": page,
        }
        response = session.get(API_URL, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        page_jobs = payload.get("jobs") or []
        records.extend(page_jobs)
        print(
            f"  [{category}] page {page}: {len(page_jobs)} rendition(s)",
            flush=True,
        )
        if len(page_jobs) < PAGE_LIMIT:
            break
        time.sleep(REQUEST_DELAY_SECONDS)
    return records


def _pick_preferred_rendition(renditions: list[dict]) -> dict:
    """Pick fr-fr if available, else en-us, else the first seen."""
    by_lang = {r.get("language"): r for r in renditions}
    for lang in LANG_PREFERENCE:
        if lang in by_lang:
            return by_lang[lang]
    return renditions[0]


def _record_to_job(data: dict) -> Job:
    cats = [c.get("name", "") for c in data.get("categories", [])]
    return Job(
        native_job_id=str(data["req_id"]),
        title=data.get("title", "").strip(),
        location=data.get("full_location") or data.get("short_location") or "",
        category=" / ".join(c for c in cats if c),
        apply_url=JD_URL.format(req_id=data["req_id"]),
        description=data.get("description"),
        posted_date=_normalize_date(data.get("posted_date")),
        employment_type=", ".join(data.get("tags2") or []),
        identifier=str(data["req_id"]),
        raw_payload=data,
    )


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    print("Listing phase (3 category queries × France filter)...", flush=True)
    started = time.time()

    # req_id -> [rendition records across languages]
    renditions_by_req: dict[str, list[dict]] = {}

    for i, category in enumerate(DATA_CATEGORIES):
        if i > 0:
            time.sleep(REQUEST_DELAY_SECONDS)
        records = _fetch_category(session, category)
        for record in records:
            data = record.get("data") or {}
            req_id = data.get("req_id")
            if not req_id:
                continue
            renditions_by_req.setdefault(str(req_id), []).append(data)

    listing_elapsed = time.time() - started
    print(
        f"  → {len(renditions_by_req)} unique req_ids in {listing_elapsed:.1f}s\n",
        flush=True,
    )

    # CDI filter (any rendition's tags2 must match)
    kept: list[Job] = []
    dropped_by_reason: dict[str, int] = {}

    for req_id, renditions in renditions_by_req.items():
        matched_renditions = [
            r for r in renditions
            if any(t in CDI_TAGS for t in (r.get("tags2") or []))
        ]
        if not matched_renditions:
            # bucket the reason by whatever tags2 the first rendition had
            sample_tags = (renditions[0].get("tags2") or ["(none)"])
            label = " | ".join(sample_tags) or "(none)"
            dropped_by_reason[label] = dropped_by_reason.get(label, 0) + 1
            continue

        chosen = _pick_preferred_rendition(matched_renditions)
        kept.append(_record_to_job(chosen))

    print(f"CDI filter {sorted(CDI_TAGS)}:", flush=True)
    print(f"  kept    : {len(kept)}", flush=True)
    print(
        f"  dropped : {sum(dropped_by_reason.values())} "
        f"(by tags2: {dict(dropped_by_reason)})",
        flush=True,
    )

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
        desc_preview = _strip_html(j["description"])
        desc_preview = desc_preview[:200] + ("…" if len(desc_preview) > 200 else "")

        print(f"[{j['identifier'] or j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc_preview}")
        print()
