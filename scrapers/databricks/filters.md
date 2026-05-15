# Databricks — filters in scope

| Axis | Kept value | Applied where |
|---|---|---|
| Department | **Engineering** (Greenhouse dept `4001015002`, including its sub-teams via `parent_id`) | client (post-fetch) |
| Country | **France** (substring match on `France` / `Paris` in `location.name` and `offices[].location`) | client (post-fetch) |

Databricks's careers site is backed by Greenhouse's public board API, which returns the entire open-positions catalog in one shot and exposes no server-side filters on the public endpoint. Both filters are applied client-side after the fetch.

**Source URL** (UI equivalent): `https://www.databricks.com/company/careers/open-positions`
**Greenhouse endpoint**: `https://boards-api.greenhouse.io/v1/boards/databricks/jobs?content=true`
**Scraper**: [scrapers/databricks/databricks.py](databricks.py) — see `ENGINEERING_DEPT_ID` and `FRANCE_LOCATION_TOKENS` to change scope.
