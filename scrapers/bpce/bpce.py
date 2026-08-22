"""BPCE group job scraper — France, Informatique + Digital, CDI only.

recrutement.bpce.fr is the group-wide board: Banque Populaire, Caisse
d'Épargne, Casden, BPCE SA, BPCE Solutions Informatiques, Oney, Estreem,
Palatine… ~1550 open postings, of which ~970 are retail-branch commercial
roles.

It runs the same BPCE WordPress platform as recrutement.natixis.com (React
SPA, `/app/wp-json/bpce/v1/` namespace — the `/app/` prefix is mandatory,
the bare `/wp-json/` path falls through to the SPA shell and returns HTML).
Unlike the Natixis tenant, this one exposes a real faceted search:

    POST /app/wp-json/bpce/v1/search/jobs

The body is a flat JSON object of facet slugs (see _search_body). Multi-value
facets are comma-joined, exactly as the UI encodes them
(`tax_contract: "stage,stage-sup-a-2-mois"`). The response carries the FULL
posting inline — title, HTML description, date, sector, sub-family, brand,
contract, and a `localisations[]` array with country — so this is a one-shot
scrape with no detail fetches at all.

Scope, and why:

* SECTOR — `Informatique` (uniformly tech: devs, architectes SI, data
  analysts, IT quants, tech leads) plus `Digital`. Digital MUST be taken
  wholesale: every row in it carries the degenerate sub-family label
  "Digital", yet that is where the Data Scientist / ML Engineer / Tech Lead
  DevOps / Chef de Projet Data & IA postings actually file. Filtering on the
  sub-family axis would silently drop all of them.
  `Risques Controles et Engagements` is deliberately excluded — unlike the
  Natixis tenant it is ~97% conformité / audit / contrôle permanent here,
  worth ~2 quant rows for 62 rows of noise.
* CONTRACT — CDI only.
* COUNTRY — France. Applied client-side on `localisations[].country` rather
  than through the `tax_country` facet, so a row with a malformed location
  block shows up in the reject log instead of vanishing server-side.
* CHANNEL — CHANNELNATIXIS postings are dropped: they are the same jobs the
  `natixis` scraper already pulls from recrutement.natixis.com, and keeping
  them would double every Natixis role in the dashboard. The exclusion is a
  denylist, not an allowlist, so a brand-new channel is kept by default
  rather than silently missed.

Sector/contract run server-side (facet slugs in SECTOR_FACET /
CONTRACT_FACET); country/channel run client-side. To widen scope, edit those
two facet strings and the matching SECTORS_IN_SCOPE / CONTRACTS_IN_SCOPE
label sets, which are re-checked on the response.
"""
from __future__ import annotations

import html
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests

HOST = "https://recrutement.bpce.fr"
SEARCH_URL = f"{HOST}/app/wp-json/bpce/v1/search/jobs"

# --- scope -----------------------------------------------------------------

# Facet slugs sent to the API (comma-joined = OR within the axis).
SECTOR_FACET = "informatique,digital"
CONTRACT_FACET = "cdi"

# The human labels the API echoes back, re-checked client-side so a facet
# rename upstream surfaces as an empty scrape rather than silent drift.
SECTORS_IN_SCOPE: set[str] = {"Informatique", "Digital"}
CONTRACTS_IN_SCOPE: set[str] = {"CDI"}
COUNTRIES_IN_SCOPE: set[str] = {"France"}

# Channels covered by another scraper in this repo. Denylist, not allowlist.
CHANNELS_EXCLUDED: set[str] = {"CHANNELNATIXIS"}

# --- transport -------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

PAGE_SIZE = 100
MAX_PAGES = 30           # defensive: 3000 rows, ~2x the whole board
REQUEST_DELAY_SECONDS = 1.0
REQUEST_TIMEOUT = 60
MAX_ATTEMPTS = 3         # transient-retry per page; fails closed after this


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


