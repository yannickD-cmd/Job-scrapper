# Morgan Stanley — filters kept

ATS: **Eightfold AI (PCSX)** at `morganstanley.eightfold.ai`.
`morganstanley.com/people` and `/careers/career-opportunities-search` are just
the marketing/redirect chain; the old in-house `resultset.json` API is dead.

Search: `GET /api/pcsx/search?domain=morganstanley.com&filter_country=France&filter_businessarea=<area>&start=<n>`
Detail: `GET /api/pcsx/position_details?position_id=<id>&domain=morganstanley.com&hl=en`

Scope locked 2026-07: **France · all tech-adjacent incl. Ops · all employment types.**

| Axis | Value kept | Applied where |
|---|---|---|
| Country | France | `filter_country=France` (server) + client-side France gate on location (`_is_france`, guards include-remote leakage) |
| Business area (wholesale) | `technology`, `technology and operations`, `operations` | `filter_businessarea` server facet, pass 1 (`KEEP_BUSINESS_AREAS`) |
| Role catch (other areas) | title passes `is_tech_role`, OR title/department matches strat / quant / data / eng / cloud / cyber / SRE… | client-side pass 2 (`_ROLE_CATCH` + `is_tech_role`) over all France roles |
| Employment type | all (Full time / Part time) | not filtered |

## Why a two-pass union

A position object exposes **no business-area field** — only `department` + title —
so business-area membership can only be learned by querying the server per area
(pass 1). But MS files quant/strat roles under the **Sales and Trading** business
area, not Technology; the only current French role
("IED - Equity Derivatives Strat - Associate/VP", dept `Strats`) is one of these.
Pass 1 misses it (wrong area) and `is_tech_role` alone misses it (no tech keyword
in the title), so pass 2 keeps it via the department/title `_ROLE_CATCH`.

## Expected yield

Near-empty. ~1 role in France today, **0** in the Technology business area — MS
engineering is London / Budapest / New York / Mumbai / Glasgow. Low (0–1) yield
is expected, like Mirakl / N26 / Salesforce; it grows when MS posts a France
tech/quant role. To tighten, drop `operations` from `KEEP_BUSINESS_AREAS`.
