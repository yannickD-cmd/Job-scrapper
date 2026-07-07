# Uber — DROPPED ON YIELD (2026-07-06)

Not built / not registered. Uber's Paris office is a **pure GTM / Operations hub**:
all 30 France jobs are Sales, Account Management, Business Operations, or University
internships. **Zero** Data / AI / ML / Analytics / Software Engineering roles in France
(Uber does eng in US / Amsterdam / Bangalore, not Paris). Scope was France · Data & AI ·
CDI → yield 0, structurally likely to stay ~0. User dropped on yield (like Pfizer /
Canal+ / Stripe-FR). See `fr_all.json` for the evidence snapshot.

## API (fully reverse-engineered — if revisiting, start here)

Clean public JSON API, no auth, no bot-guard on this host:

```
POST https://www.uber.com/api/loadSearchJobsResults?localeCode=en
Headers:
  Content-Type: application/json
  x-csrf-token: x            # literal "x" is accepted
Body:
  {"params":{"location":[{"country":"FRA"}],"page":0,"limit":50},"page":0,"limit":50}
```

- Response: `data.results[]`, `data.totalResults.low` = count.
- `country:"FRA"` → 30 FR jobs (3-letter codes; results show country "USA" etc).
  `{"city":"Paris"}` → 29. Region+city combined returns 0 (finicky) — filter by
  country FRA, or client-side by city.
- Result fields: `id`, `title`, `description` (empty in listing — needs detail fetch),
  `department`, `team`, `type`, `timeType` (both empty for FR), `location{country,region,
  city,countryName}`, `allLocations[]`, `creationDate`, `updatedDate`, `level`,
  `statusName` ("Posted"), `uniqueSkills`.
- Data/AI filter (if ever built): gate on `department`/`team` — France taxonomy today is
  only `Sales - *`, `Operations - Business Operations`, `University - *`. Engineering /
  Data Science departments exist globally but never in FR. No `timeType`/`type` populated
  for FR, so no server-side CDI signal — would need title/detail heuristics.
- Detail page: `https://www.uber.com/global/en/careers/list/<id>/` (for `description`).
