# BCG (careers.bcg.com) — filters applied

Phenom CareerConnect (refNum `BCG1US`). Single public JSON API:
`POST https://careers.bcg.com/widgets` (ddoKey `refineSearch`).

| Axis | Value kept | Applied where |
|---|---|---|
| Country | France | server-side `selected_fields.country=["France"]` |
| Category | Data Science and Analytics **+** Technology and Engineering | client-side, checked against `category` ∪ `multi_category` |
| subCategory | exclude `IT Consulting`, `Specialty Consulting` | client-side — drops **BCG Platinion** tech-consulting (shares the Tech&Eng category) |
| Category (excluded) | Design Strategy | not kept — "AI Experience Designer" is a design role, out of scope |
| Employment type | all kept raw | no CDI/CDD/stage facet exists (`type` = Full-Time/Part-Time hours only); contract filtering is dashboard-side |

Net France yield today ≈ **4** (BCG X: Forward Deployed AI Engineer ×2, Forward
Deployed AI Scientist ×2). Low is expected — BCG X's France build team is small;
the bulk of the Paris office is core strategy consulting + Platinion.

Notes:
- `reqId` (bare integer) = `native_job_id`; `jobSeqNo` (e.g. `BCG1US58176EXTERNALENGLOBAL`) = `identifier` and detail-page slug.
- Detail page `https://careers.bcg.com/global/en/job/<jobSeqNo>` serves a JSON-LD `JobPosting` — used only for the full description (best-effort; teaser fallback).
- Listing `postedDate` (ISO) is trusted over the JSON-LD `datePosted`, which is SEO-refreshed.
