"""Cisco job scraper — France, Data/AI + Software/IT, CDI (Regular) only.

Discovery note (why Workday CXS and not the careers site):
  careers.cisco.com is a Phenom People (CareerConnect, tenant CISCISGLOBAL) front.
  Its /widgets search index is rich JSON, but every `applyUrl` points at the real
  ATS — Workday, tenant `cisco`, site `Cisco_Careers`:

    https://cisco.wd5.myworkdayjobs.com/Cisco_Careers

  so we hit the standard CXS JSON API directly (same shape as Renault / Air Liquide
  / Rothschild) and skip the Phenom layer. The Phenom index also can't distinguish
  Regular from Fixed-Term/Intern (its only contract field is the "Full time" hours
  type), whereas Workday's `workerSubType` facet gives us the clean CDI gate.

    POST /wday/cxs/cisco/Cisco_Careers/jobs            (listing, faceted)
    GET  /wday/cxs/cisco/Cisco_Careers<externalPath>   (detail: desc + startDate)

Filter mapping (this scope), all server-side facets:
  locations      = every facet whose label ends in "France"   (currently just
                   "Paris, France" — Cisco France is a single Paris/IDF bucket;
                   resolved dynamically so a new French city is picked up for free)
  workerSubType  = "Regular"                                   (= CDI/permanent)
  jobFamilyGroup = Engineering + Information Technology, looped (so each row is
                   tagged with its family as `category`)

Scope decision: France + CDI, Data/AI + Software/IT. Cisco's only tech job-family
group is the catch-all "Engineering" (508 globally) — there is no finer server-side
facet (jobFamily isn't exposed), so we keep Engineering + Information Technology
wholesale. That family *can* include hardware/silicon at Cisco, but the Paris slice
is 100% software/security today (Tetragon/Cilium/Isovalent eBPF-security, WAF, Go)
— the automaker-style physical-eng problem doesn't materialise here, and per
`feedback_prefer_platform_category_over_is_tech_role` we filter on the platform
category rather than a title heuristic. If silicon/hardware rows ever start landing
in Paris, gate with `scrapers._relevance.is_tech_role` in scrape() rather than
narrowing the family. Yield is ~11 rows; low is expected (cf. Salesforce/N26/Mirakl).

Multi-location caveat: these are pan-European remote reqs. Workday's `location` is
the *primary* city (often Zurich/London, not France), but the req is genuinely open
in Paris (that's why it matched the France facet). We therefore surface the France
entry from `location + additionalLocations` as the row's `location`, so the
dashboard IDF filter (web/filters.is_idf) keeps it instead of dropping a "Zurich"
string. The full location list is preserved in raw_payload.

Facet ids are resolved at run start from an empty-facet POST (not hard-coded):
Workday WIDs are tenant-specific and can rotate, and this also auto-discovers any
new French city facet. The steady-state request count is tiny (2 family listings +
one detail per matched job, all spaced >= REQUEST_DELAY_SECONDS), so this runs fine
from GitHub Actions — Cisco's wd5 CXS is open and not WAF-gated (unlike Safran/BNP).
"""
from __future__ import annotations

import html
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

TENANT = "cisco"
SITE = "Cisco_Careers"
HOST = f"https://{TENANT}.wd5.myworkdayjobs.com"
LIST_URL = f"{HOST}/wday/cxs/{TENANT}/{SITE}/jobs"
DETAIL_URL_TEMPLATE = f"{HOST}/wday/cxs/{TENANT}/{SITE}{{external_path}}"

# jobFamilyGroup labels we keep (Data/AI + Software/IT scope). Matched by descriptor
# against the live facet list so we never hard-code a WID that may rotate.
WANTED_FAMILIES = {"Engineering", "Information Technology"}
# workerSubType label meaning permanent/CDI.
REGULAR_LABEL = "regular"

# Polite, project-naming User-Agent (playbook hard rule). The CXS JSON API is
# UA-agnostic.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
    "Origin": HOST,
    "Referer": f"{HOST}/{SITE}",
}

PAGE_SIZE = 20
MAX_PAGES = 20  # per family — defensive cap against a pagination bug
REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.5
RETRY_BACKOFF_SECONDS = 10.0
MAX_RETRIES = 3


