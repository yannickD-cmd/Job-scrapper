# Richemont — filters applied

ATS: **Workday CXS** (tenant `richemont`, site `richemont`,
`richemont.wd3.myworkdayjobs.com`). careers.richemont.com is only a marketing
wrapper over this Workday board.

| Axis | Value kept | Applied where |
|---|---|---|
| Country | France | `locationCountry` facet (WID `54c5b697…ec789a`, server-side) |
| Employment type | CDI | `workerSubType = Permanent` facet (server-side; **only** place this is exposed — detail JSON has no worker-type field) |
| Category | Technology + Data | `jobFamilyGroup` facet, looped one id per request so each row is tagged with its family as `category` |
| Title gate | *(none)* | The two families are already pure tech, so `is_tech_role` is **not** applied (prefer-platform-category rule) |

## Group-wide JOB FUNCTION facet (all countries, for reference)

Commercial 523 · Marketing 110 · Supply Chain 88 · Manufacturing 80 ·
**Technology 75** · Finance 57 · HR 57 · Customer Services 49 · R&D 32 · …
· **Data 10**. France is overwhelmingly retail/Commercial; tech is centralised
at the Geneva HQ.

## Yield note (why this is a low-count scraper, not a broken one)

- France + **Technology** + Permanent = **0** today. The ~3 France Technology
  roles (Product Owner, Program Manager, Responsable Solutions Digital) are all
  **fixed-term / assignee**, so the CDI gate drops them.
- France + **Data** + Permanent = **2** (Marketing Data Scientist, Client Data
  Analyst). Plus 1 Data **STAGE** (intern) correctly excluded by the CDI gate.
- So ~2 rows is the expected steady state. Not a bug — same shape as
  N26 / SAP / Mirakl / Salesforce.

## To widen later (one-liners)

- Include fixed-term tech/data → add the Fixed-Term Contract id to a
  `FILTER_WORKERSUBTYPE` list (would surface the 3 Technology PM roles + the Data
  stage). Note: employment_type would then need to reflect CDD, not hard-coded "CDI".
- Add more families → append ids to `FAMILIES` (e.g. "Research Innovation Product
  Development" for watchmaking R&D, but expect mostly non-software engineering).
