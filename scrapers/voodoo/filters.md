# Voodoo — filters in scope

| Axis | Kept value | Applied where |
|---|---|---|
| Location | **Paris** | client (`locationName == "Paris"`) |
| Department | **Engineering & Data** | client (`departmentName == "Engineering & Data"`) |
| Listed | **isListed == true** | client (defensive; the board never returned False but the field exists) |
| Employment type | *all* | no filter — page itself doesn't filter on this; the team mixes FullTime + Contract + Freelance |
| Workplace type | *all* | no filter — postings mix On-site / Hybrid / Remote |

**Source URLs** (no auth, JSON — Voodoo's own Ashby-backed board):
- Board listing: `https://jobs.voodoo.io/board/989a55fe-f19c-4379-b680-2029aab87cbe`
  - returns `{success, results: [...]}` — single response, no pagination (matches the page's own fetch)
- Detail (per job, for description text): `https://jobs.voodoo.io/job/<id>`
  - returns `{success, results: {descriptionPlain, descriptionHtml, ...}}`

**Do NOT use** `api.lever.co/v0/postings/voodoo` even though it resolves — it returns a stale superset that includes postings the careers page deliberately hides.

**Sanity check**: filtering the API by `departmentName == "Engineering & Data"` and `locationName == "Paris"` returns **9** postings, which matches the chip count on the public page when those filters are selected.

**Native job id**: Ashby UUID (the same `id` Voodoo uses at `/careers/job?id=<id>`). Stable per posting.

**Scraper**: [scrapers/voodoo/voodoo.py](voodoo.py) — see `LOCATIONS_IN_SCOPE` to change the scope.