def _search_body(offset: int) -> dict:
    """Flat facet object the SPA posts. Empty string = axis not filtered."""
    return {
        "lang": "fr",
        "keyword": "",
        "tax_sector": SECTOR_FACET,
        "tax_contract": CONTRACT_FACET,
        "tax_place": "",
        "tax_job": "",
        "tax_experience": "",
        "tax_degree": "",
        "tax_brands": "",
        "tax_department": "",
        "tax_city": "",
        "tax_country": "",
        "tax_channel": "",
        "jobcode": "",
        "tax_community_job": "",
        "external": False,
        "userID": "",
        "from": offset,
        "size": PAGE_SIZE,
    }


def _post_page(session: requests.Session, offset: int) -> dict:
    """One search page, with a linear-backoff retry on transient failures.

    Raises after MAX_ATTEMPTS — a page that cannot be fetched must abort the
    whole run, never return a partial list (a non-empty partial slips past
    db.persist_run_results' empty-guard and retires the missing rows).
    """
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = session.post(
                SEARCH_URL, json=_search_body(offset), timeout=REQUEST_TIMEOUT
            )
            if response.status_code >= 500:
                raise requests.HTTPError(
                    f"{response.status_code} from search/jobs", response=response
                )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data")
            if not isinstance(data, dict):
                raise ValueError(f"unexpected payload shape: {payload!r:.200}")
            return data
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError,
                requests.exceptions.ChunkedEncodingError, ValueError) as exc:
            last_exc = exc
            if attempt == MAX_ATTEMPTS:
                break
            wait = REQUEST_DELAY_SECONDS * attempt * 2
            print(f"    from={offset} attempt {attempt}/{MAX_ATTEMPTS} failed "
                  f"({type(exc).__name__}: {exc}); retrying in {wait:.0f}s",
                  flush=True)
            time.sleep(wait)
    raise RuntimeError(
        f"search/jobs from={offset} failed after {MAX_ATTEMPTS} attempts"
    ) from last_exc


def _fetch_all(session: requests.Session) -> list[dict]:
    items: list[dict] = []
    total: int | None = None

    for page in range(MAX_PAGES):
        offset = page * PAGE_SIZE
        if page:
            time.sleep(REQUEST_DELAY_SECONDS)

        data = _post_page(session, offset)
        batch = data.get("items") or []
        if total is None:
            total = int(data.get("total") or 0)
            print(f"  server reports {total} rows for "
                  f"sector={SECTOR_FACET!r} contract={CONTRACT_FACET!r}",
                  flush=True)

        print(f"  from={offset:<5} +{len(batch)}", flush=True)
        if not batch:
            break
        items += batch
        if total is not None and len(items) >= total:
            break
    else:
        raise RuntimeError(f"MAX_PAGES={MAX_PAGES} hit — pagination bug?")

    if total and len(items) < total:
        raise RuntimeError(
            f"paginated {len(items)} of {total} rows — aborting rather than "
            f"returning a partial list"
        )
    return items


def _first(value) -> str:
    """The API wraps single-valued taxonomies in a list."""
    if isinstance(value, list):
        return str(value[0]).strip() if value else ""
    return str(value or "").strip()


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def _countries(item: dict) -> set[str]:
    return {
        (loc.get("country") or "").strip()
        for loc in (item.get("localisations") or [])
        if (loc.get("country") or "").strip()
    }


