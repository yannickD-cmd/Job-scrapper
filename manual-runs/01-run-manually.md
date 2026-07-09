# 01 — Running the scrapers by hand

You almost never need to — the scheduled task (see
[02-schedule.md](02-schedule.md)) does it 4×/day. But when you want to run
something *now*, here's every option.

> **Always start by moving into the project folder.** Every command below
> assumes you did this first:
>
> ```powershell
> cd "C:\Users\dosza\OneDrive\Desktop\Projects\Job-scrapper"
> ```

---

## A) Run the full "everything not covered by the cloud" bundle

This is the same thing the scheduled task runs: the local-only scrapers
(`bnp`, `safran`, `pernodricard`) **plus** any cloud scraper that failed on the
last GitHub Actions run.

```powershell
.\logon_scrapers.ps1
```

- A minimized PowerShell window does the work (takes a few minutes — the
  scrapers wait politely between requests).
- Output is saved to `manual-runs\logs\logon_<date>_<time>.log` and copied to
  `manual-runs\logs\latest.log`.
- Safe to run as often as you like: the database only stores new jobs once, and
  the new-job email only fires the first time a job is seen. The local-only part
  also self-throttles (it skips if it already succeeded in the last 4 hours).

Force it to ignore the 4-hour throttle:

```powershell
.\run_local_scrapers.ps1 -Force
```

---

## B) Run just the local-only scrapers (bnp / safran / pernodricard)

```powershell
.\run_local_scrapers.ps1              # all three (throttled to once / 4h)
.\run_local_scrapers.ps1 -Force       # ignore the throttle, run now
.\run_local_scrapers.ps1 -Companies bnp        # just one
.\run_local_scrapers.ps1 -Companies bnp,safran.group # a couple
```

---

## C) Just rebuild the "which cloud scrapers failed" list (no scraping)

Fast — it only asks GitHub, it doesn't scrape anything:

```powershell
.\refresh_failing_companies.ps1        # writes manual-runs\logs\failing_companies.txt
.\refresh_failing_companies.ps1 -Run   # ...and then re-run those failures locally
.\refresh_failing_companies.ps1 -Open  # ...and open that run on github.com
```

---

## D) Run one specific company scraper (any of them)

Two ways, using the project's Python environment (`.venv`):

```powershell
# 1) Quick smoke test — prints the jobs it finds, does NOT touch the database:
.venv\Scripts\python.exe -m scrapers.sanofi.sanofi

# 2) The real run — scrapes AND saves to the database (and may send new-job email):
.venv\Scripts\python.exe run.py sanofi
```

Replace `sanofi` with any company key. Multi-board companies use a dot, e.g.
`dassault.systemes`, `creditagricole.amundi`. The full list of keys is in
`run.py` (the `COMPANY_NAMES` dictionary) and in `.github/workflows/scrape.yml`.

You can run several at once:

```powershell
.venv\Scripts\python.exe run.py bnp safran.group pernodricard
```

---

## What "success" looks like

- The window closes on its own (for the `.ps1` scripts).
- `manual-runs\logs\latest.log` ends with a line like `===== done ... =====`.
- `.venv\Scripts\python.exe run.py <co>` prints a per-company summary and exits.
  Exit code `0` = all good; non-zero = at least one scraper had a problem (open
  the log to see which).

If something looks stuck or errored, see [03-troubleshooting.md](03-troubleshooting.md).
