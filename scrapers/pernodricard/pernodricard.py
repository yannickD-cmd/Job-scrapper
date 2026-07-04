"""Pernod Ricard job scraper — France, Tech & IT families, CDI only.

Pernod Ricard's single global board is hosted on Workday at
pernodricard.wd3.myworkdayjobs.com/pernod-ricard. Workday exposes the usual
public JSON CXS API:

  POST /wday/cxs/pernodricard/pernod-ricard/jobs       (listing, faceted)
  GET  /wday/cxs/pernodricard/pernod-ricard<externalPath>   (detail)

Scope (locked with the user):
  - Country     : France only      (no country facet exists — filtered
                                     client-side off the listing bulletFields,
                                     which carry the country name)
  - Job families: "Tech" + "Information Technology"   (jobFamilyGroup facet)
  - Job type    : CDI only          (workerSubType = "Regular")
  - Title filter: none — keep every in-family role

Workday quirks worth knowing (this tenant) — the Rothschild lessons all apply:
  - We run COOKIE-FREE: `session.cookies.clear()` before every request. Workday
    fronts CXS with Cloudflare, which tags the first response with __cf_bm /
    wd-browser-id / PLAY_SESSION cookies. On a flagged fingerprint (datacenter
    ASNs like GitHub Actions) those cookies make every follow-up faceted POST
    keep 400-ing; bare cookie-free requests are scored fresh and succeed. (An
    earlier seeded-session version 400'd 100% in CI for exactly this reason.)
  - Faceted (filtered) POSTs also need `X-Calypso-Selected-Locale: en-US` plus
    an `/en-US/...` Referer; without them the server 400s otherwise-valid
    bodies. The detail GET doesn't need them.
  - The faceted POST is additionally metered by a slow-refilling, escalating
    token bucket (the detail GET is not). A burst earns empty-body 400s for
    tens of minutes — so probe it sparingly. We make one faceted POST per
    family (two total), well spaced, and back off on a 400.
  - We query each family on its OWN (a single jobFamilyGroup id + Regular).
    Combining both families AND workerSubType in one payload
    (`jobFamilyGroup: [Tech, IT]` + Regular) was never observed to return 200,
    whereas every single-family query is reliable — so we loop. Looping also
    lets us tag each row with its family name → that becomes `category`.
  - native_job_id: the detail's `jobReqId` is the clean public id ("JR-053956").
    The listing's bulletFields also carry it (last "JR-" element); we dedup on
    that before fetching details, then trust the detail's jobReqId.
  - Country is reliable on the detail (`country.descriptor` == "France"); the
    France country id is the Workday-global 54c5b6971ffb4bf0b116fe7651ec789a.
  - `startDate` is already ISO YYYY-MM-DD and matches the "Posted N Days Ago"
    surfaced by the listing — used directly as posted_date.
"""
from __future__ import annotations

import html
import random
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

TENANT = "pernodricard"
SITE = "pernod-ricard"
HOST = f"https://{TENANT}.wd3.myworkdayjobs.com"
LIST_URL = f"{HOST}/wday/cxs/{TENANT}/{SITE}/jobs"
DETAIL_URL_TEMPLATE = f"{HOST}/wday/cxs/{TENANT}/{SITE}{{external_path}}"

# jobFamilyGroup facet id → category label. Looped one at a time (a single id
# per faceted POST) — that's the only form that reliably returns 200 here, and
# it lets us tag each row with its family. Both families are tiny.
FAMILIES: dict[str, str] = {
    "5c4276c36b5a1001e317a08d36940000": "Tech",                    # ~34 global
    "371688745b57014fe9c19df9ef17a12f": "Information Technology",  # ~4 global
}

# workerSubType facet — "Regular" == permanent (CDI). The Regular facet is the
# only employment type in scope, so every returned row is a CDI by construction.
WORKERSUBTYPE_REGULAR = "371688745b5701d8d14db11fa6174024"

# Country lives in the listing's bulletFields as a plain name, never as a facet.
FRANCE_COUNTRY_NAME = "France"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": HOST,
    "Referer": f"{HOST}/en-US/{SITE}",
    # Required for faceted (filtered) POSTs — without it Workday 400s valid bodies.
    "X-Calypso-Selected-Locale": "en-US",
}

PAGE_SIZE = 50
MAX_PAGES = 10  # per family — defensive cap (families are tiny)
REQUEST_TIMEOUT = 30
# The faceted POST is rate-sensitive (token bucket); go slow and back off long.
REQUEST_DELAY_SECONDS = 1.5      # between detail GETs (not bucket-metered)
FACET_DELAY_SECONDS = 5.0        # between the (few) faceted listing POSTs

