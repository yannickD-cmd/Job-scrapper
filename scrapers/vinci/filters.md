# VINCI — filters applied

ATS: **Radancy / TalentBrew** (`jobs.vinci.com`). The page URL carries facets in
the *path* (geo radius + location hierarchy + categories). The AJAX endpoint
`/en/search-jobs/results` **ignores every filtering query param** and always
returns the full global board (~5 960 jobs), so all filtering is done
client-side from the listing cards after a full crawl.

| Axis | Value kept | Applied where |
|---|---|---|
| Country | France (metropolitan + DROM) | Card `job-location` region matched against a French-region allowlist (`_is_french`). Detail-page `addressCountry` is unreliable (often blank on FR rows), so the listing region is the gate; a detail page that *explicitly* names a foreign country drops the row. |
| Family / category | `IT / IT SYSTEMS` | Card `job-categories` ∈ `CATEGORIES_IN_SCOPE`. VINCI's taxonomy is construction-centric and has **no dedicated Data category** — this single family covers data, AI, BI, software, infra, security and SAP roles. Scope agreed with user: *Data & AI + Software/IT*. |
| Contract | `Permanent` (CDI) | Card `search-results--link-job-type` starts with `Permanent` (`CONTRACTS_IN_SCOPE`). Excludes Work-study/Fixed-term/Work-placement/International-assignment. |
| Title keyword | *(none)* | User chose to rely on the family selection alone. |

Result at build time (2026-06-04): 5 959 jobs crawled → **73 France · IT/IT SYSTEMS · CDI**.

## Why no server-side filtering
- The AJAX GET `/en/search-jobs/results` needs `SearchResultsModuleName=Search Results`
  to render cards, and `IsPagination=True` to drop the ~1.9 MB filters block from
  every response. But `fc`/`fl`/`Categories`/`LocationPath`/`Latitude`+`Distance`
  etc. are all **ignored** — total stays 5 959 regardless.
- Geo-radius filtering only works on the *server-rendered path page*, and a
  France-covering radius bleeds into bordering countries — not exact. The exact
  France count (3 495) comes from the country **facet**, which is path-only.
- Conclusion: crawl all pages via the fast AJAX endpoint, filter on card fields.

## Useful facet IDs (from `material/filters.html`, for reference)
- Country **France** = `3017382` (facet-type 2)
- Category **IT / IT SYSTEMS** = `32057920` (facet-type 1)
- Contract **Permanent** (custom facet `job_type`)
