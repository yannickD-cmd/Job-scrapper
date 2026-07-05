# Sia Partners — filters applied

Source: **SmartRecruiters public API**, tenant `Sia`
(`https://api.smartrecruiters.com/v1/companies/Sia/postings`). The
sia-partners.com/en/opportunities page is a Drupal front-end; every apply
button points at this ATS, so we skip the HTML.

| Axis | Value kept | Applied where |
|---|---|---|
| Country | France (`country=fr`) | API query param (server-side) |
| Employment type | `permanent` (CDI) | `typeOfEmployment.id` (client-side) |
| Role | `AI & Tech` — **whole family, no title filter** | `customField[Role]` (client-side) |
| Internship guard | drop titles saying internship/stage/alternance | title regex (ATS-mislabel fix) |

Dropped by the employment filter: `intern`, `contract`, `part-time`.
Dropped by the role filter: `Consulting`, `Design`, `Internal Role`.

**Internship guard (not scope logic):** Sia mislabels ~2 "Final Year Internship"
postings as `typeOfEmployment.id="permanent"`, so the permanent filter alone
leaks them into a CDI-only feed. A literal title regex (internship / stage /
stagiaire / alternance / apprenticeship / VIE) drops them. This is a data-bug
workaround, not tech-keyword judging.

**Why AI & Tech only (no keyword gate):** Sia already curates its data / AI /
ML / software roles under the "AI & Tech" role bucket. Taking the whole bucket
is the filter — no keyword matching needed, and it keeps roles a keyword gate
would miss (e.g. "Forward Deployed Engineer", "Operations Research Consultant").
Consulting was tried (whole, then `is_tech_role`-gated) and dropped: it's ~85%
non-tech management consulting (HR, compliance, procurement, real estate,
energy, actuarial) and mining its handful of data/AI roles added more noise than
signal.

FR snapshot at build time (2026-07-04): 152 FR postings →
AI & Tech permanent = 19 → minus 2 mislabeled interns = **17 in scope**.
