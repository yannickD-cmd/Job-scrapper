# LVMH filter scope

| Axis | Value(s) | Applied where |
|---|---|---|
| Country | `France` | Algolia filter (`country:"France"`) |
| Contract | `CDI` | Algolia filter (`contractFilter:"CDI"`) |
| Function | `Technologie`, `Omnicanal et données` | Algolia filter (`functionFilter` OR-clause) |
| Title regex | — | Not applied (function buckets are already narrow) |

LVMH's `functionFilter` is the FR-side facet label. Two buckets capture
tech-adjacent roles:
- **Technologie** — IT / Software / Cloud / Infrastructure / Security
- **Omnicanal et données** — Digital / E-commerce / Data / CRM

Result count at scaffolding time (2026-05-17): ~34 jobs across ~10 maisons
(Louis Vuitton, Dior, Guerlain, Sephora, Rimowa, …).
