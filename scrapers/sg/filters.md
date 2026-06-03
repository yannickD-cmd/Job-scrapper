# Société Générale — filters in scope

| Axis | Kept value | Applied where |
|---|---|---|
| Country | **France** (`FRA`) | server (Quantum `sourcecsv1`) |
| Contract type | **CDI** (`STANDARD`) + **Alternance** (`APPRENTICESHIP`) | server (Quantum `sourcestr8`) |
| Job family | **IT** (`BJ725`) + **Innovation / Digital / Projet / Organisation** (`JN482`) | server (Quantum `sourcestr10`) |

All three filters are applied server-side in the Quantum search request — every row returned is already in scope, no client-side filter pass.

**Note on "Data / AI":** SG's job-family taxonomy has no separate Data family. Data and AI roles are filed under IT (`BJ725`) or Innovation/Digital (`JN482`); we include both to cover them.

**Source URL** (UI equivalent): `https://careers.societegenerale.com/rechercher`
**Scraper**: [scrapers/sg/sg.py](sg.py) — see `COUNTRY_IDS` / `CONTRACT_IDS` / `JOB_FAMILY_IDS` constants to change scope.
**Filter ID source**: `drupalSettings.quantum.quantum_filters` (saved in [material/rechercher.html](material/rechercher.html)).
