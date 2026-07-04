# Datadog — filters kept

The Greenhouse boards API (`boards-api.greenhouse.io/v1/boards/datadog/jobs`)
has no server-side filter params, so every axis is applied client-side.

| axis | value | applied where |
|---|---|---|
| Country | France — `location.name` word-matches `france`; keeps "Paris, France", "Bordeaux, France; …", "France, Remote" and multi-country lists that include France | client-side (`FRANCE_RE`) |
| Department | `Dev Eng`, `Security` unconditionally; `Leadership` only when the posting has `Area - Engineering` metadata (separates eng managers from sales/marketing leadership) | client-side (`_in_scope`) |
| Employment type | metadata `Time Type` = `Full time`; fail-open when absent (all 411 postings carry it today). Interns/new-grads sit in the excluded `Early Career` dept | client-side (`_in_scope`) |
| Title keywords | none — departments define the scope | — |

Scope locked 2026-07-01: 16 France roles at build time (12 Dev Eng, 3
Leadership eng managers, 1 Security).
