# Decathlon Digital — filters in scope

| Axis | Kept value | Applied where |
|---|---|---|
| Country | **France** | client (Greenhouse metadata `Job posting country == FRANCE`) |
| Employment Type | **Permanent (full time 100%)** | client (Greenhouse metadata `Employment Type`) |
| Team (department) | **Engineering**, **Operations**, **Data** | client (job `departments[0].name`) |

Maps to the two team buckets on the public site:
- *Engineering & Ops* → `Engineering` + `Operations`
- *Data science & engineering* → `Data`

Out of scope on the same boards: `Cybersecurity`, `Product Management`, `PMO`, `Decathlon Digital` (catch-all). Apprenticeship and Internship contracts are also dropped — the filter targets CDI-equivalent only.

**Source URLs** (no auth, JSON, single response — Greenhouse public board API):
- FR board: `https://boards-api.greenhouse.io/v1/boards/decathlontechnology/jobs?content=true`
- EN board: `https://boards-api.greenhouse.io/v1/boards/decathlontechnologyen/jobs?content=true`

The two boards mirror each other for many roles (same `requisition_id`, different Greenhouse `id`). We dedupe on `requisition_id`, preferring the FR row; EN-only postings are kept.

**Scraper**: [scrapers/decathlon/decathlon.py](decathlon.py) — see `TEAMS_IN_SCOPE`, `EMPLOYMENT_TYPES_IN_SCOPE`, `COUNTRY_IN_SCOPE` constants to change.
