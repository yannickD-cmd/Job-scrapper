# Danone filters

ATS: iCIMS **Jibe** search API — `https://notifications.careers.danone.com/api/jobs`
(the careers.danone.com front is Adobe AEM; the SPA reads this origin). CI-safe:
plain `requests`, polite UA, no cookies.

Scope locked 2026-07-04: **France · Data & AI · CDI (permanent)**.

| Axis | Value in scope | Applied where |
|---|---|---|
| Country | France | server-side query param `country=France` |
| Contract | CDI / permanent | client-side `_is_permanent()` — TITLE prefix, not `employment_type` |
| Category | Data & AI | client-side `_in_scope()` — `DATA_AI_RE` on title + `Data` family wholesale |

## Contract detection (title prefix, not `employment_type`)

Danone France writes the contract into the title prefix. The structured
`employment_type` field is **hours** (`FULL_TIME`/`TEMPORARY`/`PART_TIME`), not
permanence — `FULL_TIME` contains CDI *and* CDD *and* interns, so it's useless
as a CDI signal.

- Keep: `CDI - ...`, or **no** temporary prefix and not flagged `TEMPORARY`
  (keeps unprefixed permanent director roles like `Director DDAI AI & Tech`).
- Drop: `ALTERNANCE`, `APPRENTICESHIP`/`APRENTICESHIP` (misspelt, single-P, on
  the live board), `APPRENTISSAGE`, `CDD`, `STAGE`, `INTERNSHIP`, `VIE`,
  `GRADUATE PROGRAM`, `SUMMER`, `THÈSE`/`THESIS`.

## Data/AI detection (title, not family)

The ATS exposes no category *facet*, and Data/AI leaks out of `Information
Technology` into `Research & Innovation` (Danone's **DDAI** = Data, Digital &
AI org). So match the title (`DATA_AI_RE`), plus keep the `Data` family
wholesale.

Deliberately **not** keeping all of `Information Technology`: the user chose
Data & AI, not Software/IT. That family's SAP-platform / cybersecurity /
infra-architecture CDIs are Software-IT and are correctly dropped. To widen to
all tech later, add `"Information Technology"` to `WHOLESALE_FAMILIES`.

Yield today ≈ 6 roles (all DDAI / R&I AI / IT Data-governance). Small is
expected and fine.
