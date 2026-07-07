# Snowflake — filters applied

Source: **Ashby** public posting API `api.ashbyhq.com/posting-api/job-board/snowflake`
(careers.snowflake.com is a Phenom mirror; Apply always jumps to Ashby). One
anonymous GET, whole board (~400), descriptions inline. CI-safe (plain requests).

| Axis | Value kept | Applied where |
|---|---|---|
| Country | France | `_in_france`: primary `FR-*` / `addressCountry`=France, OR any `secondaryLocation` is `FR-...` (France-remote-eligible) |
| Department (wholesale) | Engineering · Data Analytics and AI · Enterprise Technology · Security | `WHOLESALE_DEPARTMENTS` |
| Department (mixed → title gate) | Solution Engineering · Professional Services · Product Management · Global Support | `MIXED_DEPARTMENTS`, kept only if `is_tech_role(title)` |
| Department (dropped) | Sales · Sales Development · Marketing · Alliances and Channels · Revenue Operations · Finance · People · Legal · Office of the CEO | not in either set |
| Employment type | ALL (FullTime + Intern) | no gate — "err inclusive on data/AI roles" |
| Listed | `isListed == true` | `_in_scope` |

## Notes / non-obvious decisions

- **France yield is often 0.** Snowflake's France office is GTM-heavy (Sales,
  Marketing, pre-sales Solution Engineering). Engineering / Data / AI sit in the
  US and other EMEA hubs. `Software/Data/AI ∩ France` is frequently empty — this
  is the correct result, not a scraper failure (cf. Mirakl / N26 / Richemont).
- **Solution Engineering is a MIXED family, not wholesale.** Snowflake's France
  SEs are titled plainly ("Senior Solution Engineer") → no tech keyword →
  `is_tech_role` drops them. That matches the chosen scope (Sales Engineering
  refined by title). To keep SE in full, move it to `WHOLESALE_DEPARTMENTS`.
- `department` == `team` on this board (checked), so only `department` is used.
- Phenom's country facet is **loose** — it returns London "Observe by Snowflake"
  roles for a France search because they list an `FR-France-Remote` secondary
  location. Reading Ashby directly avoids that; those roles are `Sales` dept and
  drop anyway.
