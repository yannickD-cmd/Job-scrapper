# Hermès — filters applied

ATS: **Oracle Cloud Recruiting (ORC) Candidate Experience**, site `CX_12001`.
Public JSON API on `fa-eoic-saasfaprod1.fa.ocs.oraclecloud.com` (no auth, CI-safe).

Scope locked 2026-07-04: **France · tech (Data/AI + Software/IT/Digital) · CDI**.

| Axis | Value kept | Applied where | Notes |
|---|---|---|---|
| Country | France | `PrimaryLocationCountry == "FR"`, client-side | Full board crawled (~817), FR ≈ 544. Client-side filter avoids partial-facet false-close. |
| Contract | CDI | detail `RequisitionType == "CDI"` | Listing `ContractType`/`WorkerType` are null. Title prefix (`STAGE -`/`CDD -`/`ALTERNANCE -`) only pre-drops obvious temps before detail fetch. |
| Tech family | SI + Digital | detail `JobFunction` ("Systèmes d'Informations" / "Digital") **or** `Category` ("SI - ..." / "Digital - ...") | The clean tech signal; kept wholesale. This is the user's "IT/Digital" scope (incl. MOA/ERP/functional IT). |
| Tech family | drop everything else | same | Production / Supply Chain / Développement-Innovation / Retail / Finance / RH… all dropped — even when the title matches `is_tech_role`. |
| Unfiled tail | tech title only | `is_tech_role(title)` or `\bSI\b/\bDSI\b/\bERP\b` | Fires **only** when JobFunction *and* Category are both null. Recovers Lead AI Security / DevSecOps / Data Integration; can't re-admit a filed craft role. |

## Why not a title-only tech filter?

Luxury-house titles use "développement / développeur" for **product/craft**
development (cuir, pièces métalliques, haute joaillerie, formulation parfum,
prototypiste, CNC), not software — `is_tech_role` alone is ~25% craft false
positives here, and the craft is filed under Production/Supply, so a keyword
exclude list can't catch it. The ATS separates tech from craft cleanly only on
the detail record's `JobFunction`/`Category`, so that is the gate. See
`feedback_prefer_platform_category_over_is_tech_role`.

## Cost

The tech category is detail-only and the CATEGORIES search facet is capped at 10
values (SI sub-cats + Digital don't all fit), so the SI/Digital reqs can't be
enumerated server-side. We fetch a detail for **every non-temporary FR req
(~350)** and let the detail decide — no title pre-filter, because one would
silently drop SI/Digital reqs with unconventional titles (Engineering Manager,
ServiceDesk, Référentiel Articles). Recall > a few extra requests here.

Yield ≈ 50 roles (2026-07-04).
