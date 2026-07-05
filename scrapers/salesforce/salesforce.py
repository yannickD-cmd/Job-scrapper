"""Salesforce job scraper — France, tech / Data & AI roles.

The France careers landing page (salesforce.com/company/careers/locations/emea/
france/) and the real board (careers.salesforce.com) are both Akamai Bot-Manager
guarded, so we don't touch them. Instead we hit the *static JSON export* the
board's own front-end downloads and renders client-side:

    https://a.sfdcstatic.com/digital/xsf/careers/prod/jobs_2.json

That URL is discovered in careers.js (component `careers.js` on the board):
`Xe()` builds `${base}/jobs_2.json` where the path segment is `Ke()` = the
`sfdcBase.env` ("prod"). `jobs_1.json` is a smaller "primary" slice the page
shows first; `jobs_2.json` is the full set (~1,500 reqs globally). Both have
`*_backup.json` twins we fall back to. The host is a plain CDN asset origin — no
bot protection, UA-agnostic — so this is CI-safe (unlike careers.salesforce.com).

Schema (Workday export flattened into `Report_Entry[]`):
  Job_Requisition_Ref_ID          -> native id, e.g. "JR349759"  (stable)
  Job_Posting_Title               -> title
  Job_Family_Group                -> category ("Software Engineering", "Sales", ...)
  Employee_Type                   -> "Regular" (CDI) / "Intern" / "New Grads" / ...
  Time_Type                       -> "Full time" / "Part time"
  Countries / Regions / Locations -> geo arrays (aggregate of primary + additional)
  Job_Requisition_Primary_Location-> "France - Paris"
  External_Job_Posting_Start_Date -> ISO YYYY-MM-DD (used as posted_date)
  External_Job_Posting_Site       -> apply URL (Salesforce's Workday external site)
  Job_Description                 -> HTML

Scope (locked 2026-07): France, Data & AI / tech, all employment types.
Salesforce's ATS has NO Data & AI family group and France is ~75% Sales/GTM, so
the keep-filter is:

  - Country gate: `Countries` contains "France". It's the aggregate of primary +
    additional locations, so a France posting always lists France here (verified:
    0 France-in-additional-only cases, 0 multi-country reqs including France).
  - `Software Engineering` family is kept WHOLESALE, every employment type — this
    is the only tech family the ATS exposes, and it's where the AI Builder
    (New Grads), Deployment Strategist and Forward Deployed Engineer roles live.
  - Any OTHER family is kept only if the title passes the shared `is_tech_role`
    predicate (catches e.g. "Data Analyst/AI" interns filed under Fixed Term).
  - `Sales` and `Customer Success` families are dropped even when the title
    matches — Salesforce names quota-carrying AE / pre-sales roles after products
    ("Account Executive - Data Foundation (MuleSoft + Informatica)", "Solution
    Engineer - Marketing & Data"), which `is_tech_role` would otherwise keep. GTM
    is out of scope for this board; excluding the family is consistent with
    is_tech_role's own business-development / technico-commercial hard-excludes.

Low current yield (a handful of roles) is expected and fine — like Mirakl, the
row count grows when Salesforce posts a Tech/Data CDI in France. Don't "fix" a
small return. To widen scope, add to TECH_FAMILY_GROUPS or relax GTM_FAMILY_GROUPS.
"""
from __future__ import annotations

import sys
import time
from dataclasses import asdict, dataclass

import requests
from bs4 import BeautifulSoup

from scrapers._relevance import is_tech_role

# Full job export + its backup twin (careers.js falls back to the backup too).
JOBS_URL = "https://a.sfdcstatic.com/digital/xsf/careers/prod/jobs_2.json"
JOBS_BACKUP_URL = "https://a.sfdcstatic.com/digital/xsf/careers/prod/jobs_2_backup.json"

COUNTRY_IN_SCOPE = "France"

# Kept wholesale (every employment type) — the ATS's only tech family group.
TECH_FAMILY_GROUPS = {"Software Engineering"}
# Dropped even when the title matches is_tech_role — quota / pre-sales roles.
GTM_FAMILY_GROUPS = {"Sales", "Customer Success"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
}

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.0


@dataclass
class Job:
    native_job_id: str          # Job_Requisition_Ref_ID, e.g. "JR349759"
    title: str
    location: str               # Job_Requisition_Primary_Location (+ additional)
    category: str | None        # Job_Family_Group
    apply_url: str              # External_Job_Posting_Site (Workday external site)
    employment_type: str        # Employee_Type (+ Time_Type when not "Full time")
    description: str | None = None
    posted_date: str | None = None    # External_Job_Posting_Start_Date, YYYY-MM-DD
    identifier: str | None = None      # = native_job_id (Salesforce's own ref)
    raw_payload: dict | None = None


