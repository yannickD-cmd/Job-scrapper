# Air Liquide — filters applied

Source: Workday CXS JSON API behind the global board at
`airliquidehr.wd3.myworkdayjobs.com/fr-FR/AirLiquideExternalCareer`.
`POST .../wday/cxs/airliquidehr/AirLiquideExternalCareer/jobs` with
`appliedFacets`. All three axes below are server-side facets, so the listing
already comes back scoped — no client-side category/title filtering.

| Axis | Kept | Applied where (facet) |
|---|---|---|
| Country | France | `appliedFacets.locationCountry = ["54c5b6971ffb4bf0b116fe7651ec789a"]` |
| Contract | CDI ("Regular - Open ended") | `appliedFacets.workerSubType = ["431d4efb2c9c01cabd63a83fa900d90c"]` |
| Family | Digital & IT | `appliedFacets.jobFamilyGroup`, looped per family in `FAMILIES` |

## Facet reference (discovered via the `facets` block of an unfiltered call)

`jobFamilyGroup` (Job Category) values, global counts:

| id | family | global count |
|---|---|---|
| `93b630c7b5ff018c34308ce510054f46` | **Digital & IT** (kept) | 19 |
| `93b630c7b5ff0105853483e510054346` | Research - Engineering - Technology | 114 |
| `93b630c7b5ff014b6ce081e510054146` | Operations | 559 |
| `93b630c7b5ff014e1a9592e510055946` | Management / Administration | 197 |
| `93b630c7b5ff0198a76386e510054746` | Sales & Business Management | 174 |
| (others: Finance, HR, HSE, Procurement, Marketing, Legal, Comms, …) | | |

`workerSubType` (Job Type): CDI = `431d4efb2c9c01cabd63a83fa900d90c`
("Regular - Open ended"). Others available but out of scope: Regular - Fixed
term (CDD), Internship (Trainee), Apprenticeship, Consultant/Freelance,
Temporary, External/Contract Worker, VIE, Seasonal.

`locationCountry`: France = `54c5b6971ffb4bf0b116fe7651ec789a` (357 jobs, all
families/types). Same France GUID as the Rothschild Workday tenant — Workday
uses a global country reference.

## Scope note — where do the Data/AI roles live?

Air Liquide is an industrial-gas group: "Digital & IT" is mostly IT / infra /
SAP / cybersecurity, and is small in France (only ~6 roles across all contract
types). Genuine Data/AI roles may instead be classified under
"Research - Engineering - Technology" (114 global, but mostly process / R&D
engineering, not data). Widening to that family was explicitly declined for this
scope; the locked decision is Digital & IT only, category facet alone (no title
keyword gate). To revisit, uncomment the R-E-T id in `FAMILIES` and consider a
title gate so non-data engineering roles don't flood in.

## Notes

- Dedup key = `native_job_id` = requisition id from listing `bulletFields[0]`
  (e.g. `R10092364`). The detail's `jobPostingInfo.id` (internal hash) is stored
  as `identifier` only.
- `posted_date` = detail's `startDate` (ISO). The listing only gives a relative
  "Posted N Days Ago".
- The endpoint is Cloudflare-fronted and rate-limits aggressively on bursts
  (empty-body HTTP 400, ~60s recovery). The scraper paces requests ≥2s, clears
  cookies, and backs off 35s on a 400. Steady-state request count is low, so it
  runs fine from GitHub Actions (unlike Safran/BNP).
