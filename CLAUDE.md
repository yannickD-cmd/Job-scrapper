# Job-scrapper — Claude playbook

## When the user pastes a URL in chat

Before responding to anything else, classify every bare URL the user sends:

1. **Fetch the page once** with `WebFetch` (or curl) to confirm it is reachable and read the title / headings. Do not skip this — extension or path is not enough (e.g. `/careers`, `/jobs`, `/recrutement`, `/talent`, ATS-tenant subdomains like `*.workday*.com`, `*.greenhouse.io`, `*.lever.co`, `*.smartrecruiters.com`, `*.taleo.net`, `*.icims.com`, `*.successfactors.eu`, `*.talent-soft.com`, `*.teamtailor.com`, `*.avature.net`).
2. **Decide:**
   - **Career-website** (a company's job board, listing many open positions for that company) → enter the scraper-creation flow below.
   - **Single job posting on an aggregator** (Welcome to the Jungle, LinkedIn, Indeed, etc.) → do NOT scaffold a scraper. Tell the user and ask if they instead want the company's own board for that role.
   - **Anything else** → respond normally.
3. State your classification in one sentence before doing anything else, so the user can correct you cheaply.

## Scraper-creation flow (when a career URL is confirmed)

Existing scrapers under `scrapers/` are the source of truth for shape and conventions. Read at least one before scaffolding a new one.

### Phase 1 — Scope discussion (mandatory, blocking)

Never start probing or building before the user has answered, explicitly:
- **Country / region** to keep (e.g. France only, France + remote, Europe).
- **Job categories / families** in scope (e.g. Data & AI, Software, all tech, all roles).
- **Employment types** in scope (CDI / CDD / internship / apprenticeship / contract / regular).
- **Title-keyword filter** if categories alone are too broad (e.g. `AI|ML|data|analyst`).

If the user has already given scope upstream, paraphrase it back in one line and proceed. Don't ask for things the user already specified.

### Phase 2 — Probe (only while scope is being locked, not after)

- Use `WebFetch` and `curl -sL` to inspect the listing page. Look for:
  - A JSON / XHR endpoint feeding the page (Network-tab style discovery via `<script>` tags, `window.__INITIAL_STATE__`, `__NEXT_DATA__`, `apisearch`, `/api/`, `/wday/cxs/...`, `/widgets`, `boards-api.greenhouse.io`, `api.lever.co`, `careers-api.*`).
  - JSON-LD `JobPosting` blocks on detail pages (`<script type="application/ld+json">`) — used by Sanofi, Thales, IBM, etc.
  - Sitemap (`/sitemap.xml`) for an enumerable URL list.
- Save every probe artifact (HTML, JSON, JS) to `scrapers/<company>/material/`. **Never save probe files to the repo root, `c:/tmp`, or `/tmp`** — see `feedback_scraper_research_folder` in memory.
- **Once scope is locked, stop probing and build the real scraper.** No standalone `_inspect.py` / `_probe.py` files inside the scraper module after that point — see `feedback_stop_probing_when_scope_locked` in memory.

### Phase 3 — Build the scraper

**Folder layout** (single-board company):
```
scrapers/<company>/
    __init__.py        # from .<company> import scrape
    <company>.py       # the scraper
    material/          # saved HTML/JSON probes (gitignored from db, kept for reference)
    filters.md         # optional: a small table of which filters are kept (axis | value | applied where)
```

**Folder layout** (multi-board company, e.g. Dassault has Systèmes + Aviation):
```
scrapers/<company>/
    __init__.py        # comment block explaining the split; no scrape import here
    <board1>.py        # exposes scrape()
    <board2>.py        # exposes scrape()
    material/
```
Then in `run.py`, register each board as `<company>.<board1>` / `<company>.<board2>` (see `dassault.systemes`, `dassault.aviation`).

**`scrape()` contract** — see [scrapers/sanofi/sanofi.py](scrapers/sanofi/sanofi.py) for the canonical example. The function MUST:
- Take no arguments.
- Return `list[dict]`.
- Each dict MUST have: `native_job_id`, `title`, `apply_url`.
- Each dict SHOULD have when available: `description`, `location`, `category`, `posted_date` (ISO `YYYY-MM-DD`), `employment_type`, `identifier`, `raw_payload` (dict of upstream response for forensics).

**Use a `@dataclass Job:` and convert with `asdict(j)` at return time.** Don't hand-build dicts.

**Patterns by ATS** (pick the closest existing scraper as a template):

| ATS / pattern | Template scraper |
|---|---|
| Server-rendered HTML + JSON-LD on detail | [scrapers/sanofi/sanofi.py](scrapers/sanofi/sanofi.py) |
| Workday `wday/cxs/<tenant>/<site>/jobs` JSON API | [scrapers/rothschild/rothschild.py](scrapers/rothschild/rothschild.py) (also handles per-facet looping + Cloudflare cookie-clear) |
| Greenhouse `boards-api.greenhouse.io` | [scrapers/doctolib/doctolib.py](scrapers/doctolib/doctolib.py) |
| Lever `api.lever.co/v0/postings/<co>` | [scrapers/voodoo/voodoo.py](scrapers/voodoo/voodoo.py) |
| Teamtailor public API | [scrapers/deezer/deezer.py](scrapers/deezer/deezer.py) |
| Talentsoft (ASP.NET WebForms, facet=session quirk) | [scrapers/dassault/aviation.py](scrapers/dassault/aviation.py) — see `project_talentsoft_facet_session` memory |
| Avature | [scrapers/ibm/ibm.py](scrapers/ibm/ibm.py) |
| In-house Exalead / Nuxt SPA + JSON API | [scrapers/dassault/systemes.py](scrapers/dassault/systemes.py) |
| Akamai / Cloudflare blocked from CI IPs | [scrapers/bnp/bnp.py](scrapers/bnp/bnp.py) — note: such scrapers must be excluded from `.github/workflows/scrape.yml` with a comment |

**Hard rules for every scraper:**
- A polite User-Agent that names the project (see Sanofi for the format).
- `time.sleep(REQUEST_DELAY_SECONDS)` between requests (≥ 2.0s for HTML pages, ≥ 1.0s for JSON APIs).
- A defensive page cap (`MAX_PAGES`) so a pagination bug can't loop forever.
- A `if __name__ == "__main__":` block that prints a clean per-job summary — used for manual smoke tests via `python -m scrapers.<co>.<co>`.
- `from __future__ import annotations` at the top.

### Phase 4 — Register in TWO places (both required)

A new scraper MUST be added to BOTH:

1. **`COMPANY_NAMES`** in [run.py](run.py) — keys here are also the filename under `scrapers/`. The display name (value) goes in DB rows + alert emails.
2. **`matrix.company`** list in [.github/workflows/scrape.yml](.github/workflows/scrape.yml) — the GitHub Actions matrix is hardcoded separately, not derived from `COMPANY_NAMES`.

Forgetting either one means the scraper never runs in CI. See `feedback_new_scraper_register_both` in memory.

### Phase 5 — Smoke test before declaring done

Run, in order:
1. `python -m scrapers.<co>.<co>` — direct module run, prints the parsed jobs without touching the DB.
2. `python run.py <co>` — full path through `db.persist_run_results`, exercises Supabase upsert.

Both must succeed before the scraper is considered shippable. If the scraper depends on a heavy headless browser (Playwright) or a bot-protected origin, mark it CI-excluded with a comment in `scrape.yml` (see BNP).

### Phase 6 — Commit

When the user asks to commit:
- Single coherent commit per scraper. Body explains the ATS shape and the scope decision (why these filters), not the file list.
- No `Co-Authored-By: Claude` / no "Generated with Claude Code" footer — see `feedback_no_coauthor_trailer` in memory.
- Never stage `.env`. Never read it either — see `feedback_never_read_env` in memory.

## DB contract (for reference)

[db.py](db.py) — Supabase Postgres via the Transaction pooler.
- Dedup key: `UNIQUE (company, native_job_id)`. Reruns are idempotent.
- A row's `still_open` flips to `FALSE` automatically when its `native_job_id` no longer appears in the latest scrape result.
- An empty `scrape()` return is treated as "scraper failed silently" — no rows are closed (safety guard in `persist_run_results`).
- `posted_date` must be `YYYY-MM-DD` (or `None`). Sanofi-style unpadded dates need normalisation.
- `raw_payload` should be a dict — JSONB-queryable later.

## Things NOT to do

- Don't add per-call `Bash(curl -s -A "..." ...)` permissions to settings.local.json for every probe — the broad patterns in `.claude/settings.json` already cover read-only HTTP, `mkdir -p`, `mv` into `scrapers/<co>/material/`, `python -m scrapers.*`, `awk`, `grep`, `git status|diff|log|show`, etc. If a probe needs a permission that's NOT covered, that means it would mutate state outside the project — pause and ask.
- Don't fix `posted_date` parsing for Orange — it's SEO-refreshed daily; dedup is by `native_job_id` so the noise is harmless. See `project_orange_dateposted_bogus` in memory.
- Don't run `python` in autonomous Airflow / scheduling discussions for this repo — the user's Airflow context is interview-prep / teaching mode (see `feedback_airflow_teaching_mode`), so wait for them to ask before scaffolding DAGs.
