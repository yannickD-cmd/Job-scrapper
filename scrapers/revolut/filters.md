# Revolut — scope filters

| Axis | Value kept | Applied where |
|---|---|---|
| Team | Engineering, Data | client-side (`TEAMS_IN_SCOPE`) — payload has no server filter |
| Geography | Europe (any location's `country` in `EUROPEAN_COUNTRIES`) | client-side; job kept if ≥1 European location, `location` = European subset only |
| Employment type | n/a | payload has no employment_type field (Revolut lists full-time-style roles only) |

Source: full position list (~585 jobs, all geos) embedded in the careers page's
Next.js data route — no server-side filtering exists at all.
