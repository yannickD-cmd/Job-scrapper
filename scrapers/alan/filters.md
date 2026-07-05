# Alan — filters applied

ATS: **Ashby** public posting API (`api.ashbyhq.com/posting-api/job-board/alan`),
single anonymous GET returning the whole board (descriptions inline).

| axis | kept value | applied where |
|---|---|---|
| department | `Engineering`, `Data` | `DEPARTMENTS_IN_SCOPE` in `_in_scope` |
| employment type | `FullTime` (drops Intern / Contract / PartTime) | `EMPLOYMENT_TYPES_IN_SCOPE` in `_in_scope` |
| country | France | `_in_france` — `"France"` substring in `location`, or a French-city token |
| listed | `isListed == true` | `_in_scope` |

## Notes
- Country can't come from `address.postalAddress.addressCountry` — Ashby fills it
  with a generic `"European Union"`. It's read from the free-text `location`
  string, which lists cities (`"Paris, France; Lyon, France; …"`) or reads
  `"Anywhere in France, Belgium, Spain"`.
- Multi-country listings that include France (e.g. `"Anywhere in France, Belgium,
  Spain"`) are kept — the role is open to a France-based hire.
- Bare-city listings (`"Paris"`) carry no country suffix, hence `FRENCH_CITY_TOKENS`.
- Expected yield at build time (2026-07-04): **19 rows** — 20 FullTime Eng/Data
  minus the one Spain-only role; the sole in-scope internship is dropped by the
  employment-type gate.