@dataclass
class Job:
    native_job_id: str          # Workday requisition id, e.g. "2004725"
    title: str
    location: str               # France-preferring location string (see module doc)
    category: str | None        # jobFamilyGroup label
    apply_url: str              # detail's externalUrl
    employment_type: str        # always "CDI" for this scope (workerSubType=Regular)
    description: str | None = None
    posted_date: str | None = None    # YYYY-MM-DD from detail's startDate
    identifier: str | None = None     # detail's jobPostingInfo.id (internal hash)
    raw_payload: dict | None = None


def _clean_description(content: str | None) -> str | None:
    if not content:
        return None
    text = BeautifulSoup(html.unescape(content), "html.parser").get_text(" ", strip=True)
    return text or None


def _request_with_retry(
    session: requests.Session, method: str, url: str, json_body: dict | None = None
) -> dict:
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        # Clear cookies before each attempt: Workday is fronted by Cloudflare and a
        # __cf_bm cookie that gets tagged "suspicious" makes every follow-up on the
        # same Session keep failing; cookie-free callers recover after the window.
        session.cookies.clear()
        if method == "POST":
            response = session.post(url, json=json_body, timeout=REQUEST_TIMEOUT)
        else:
            response = session.get(url, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            return response.json()
        last_exc = requests.HTTPError(
            f"{response.status_code} on attempt {attempt}: {response.text[:120]}",
            response=response,
        )
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS)
    assert last_exc is not None
    raise last_exc


def _resolve_facets(session: requests.Session) -> tuple[list[str], dict[str, str], str]:
    """Read the live facet list once and resolve the WIDs we filter on.

    Returns (france_location_ids, {family_id: family_label}, regular_worker_subtype_id).
    Raises if the category/contract facets are missing (a structural change we want
    to hear about); an empty France list is a legitimate zero, not an error.
    """
    payload = _request_with_retry(
        session, "POST", LIST_URL,
        json_body={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
    )

    france_ids: list[str] = []
    family_ids: dict[str, str] = {}
    regular_id: str | None = None

    for facet in payload.get("facets") or []:
        param = facet.get("facetParameter")
        values = facet.get("values") or []
        if param == "jobFamilyGroup":
            for v in values:
                if (v.get("descriptor") or "") in WANTED_FAMILIES:
                    family_ids[v["id"]] = v["descriptor"]
        elif param == "workerSubType":
            for v in values:
                if (v.get("descriptor") or "").strip().lower() == REGULAR_LABEL:
                    regular_id = v["id"]
        # The location facet is nested: a "locationMainGroup" facet whose values are
        # themselves a facet with facetParameter "locations" (city-level entries).
        for group in values:
            if isinstance(group, dict) and group.get("facetParameter") == "locations":
                for v in group.get("values") or []:
                    if (v.get("descriptor") or "").strip().endswith("France"):
                        france_ids.append(v["id"])

    if not family_ids:
        raise RuntimeError(
            f"Cisco: no wanted jobFamilyGroup facet found (looked for {WANTED_FAMILIES}) "
            "— Workday facet structure may have changed."
        )
    if not regular_id:
        raise RuntimeError(
            "Cisco: no 'Regular' workerSubType facet found — structure may have changed."
        )
    return france_ids, family_ids, regular_id


def _post_listing(
    session: requests.Session,
    france_ids: list[str],
    family_id: str,
    regular_id: str,
    page: int,
) -> dict:
    body = {
        "appliedFacets": {
            "locations": france_ids,
            "jobFamilyGroup": [family_id],
            "workerSubType": [regular_id],
        },
        "limit": PAGE_SIZE,
        "offset": (page - 1) * PAGE_SIZE,
        "searchText": "",
    }
    return _request_with_retry(session, "POST", LIST_URL, json_body=body)


def _collect_family_rows(
    session: requests.Session,
    france_ids: list[str],
    family_id: str,
    family_name: str,
    regular_id: str,
) -> list[dict]:
    """Fetch the full France + CDI listing for one job family, with paging.
    Stamps each row with `_family_name` for downstream category tagging."""
    page = 1
    payload = _post_listing(session, france_ids, family_id, regular_id, page)
    total = int(payload.get("total") or 0)
    rows: list[dict] = list(payload.get("jobPostings") or [])

    while len(rows) < total and page < MAX_PAGES:
        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)
        payload = _post_listing(session, france_ids, family_id, regular_id, page)
        new = payload.get("jobPostings") or []
        if not new:
            break
        rows.extend(new)

    for r in rows:
        r["_family_name"] = family_name

    print(f"  {family_name}: {len(rows)}/{total} rows", flush=True)
    return rows


