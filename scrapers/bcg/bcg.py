"""BCG (Boston Consulting Group) job scraper — France, BCG X build roles only.

careers.bcg.com is a Phenom CareerConnect site (refNum BCG1US). Phenom exposes a
single public "widgets" JSON API that the search-results page calls:

  POST https://careers.bcg.com/widgets   (ddoKey=refineSearch — the faceted list)

One POST returns the whole faceted result set (jobs + facet aggregations); we
page it with from/size. Detail pages are plain HTML with a JSON-LD JobPosting
block used only to enrich the full description.

Scope (locked with the user 2026-07):
  - Country     : France only (server-side `country` facet — reliable, and the
                  cityCountry / cityState aggregations confirm France == Paris).
  - Job family  : AI / Data Science + Software / Engineering **build** roles, i.e.
                  BCG X (the AI/software delivery unit) and the standalone Data
                  Science practice — NOT tech-consulting.
  - Employment  : all kept raw (CDI + internships in scope; contract filtering is
                  dashboard-side, and BCG exposes no CDI/CDD/stage facet anyway —
                  `type` is only Full-Time / Part-Time hours).

Why the gate is category + a subCategory *exclusion* (not category alone):
  BCG files three very different things under ONE category, "Technology and
  Engineering":
    * BCG X build      -> subCategory "Software Engineering"      (IN scope)
    * BCG Platinion    -> subCategory "IT Consulting" /
                          "Specialty Consulting"                  (tech-CONSULTING,
                                                                   OUT of scope —
                                                                   user deselected it)
  and the AI/Data build roles ("Forward Deployed AI Scientist") sit under a
  separate category "Data Science and Analytics" / subCategory "Data Science".
  So we keep {Data Science and Analytics, Technology and Engineering} but drop the
  two Platinion consulting subCategories. Category "Design Strategy" (the "AI
  Experience Designer") is a design role and is intentionally NOT kept.
  See _in_scope for the exact rule (checked against multi_category so a build role
  mis-primaried as "Consulting" but tagged Tech&Eng secondary is still caught).

Why detail fetches are best-effort (not abort-on-failure like Workday scrapers):
  Every REQUIRED and most SHOULD-have fields (native_job_id, title, apply_url,
  category, location, posted_date, type) come from the LISTING. The detail page
  is fetched ONLY for the full description. So a flaky detail page must NOT abort
  the run or drop the row — that would false-close it. We keep the row with the
  descriptionTeaser as a fallback and move on. The recall-critical part is the
  listing enumeration; if THAT fails we raise (run.py closes nothing).
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

BASE = "https://careers.bcg.com"
WIDGETS_URL = f"{BASE}/widgets"
DETAIL_URL_TEMPLATE = f"{BASE}/global/en/job/{{seq}}"

COUNTRY = "France"

# Categories kept wholesale (AI/Data build + engineering build). Checked against
# the union of `category` and `multi_category`.
KEEP_CATEGORIES = {"Data Science and Analytics", "Technology and Engineering"}
# BCG Platinion tech-consulting subCategories — deselected by the user. These
# share the "Technology and Engineering" category with genuine BCG X build roles,
# so the subCategory exclusion is what separates build from consulting.
EXCLUDED_SUBCATEGORIES = {"IT Consulting", "Specialty Consulting"}

# BCG requisition id (dedup key). Validated to digits so a listing-schema change
# can't collapse every row onto one shared dedup key (see _req_id).
REQ_ID_RE = re.compile(r"\d+")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE,
    "Referer": f"{BASE}/global/en/search-results",
}

PAGE_SIZE = 50
MAX_PAGES = 20          # defensive cap (France total is ~37 across ALL categories)
REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.5   # between requests (JSON API / HTML detail)
RETRY_BACKOFF_SECONDS = 20.0
MAX_RETRIES = 3


@dataclass
class Job:
    native_job_id: str          # BCG reqId, e.g. "58176"
    title: str
    apply_url: str              # canonical Phenom detail page
    location: str | None = None
    category: str | None = None
    subcategory: str | None = None
    employment_type: str | None = None   # "Full-Time"/"Part-Time" (hours; no CDI facet)
    description: str | None = None
    posted_date: str | None = None       # YYYY-MM-DD from listing postedDate
    identifier: str | None = None        # Phenom jobSeqNo
    raw_payload: dict | None = None


def _in_scope(job: dict) -> bool:
    """AI/Data + Software/Eng build roles; exclude BCG Platinion tech-consulting."""
    subcategory = (job.get("subCategory") or "").strip()
    if subcategory in EXCLUDED_SUBCATEGORIES:
        return False
    cats = set(job.get("multi_category") or [])
    if job.get("category"):
        cats.add(job["category"])
    return bool(cats & KEEP_CATEGORIES)


def _req_id(job: dict) -> str | None:
    """BCG requisition id, VALIDATED to a bare integer.

    We don't trust reqId/jobId verbatim: if Phenom ever hands a shared/blank value
    into that slot, every row would collapse onto one dedup key and the non-empty
    result would slip past db.persist_run_results' empty-return guard and
    false-close every other open BCG row. Requiring \\d+ means a wrong-shaped value
    yields None (row skipped); a board-wide shape change yields an empty result the
    guard then protects."""
    for key in ("reqId", "jobId"):
        val = job.get(key)
        if val is None:
            continue
        candidate = str(val).strip()
        if REQ_ID_RE.fullmatch(candidate):
            return candidate
    return None


def _build_location(job: dict) -> str | None:
    """Join every listed city so the dashboard's is_idf sees the France one even on
    a multi-location req (same rationale as the Cisco / Ipsen scrapers)."""
    cities: list[str] = []
    multi = job.get("multi_location") or []
    for loc in multi:
        if isinstance(loc, str) and loc.strip():
            cities.append(loc.strip())
    primary = (job.get("location") or "").strip()
    if primary:
        cities.append(primary)
    location = "; ".join(dict.fromkeys(c for c in cities if c))
    return location or None


def _clean_html(content: str | None) -> str | None:
    if not content:
        return None
    text = BeautifulSoup(html.unescape(content), "html.parser").get_text(" ", strip=True)
    return text or None


def _iso_date(value: str | None) -> str | None:
    if isinstance(value, str) and len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value[:10]
    return None


def _new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def _post_listing(session: requests.Session, offset: int) -> dict:
    body = {
        "lang": "en_global",
        "deviceType": "desktop",
        "country": "global",
        "pageName": "search-results",
        "ddoKey": "refineSearch",
        "sortBy": "",
        "subsearch": "",
        "from": offset,
        "jobs": True,
        "counts": False,
        "all_fields": [
            "category", "subCategory", "multi_category", "country",
            "city", "type", "postedDate",
        ],
        "pageType": "",
        "size": PAGE_SIZE,
        "clientName": "bcg",
        "locationData": {},
        "keywords": "",
        "global": True,
        "selected_fields": {"country": [COUNTRY]},
        "locationType": "",
        "sort": {"order": "", "field": ""},
    }
    last_err: str | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        response = session.post(WIDGETS_URL, json=body, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            payload = response.json().get("refineSearch") or {}
            if payload.get("status") == 200 and "data" in payload:
                return payload
            last_err = f"unexpected payload status {payload.get('status')}"
        else:
            last_err = f"HTTP {response.status_code}: {response.text[:160]}"
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS)
    raise requests.HTTPError(f"widgets listing failed (offset {offset}): {last_err}")


def _collect_france(session: requests.Session) -> list[dict]:
    """Enumerate every France job (all categories); filtered client-side after."""
    first = _post_listing(session, offset=0)
    total = int(first.get("totalHits") or 0)
    rows: list[dict] = list(first.get("data", {}).get("jobs") or [])

    page = 0
    while len(rows) < total and page < MAX_PAGES:
        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)
        payload = _post_listing(session, offset=page * PAGE_SIZE)
        new = payload.get("data", {}).get("jobs") or []
        if not new:
            break
        rows.extend(new)
    return rows


def _fetch_description(session: requests.Session, seq: str) -> str | None:
    """Best-effort: pull the full description from the detail page's JSON-LD.

    Enrichment only — never raises. On any failure the caller falls back to the
    listing's descriptionTeaser, so a flaky detail page can't drop the row (which
    would false-close it) or abort the run."""
    url = DETAIL_URL_TEMPLATE.format(seq=seq)
    try:
        response = session.get(
            url,
            headers={"Accept": "text/html", "Content-Type": "text/html"},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        blocks = re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            response.text,
            re.S,
        )
        for block in blocks:
            raw = block.strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                try:
                    data = json.loads(html.unescape(raw))
                except json.JSONDecodeError:
                    continue
            if isinstance(data, dict) and data.get("@type") == "JobPosting":
                return _clean_html(data.get("description"))
    except (requests.RequestException, ValueError) as exc:
        print(f"  detail enrich failed for {seq} ({type(exc).__name__}); using teaser",
              flush=True)
    return None


def _row_to_job(session: requests.Session, row: dict) -> Job | None:
    native_job_id = _req_id(row)
    if not native_job_id:
        print(f"  skip row (no valid req id): {row.get('title')!r}", flush=True)
        return None

    seq = (row.get("jobSeqNo") or "").strip()
    apply_url = (
        DETAIL_URL_TEMPLATE.format(seq=seq) if seq
        else html.unescape((row.get("applyUrl") or "").strip())
    )
    if not apply_url:
        print(f"  skip {native_job_id}: no apply url", flush=True)
        return None

    description = _fetch_description(session, seq) if seq else None
    if not description:
        description = _clean_html(row.get("descriptionTeaser"))
    time.sleep(REQUEST_DELAY_SECONDS)

    return Job(
        native_job_id=native_job_id,
        title=(row.get("title") or "").strip(),
        apply_url=apply_url,
        location=_build_location(row),
        category=row.get("category"),
        subcategory=row.get("subCategory"),
        employment_type=(row.get("type") or "").strip() or None,
        description=description,
        posted_date=_iso_date(row.get("postedDate")),
        identifier=seq or None,
        raw_payload={"listing": row},
    )


def scrape() -> list[dict]:
    session = _new_session()
    started = time.time()

    print("Listing phase (France, all categories)...", flush=True)
    rows = _collect_france(session)   # recall-critical — raises on failure
    print(f"  -> {len(rows)} France rows total", flush=True)

    in_scope = [r for r in rows if _in_scope(r)]
    print(f"  -> {len(in_scope)} in scope (AI/Data + Software/Eng build; "
          f"Platinion tech-consulting excluded)", flush=True)

    print("\nDetail phase (description enrichment)...", flush=True)
    jobs: list[Job] = []
    seen: set[str] = set()
    for row in in_scope:
        job = _row_to_job(session, row)
        if job is None or job.native_job_id in seen:
            continue
        seen.add(job.native_job_id)
        jobs.append(job)

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
        print(f"  Category   : {j['category']} / {j['subcategory']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
