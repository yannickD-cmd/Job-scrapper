"""TotalEnergies job scraper — France, Data/AI/ML scope.

ATS: Avature (career portal id 4) at https://jobs.totalenergies.com. The
public marketing site careers.totalenergies.com (Drupal) just links out to
this. Listing UI is server-rendered: each row is an
`<div class="article article--result">` containing the slug+id URL and
five `<li class="list-item list-item-*">` cells (creation date, country,
employment type, employer company).

Filter strategy (server-side country, client-side title):
- COUNTRY: Avature filter field id 3834. Option ID 41588 = France
  (resolved by brute-probing the dropdown — e.g. 41572 = Denmark,
  41577 = United Kingdom, 41594 = Spain). The filter URL is:
    ?3834=[41588]&3834_format=3639&listFilterMode=1
  `3834_format=3639` is the operator (match) and `listFilterMode=1`
  switches the form to "applied filter" mode rather than just the
  default landing carousel. Note: this filter returns ~420 France
  postings as of 2026-05.
- TITLE: filter in-process with DATA_AI_TITLE_RE. The Avature Domain
  field (704) is too coarse — most France Data/AI roles land under
  "Information Systems" alongside generic IT, and the per-option IDs
  aren't stable enough to depend on. Same rationale as the CGI scraper.

Pagination: `&jobOffset=N` in steps of 20 (jobRecordsPerPage caps at 20
regardless of what you pass). Walk until a page returns zero rows.

Detail page (`/JobDetail/<slug>/<id>`) exposes a `<dl>` field grid with
Country, City, Workplace location, Employer company, Domain, Type of
contract, Experience — and per-section `<h3><strong>Section</strong></h3>`
blocks ("Context & Environment", "Activities", "Candidate Profile",
"Additional Information") whose body lives in
`<dd class="article__content__view__field__value">`.

native_job_id = the integer in the JobDetail URL (e.g. 68768). Stable,
the same id Avature uses for `/ApplicationMethods?jobId=<id>`.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

LISTING_URL = "https://jobs.totalenergies.com/en_US/careers/SearchJobs/"
DETAIL_BASE = "https://jobs.totalenergies.com/en_US/careers/JobDetail/"
APPLY_BASE = "https://jobs.totalenergies.com/en_US/careers/ApplicationMethods?jobId="

# Avature filter: Country (field 3834) = France (option 41588).
COUNTRY_FILTER_PARAMS = {
    "3834": "[41588]",
    "3834_format": "3639",
    "listFilterMode": "1",
}

PAGE_SIZE = 20  # server caps at 20 regardless of jobRecordsPerPage value

# Title regex for Data / AI / ML / MLOps roles. Covers French + English
# terminology since TotalEnergies posts in both. Mirrors the CGI scraper's
# DATA_AI_TITLE_RE structure.
DATA_AI_TITLE_RE = re.compile(
    r"\b("
    r"data\s*(?:engineer|scientist|analyst|architect|lead|steward|"
    r"strateg|management|manager|governance|platform|factory|quality)"
    r"|data\s*&\s*(?:performance|reporting|ai|ml|rh|hr|analy)"
    r"|ai\s*/?\s*ml"
    r"|ai\s+(?:engineer|scientist|specialist|architect|lead|developer)"
    r"|ml\s*engineer"
    r"|machine\s*learning"
    r"|deep\s*learning"
    r"|ing[eé]nieur[(e)]*\s+(?:data|ia)"
    r"|architect[(e)]*\s+data"
    r"|scientifique\s+(?:des\s+)?donn[eé]es"
    r"|analyste\s+(?:de\s+)?donn[eé]es"
    r"|analyste\s+data"
    r"|consultant[(e)]*\s+(?:ia|data)"
    r"|\bia\s*/\s*ml"
    r"|gen(?:erative)?\s*ai"
    r"|\bllm\b"
    r"|\bnlp\b"
    r"|databricks|snowflake|spark|dbt|kafka|airflow"
    r"|data\s*streaming"
    r"|mlops|dataops"
    r"|artificial\s*intelligence"
    r"|intelligence\s*artificielle"
    r")\b",
    re.I,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    "From": "yannickarieldossa@gmail.com",
}

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.0
TOTAL_OFFSET_HARD_CAP = 2000

# Detail-page section h3 titles we treat as description body.
DESCRIPTION_SECTIONS = (
    "Context & Environment",
    "Activities",
    "Candidate Profile",
    "Additional Information",
)

JOBID_FROM_URL_RE = re.compile(r"/JobDetail/[^/]+/(\d+)", re.I)


@dataclass
class Job:
    native_job_id: str       # Avature job id (e.g. "68768")
    title: str
    location: str            # "City, Country" composed from detail-page fields
    category: str | None     # Avature Domain (e.g. "Information Systems")
    apply_url: str           # canonical JobDetail URL
    employment_type: str | None = None
    description: str | None = None
    posted_date: str | None = None    # YYYY-MM-DD
    identifier: str | None = None     # same as native_job_id, parity with other scrapers
    raw_payload: dict | None = None


def _parse_creation_date(s: str) -> str | None:
    """Listing emits DD-MM-YYYY; normalize to YYYY-MM-DD."""
    m = re.match(r"^(\d{2})-(\d{2})-(\d{4})$", s.strip())
    if not m:
        return None
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"


def _parse_listing_rows(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict] = []
    for article in soup.select("div.article.article--result"):
        a = article.find("a", class_="link", href=re.compile(r"/JobDetail/", re.I))
        if not a:
            continue
        href = a.get("href", "")
        m = JOBID_FROM_URL_RE.search(href)
        if not m:
            continue
        job_id = m.group(1)

        # Subtitle <ul> holds the five list-item-* cells.
        meta: dict[str, str] = {}
        for li in article.select("ul.article__header__text__subtitle li.list-item"):
            classes = li.get("class") or []
            cell_class = next(
                (c for c in classes if c.startswith("list-item-")), None
            )
            if not cell_class:
                continue
            key = cell_class[len("list-item-"):]
            meta[key] = li.get_text(" ", strip=True)

        rows.append({
            "native_job_id": job_id,
            "title": a.get_text(" ", strip=True),
            "apply_url": href,
            "posted_date": _parse_creation_date(meta.get("jobCreationDate", "")),
            "country": meta.get("jobCountry"),
            "employment_type": meta.get("employmentType"),
            "employer_company": meta.get("jobEmployerCompany"),
        })
    return rows


def _fetch_listing_page(session: requests.Session, offset: int) -> str:
    params = dict(COUNTRY_FILTER_PARAMS)
    if offset:
        params["jobOffset"] = str(offset)
    print(f"  fetching offset={offset}...", flush=True)
    r = session.get(LISTING_URL, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def _in_scope(row: dict) -> bool:
    if row.get("country") != "France":
        return False
    return bool(DATA_AI_TITLE_RE.search(row["title"]))


def _dl_field_value(soup: BeautifulSoup, label: str) -> str | None:
    """Lookup a value in the detail page's <dl>-style field grid."""
    for dl in soup.select("dl.article__content__view__field"):
        dt = dl.find("dt", class_="article__content__view__field__label")
        if not dt:
            continue
        if dt.get_text(" ", strip=True) != label:
            continue
        dd = dl.find("dd", class_="article__content__view__field__value")
        if dd:
            t = dd.get_text(" ", strip=True)
            return t or None
    return None