# The faceted listing POST is metered by an ESCALATING token bucket: empty-message
# HTTP 400s (errorCode HTTP_400, message="") that clear on their own after a
# cooldown; a real burst earns a multi-HOUR ban. The request itself is valid —
# every facet id is current and Tech+Regular returns 200 when the bucket has
# tokens. Key insight: retrying HARDER feeds the escalation, so we do NOT add
# request pressure vs the old code (it made 3 attempts). Same 3 attempts, but
# spaced far longer so they straddle a shallow transient dip instead of hammering
# a ~90s window. If still 400 after this, the bucket is deeply penalised (likely
# a multi-hour ban) — no in-run retry can fix that, so fail fast: run.py closes
# nothing and the next scheduled run (4×/day) recovers after the cooldown.
LISTING_RETRY_BACKOFFS = (90.0, 300.0)  # 2 retries after the 1st try (3 attempts)
# Detail GETs are NOT bucket-metered — a short, few-shot retry is plenty.
DETAIL_RETRY_BACKOFF_SECONDS = 30.0
DETAIL_MAX_RETRIES = 3

JR_RE = re.compile(r"\bJR[-\d]+\b")


@dataclass
class Job:
    native_job_id: str          # jobReqId, e.g. "JR-053956"
    title: str
    location: str               # detail's location (clean city)
    category: str | None        # job family ("Tech" / "Information Technology")
    apply_url: str              # detail's externalUrl
    employment_type: str        # always "CDI" (Regular facet)
    description: str | None = None
    posted_date: str | None = None   # YYYY-MM-DD from detail's startDate
    identifier: str | None = None    # detail's jobPostingInfo.id (internal hash)
    raw_payload: dict | None = None


def _clean_description(content: str | None) -> str | None:
    if not content:
        return None
    text = BeautifulSoup(html.unescape(content), "html.parser").get_text(" ", strip=True)
    return text or None


def _native_job_id_from_listing(row: dict) -> str | None:
    """The JR id is the last "JR-…" token in bulletFields, e.g. "JR-053956"."""
    for field in reversed(row.get("bulletFields") or []):
        if isinstance(field, str) and JR_RE.fullmatch(field.strip()):
            return field.strip()
    # Fallback: tail of externalPath, e.g. "..._JR-053956" or "..._JR-034242-1".
    path = row.get("externalPath") or ""
    m = re.search(r"_(JR-\d+)", path)
    return m.group(1) if m else None


def _row_is_france(row: dict) -> bool:
    return any(
        isinstance(f, str) and f.strip() == FRANCE_COUNTRY_NAME
        for f in (row.get("bulletFields") or [])
    )


def _new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def _is_error_payload(data: object) -> bool:
    return isinstance(data, dict) and "errorCode" in data