def _france_location(info: dict) -> str:
    """Build a France-preferring location string from the detail's location set.

    These reqs are multi-location; Workday's primary `location` is often *not*
    France (e.g. Zurich). We lead with the French entry so web/filters.is_idf keeps
    the row, and note how many other locations there are."""
    locs = [info.get("location")] + list(info.get("additionalLocations") or [])
    locs = [l.strip() for l in locs if l and l.strip()]
    if not locs:
        return ""
    french = [l for l in locs if l.endswith("France")]
    base = french[0] if french else locs[0]
    others = len(locs) - 1
    return f"{base} (+{others} other locations)" if others > 0 else base


def _native_job_id(listing_row: dict, info: dict) -> str:
    """Prefer the detail's authoritative jobReqId; fall back to the listing's
    bulletFields[0] (Cisco puts the reqId there)."""
    rid = (info.get("jobReqId") or "").strip()
    if rid:
        return rid
    bullets = listing_row.get("bulletFields") or []
    if bullets and str(bullets[0]).strip():
        return str(bullets[0]).strip()
    raise RuntimeError(f"Cisco listing row missing requisition id: {listing_row!r}")


def _row_to_job(listing_row: dict, detail: dict, family_name: str) -> Job:
    info = detail.get("jobPostingInfo") or {}
    if not info:
        raise RuntimeError(f"Cisco detail missing jobPostingInfo: {detail!r}")

    apply_url = (info.get("externalUrl") or "").strip()
    if not apply_url:
        ext = listing_row.get("externalPath") or ""
        if ext:
            apply_url = f"{HOST}/{SITE}{ext}"
    if not apply_url:
        raise RuntimeError(f"Cisco detail missing externalUrl: {info!r}")

    posted = info.get("startDate")
    posted_date = posted[:10] if isinstance(posted, str) and len(posted) >= 10 else None

    title = (info.get("title") or listing_row.get("title") or "").strip()

    return Job(
        native_job_id=_native_job_id(listing_row, info),
        title=title,
        location=_france_location(info),
        category=family_name,
        apply_url=apply_url,
        employment_type="CDI",
        description=_clean_description(info.get("jobDescription")),
        posted_date=posted_date,
        identifier=info.get("id"),
        raw_payload={"listing": listing_row, "detail": info},
    )


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    france_ids, family_ids, regular_id = _resolve_facets(session)
    if not france_ids:
        # No French location facet => no France jobs in scope. Return empty; the DB
        # empty-guard treats this as "no change" and won't false-close prior rows.
        print("Cisco: no France location facet present — 0 jobs.", flush=True)
        return []
    print(
        f"Resolved facets: France={len(france_ids)} city id(s), "
        f"families={list(family_ids.values())}",
        flush=True,
    )

    print("Fetch phase (per-family listing)...", flush=True)
    all_rows: list[dict] = []
    for family_id, family_name in family_ids.items():
        # Do NOT swallow a per-family HTTP error and continue: a partial return
        # would let the DB false-close the missing family's still-open rows (see
        # feedback_partial_scrape_false_close). Abort instead; the only tolerated
        # per-row drop is a detail failure below.
        rows = _collect_family_rows(session, france_ids, family_id, family_name, regular_id)
        all_rows.extend(rows)
        time.sleep(REQUEST_DELAY_SECONDS)

    # Dedup by requisition id; keep first-seen (= first family it surfaced under).
    by_id: dict[str, dict] = {}
    for r in all_rows:
        bullets = r.get("bulletFields") or []
        rid = str(bullets[0]).strip() if bullets else (r.get("externalPath") or "")
        by_id.setdefault(rid, r)
    print(f"  -> {len(by_id)} unique France/CDI tech jobs across families", flush=True)

    print("\nDetail phase...", flush=True)
    jobs: list[Job] = []
    for r in by_id.values():
        ext = r.get("externalPath") or ""
        if not ext:
            print(f"  skip: missing externalPath ({r.get('title')!r})", flush=True)
            continue
        try:
            detail = _request_with_retry(
                session, "GET", DETAIL_URL_TEMPLATE.format(external_path=ext)
            )
            jobs.append(_row_to_job(r, detail, r.get("_family_name") or ""))
        except requests.HTTPError as exc:
            # A detail 404/failure is the one tolerated per-row drop (the posting
            # likely just closed between listing and detail).
            print(f"  detail failed for {ext}: {exc}", flush=True)
        except RuntimeError as exc:
            print(f"  parse failed for {ext}: {exc}", flush=True)
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
