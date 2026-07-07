# manual-runs — how to run the job scrapers yourself + where the logs are

This folder is your control panel for running the scrapers **outside** of GitHub
Actions. It holds:

- **the tutorials** (the `.md` files listed below)
- **the logs** — everything the scrapers print, in [`logs/`](logs/)

There's a Desktop shortcut **"JobScraper Logs"** that opens `manual-runs\logs`
directly.

---

## The 10-second version

Everything runs automatically **4× a day** (a Windows scheduled task called
`JobScraper-Runs`), and it **catches up** a run if your PC was off when one was
due. You don't have to do anything.

To check what happened, open the **JobScraper Logs** shortcut and read
**`latest.log`**.

To run it yourself right now, open PowerShell and paste **both** lines (the
first one matters — see the gotcha below):

```powershell
cd "C:\Users\dosza\OneDrive\Desktop\Projects\Job-scrapper"
.\logon_scrapers.ps1
```

---

## ⚠️ The #1 gotcha (the error you hit)

If you type `.\logon_scrapers.ps1` and get:

> The term '.\logon_scrapers.ps1' is not recognized as the name of a cmdlet...

it's **not broken** — you're just in the wrong folder. `.\` means "in the folder
I'm currently standing in", and the script lives in the project folder, not in
`C:\Users\dosza`.

**Fix:** always `cd` into the project first:

```powershell
cd "C:\Users\dosza\OneDrive\Desktop\Projects\Job-scrapper"
```

Your prompt should then read `PS C:\Users\dosza\OneDrive\Desktop\Projects\Job-scrapper>`.
Now `.\logon_scrapers.ps1` will work. More on this in
[03-troubleshooting.md](03-troubleshooting.md).

---

## The tutorials

| File | What it covers |
|---|---|
| [01-run-manually.md](01-run-manually.md) | Run the whole thing, or just one company, by hand |
| [02-schedule.md](02-schedule.md) | The 4×/day auto-run: how it works, check it, change the times, turn it off |
| [03-troubleshooting.md](03-troubleshooting.md) | "not recognized", "is it running?", temp files, error codes |
| [logs/_READ_ME.txt](logs/_READ_ME.txt) | What each log file in the logs folder means |

---

## The three kinds of scraper run (so the logs make sense)

1. **Cloud (GitHub Actions)** — ~55 companies scrape themselves 4×/day on
   GitHub's servers. You do nothing; you already get an email when a new job
   appears. This is the main engine.

2. **Local-only** — `bnp`, `safran`, `pernodricard`. Their websites block
   GitHub's servers, so the cloud *can't* run them. Your PC (a normal home
   internet connection) *can*. These are what the scheduled task runs for you.

3. **Fail-watch** — each local run also asks GitHub "did any cloud scraper fail
   on the last run?" and, if so, re-runs those on your PC too (a home connection
   often succeeds where the cloud got blocked). The list of failures is saved to
   `logs\failing_companies.txt`.

`logon_scrapers.ps1` does #2 and #3 together. That's the one command to remember.