def _post_listing(session: requests.Session, family_id: str, offset: int) -> dict:
    body = {
        "appliedFacets": {
            "jobFamilyGroup": [family_id],            # one family per query
            "workerSubType": [WORKERSUBTYPE_REGULAR],
        },
        "limit": PAGE_SIZE,
        "offset": offset,
        "searchText": "",
    }
    last_err: str | None = None
    for attempt in range(1 + len(LISTING_RETRY_BACKOFFS)):
        # Cookie-free: drop any Cloudflare/Workday cookie before each attempt.
        # A cookie tied to a flagged fingerprint keeps 400-ing; bare requests
        # are scored fresh. (See module docstring.)
        session.cookies.clear()
        response = session.post(LIST_URL, json=body, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            if not _is_error_payload(data):
                return data
            last_err = f"error payload {data.get('errorCode')}"
        else:
            last_err = f"HTTP {response.status_code}: {response.text[:200]}"
        # Empty-message 400 == token-bucket rate limit. Back off with an
        # escalating, jittered delay long enough to outlast the penalty before
        # the next cookie-free retry.
        if attempt < len(LISTING_RETRY_BACKOFFS):
            base = LISTING_RETRY_BACKOFFS[attempt]
            print(
                f"    listing {family_id} attempt {attempt + 1} failed "
                f"({last_err}); backing off ~{base:.0f}s",
                flush=True,
            )
            time.sleep(base + random.uniform(0.0, base * 0.25))
    raise requests.HTTPError(f"listing failed for family {family_id}: {last_err}")


def _get_detail(session: requests.Session, external_path: str) -> dict:
    url = DETAIL_URL_TEMPLATE.format(external_path=external_path)
    last_err: str | None = None
    for attempt in range(1, DETAIL_MAX_RETRIES + 1):
        session.cookies.clear()
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            if not _is_error_payload(data):
                return data
            last_err = f"error payload {data.get('errorCode')}"
        elif response.status_code == 404:
            # Terminal, not transient: the job was removed between the listing
            # snapshot and this fetch. Don't waste retries — surface the 404 so
            # the caller can drop it (closing it is correct; it's genuinely gone).
            raise requests.HTTPError(f"detail 404 for {external_path}", response=response)
        else:
            last_err = f"HTTP {response.status_code}: {response.text[:200]}"
        if attempt < DETAIL_MAX_RETRIES:
            time.sleep(DETAIL_RETRY_BACKOFF_SECONDS)
    raise requests.HTTPError(f"detail failed for {external_path}: {last_err}")


def _collect_family_rows(
    session: requests.Session,
    family_id: str,
    family_name: str,
) -> list[dict]:
    """Fetch the full France-filtered CDI listing for one family, tagging each
    surviving row with its family name (the listing doesn't echo the family)."""
    offset = 0
    page = 0
    payload = _post_listing(session, family_id, offset)
    total = int(payload.get("total") or 0)
    raw: list[dict] = list(payload.get("jobPostings") or [])

    while len(raw) < total and page < MAX_PAGES:
        page += 1
        offset += PAGE_SIZE
        time.sleep(FACET_DELAY_SECONDS)  # paging is another faceted POST
        payload = _post_listing(session, family_id, offset)
        new = payload.get("jobPostings") or []
        if not new:
            break
        raw.extend(new)

    france = [r for r in raw if _row_is_france(r)]
    for r in france:
        r["_family_name"] = family_name
    print(f"  {family_name}: {len(raw)}/{total} CDI rows, {len(france)} in France", flush=True)
    return france


def _row_to_job(listing_row: dict, detail: dict, family_name: str) -> Job:
    info = detail.get("jobPostingInfo") or {}

    apply_url = (info.get("externalUrl") or "").strip()
    if not apply_url:
        ext = listing_row.get("externalPath") or ""
        apply_url = f"{HOST}/{SITE}{ext}" if ext else ""
    if not apply_url:
        raise RuntimeError(f"Pernod Ricard detail missing externalUrl: {info!r}")

    native_job_id = (
        (info.get("jobReqId") or "").strip()
        or _native_job_id_from_listing(listing_row)
        or ""
    )
    if not native_job_id:
        raise RuntimeError(f"Pernod Ricard row missing JR id: {listing_row!r}")

    location = (
        (info.get("location") or "").strip()
        or (info.get("jobRequisitionLocation") or {}).get("descriptor")
        or listing_row.get("locationsText")
        or ""
    ).strip()

    posted = info.get("startDate")
    posted_date = posted[:10] if isinstance(posted, str) and len(posted) >= 10 else None

    title = (info.get("title") or listing_row.get("title") or "").strip()

    return Job(
        native_job_id=native_job_id,
        title=title,
        location=location,
        category=family_name,
        apply_url=apply_url,
        employment_type="CDI",
        description=_clean_description(info.get("jobDescription")),
        posted_date=posted_date,
        identifier=info.get("id"),
        raw_payload={"listing": listing_row, "detail": info},
    )


def scrape() -> list[dict]:
    session = _new_session()

    started = time.time()
    print("Listing phase (per-family, France-filtered)...", flush=True)

    by_jr: dict[str, dict] = {}
    for i, (family_id, family_name) in enumerate(FAMILIES.items()):
        if i:
            # Space the faceted POSTs out — this endpoint is token-bucket metered.
            time.sleep(FACET_DELAY_SECONDS)
        # Deliberately NOT caught: with only two families, swallowing a
        # rate-limited family and returning the other's (non-empty) rows would
        # make db.persist_run_results treat the missing family's entire catalog
        # as closed (still_open=FALSE) — the empty-return guard only fires on a
        # *fully* empty result. A partial listing is worse than none, so we let
        # the HTTPError propagate: run.py logs a failed run and closes nothing,
        # and the next scheduled run (4×/day) recovers.
        rows = _collect_family_rows(session, family_id, family_name)
        for r in rows:
            jr = _native_job_id_from_listing(r)
            if jr:
                # Keep first-seen (Tech wins over IT if a role ever appeared in
                # both; in practice the two families are disjoint).
                by_jr.setdefault(jr, r)

    print(f"  -> {len(by_jr)} unique France Tech/IT CDI roles", flush=True)

    print("\nDetail phase...", flush=True)
    jobs: list[Job] = []
    for jr, row in by_jr.items():
        ext = row.get("externalPath") or ""
        if not ext:
            print(f"  skip {jr}: missing externalPath", flush=True)
            continue
        try:
            detail = _get_detail(session, ext)
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            if status == 404:
                # Removed since the listing snapshot — drop it. It will close,
                # which is correct: it's genuinely gone.
                print(f"  {jr}: detail 404, dropping (job removed)", flush=True)
                continue
            # Any other detail failure means the result would be incomplete;
            # don't return a partial list (it would false-close the dropped
            # rows). Abort so run.py logs a failed run and closes nothing.
            raise
        jobs.append(_row_to_job(row, detail, row.get("_family_name") or ""))
        time.sleep(REQUEST_DELAY_SECONDS)

    elapsed = time.time() - started
    print(f"  -> {len(jobs)} jobs in {elapsed:.1f}s\n", flush=True)
    return [asdict(j) for j in jobs]


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
        desc = j["description"] or ""
        desc = desc[:200] + ("..." if len(desc) > 200 else "")
        print(f"[{j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
