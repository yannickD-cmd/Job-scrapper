# Accenture — filters in scope

| Axis | Kept value | Applied where |
|---|---|---|
| Country | **France** | server (POST body `jobCountry: "France"`) |
| Employee type | **Full-time** | client (per-row `employeeType`) |
| Skill / competence | **Software Engineering**, **AI & Data**, **Security**, **Engineering & Networks** | client (per-row `skill`) |
| City | **Paris** | client (per-row `jobCityState`) |

Source = the live `accenture.com` AEM-fronted JSON API (`POST /api/accenture/jobsearch/result`), not the Workday backend behind it.

City uses an "any-of" rule against the per-row `jobCityState` list, so a multi-city role like `["Lyon", "Paris"]` still qualifies.

Skill + city are client-side because the API silently rejects ad-hoc `jobFilters` payloads with a 0-byte 200 — cheaper to pull all France rows (5 pages × 50, ~10s) and filter locally.

**Source URL**: `https://www.accenture.com/fr-fr/careers/jobsearch`
**Scraper**: [scrapers/accenture/accenture.py](accenture.py) — see `EMPLOYEE_TYPES_IN_SCOPE`, `SKILLS_IN_SCOPE`, `CITIES_IN_SCOPE` constants to change.
