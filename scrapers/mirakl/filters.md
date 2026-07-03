# Mirakl — filters kept

Source: Greenhouse Job Board API, board `mirakl`
(`boards-api.greenhouse.io/v1/boards/mirakl/jobs?content=true`, one GET).
The API has no filter params — everything below is applied client-side.

| Axis | Value kept | Applied where |
|---|---|---|
| Country | France (`location.name` word-matches "France"; multi-location kept if any entry is French) | `_in_scope` |
| Department | `Tech` only (Sales / Connect / Marketing / G&A dropped) | `_in_scope` |
| Employment type | CDI-ish only — board has no type field, so titles matching intern/internship/apprentice/apprenticeship/apprenti(e)/alternance/alternant(e)/stage/stagiaire/freelance are dropped | `_in_scope` (`EXCLUDED_TITLE_RE`) |

Note (2026-07, scope lock): the Tech department held only 2 Paris
apprenticeships — both title-excluded — so 0 rows is the expected yield
until Mirakl posts a Tech CDI. Built as cheap insurance to catch that day
one; empty runs are safe (DB empty-guard closes nothing, logs success).
