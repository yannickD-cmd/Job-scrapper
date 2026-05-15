# Allianz — filters in scope

| Axis | Kept value(s) | Applied where |
|---|---|---|
| Country | **France** | client (`country == "France"`) |
| Category | **IT & Tech Engineering, Data & AI** | client (`category in {...}`) |
| Job level | *all* | no filter — includes Professional, Apprenticeship, Student, Entry, Management |
| Employment type | *all* | no filter — mix of Permanent / Temporary |
| Remote | *all* | no filter |
| Unit | *all* | no filter — Allianz Technology, Allianz France, Allianz Trade, etc. |

**Source URL** (no auth, SSR-rendered JSON inside HTML):
- `https://careers.allianz.com/global/en/search-results?from=<n>&size=<≤500>`
  - parse the `eagerLoadRefineSearch` blob → `data.jobs[]` and `data.aggregations[]`
  - Page size caps at 500. Total ≈2015 postings → 5 paginated SSR calls.
- The internal `/api/careers/searchJobs` endpoint exists but returns "Tenant not identified" for direct calls (session+CSRF-bound). Don't try to hit it.
- URL filter params like `?country=France` are accepted but **ignored** by the SSR. All filtering happens client-side after we collect every page.

**Backing ATS**: SAP SuccessFactors, company `AZGROUPPROD`.

**Apply URL pattern (used by the public site's "Apply" button)**:
- `https://career5.successfactors.eu/careers?company=AZGROUPPROD&career_job_req_id=<jobId>&career_ns=job_application`
- The scraper instead stores the canonical careers-site detail page as `apply_url`: `https://careers.allianz.com/global/en/job/<jobId>` — human-readable and lands on the page with the "Apply" button.

**Sanity check (2026-05-16)**: site aggregation shows 412 France postings overall. After deduping by jobId across all 5 SSR pages we get ~384 unique France jobs (≈28 multi-location dupes collapse). Of those, `category in {"IT & Tech Engineering", "Data & AI"}` matches **6** postings.

**Native job id**: SuccessFactors requisition id (`jobId == reqId`, integer string).

**Scraper**: [scrapers/allianz/allianz.py](allianz.py) — see `COUNTRIES_IN_SCOPE` and `CATEGORIES_IN_SCOPE` to change scope.