def _extract_description(soup: BeautifulSoup) -> str | None:
    """Concatenate text of the standard description sections."""
    parts: list[str] = []
    for section in soup.select("div.article.article--details"):
        title_el = section.select_one("h3.article__header__text__title strong")
        if not title_el:
            continue
        section_title = title_el.get_text(" ", strip=True)
        if section_title not in DESCRIPTION_SECTIONS:
            continue
        dd = section.select_one("dd.article__content__view__field__value")
        if not dd:
            continue
        body = dd.get_text("\n", strip=True)
        if body:
            parts.append(f"\n{section_title}:\n{body}")
    desc = "\n".join(parts).strip()
    return desc or None


def _fetch_detail(session: requests.Session, apply_url: str) -> dict:
    r = session.get(apply_url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    return {
        "country": _dl_field_value(soup, "Country"),
        "city": _dl_field_value(soup, "City"),
        "workplace_location": _dl_field_value(soup, "Workplace location"),
        "employer_company": _dl_field_value(soup, "Employer company"),
        "domain": _dl_field_value(soup, "Domain"),
        "contract_type": _dl_field_value(soup, "Type of contract"),
        "experience": _dl_field_value(soup, "Experience"),
        "description": _extract_description(soup),
    }


def _compose_location(row: dict, detail: dict) -> str:
    parts = [detail.get("city"), detail.get("country") or row.get("country")]
    return ", ".join(p for p in parts if p)


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Listing phase (France, Avature filter 3834=41588)...", flush=True)

    all_rows: list[dict] = []
    seen_ids: set[str] = set()
    offset = 0
    while offset < TOTAL_OFFSET_HARD_CAP:
        html = _fetch_listing_page(session, offset)
        rows = _parse_listing_rows(html)
        if not rows:
            break
        for row in rows:
            if row["native_job_id"] in seen_ids:
                continue
            seen_ids.add(row["native_job_id"])
            all_rows.append(row)
        print(f"    {len(rows)} rows (cumulative {len(all_rows)})", flush=True)
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"  collected {len(all_rows)} France postings", flush=True)

    print("Filter phase (title regex Data/AI/ML)...", flush=True)
    kept = [r for r in all_rows if _in_scope(r)]
    print(
        f"  kept {len(kept)} (dropped {len(all_rows) - len(kept)} out-of-scope)",
        flush=True,
    )

    print(
        f"Enrichment phase: fetching {len(kept)} detail pages "
        f"(~{int(len(kept) * REQUEST_DELAY_SECONDS / 60)} min)...",
        flush=True,
    )

    out: dict[str, Job] = {}
    failed = 0
    for i, row in enumerate(kept, 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            detail = _fetch_detail(session, row["apply_url"])
        except Exception as exc:
            print(
                f"  [{i}/{len(kept)}] {row['native_job_id']} detail FAILED: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            detail = {}
            failed += 1

        job = Job(
            native_job_id=row["native_job_id"],
            title=row["title"],
            location=_compose_location(row, detail),
            category=detail.get("domain"),
            apply_url=row["apply_url"],
            employment_type=detail.get("contract_type") or row.get("employment_type"),
            description=detail.get("description"),
            posted_date=row.get("posted_date"),
            identifier=row["native_job_id"],
            raw_payload={**row, **{f"detail_{k}": v for k, v in detail.items()}},
        )
        if job.native_job_id in out:
            continue
        out[job.native_job_id] = job
        print(
            f"  [{i}/{len(kept)}] {job.native_job_id} {job.title!r} -> KEEP",
            flush=True,
        )

    elapsed = time.time() - started
    print(flush=True)
    print(
        f"  -> {len(out)} jobs in {elapsed:.1f}s "
        f"({failed} detail fetches failed)\n",
        flush=True,
    )
    return [asdict(j) for j in out.values()]


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
        desc = (j["description"] or "")[:200]
        desc = desc + ("..." if j["description"] and len(j["description"]) > 200 else "")
        print(f"[{j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
