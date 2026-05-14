# L'Oréal — filters in scope

| Axis | Kept value | Applied where |
|---|---|---|
| Country | **France** | server (URL facet `?3_110_3=18022`) |
| Function | **Tech**, **Data** | client (from per-row `dataLayer` eventLabel) |
| Contract type | **Permanent** | client (from per-row `dataLayer` eventLabel) |

Tolerance note: rows whose dataLayer `contract_type` slot is blank but whose `function` is in scope are **kept tentatively** (probed sample: jobId 240207 "Data Project Manager, HR Domain" — function=Tech, contract_type empty on L'Oréal's side). Better to surface a borderline tech role than silently lose it over missing metadata.

**Source URL**: `https://careers.loreal.com/en_US/jobs/SearchJobs?3_110_3=18022`
**Scraper**: [scrapers/loreal/loreal.py](loreal.py) — see `FRANCE_FACET`, `CONTRACT_TYPES_IN_SCOPE`, `FUNCTIONS_IN_SCOPE` constants to change.
