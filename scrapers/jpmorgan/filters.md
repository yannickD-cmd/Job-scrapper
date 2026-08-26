# JPMorganChase — filters applied

Board: Oracle Recruiting Cloud, tenant `jpmc`, site `CX_1001`
(`careers.jpmorgan.com` is a marketing redirect). Same ATS engine as
[scrapers/hermes/hermes.py](../hermes/hermes.py).

| axis | value kept | applied where |
|---|---|---|
| Country | `PrimaryLocationCountry == "FR"` | client-side, after a full-board crawl |
| Job family | 10 wholesale tech families | client-side on `JobFamily` |
| Content | data/AI signal in the full JD | client-side on `ExternalDescriptionStr` |
| Requisition type | all (Professional + Campus) | not filtered |
| **Title** | **never used** | — |

## Why there is no title gate

Measured 2026-08-23 on the live board:

| stage | rows |
|---|---|
| whole board | 7340 |
| `PrimaryLocationCountry == FR` | 23 |
| passing the category-or-content gate | 5 |

**Not one of the 23 France requisitions carries a technology JobFunction.**
The tech families are real and large board-wide — Software Engineering 1054,
Predictive Science 164, Risk Analytics/Modeling 43 — but have zero France rows
today. France is Private Banking, Markets and Corporate Banking.

So on this board a category-only gate returns **0**, and a title gate returns
**0** as well. What actually finds the roles is the job content:

| kept role | JobFunction | why it was kept |
|---|---|---|
| Quant Model Risk Auditor | Internal Audit | JD: "Review complex models and build AI/ML tools…" — 9 signals |
| ALM Risk Analyst | Risk | Python, Alteryx, Tableau, AI, quantitative |
| ALM Risk – Senior Associate/VP | Risk | same stack |
| EMEA Equity Delta One Trading | Program Analysts & Associate | python, automation, quantitative |
| EMEA Flow Single Stock Options Trader | Program Analysts & Associate | python, quantitative |

The `Quant Model Risk Auditor` is the case that motivated the scraper: filed
under **Auditing**, titled like an audit job, and actually an AI/ML modelling
role. Both a category gate and a title gate discard it.

## Content gate shape

Two tiers, so that one-line boilerplate ("strong quantitative skills") in a
banking JD cannot carry a row on its own:

- **STRONG** — one hit is enough. `machine learning`, `artificial intelligence`,
  `ai`, `ml`, `nlp`, `llm`, `data science`, `data engineer`, `big data`,
  `model risk`, `model validation`, `quantitative model`, `econometric`, …
- **SUPPORTING** — two distinct hits needed. `python`, `sql`, `spark`,
  `tableau`, `alteryx`, `aws`, `quantitative`, `analytics`, `automation`,
  `algorithm`, `modelling`, …

Verified against all 23 France rows: the five above pass; the private bankers,
FX sales, marketing associate and the Campus internships that merely ask for
"quantitative skills" all fail with 0–1 supporting signals.

## Crawl shape

Full-board crawl (7340 rows, 37 pages at `limit=200`, ~40s) then client-side
country filter. The server-side location facet was **measured to be lossy in
both directions**: `selectedLocationsFacet=300000036802490` returns 24 rows —
it includes a Frankfurt requisition whose secondary location is Paris, and the
true France count is 23. A facet loop that silently under-returns produces a
non-empty partial result, which slips past `db.persist_run_results`' empty
guard and retires the missing rows.

## Planned outages

Oracle Fusion pods take scheduled maintenance windows and answer **every**
endpoint with `HTTP 503` and a `Planned Outage` HTML page (observed 2026-08-22).
`_request` detects this and raises `PlannedOutage` immediately without retrying,
so the run fails loudly instead of returning `[]` and looking like a
silent-zero scrape.

## Identity

`native_job_id` = ORC `Id` (e.g. `210768512`) — the id in the public job URL
`…/sites/CX_1001/job/<Id>`. `RequisitionId` is kept as `identifier`.
