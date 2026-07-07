# 03 — Troubleshooting

## "The term '.\logon_scrapers.ps1' is not recognized..."

**Cause:** you're not in the project folder. `.\` means "the folder I'm standing
in right now", and PowerShell opens in `C:\Users\dosza` by default — the script
isn't there.

**Fix:** move into the project first, then run it:

```powershell
cd "C:\Users\dosza\OneDrive\Desktop\Projects\Job-scrapper"
.\logon_scrapers.ps1
```

Tip: you can confirm you're in the right place — your prompt should end with
`...\Job-scrapper>`. You can also just type the full path from anywhere:

```powershell
& "C:\Users\dosza\OneDrive\Desktop\Projects\Job-scrapper\logon_scrapers.ps1"
```

---

## "...cannot be loaded because running scripts is disabled on this system"

That's the PowerShell execution policy. Run it with a one-time bypass:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\dosza\OneDrive\Desktop\Projects\Job-scrapper\logon_scrapers.ps1"
```

(The scheduled task already uses `-ExecutionPolicy Bypass`, so the automatic runs
are never affected by this.)

---

## "Is it still running, or did it hang?"

A run takes a few minutes (the scrapers deliberately pause between requests).
Check for a live Python process:

```powershell
Get-Process python -ErrorAction SilentlyContinue | Select-Object Id, StartTime, CPU
```

- Something listed → it's still working. Leave it.
- Nothing listed → it has finished (or wasn't running). Open
  `manual-runs\logs\latest.log` to see how it ended.

---

## The logs folder has `.log.out` and `.log.err` files — what are those?

They're **temporary**. While the local-only scrapers run, their output is
written to `<name>.log.out` / `<name>.log.err`, and when the run finishes those
are merged into one clean `local_scrapers_<date>.log` and the temp pair is
deleted. So:

- Seeing `.out` / `.err` sitting there = a run is **in progress** (or was
  interrupted before it could finish merging).
- After a clean finish, you'll only see the merged `local_scrapers_<date>.log`.

---

## "It ran but I didn't get an email"

That's expected. Emails only fire for **genuinely new** job postings, and only
the **first** time a job is seen. A run that finds nothing new (or re-finds jobs
already in the database) sends no email. The email behaviour is unchanged by all
of this — it comes from the normal scraper pipeline, not from these scripts.

---

## Task result codes (from `Get-ScheduledTaskInfo`)

| `LastTaskResult` | Meaning |
|---|---|
| `0` | Last run finished successfully |
| `267009` (`0x41301`) | Task is **currently running** — not an error |
| `267011` (`0x41303`) | Task has not run yet |
| anything else non-zero | The run exited with an error — open `latest.log` |

---

## Where is everything?

| Thing | Path |
|---|---|
| Project folder | `C:\Users\dosza\OneDrive\Desktop\Projects\Job-scrapper` |
| Run the bundle | `...\Job-scrapper\logon_scrapers.ps1` |
| Local-only runner | `...\Job-scrapper\run_local_scrapers.ps1` |
| Fail-watch | `...\Job-scrapper\refresh_failing_companies.ps1` |
| Schedule setup | `...\Job-scrapper\setup_schedule.ps1` |
| Logs | `...\Job-scrapper\manual-runs\logs\` |
| Desktop shortcut to logs | `C:\Users\dosza\OneDrive\Desktop\JobScraper Logs.lnk` |
| The scheduled task | Task Scheduler → `JobScraper-Runs` |

---

## Still stuck?

Open `manual-runs\logs\latest.log` and read the last 20–30 lines — the failing
step almost always prints why. If a single company is the problem, run just that
one to see the full error (see [01-run-manually.md](01-run-manually.md), option D).
