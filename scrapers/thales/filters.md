# Thales — filters in scope

| Axis | Kept value | Applied where |
|---|---|---|
| Country | **France** | client (per-row `country`) |
| Hiring type | **Permanent** | client (per-row `workerSubType == "Regular Employee"`) |
| Category | **Software**, **Information Systems / Information Technology**, **Engineering & Technical Specialities** | client (per-row `multi_category[0].category`) |
| City | **Issy-les-Moulineaux**, **Paris 9e Arrondissement**, **Rungis** | client (per-row `city`) |

Source = the live `careers.thalesgroup.com` sitemap + each detail page's embedded `phApp.ddo.jobDetail.data.job` JSON object. Thales runs on Phenom People (tenant `TGPTGWGLOBAL`); the widget XHR API (`/widgets`, `/api/jobs`) is closed to unauthenticated callers, and the search-results SSR ignores URL filter params — see the module docstring for the full diagnosis.

Hiring type "Permanent" maps to `workerSubType: "Regular Employee"` in the DDO, *not* to the schema.org `employmentType` field (which on Thales France is always `"Full time"` — that's worker type, not hiring type). Apprenticeships surface as `workerSubType: "Apprentice (Fixed Term)"`, e.g. titles prefixed `ALTERNANCE`.

City uses Phenom's canonical names — Thales France posts under Paris arrondissements (e.g. "Paris 9e Arrondissement"), not the generic `"Paris"`.

Two-pass scrape (same shape as BNP/Sanofi):

1. **Inventory.** Walks `sitemap{1..8}.xml` (~3569 `/job/<reqId>/...` URLs). Sitemap entries carry only `<loc>` + `<lastmod>` — no facet metadata.
2. **Enrichment.** Fetches every detail page in parallel (6 workers, 150–450 ms jitter), brace-balances `phApp.ddo = {...};` out of the inline script, and filters client-side. Typical runtime: ~10 min for the full France/Permanent/3-category scope.

**Source URL**: `https://careers.thalesgroup.com/global/en/`
**Scraper**: [thales.py](thales.py) — see `SCOPE_CATEGORIES` / `SCOPE_CITIES` constants to change.
