# Stripe — applied filters

Source: `stripe.com/jobs/search` (server-rendered HTML table, `?skip=N` pagination,
100 rows/page). No JSON API, no JSON-LD, no posted date published anywhere.

| axis | value kept | applied where |
|---|---|---|
| Geo | country code ∈ {GB, IE, FR, LU, CH} | listing row — flag `alt` / `Flag--country<CC>` |
| Category | Software Engineering + Data/AI/ML | listing row — title-keyword include/exclude |
| Employment | Full time, Internship, Apprenticeship (+ unknown kept) | detail page — JobDetailCard "Job type" |

## Notes / decisions

- **Geo = London, Dublin, Luxembourg, Switzerland, Paris + remote.** Mapped to ISO
  codes `GB, IE, FR, LU, CH`. The same code covers office roles *and* "Remote in
  <country>" roles, so remote-in-scope is included for free.
- **Title filter, not team filter.** Stripe "teams" are product areas (Payments,
  Money Movement, Terminal…), each mixing engineers, PMs, designers and sales —
  too coarse to isolate Software/Data. So category is decided by the title:
  include `engineer|software|developer|backend|frontend|full-stack|SRE|devops|
  data scien|data engineer|data analyst|analytics|machine learning|ML|AI|applied
  scien`, minus an exclude that drops GTM/pre-sales titles that still say
  "engineer" (`sales engineer`, `solutions architect/engineer`, `customer
  engineer`, `developer advocate`, …).
- **No posted_date.** Stripe never renders one; `posted_date=None`. Dedup is by
  `native_job_id` (the numeric id in the listing URL), so this is harmless.
- **Geo is read from the listing's single primary location.** A role open in
  several offices shows one country in the row; if its primary is outside scope it
  can be missed. Acceptable for v1 — revisit only if roles look under-counted.
