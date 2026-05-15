"""Visa job scraper — France, Tech / Data / AI / ML / Cloud roles.

The public careers landing at https://corporate.visa.com/en/careers.html links
to a Workday ATS tenant at https://visa.wd5.myworkdayjobs.com/Visa. The Workday
JSON endpoints are public (no auth, no CSRF) and accept POSTed facet filters.

Listing (paginated, country-scoped via facet):
  POST https://visa.wd5.myworkdayjobs.com/wday/cxs/visa/Visa/jobs
  body: {"appliedFacets":{"locationCountry":[<id>]},
         "limit":20, "offset":<n>, "searchText":""}

Each posting in `jobPostings` exposes:
  - title
  - externalPath        # "/job/FR---Paris-France/<slug>_REF<id>"
  - locationsText       # "FR - Paris, France"
  - postedOn            # "Posted Today" / "Posted 30+ Days Ago" — useless for sorting
  - bulletFields[0]     # the REF<id> — same as jobReqId on the detail page
  - remoteType          # "Hybrid" / "Fully Remote" / etc. (optional)

Detail (for description + exact start date):
  GET https://visa.wd5.myworkdayjobs.com/wday/cxs/visa/Visa/job/<externalPath>
  returns jobPostingInfo with:
    - jobReqId            # the REF<id> string (our native_job_id)
    - startDate           # ISO date "YYYY-MM-DD" — the canonical posted_date
    - jobDescription      # HTML body
    - timeType            # "Full time" / "Part time"
    - remoteType          # same field as listing
    - externalUrl         # public job URL on visa.wd5.myworkdayjobs.com

Workday's listing payload does not return jobFamily per posting (only as a
facet aggregation), so role-type filtering happens client-side on the title
via AI_KEYWORDS_RE. France currently has ~10 total postings, so post-filtering
is cheap and matches the IBM/Allianz scraper pattern in this repo.

To widen scope, edit COUNTRY_FACET_ID (and SCOPE_COUNTRY label) or
AI_KEYWORDS_RE.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

TENANT_BASE = "https://visa.wd5.myworkdayjobs.com"
CXS_BASE = f"{TENANT_BASE}/wday/cxs/visa/Visa"
LISTING_URL = f"{CXS_BASE}/jobs"
DETAIL_URL_TEMPLATE = f"{CXS_BASE}{{external_path}}"
PUBLIC_JOB_URL_TEMPLATE = f"{TENANT_BASE}/Visa{{external_path}}"

PAGE_SIZE = 20
MAX_OFFSET = 2_000  # safety cap; France typically has < 20 postings total

SCOPE_COUNTRY = "France"
COUNTRY_FACET_ID = "54c5b6971ffb4bf0b116fe7651ec789a"  # France locationCountry id

# Title gate: keep only roles that look like Data / AI / ML / Cloud / SW Eng.
# Word-boundary based, case-insensitive.
AI_KEYWORDS_RE = re.compile(
    r"\b("
    r"AI|ML|MLOps|NLP|LLM|LLMs|GenAI"
    r"|Machine\s+Learning|Deep\s+Learning|Generative\s+AI|Foundation\s+Models?"
    r"|Data\s+(?:Scientist|Engineer|Analyst|Architect|Science|Engineering|Analytics)"
    r"|Applied\s+Scientist|Research\s+Scientist|Analytics"
    r"|Software\s+Engineer(?:ing)?|SW\s+Engineer"
    r"|Backend|Back-end|Frontend|Front-end|Full[-\s]?Stack"
    r"|Platform\s+Engineer(?:ing)?|Systems?\s+Engineer(?:ing)?"
    r"|Site\s+Reliability|SRE|DevOps|DevSecOps"
    r"|Cloud\s+(?:Engineer|Architect|Developer|Native)|Kubernetes|AWS|GCP|Azure"
    r"|Cyber\s*Security|Security\s+Engineer(?:ing)?"
    r"|Database\s+Engineer(?:ing)?|Solutions?\s+Architect"
    r")\b",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{TENANT_BASE}/Visa",
    "From": "yannickarieldossa@gmail.com",
}

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 0.7

REF_ID_RE = re.compile(r"(REF\d+[A-Z]?)", re.IGNORECASE)


@dataclass
class Job:
    native_job_id: str         # Workday jobReqId, e.g. "REF079498W"
    title: str
    location: str              # "FR - Paris, France"
    category: str | None       # Workday remoteType ("Hybrid"/"Fully Remote") — best signal exposed
    apply_url: str             # public myworkdayjobs.com job URL
    employment_type: str       # timeType ("Full time" / "Part time")
    description: str | None = None
    posted_date: str | None = None    # ISO YYYY-MM-DD from jobPostingInfo.startDate
    identifier: str | None = None     # same as native_job_id, kept for parity
    raw_payload: dict | None = None


def _post_listing(session: requests.Session, offset: int) -> dict:
    body = {
        "appliedFacets": {"locationCountry": [COUNTRY_FACET_ID]},
        "limit": PAGE_SIZE,
        "offset": offset,
        "searchText": "",
    }
    response = session.post(LISTING_URL, json=body, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def _crawl_listing(session: requests.Session) -> list[dict]:
    """Walk every page of the country-scoped listing."""
    all_postings: list[dict] = []
    seen: set[str] = set()
    offset = 0
    page_no = 1
    total = None
    while offset < MAX_OFFSET:
        data = _post_listing(session, offset)
        if total is None:
            total = data.get("total", 0)
            print(f"  Workday reports {total} total postings in {SCOPE_COUNTRY}", flush=True)
        postings = data.get("jobPostings", []) or []
        new = []
        for p in postings:
            ref_id = (p.get("bulletFields") or [None])[0]
            if not ref_id:
                m = REF_ID_RE.search(p.get("externalPath") or "")
                ref_id = m.group(1) if m else None
            if not ref_id or ref_id in seen:
                continue
            seen.add(ref_id)
            p["_ref_id"] = ref_id
            new.append(p)
        all_postings.extend(new)
        print(
            f"  page {page_no:>2} (offset={offset:>3}): "
            f"{len(postings)} on page, {len(new)} new, {len(all_postings)} total",
            flush=True,
        )
        if not postings or len(postings) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        page_no += 1
        time.sleep(REQUEST_DELAY_SECONDS)
    return all_postings


def _in_scope(posting: dict) -> bool:
    """Title gate — country is already enforced by the listing facet."""
    return bool(AI_KEYWORDS_RE.search(posting.get("title") or ""))


def _html_to_text(html: str | None) -> str | None:
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    # Normalise <br> to newlines so paragraph breaks survive get_text.
    for br in soup.find_all("br"):
        br.replace_with("\n")
    text = soup.get_text("\n", strip=True)
    # Collapse runs of 3+ blank lines.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _fetch_detail(session: requests.Session, external_path: str) -> dict | None:
    """Return the jobPostingInfo dict, or None on failure."""
    url = DETAIL_URL_TEMPLATE.format(external_path=external_path)
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json().get("jobPostingInfo")


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Listing phase...", flush=True)
    postings = _crawl_listing(session)
    print(
        f"  {len(postings)} {SCOPE_COUNTRY} postings in {time.time() - started:.1f}s\n",
        flush=True,
    )

    print("Filter phase: tech/data/AI/ML/cloud titles only...", flush=True)
    candidates = [p for p in postings if _in_scope(p)]
    print(
        f"  kept {len(candidates)} candidates "
        f"(dropped {len(postings) - len(candidates)} non-tech roles)\n",
        flush=True,
    )

    if not candidates:
        print("No matching France tech roles right now.", flush=True)
        return []

    print(
        f"Enrichment phase: fetching {len(candidates)} detail pages...",
        flush=True,
    )

    kept: dict[str, Job] = {}
    failed = 0

    for i, posting in enumerate(candidates, 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        ref_id = posting["_ref_id"]
        external_path = posting.get("externalPath") or ""
        try:
            info = _fetch_detail(session, external_path)
        except Exception as exc:
            print(
                f"  [{i}/{len(candidates)}] {ref_id} detail fetch FAILED: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            info = None
            failed += 1

        info = info or {}
        description = _html_to_text(info.get("jobDescription"))
        posted_date = info.get("startDate") or None  # already ISO YYYY-MM-DD
        time_type = info.get("timeType") or ""
        remote_type = info.get("remoteType") or posting.get("remoteType") or ""
        location = info.get("location") or posting.get("locationsText") or ""
        apply_url = (
            info.get("externalUrl")
            or PUBLIC_JOB_URL_TEMPLATE.format(external_path=external_path)
        )

        job = Job(
            native_job_id=ref_id,
            title=posting.get("title") or info.get("title") or "",
            location=location,
            category=remote_type or None,
            apply_url=apply_url,
            employment_type=time_type,
            description=description,
            posted_date=posted_date,
            identifier=None,
            raw_payload={
                "externalPath": external_path,
                "postedOn": posting.get("postedOn"),
                "remoteType": remote_type or None,
                "timeType": time_type or None,
                "locationsText": posting.get("locationsText"),
            },
        )
        kept[ref_id] = job
        print(
            f"  [{i}/{len(candidates)}] {ref_id} {job.title!r} -> KEEP "
            f"(posted {posted_date})",
            flush=True,
        )

    elapsed = time.time() - started
    print(flush=True)
    print("Enrichment summary:", flush=True)
    print(f"  kept                       : {len(kept)}", flush=True)
    print(f"  failed                     : {failed}", flush=True)
    print(f"  total runtime              : {elapsed:.1f}s", flush=True)

    return [asdict(j) for j in kept.values()]


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
        desc = (j["description"] or "").strip()
        desc = desc[:200] + ("..." if len(desc) > 200 else "")
        print(f"[{j['native_job_id']}] {j['title']}")
        print(f"  Location   : {j['location']}")
        print(f"  Remote     : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
