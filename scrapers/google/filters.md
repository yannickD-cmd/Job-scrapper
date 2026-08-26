# Google — filters

Board: server-rendered Wiz app at
`https://www.google.com/about/careers/applications/jobs/results/`.
The old public JSON API (`careers.google.com/api/v3/search`) is **dead** (404) —
do not re-probe it. No JSON-LD on detail pages. HTML parsing, plain `requests`,
CI-safe.

| Axis | Value kept | Applied where |
|---|---|---|
| Country | France | `?location=France` — Google's own facet, **the gate**. Never re-filtered on the card's city. |
| Category | Tech category, wholesale | `?category=<CAT>` facet, `TECH_CATEGORIES` |
| Category | Blocked category | `BLOCKED_CATEGORIES` — dropped, content gate cannot rescue |
| Category | Mixed / absent | Content gate on the **full JD**, never the title |
| Employment type | **All** (intern, fixed-term, full-time) | Not filtered |
| Seniority | All | Not filtered; `Early`/`Mid`/`Advanced` recorded in `raw_payload.experience_level` |

## The `category` facet is real but invisible

The filter panel renders client-side, so **no option list exists in the HTML**.
But `?category=SOFTWARE_ENGINEERING` *is* honoured server-side, and an invalid
value returns 0 matches — which makes the vocabulary enumerable by probing.
Complete list (board-wide counts, 2026-08-25; 17 values summing to 3334 of 3341):

| Keep | Block | Content-gated |
|---|---|---|
| SOFTWARE_ENGINEERING (1025) | DATA_CENTER_OPERATIONS (202) | PROGRAM_MANAGEMENT (376) |
| HARDWARE_ENGINEERING (287) | SALES_OPERATIONS (299) | PRODUCT_MANAGEMENT (151) |
| TECHNICAL_SOLUTIONS (283) | SALES (203) | |
| USER_EXPERIENCE (72) | BUSINESS_STRATEGY (134) | |
| NETWORK_ENGINEERING (39) | MARKETING (64), FINANCE (60) | |
| DEVELOPER_RELATIONS (3) | PARTNERSHIPS (59), LEGAL (42), ADMINISTRATIVE (35) | |

Google exposes no category on the card or the detail page, so the scraper
re-runs the France query once per category (17 cheap requests) to label the set.

**A requisition can hold several categories.** "Founder Advocate, EMEA Startup,
Google Cloud" is in both SALES_OPERATIONS and TECHNICAL_SOLUTIONS. Precedence is
therefore explicit: **tech wins → content-gated → only an all-blocked row is
dropped.** Keeping merely the first label let a blocked category beat a tech one
by alphabetical accident (this was a real bug, caught in the first smoke test).

## Traps that cost probing time

- **Never re-filter on the card's displayed location.** Several France-matching
  cards render a non-French primary city — the Munich "Customer Engineer, Google
  Distributed Cloud" genuinely reads *"Munich, Germany; Paris, France; London,
  UK; Madrid, Spain; Copenhagen, Denmark"*. The facet is correct; the card shows
  only the primary. Re-filtering would silently drop every multi-location France
  role.
- **Detail location is not `span.r0wTof`.** That class also wraps a boilerplate
  office list (Bengaluru / Dublin / Kirkland / Mexico City / Mountain View)
  present on *every* page. Use the "…share your preferred working location from
  the following:" sentence, falling back to the `place` marker block — and stop
  that block at the first line not prefixed `;`, or icon names
  (`laptop_windows`, `bar_chart`) and badge text ("Mid", "Remote eligible") leak
  into the field. Also a real bug caught in the first smoke test.
- **`page=N`, 20 cards per page.** A country with fewer than 20 still reports its
  true total in "N jobs matched", so `page=2` returning 0 cards is normal.
- **`posted_date` is always `None`** — Google publishes no posting date anywhere
  in the markup. Same as Air France / OVHcloud; dedup is by `native_job_id`.
- `family=` in the HTML is the **Google Fonts** stylesheet, not a job facet.

## Expected yield

**~7 of 14 France postings.** Unlike Microsoft, Google *does* run a Paris
engineering site, and the kept set is genuinely technical: *Senior AI/ML Software
Engineer (YouTube)*, *Staff Research Scientist, Compiler Optimization*,
*Technical Solutions Engineer, Data*, *Senior Red Team (Mandiant)*, *Technical
Account Manager, Google Cloud Consulting*, *Research Scientist PhD Intern 2027*.

Correctly dropped: *Legal Trainee*, *People Consultant*, *EHS Construction
Program Manager*, *Energy Communications Campaign Manager*, and both datacentre
roles — including "Technical Program Manager, YAWN", whose title reads technical
but whose JD is data-centre construction management (HVAC, switchgear,
chillers). That one is the clearest proof the content gate beats a title gate.

**Boundary case to revisit:** *Customer Engineer, Google Distributed Cloud* is
filed by Google under SALES_OPERATIONS only (not TECHNICAL_SOLUTIONS), so it is
dropped. It is a Google Cloud pre-sales solutions engineer — the closest Google
analogue to a Forward Deployed Engineer. If pre-sales should be in scope, move
SALES_OPERATIONS from `BLOCKED_CATEGORIES` to `CONTENT_GATED_CATEGORIES`; the
blast radius is small because only France rows are ever evaluated.