def _build_job(item: dict) -> Job | None:
    advert_id = str(item.get("advert_id") or "").strip()
    if not advert_id:
        return None

    sector = _first(item.get("sector"))
    sub_family = _first(item.get("job"))
    # Digital rows echo the sector as their sub-family — don't print it twice.
    category = sector if sub_family in ("", sector) else f"{sector} — {sub_family}"

    locations = [
        _clean(loc.get("city") or loc.get("localisation") or "")
        for loc in (item.get("localisations") or [])
    ]
    location = ", ".join(dict.fromkeys(p for p in locations if p)) or None

    detail = (item.get("link") or {}).get("url") or ""
    apply_url = f"{HOST}{detail}" if detail.startswith("/") else (detail or HOST)

    return Job(
        native_job_id=advert_id,
        title=_clean(item.get("title") or ""),
        apply_url=apply_url,
        description=item.get("description") or None,
        location=location,
        category=category or None,
        posted_date=(item.get("date") or "").strip() or None,
        employment_type=_first(item.get("contract")) or None,
        identifier=str(item.get("job_number") or advert_id),
        raw_payload={
            "post_id": item.get("post_id"),
            "advert_id": advert_id,
            "job_number": item.get("job_number"),
            "opening_id": item.get("opening_id"),
            "technical_id": item.get("technical_id"),
            "sector": item.get("sector"),
            "job": item.get("job"),
            "brand": item.get("brand"),
            "contract": item.get("contract"),
            "degree": item.get("degree"),
            "experience": item.get("experience"),
            "teletravail": item.get("teletravail"),
            "localisations": item.get("localisations"),
            "postulate_link": (item.get("postulate_link") or {}).get("url"),
        },
    )


def _is_in_scope(item: dict) -> tuple[bool, str]:
    """Returns (keep, reason). reason names the rejecting axis."""
    channels = {str(c).strip() for c in (item.get("technical_id") or [])}
    if channels & CHANNELS_EXCLUDED:
        return False, f"channel={sorted(channels & CHANNELS_EXCLUDED)[0]}"

    sector = _first(item.get("sector"))
    if sector not in SECTORS_IN_SCOPE:
        return False, f"sector={sector!r}"

    contract = _first(item.get("contract"))
    if contract not in CONTRACTS_IN_SCOPE:
        return False, f"contract={contract!r}"

    countries = _countries(item)
    if not countries:
        return False, "country=<missing>"
    if not countries & COUNTRIES_IN_SCOPE:
        return False, f"country={sorted(countries)[0]!r}"

    return True, ""


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()

    print("Search phase (server-side sector + contract facets)...", flush=True)
    items = _fetch_all(session)
    print(f"  {len(items)} rows fetched\n", flush=True)

    print("Client-side gates: country + channel...", flush=True)
    kept: list[Job] = []
    rejected: dict[str, int] = {}
    malformed = 0

    for item in items:
        keep, reason = _is_in_scope(item)
        if not keep:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue

        job = _build_job(item)
        if job is None:
            print(f"  no advert_id on post_id={item.get('post_id')!r} — skipped",
                  flush=True)
            malformed += 1
            continue

        kept.append(job)
        print(f"  [{job.native_job_id}] {job.title!r} — {job.category} — "
              f"{job.location}", flush=True)

    elapsed = time.time() - started
    print(flush=True)
    print(f"Filters {sorted(COUNTRIES_IN_SCOPE)} × {sorted(CONTRACTS_IN_SCOPE)} "
          f"× {sorted(SECTORS_IN_SCOPE)} "
          f"− channels {sorted(CHANNELS_EXCLUDED)}:", flush=True)
    print(f"  kept      : {len(kept)}", flush=True)
    print(f"  dropped   : {sum(rejected.values())}", flush=True)
    print(f"  malformed : {malformed}", flush=True)
    print(f"  runtime   : {elapsed:.1f}s", flush=True)

    if rejected:
        print("  reject reasons:", flush=True)
        for reason, n in sorted(rejected.items(), key=lambda kv: -kv[1]):
            print(f"    {n:3d}  {reason}", flush=True)

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
        desc = re.sub(r"<[^>]+>", " ", j.get("description") or "")
        desc = _clean(desc)
        desc = desc[:200] + ("…" if len(desc) > 200 else "")

        brand = (j["raw_payload"].get("brand") or ["?"])[0]
        print(f"[{j['identifier']}] {j['title']}")
        print(f"  Brand      : {brand}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
