# BPCE — filters applied

Board: `recrutement.bpce.fr` (group-wide: Banque Populaire, Caisse d'Épargne,
Casden, BPCE SA, BPCE Solutions Informatiques, Oney, Estreem, Palatine…).
API: `POST /app/wp-json/bpce/v1/search/jobs` — flat facet body, full posting
inline in the response (no detail fetch).

| axis | value kept | applied where |
|---|---|---|
| Famille de métier (`tax_sector`) | `Informatique`, `Digital` | server-side facet `informatique,digital`, re-checked client-side |
| Type de contrat (`tax_contract`) | `CDI` | server-side facet `cdi`, re-checked client-side |
| Localisation | `France` | client-side on `localisations[].country` |
| Channel (`technical_id`) | everything except `CHANNELNATIXIS` | client-side denylist |

Board scale, 2026-08-21 probe (`material/probe_full_board_slim_2026-08-21.json`):

| stage | rows |
|---|---|
| whole board | 1548 |
| sector ∈ {Informatique, Digital} ∧ CDI | 124 |
| − CHANNELNATIXIS (25) | 99 |
| − non-France (9, "International") | **97** |

## Why these axes

**`Digital` is taken wholesale and must stay that way.** Every row in that
family carries the degenerate sub-family label `"Digital"` — the `tax_job`
axis is useless there. It is nevertheless where Data Scientist, ML Engineer,
Tech Lead DevOps, Expert Sécurité and Chef de Projet Data & IA actually file.
Filtering `Digital` on sub-family would drop all of them.

**`Risques Controles et Engagements` is excluded** (70 rows, 64 FR). Unlike
the Natixis tenant — where the same family splits roughly half quant/tech —
this board's risk family is ~97% conformité, audit, contrôle permanent and
sécurité financière. It is worth ~2 quant rows (`Analyste risques de marché`,
`Analyste risques de crédit`) against 62 rows of compliance noise. Revisit by
adding a sub-family whitelist like `scrapers/natixis/natixis.py` does.

**`Commercial` (969 rows, 63% of the board) is the retail-branch bulk** —
conseiller particulier / professionnel, chargé de clientèle. Never in scope.

**CHANNELNATIXIS overlap.** 157 board rows (25 within tech+CDI) are the same
postings `scrapers/natixis/` pulls from `recrutement.natixis.com`. They are
dropped here so a Natixis role does not appear twice in the dashboard under
two company names. The rule is a denylist on `technical_id`, so an unknown
new channel is kept rather than silently missed.

## Identity

`native_job_id` = `advert_id` — the TalentLink advert id, the one that appears
in the apply URL (`…/apply.html?jobId=<configKey>-<advert_id>`). Unique across
all 1548 board rows. `job_number` and `opening_id` are NOT unique (23 and 1
duplicate keys respectively board-wide) and must not be used as the key.
