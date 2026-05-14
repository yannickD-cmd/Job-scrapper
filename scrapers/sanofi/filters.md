# Sanofi — filters in scope

| Axis | Kept value | Applied where |
|---|---|---|
| Country | **France** | server (URL path) |
| Category | **Digital Data & Technology** | client (from listing card HTML) |
| Job type | **Regular** | client (from detail-page JSON-LD `employmentType`) |

**Source URL pattern**: `https://jobs.sanofi.com/en/location/france-jobs/2649/3017382/2/{page}`
**Scraper**: [scrapers/sanofi/sanofi.py](sanofi.py) — see `CATEGORIES_IN_SCOPE` and `JOB_TYPES_IN_SCOPE` constants to change.
