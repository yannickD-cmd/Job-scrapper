# Renault Group — filters

Source ATS: **Workday CXS**, tenant `alliancewd`, site `renault-group-careers`
(`https://alliancewd.wd3.myworkdayjobs.com/renault-group-careers`). The
renaultgroup.com careers page is a WordPress/Next.js front whose `workdayJobs`
GraphQL feed proxies this same Workday board; we hit Workday directly.

| Axis | Value kept | Applied where |
|---|---|---|
| Country | France (`54c5b6971ffb4bf0b116fe7651ec789a`) | server facet `locationCountry` |
| Employment type | CDI = "a - Regular (no fixed end date)" (`62e55b3e447c01871e63baa4ca0f9391`) | server facet `workerSubType` |
| Job family | Information Technologies & Systems (`…fd170`) + Research & Development (`…dd70`) | server facet `jobFamilyGroup`, one family per request loop |
| Title | none | — (kept "all tech" wholesale; no keyword gate) |

## Scope notes

- **Widened from Data/AI to all tech** at the user's request: both tech families
  are kept wholesale, so cybersecurity / software / devops / data-platform roles
  all pass. Original ask was Data & AI only (~1 role); "all tech" is ~4.
- **Yield is intentionally small.** Group-wide the board is ~270 reqs; France +
  permanent is ~36, and almost all of those are manufacturing / finance / HR /
  sales-financing / medical. At build time only IT & Systems carried tech (4
  rows); R&D-permanent-France was empty. Low/zero output is expected, not a bug.
- **R&D is kept wholesale despite the physical-eng risk.** "R&D" at an automaker
  is mostly automotive hardware engineering (chassis/powertrain/materials), which
  `scrapers/_relevance.py` (`is_tech_role`) drops for the defense scrapers. It was
  left wholesale here per the explicit scope choice and is empty for now. If
  mechanical-eng rows start showing up, gate the R&D family with `is_tech_role`
  in `scrape()` rather than removing the family.
- **Not filtered in the scraper:** IDF / employment-type-display / title-keyword
  refinements live in the dashboard, per repo convention. The scraper keeps every
  France/CDI IT&Systems+R&D row.
