# BNP Paribas — filters in scope

| Axis | Kept value | Applied where |
|---|---|---|
| Country | **France** | server (URL path) |
| Domain / job family | **Digital transformation and data** | server (URL path) |
| Contract type | **Permanent** | server (URL path) |

All three filters live entirely in the URL — no client-side filtering pass, every row the listing returns is already in scope by construction. Order matters: `type → domain → country` (any other order 404s).

**Source URL**: `https://group.bnpparibas/en/careers/all-job-offers/permanent/digital-transformation-and-data/france`
**Scraper**: [scrapers/bnp/bnp.py](bnp.py) — see `BASE_URL` constant to change.
