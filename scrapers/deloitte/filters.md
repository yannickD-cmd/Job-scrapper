# Deloitte France — filters applied

Source: `POST .../prod/offres_v2` (KMB Labs "intconv" SPA backing the board at
`deloitte.com/fr/fr/careers/content/job/results.html`). One call returns every
offer across all geos; we filter in `scrape()`.

| Axis | Kept | Applied where |
|---|---|---|
| Country | `country == "France"` | `_in_scope()` — API also serves Maroc, UK, Monaco, Côte d'Ivoire, Sénégal, Gabon, Cameroun |
| Contract | `contract_type == "CDI"` | `_in_scope()` — drops Stage / Alternance / CDD / Contrat libéral |
| Family | Consulting (implicitly, via specialty) | the 6 specialty ids below are all under `activity_title == "Consulting"` |
| Specialty | tech / digital advisory subset | `_in_scope()` on `job_specialty_id` |

## Specialty subset (`SPECIALTY_IDS_IN_SCOPE`)

| job_specialty_id | display title |
|---|---|
| `stratGiesIt` | Stratégies IT |
| `transformationErpSapOracleEmergingErp` | Transformation ERP (SAP, Oracle, Emerging ERP) |
| `iaDataCloud` | IA, Data & Cloud |
| `cyber` | Cyber |
| `marketingDigital` | Marketing digital |
| `InformationTechnology` | Information Technology |

## Deliberately dropped (still Consulting, but not tech advisory)

Stratégie et Transactions · Transformation de la fonction Finance ou RH ·
Risk, Regulatory & Forensic · Stratégie & Opérations · Supply Chain & Network
Operations · M&A · Économie · Finance · Développement commercial/marketing

And the other families entirely: Audit, Conseil juridique et fiscal /
Tax & Legal, Financial Advisory, Risk Advisory, Fonctions support / Corporate.

## Notes

- Dedup key = `reference` ("R-####", the Workday requisition; also what
  `offer_url` keys on). `id` (hex hash) is stored as `identifier`.
- `date_publication` is SEO-refreshed to "today" for every offer — stored as
  `posted_date` but not meaningful. Dedup is by `reference`, so harmless (same
  call as Orange).
- `apply_url` = `offer_url` (canonical Deloitte posting). The underlying Workday
  apply link is preserved in `raw_payload.link`.
