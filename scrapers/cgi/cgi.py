"""CGI France job scraper — Data/AI/ML scope.

ATS: Njoyn (CGI's career portal, CLID=21001). The public listing UI at
https://cgi.njoyn.com/corp/xweb/xweb.asp?CLID=21001&page=joblisting&lang=1
is server-rendered ASP. It sits behind **Radware/ShieldSquare** (hCaptcha
challenge) — plain `requests` gets a captcha page. We use curl_cffi with
chrome131 impersonation (same trick as L'Oréal) which forges a real Chrome
TLS handshake and clears the check.

Filter strategy (server-side country, client-side category):
- COUNTRY: query param `&CountryID=FR` filters server-side to France.
  The form's POST flow with inp_Country_CGI=FR is silently ignored (returns
  the full 2948-row listing); only the GET param works. Confirmed by
  probing — 8 pages of France-only results vs 59 pages without the param.
- CATEGORY: the listing table emits the category as a plain <td> in each
  row, so we filter in-process. No need to loop the portal once per
  category.

Scope (399 France postings as of 2026-05; ~50 after filtering):
- Category "Analytics and Emerging Digital Technologies" → keep all.
  This is CGI's pure Data/AI vertical: Data Engineer (Snowflake,
  Databricks, Spark, DBT), Data Scientist, Consultant IA, etc.
- Category "Software Development / Engineering" → keep only titles that
  match DATA_AI_TITLE_RE. The SWE bucket is dominated by generic
  Java/.NET/Angular jobs; we only want the data/AI-flavored ones
  ("Data & AI Engineer", "AI/ML Engineer", "Ingénieur IA", "Tech Lead
  Java / IA", "Data Engineer DBT", etc.).

Listing row shape (each <tr> in the results table):
  <tr HasMultipleLocations='0' RemoteWork='False' CountryIDs='FR'>
    <td><a href='xweb.asp?...Jobid=J0526-1349&BRID=1300825&lang=1'>
        J0526-1349
    </a></td>
    <td>Senior IT Project Manager F/H</td>
    <td>Business Consulting, Strategy and Digital Transformation</td>
    <td>Lyon</td>
    <td name='CountryCell'>France</td>
  </tr>

Detail page shape (Page=JobDetails):
  <span class="bolder">Category:</span> <span>...</span>
  <span class="bolder">Main location:</span> <span>France, Rhône, Lyon</span>
  <span class="bolder">Position ID:</span> <span>J0526-1349</span>
  <span class="bolder">Employment Type:</span> <span>Full Time</span>
  <h2>Position Description:</h2> <p>...</p>
  <h2>Your future duties and responsibilities:</h2> ...
  <h2>Required qualifications...:</h2> ...
  <h2>Skills:</h2> <ul><li>...</li></ul>
  <h2>What you can expect from us:</h2>   <-- boilerplate; we cut here.

Pagination: `&pn=N` (1-indexed). The page emits "Page X of N" so we stop
once N is reached. 50 rows per page is fixed by the portal.

native_job_id = Position ID (e.g. "J0526-1349"). Stable, human-readable,
and the value CGI itself uses in every public surface.

To widen scope, edit CATEGORIES_KEEP_ALL, CATEGORIES_KEYWORD_FILTER, and
DATA_AI_TITLE_RE.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

LISTING_URL = (
    "https://cgi.njoyn.com/corp/xweb/xweb.asp"
    "?CLID=21001&page=joblisting&lang=1&CountryID=FR"
)
DETAIL_BASE = "https://cgi.njoyn.com/corp/xweb/"

# Server-side filter: France only (see module docstring).
COUNTRY_CODE = "FR"

# Categories whose every posting we keep. CGI's pure Data/AI vertical.
CATEGORIES_KEEP_ALL: set[str] = {
    "Analytics and Emerging Digital Technologies",
}

# Categories we keep only if the title matches DATA_AI_TITLE_RE. The SWE
# bucket is mostly generic Java/.NET/Angular — we filter for data/AI roles.
CATEGORIES_KEYWORD_FILTER: set[str] = {
    "Software Development / Engineering",
    "Software Development/ Engineering",  # CGI is inconsistent about the slash spacing
    "Software Development/Engineering",
}

# Title regex for the keyword-filter bucket. Covers French + English data/AI
# terminology: Data Engineer, Data Scientist, AI/ML Engineer, Ingénieur IA,
# NLP, LLM, generative AI, Databricks/Snowflake/Spark/Kafka/DBT roles.
DATA_AI_TITLE_RE = re.compile(
    r"\b("
    r"data\s*(?:engineer|scientist|analyst|architect|lead)"
    r"|data\s*&?\s*ai"
    r"|ai\s*/?\s*ml"
    r"|ml\s*engineer"
    r"|machine\s*learning"
    r"|ing[eé]nieur[(e)]*\s+ia"
    r"|tech\s*lead\s+(?:java\s*/\s*ia|ia)"
    r"|consultant[(e)]*\s+(?:ia|data)"
    r"|\bia\s*/\s*ml"
    r"|gen(?:erative)?\s*ai"
    r"|\bllm\b"
    r"|\bnlp\b"
    r"|databricks|snowflake|spark|dbt|kafka"
    r"|data\s*streaming"
    r"|mlops|dataops"
    r")\b",
    re.I,
)

HEADERS_FROM = "yannickarieldossa@gmail.com"
IMPERSONATE_PROFILE = "chrome131"
REQUEST_DELAY_SECONDS = 1.5
REQUEST_TIMEOUT = 30

# h2 headers that mark the start of the standard CGI footer boilerplate
# (legal disclaimers, "What you can expect from us"). Description capture
# stops at the first one of these we hit.
DESCRIPTION_END_HEADINGS = (
    "What you can expect from us",
    "How can we help",
    "Insights you can act on",
    "Company",
    "Follow us",
)

PAGINATION_RE = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)", re.I)


@dataclass
class Job:
    native_job_id: str       # CGI Position ID, e.g. "J0526-1349"
    title: str
    location: str            # City as shown in the listing table
    category: str            # Job category from the listing table
    apply_url: str           # absolute JobDetails URL
    employment_type: str | None = None    # from detail page (Full Time / Internship / ...)
    description: str | None = None
    posted_date: str | None = None        # Njoyn does not expose this
    identifier: str | None = None         # internal BRID (kept for parity)
    raw_payload: dict | None = None


def _make_session() -> cffi_requests.Session:
    s = cffi_requests.Session(impersonate=IMPERSONATE_PROFILE)
    s.headers.update({
        "From": HEADERS_FROM,
        "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    })
    return s


def _fetch_listing_page(session: cffi_requests.Session, page_num: int) -> str:
    url = LISTING_URL if page_num == 1 else f"{LISTING_URL}&pn={page_num}"
    r = session.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    r.encoding = "utf-8"
    if "Radware Captcha" in r.text or "We apologize for the inconvenience" in r.text:
        raise RuntimeError(
            f"cgi.njoyn.com served a Radware captcha on page {page_num} — "
            "TLS impersonation may need updating."
        )
    return r.text


def _parse_listing_rows(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict] = []
    for tr in soup.find_all("tr"):
        a = tr.find("a", href=re.compile(r"Page=JobDetails", re.I))
        if not a:
            continue
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        href = a.get("href", "")
        m_jobid = re.search(r"Jobid=([^&]+)", href, re.I)
        m_brid = re.search(r"BRID=([^&]+)", href, re.I)
        rows.append({
            "native_job_id": a.get_text(strip=True),
            "title": tds[1].get_text(strip=True),
            "category": tds[2].get_text(strip=True),
            "location": tds[3].get_text(strip=True),
            "country": tds[4].get_text(strip=True),
            "jobid_url": m_jobid.group(1) if m_jobid else None,
            "brid": m_brid.group(1) if m_brid else None,
            "apply_url": DETAIL_BASE + href if not href.startswith("http") else href,
        })
    return rows


def _last_page(html: str) -> int:
    m = PAGINATION_RE.search(html)
    return int(m.group(2)) if m else 1


def _in_scope(row: dict) -> bool:
    if row["country"] != "France":
        return False
    cat = row["category"]
    if cat in CATEGORIES_KEEP_ALL:
        return True
    if cat in CATEGORIES_KEYWORD_FILTER:
        return bool(DATA_AI_TITLE_RE.search(row["title"]))
    return False


def _label_value(soup: BeautifulSoup, label: str) -> str | None:
    """Find <span class="bolder">{label}:</span> and return the next sibling text."""
    for span in soup.find_all("span", class_="bolder"):
        if span.get_text(strip=True).rstrip(":").strip() == label:
            sib = span.next_sibling
            while sib is not None:
                if hasattr(sib, "get_text"):
                    t = sib.get_text(" ", strip=True)
                    if t:
                        return t
                elif isinstance(sib, str):
                    t = sib.strip()
                    if t:
                        return t
                sib = sib.next_sibling
    return None


def _extract_description(soup: BeautifulSoup) -> str | None:
    """Walk from 'Position Description:' h2 forward, accumulate h2/p/li text
    until a footer/boilerplate heading."""
    start = None
    for h2 in soup.find_all("h2"):
        if "Position Description" in h2.get_text(strip=True):
            start = h2
            break
    if start is None:
        return None
    parts: list[str] = []
    el = start
    while True:
        el = el.find_next()
        if el is None:
            break
        if getattr(el, "name", None) == "h2":
            t = el.get_text(strip=True)
            if any(end in t for end in DESCRIPTION_END_HEADINGS):
                break
            parts.append("\n" + t.rstrip(":") + ":")
        elif getattr(el, "name", None) in ("p", "li"):
            t = el.get_text(" ", strip=True)
            if t:
                parts.append(("- " if el.name == "li" else "") + t)
    desc = "\n".join(parts).strip()
    return desc or None


def _fetch_detail(session: cffi_requests.Session, apply_url: str) -> tuple[str | None, str | None]:
    r = session.get(apply_url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    employment_type = _label_value(soup, "Employment Type")
    description = _extract_description(soup)
    return employment_type, description


def scrape() -> list[dict]:
    session = _make_session()

    started = time.time()
    print("Listing phase...", flush=True)
    first_html = _fetch_listing_page(session, 1)
    total_pages = _last_page(first_html)
    print(f"  pagination: {total_pages} page(s)", flush=True)

    all_rows: list[dict] = []
    all_rows.extend(_parse_listing_rows(first_html))
    for pn in range(2, total_pages + 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        print(f"  page {pn}/{total_pages}...", flush=True)
        page_html = _fetch_listing_page(session, pn)
        all_rows.extend(_parse_listing_rows(page_html))
    print(f"  {len(all_rows)} France postings listed", flush=True)

    print("Filter phase...", flush=True)
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
            emp_type, description = _fetch_detail(session, row["apply_url"])
        except Exception as exc:
            print(
                f"  [{i}/{len(kept)}] {row['native_job_id']} detail FAILED: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            emp_type, description = None, None
            failed += 1

        job = Job(
            native_job_id=row["native_job_id"],
            title=row["title"],
            location=row["location"],
            category=row["category"],
            apply_url=row["apply_url"],
            employment_type=emp_type,
            description=description,
            identifier=row.get("brid"),
            raw_payload=row,
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
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
