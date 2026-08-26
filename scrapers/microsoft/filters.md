# Microsoft — filters

Board: **Eightfold PCSX** at `apply.careers.microsoft.com`
(`jobs.careers.microsoft.com` 302s here; the old `gcsservices…/search/api/v1`
API is dead, and `/api/apply/v2/jobs` returns *"Not authorized for PCSX"*).

| Axis | Value kept | Applied where |
|---|---|---|
| Country | France | Client-side, on `locations` (country **first**: `"France, Paris, Paris"`) ∪ `standardizedLocations` (`", FR"` suffix). A multi-country req counts if France is one of them. |
| Category | Tech **Discipline**, wholesale | `efcustomTextTaDisciplineName` / listing `department` ∈ `TECH_DISCIPLINES` |
| Category | Out-of-scope Discipline | `BLOCKED_DISCIPLINES` — dropped unconditionally, content gate cannot rescue |
| Category | Anything else | Content gate on the **full JD**, never the title |
| Employment type | **All** (Full-Time, Internship, Temp/Contract) | Not filtered — stage/alternance are in scope, they just don't exist on this board |
| Seniority | All | Not filtered |

## Why Discipline and not Profession

The detail payload carries both. **Profession is the wrong axis** — it misfiles
technical work into commercial buckets:

| Discipline | Profession |
|---|---|
| Cloud Solution Architecture | Customer Success |
| Solution Engineering | Customer Success |
| Customer Experience Engineering | Program Management |
| Strategic Account Technology | Technology Sales |

Gating on Profession drops every one of those. Discipline is finer, is free at
listing level, and is the honest axis. Profession is still stored in `category`
(second half) and `raw_payload.profession` for forensics.

## Blocked disciplines (scope decision, not noise)

- **Datacentre / physical infra ops** — Data Center Technicians, Data Center
  Operations Management, Critical Environment Ops, Logistics Technician,
  Materials Handling, Construction Project Management. Real infrastructure work
  and **13 of the 21 open France roles**, but hands-on hardware/warehouse.
- **Physical-product engineering** — Mechanical / Manufacturing / NPI /
  Sourcing Engineering (Surface & Xbox devices).
- **Technical pre-sales & account management** — Strategic Account Technology,
  Account Technology, Solution Area Specialists, Customer Success Account Mgmt,
  Industry Advisory. Quota-carrying.

These are blocked *before* the content gate on purpose: a datacentre technician
JD legitimately mentions "automation" and "PowerShell" and would otherwise pass.

## Content gate (`_STRONG` / `_SUPPORTING`)

Stricter than the JPMorgan equivalent. A bare `AI`, `Azure`, `Copilot` or
`cloud` is **deliberately absent from both lists** — Microsoft's mission
boilerplate puts them in every JD including pure sales ones, so they carry zero
signal here. One STRONG hit, or two distinct SUPPORTING hits, keeps the row.

## API quirks (probed, see `material/`)

| Quirk | Consequence |
|---|---|
| `num` is ignored; page size is hard-wired to **10**. `num=100` returns an empty body. | ~207 pages for a ~2.1k board, ~340s |
| `employment_type=…` is ignored — returns the identical unfiltered count | Employment type must be filtered client-side off the detail |
| `location=France` (geo) **is** honoured, and agreed exactly with a full crawl (21 = 21) | Still not used — a geo radius is a silent-loss risk. Full crawl + client-side gate instead. |
| Short-window burst limit → **429**, no `Retry-After`, clears in seconds | `_request()` retries with linear backoff and fails closed |
| `displayJobId` = ATS req number (stable); `id` = Eightfold PID (churns on edit) | `native_job_id` = `displayJobId`; `apply_url` uses the PID |

## Expected yield

**~1 row.** Microsoft has **no engineering R&D site in France** — the French
entity is a commercial subsidiary (Issy-les-Moulineaux) plus datacentre regions.
Snapshot 2026-08-25: 2064 reqs board-wide, 506 of them Software Engineering,
**zero** in France. The 21 France roles are 13 datacentre/logistics + 8
sales/consulting. This scraper exists to catch a France engineering req the day
one opens, not to produce volume today — same posture as Mirakl / N26 /
Snowflake. A run returning 0–2 is normal and is **not** a silent-zero failure.