def _fetch_records(session: requests.Session) -> list[dict]:
    last_exc: Exception | None = None
    for url in (JOBS_URL, JOBS_BACKUP_URL):
        try:
            print(f"  GET {url} ...", flush=True)
            response = session.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            records = payload.get("Report_Entry")
            if not isinstance(records, list):
                raise RuntimeError(
                    f"unexpected schema: Report_Entry is {type(records).__name__}, not list"
                )
            print(
                f"    {len(records)} records "
                f"(Total_Jobs={payload.get('Total_Jobs')}, "
                f"fetched={payload.get('Fetch_Timestamp')})",
                flush=True,
            )
            # A genuinely empty export means "the feed broke", not "no jobs" —
            # the DB empty-guard would then keep the last good rows. Fail loud so
            # the failure is logged instead of silently returning [].
            if not records:
                raise RuntimeError("Report_Entry is empty — feed likely broken")
            return records
        except Exception as exc:  # noqa: BLE001 — try the backup twin, then re-raise
            last_exc = exc
            print(f"    {type(exc).__name__}: {exc} — trying backup", flush=True)
            time.sleep(REQUEST_DELAY_SECONDS)
    raise RuntimeError(f"both jobs feeds failed: {last_exc}")


def _in_scope(rec: dict) -> bool:
    if COUNTRY_IN_SCOPE not in (rec.get("Countries") or []):
        return False
    family = (rec.get("Job_Family_Group") or "").strip()
    if family in TECH_FAMILY_GROUPS:
        return True
    if family in GTM_FAMILY_GROUPS:
        return False
    return is_tech_role(rec.get("Job_Posting_Title"))


def _location(rec: dict) -> str:
    primary = (rec.get("Job_Requisition_Primary_Location") or "").strip()
    additional = rec.get("Job_Requisition_Additional_Locations")
    if isinstance(additional, list) and additional:
        extra = "; ".join(str(a).strip() for a in additional if str(a).strip())
        if extra:
            return f"{primary}; {extra}" if primary else extra
    return primary


def _employment_type(rec: dict) -> str:
    emp = (rec.get("Employee_Type") or "").strip()
    time_type = (rec.get("Time_Type") or "").strip()
    # "Full time" is the default and adds no signal; surface anything else.
    if time_type and time_type.lower() != "full time":
        return f"{emp} / {time_type}".strip(" /")
    return emp


def _description(rec: dict) -> str | None:
    content = rec.get("Job_Description")
    if not content:
        return None
    # HTML with a few numeric entities (&#39; &#43;); BeautifulSoup decodes both.
    text = BeautifulSoup(content, "html.parser").get_text(" ", strip=True)
    return text or None


def _posted_date(rec: dict) -> str | None:
    raw = rec.get("External_Job_Posting_Start_Date")
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    return raw[:10]


def _rec_to_job(rec: dict) -> Job:
    ref_id = (rec.get("Job_Requisition_Ref_ID") or "").strip()
    if not ref_id:
        raise RuntimeError(
            f"Salesforce record missing Job_Requisition_Ref_ID "
            f"(title={rec.get('Job_Posting_Title')!r})"
        )
    apply_url = (rec.get("External_Job_Posting_Site") or "").strip()

    return Job(
        native_job_id=ref_id,
        title=(rec.get("Job_Posting_Title") or "").strip(),
        location=_location(rec),
        category=(rec.get("Job_Family_Group") or "").strip() or None,
        apply_url=apply_url,
        employment_type=_employment_type(rec),
        description=_description(rec),
        posted_date=_posted_date(rec),
        identifier=ref_id,
        raw_payload=rec,
    )


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Listing phase...", flush=True)
    records = _fetch_records(session)

    print("Filter phase...", flush=True)
    candidates = [r for r in records if _in_scope(r)]
    print(
        f"  kept {len(candidates)} (dropped {len(records) - len(candidates)} out-of-scope)",
        flush=True,
    )

    kept: dict[str, Job] = {}
    for rec in candidates:
        job = _rec_to_job(rec)
        if job.native_job_id in kept:
            continue
        kept[job.native_job_id] = job
        print(
            f"  {job.native_job_id} [{job.category} | {job.employment_type}] "
            f"{job.title!r} -> KEEP",
            flush=True,
        )

    elapsed = time.time() - started
    print(f"\n  -> {len(kept)} jobs in {elapsed:.1f}s\n", flush=True)
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
