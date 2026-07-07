# 02 — The automatic 4×/day schedule

A Windows **Task Scheduler** task named **`JobScraper-Runs`** runs
`logon_scrapers.ps1` for you automatically. You do not have to remember anything.

## When it runs

- **08:00, 12:30, 16:30, 20:30** every day (your PC's local time). These sit
  shortly after the cloud runs (07:00 / 12:00 / 16:00 / 20:00 Paris) so the
  "which cloud scrapers failed" list is fresh.
- **Catch-up:** if your PC was off, asleep, or you were logged out when one of
  those times passed, Windows runs the missed one **as soon as you're back**
  (this is the *StartWhenAvailable* setting). That's why you saw it fire at
  09:28 once — it was catching up the 08:00 run you missed.
- Runs on battery too (laptops normally skip scheduled tasks on battery; this
  one is allowed).
- It only runs while you're logged in (no password is stored anywhere).

## Check it's healthy / see when it runs next

```powershell
Get-ScheduledTask -TaskName 'JobScraper-Runs' | Get-ScheduledTaskInfo
```

Read the result like this:

| Field | Meaning |
|---|---|
| `LastRunTime` | When it last started |
| `NextRunTime` | When it will next run |
| `NumberOfMissedRuns` | How many scheduled starts were missed (catch-up handles them) |
| `LastTaskResult` | `0` = last run finished OK. `267009` = it's **currently running** (that's `0x41301`, not an error). Other non-zero = something failed — open `manual-runs\logs\latest.log`. |

See the last few runs and their outcomes:

```powershell
Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-TaskScheduler/Operational'} -MaxEvents 40 |
  Where-Object Message -match 'JobScraper-Runs' | Select-Object TimeCreated, Id, Message | Format-Table -Wrap
```

## Change the run times

Edit the four `New-ScheduledTaskTrigger -Daily -At ...` lines in
`..\setup_schedule.ps1`, then re-run it once (from the project folder):

```powershell
cd "C:\Users\dosza\OneDrive\Desktop\Projects\Job-scrapper"
.\setup_schedule.ps1
```

Re-running just updates the existing task (it won't create duplicates).

## Also run at every login (on top of the 4 times)

```powershell
.\setup_schedule.ps1 -AlsoAtLogon
```

## Run it once, right now, through the scheduler (to test it)

```powershell
Start-ScheduledTask -TaskName 'JobScraper-Runs'
# then, a few minutes later, open the JobScraper Logs shortcut and read latest.log
```

## Turn it off / remove it

```powershell
.\setup_schedule.ps1 -Remove
```

Or open **Task Scheduler** (press Start, type "Task Scheduler"), find
`JobScraper-Runs` in the top-level library, right-click → Disable or Delete.
