# Accenture — filters in scope

| Axis | Kept value | Applied where |
|---|---|---|
| Country | **France** | server (POST body `jobCountry: "France"`) |
| Employee type | **Full-time** | client (per-row `employeeType`) |
| Skill / competence | see two-tier gate below | client (per-row `skill` + title) |
| City | **Paris** | client (per-row `jobCityState`) |

Source = the live `accenture.com` AEM-fronted JSON API (`POST /api/accenture/jobsearch/result`), not the Workday backend behind it.

City uses an "any-of" rule against the per-row `jobCityState` list, so a multi-city role like `["Lyon", "Paris"]` still qualifies.

### Two-tier skill gate

Accenture's `skill` taxonomy is coarse and miscategorises tech roles into consulting buckets — e.g. `R00341772 "Infrastructure AI Specialist Junior F/H"` is filed under **Business & Technology Integration**, not AI & Data. So a plain skill allow-list silently dropped a whole class of Paris tech roles (Infrastructure/Cloud/SAP/PLM/Salesforce-architect/IAM). Fixed with two tiers:

1. **Wholesale (`WHOLESALE_SKILLS`)** — clean tech buckets kept as-is, no title gate: **Software Engineering, AI & Data, Security, Engineering & Networks**. Kept inclusive because a title gate here would wrongly drop real roles whose titles carry no hard keyword (`Consultant CTEM`, `Sécurité des Applications (AppSec)`, `Lead Data Manufacturing`).
2. **Every other skill** — admitted only if `is_tech_role(title)` (shared `scrapers/_relevance.py`) matches. This rescues the tech roles buried in *Business & Technology Integration / Consulting / Industry Solutions & Services / Strategy Services / Infrastructure & Capital Projects / Program Project & Service Management* while dropping the finance/sales/strategy/marketing majority of those buckets. No explicit bucket list, so renamed/new Accenture skills self-cover.

At probe time (Paris/Full-time): 31 wholesale + 28 title-gated = **59 kept**; the gate rejected 56 non-tech rows from the mixed buckets (Consulting −23, Sales/Finance/Marketing/etc.).

Skill + city are client-side because the API silently rejects ad-hoc `jobFilters` payloads with a 0-byte 200 — cheaper to pull all France rows (5 pages × 50, ~10s) and filter locally.

**Source URL**: `https://www.accenture.com/fr-fr/careers/jobsearch`
**Scraper**: [scrapers/accenture/accenture.py](accenture.py) — see `EMPLOYEE_TYPES_IN_SCOPE`, `SKILLS_IN_SCOPE`, `CITIES_IN_SCOPE` constants to change.
