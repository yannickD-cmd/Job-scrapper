"""Natixis job scraper — France, Tech / Risk / Finance, CDI only.

Natixis recruitment runs on the shared BPCE-group platform (React SPA on top of
a WordPress REST backend). The /wp-json/ namespace is hidden behind /app/ on
this tenant — direct probes against the public host fall through to the SPA
shell and return HTML, not JSON.

Two-pass scrape:

1. ROUTES. One call to /app/wp-json/bpce/v1/routes lists every page the SPA
   knows about — including every open job, one per route of
   `component: "Template Job"`. Each route exposes a path
   (e.g. /job/<slug>) and a _uid like "job-19562-<hash>", where 19562 is the
   WordPress post id. The path slug is enough to short-circuit obvious
   non-CDI listings (alternance / stage / cdd-N-mois) before any detail fetch.

2. ENRICHMENT. For each surviving slug, fetch
   /app/wp-json/bpce/v1/posts/?lang=fr&slug=<slug>&_uid=<uid>. The response
   carries `content.top.criteria` (country, city, contract, sector, job) plus
   a JSON-LD JobPosting block under `content.microdatas`. We filter on
   criteria.country == "France", criteria.contract == "CDI",
   criteria.sector ∈ SECTORS_IN_SCOPE.

The same backend serves multiple BPCE brands (Banque Populaire, Caisse
d'Épargne, …) at their own subdomains. Each tenant exposes its own /app/
WP install, so this scraper is specific to recrutement.natixis.com.

To change scope, edit SECTORS_IN_SCOPE / CONTRACTS_IN_SCOPE / COUNTRIES_IN_SCOPE.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass

import requests

HOST = "https://recrutement.natixis.com"
ROUTES_URL = f"{HOST}/app/wp-json/bpce/v1/routes?lang=fr"
POST_URL = f"{HOST}/app/wp-json/bpce/v1/posts/"

# Job-family axis — Natixis uses `criteria.sector` for the high-level family.
# Mapping back to user scope:
#   Informatique                      → Software / IT (Data & AI sit here too —
#                                       Data Scientist IA F/H confirmed in
#                                       material/sample probes).
#   Risques Controles et Engagements  → Risk / Compliance.
#   Finance & Stratégie               → Quant / Finance-engineering / Finance.
#   Finance de marché                 → Market finance / asset management
#                                       (portfolio managers, etc.).
# (Strings reproduced exactly as the API emits them, typos included —
# "Controles" has no accent server-side.)
SECTORS_IN_SCOPE: set[str] = {
    "Informatique",
    "Risques Controles et Engagements",
    "Finance & Stratégie",
    "Finance de marché",
}

# Contract axis — keep CDI only (per current scope). Other observed values:
# "CDD", "Stage", "Contrat en alternance".
CONTRACTS_IN_SCOPE: set[str] = {"CDI"}

# Country axis — France only. Observed alternate value: "International".
COUNTRIES_IN_SCOPE: set[str] = {"France"}

# Slug-level pre-filter to skip obviously-non-CDI postings before fetching
# detail. Roughly two-thirds of Natixis postings are stages/alternances/CDD
# and their slugs reliably carry that marker — so this saves ~7min in CI.
# The final CDI check still runs on the detail payload.
NON_CDI_SLUG = re.compile(
    r"^(alternance|stage|apprentissage|vie|cdd|stagiaire)[-/]"
    r"|[-/](cdd|stage|alternance)[-/]"
    r"|[-/](cdd|stage|alternance)-\d"
)

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


def _fetch_routes(session: requests.Session) -> list[dict]:
    response = session.get(ROUTES_URL, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def _job_routes(routes: list[dict]) -> list[dict]:
    return [r for r in routes if r.get("component") == "Template Job"]


def _post_id_from_uid(uid: str) -> str | None:
    """`_uid` format is `job-<post_id>-<hash>`. Extract <post_id>."""
    parts = uid.split("-")
    if len(parts) >= 2 and parts[0] == "job" and parts[1].isdigit():
        return parts[1]
    return None


def _fetch_detail(session: requests.Session, slug: str, uid: str) -> dict | None:
    params = {"lang": "fr", "slug": slug, "_uid": uid}
    response = session.get(POST_URL, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or "content" not in data:
        return None
    return data


def _build_job(route: dict, payload: dict) -> Job | None:
    uid = route.get("_uid", "")
    post_id = _post_id_from_uid(uid)
    if not post_id:
        return None

    content = payload.get("content", {})
    top = content.get("top", {})
    criteria = top.get("criteria", {})
    microdatas = content.get("microdatas", {})

    title = top.get("title") or microdatas.get("title") or ""
    canonical = payload.get("seo_link", {}).get("canonical")
    apply_url = canonical or f"{HOST}{route.get('path', '')}"

    city = (criteria.get("city") or "").strip()
    country = (criteria.get("country") or "").strip()
    location = ", ".join(p for p in (city, country) if p) or None

    sector = (criteria.get("sector") or "").strip()
    job_family = (criteria.get("job") or "").strip()
    category = " — ".join(p for p in (sector, job_family) if p) or None

    return Job(
        native_job_id=post_id,
        title=title,
        apply_url=apply_url,
        description=content.get("main", {}).get("text"),
        location=location,
        category=category,
        posted_date=microdatas.get("datePosted") or None,
        employment_type=(criteria.get("contract")
                         or microdatas.get("employmentType")
                         or None),
        identifier=str(top.get("opening_id") or top.get("job_number") or post_id),
        raw_payload={
            "criteria": criteria,
            "microdatas": microdatas,
            "post_id": top.get("post_id"),
            "opening_id": top.get("opening_id"),
        },
    )


def _is_in_scope(job: Job, payload: dict) -> tuple[bool, str]:
    """Returns (keep, reason). reason describes the rejection axis."""
    criteria = payload.get("content", {}).get("top", {}).get("criteria", {})
    country = (criteria.get("country") or "").strip()
    contract = (criteria.get("contract") or "").strip()
    sector = (criteria.get("sector") or "").strip()

    if country not in COUNTRIES_IN_SCOPE:
        return False, f"country={country!r}"
    if contract not in CONTRACTS_IN_SCOPE:
        return False, f"contract={contract!r}"
    if sector not in SECTORS_IN_SCOPE:
        return False, f"sector={sector!r}"
    return True, ""


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()

    print("Routes phase...", flush=True)
    routes = _fetch_routes(session)
    all_jobs = _job_routes(routes)
    print(f"  {len(routes)} routes total, {len(all_jobs)} jobs", flush=True)

    candidates = [
        r for r in all_jobs
        if not NON_CDI_SLUG.search(r.get("path", "").rsplit("/", 1)[-1].lower())
    ]
    skipped_slugs = len(all_jobs) - len(candidates)
    print(
        f"  slug pre-filter (drop alternance/stage/cdd-N): "
        f"{len(candidates)} candidates, {skipped_slugs} dropped\n",
        flush=True,
    )

    print(
        f"Enrichment phase: {len(candidates)} detail fetches "
        f"(~{int(len(candidates) * REQUEST_DELAY_SECONDS)}s)...",
        flush=True,
    )

    kept: list[Job] = []
    rejected: dict[str, int] = {}
    failed = 0

    for i, route in enumerate(candidates, 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        slug = route.get("path", "").rsplit("/", 1)[-1]
        uid = route.get("_uid", "")

        try:
            payload = _fetch_detail(session, slug, uid)
        except Exception as exc:
            print(f"  [{i}/{len(candidates)}] {slug} FAILED: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            failed += 1
            continue

        if payload is None:
            print(f"  [{i}/{len(candidates)}] {slug} no content", flush=True)
            failed += 1
            continue

        job = _build_job(route, payload)
        if job is None:
            print(f"  [{i}/{len(candidates)}] {slug} could not build Job", flush=True)
            failed += 1
            continue

        keep, reason = _is_in_scope(job, payload)
        if keep:
            kept.append(job)
            marker = "KEEP"
        else:
            rejected[reason] = rejected.get(reason, 0) + 1
            marker = f"drop ({reason})"

        print(f"  [{i}/{len(candidates)}] {job.identifier} {job.title!r} → {marker}",
              flush=True)

    elapsed = time.time() - started
    print(flush=True)
    print(f"Filters {sorted(COUNTRIES_IN_SCOPE)} × {sorted(CONTRACTS_IN_SCOPE)} "
          f"× {sorted(SECTORS_IN_SCOPE)}:", flush=True)
    print(f"  kept    : {len(kept)}", flush=True)
    print(f"  dropped : {sum(rejected.values())}", flush=True)
    print(f"  failed  : {failed}", flush=True)
    print(f"  runtime : {elapsed:.1f}s", flush=True)

    if rejected:
        top_rejections = sorted(rejected.items(), key=lambda kv: -kv[1])[:10]
        print("  top reject reasons:", flush=True)
        for r, n in top_rejections:
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
        # rough HTML strip for the preview only
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
