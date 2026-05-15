# IBM — filters in scope

| Axis | Kept value | Applied where |
|---|---|---|
| Country | **France** (substring in `card-item-location`, validated against detail-page Country) | client |
| Category | **Data & Analytics**, **Research** (kept), **Software Engineering**, **Cloud** (kept only if title matches Data/AI keywords) | client (`article__header__text__pretitle`) |
| Employment type | *all* | no filter |
| Position type | *all* — Professional, Entry Level, Intern | no filter |
| Workplace | *all* — Onsite / Hybrid / Remote | no filter |

**Source URLs** (no auth, HTML — Avature ATS, requires Googlebot UA to bypass `awselb` bot gate):

- Listing (paginated, all jobs worldwide):
  `https://careers.ibm.com/en_US/careers/SearchJobs/?jobRecordsPerPage=48&jobOffset={N}`
  - ~2000–2100 jobs total; walk offset 0, 48, 96, … until an empty page
- Detail (per job, for description + Country):
  `https://careers.ibm.com/en_US/careers/JobDetail?jobId={id}`

**Why the legacy ATS, not `www.ibm.com/careers/search`**:
The careers.ibm.com page renders a Next.js app that calls an internal "kepler" JSON service. Every JS path under the bundle prefix `https://www.ibm.com/marketplace/static/components/search/embedded/v2.2/scripts/...` 301s to `/products` when fetched directly — only the CSS is exposed. Reverse-engineering the bundle would require running a real browser. The Avature ATS underneath is the same data source, served as plain HTML.

**Why the URL params can't filter**:
Avature stores filter state in a server-side session, not in the URL. The form POSTs to `/en_US/careers/OpenJobs` with internal field IDs (`10296[]` for Location, `13790[]` for Team, etc.); the `<select>` options come from a separate AJAX autocomplete. Trying to filter via `?searchQuery=France` matches "France" in descriptions, not locations (49 results, none in France). Simpler and more robust to scrape all ~2000 jobs and filter client-side — the country `card-item-location` substring is a flat text field on every card.

**AI title keywords** (applied only to Software Engineering + Cloud spillover):
`AI, ML, Machine Learning, Deep Learning, Data Scientist|Engineer|Analyst, NLP, LLM, Generative AI, GenAI, Foundation Model, Applied Scientist, Research Scientist, MLOps, Analytics, Watsonx`.

**No posted_date**: IBM/Avature does not surface a posted date on either listing card or detail page; we set `posted_date = None`.

**Native job id**: the integer `jobId` from `…/JobDetail?jobId=XXXXXX` (6-digit, stable per posting).

**Scraper**: [scrapers/ibm/ibm.py](ibm.py) — edit `SCOPE_COUNTRY`, `CORE_CATEGORIES`, `TITLE_FILTERED_CATEGORIES`, or `AI_KEYWORDS_RE` to change scope.
