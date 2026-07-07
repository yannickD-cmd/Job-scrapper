<#
    setup_schedule.ps1

    Registers the "JobScraper-Runs" scheduled task so logon_scrapers.ps1 runs
    4x/day AND catches up a missed run when the PC comes back on. Replaces the
    old login-only Startup shortcut.

    "Catch up" = the StartWhenAvailable setting: if the PC was off / asleep /
    you were logged off when a scheduled time passed, Windows runs that missed
    run as soon as it can (i.e. right after you next log in).

    Run this ONCE. If it prints "Access is denied", open PowerShell as
    Administrator (right-click -> Run as administrator) and run it again.

    Usage:
        .\setup_schedule.ps1               # register / update the task
        .\setup_schedule.ps1 -AlsoAtLogon  # ALSO run at every login, on top of the 4 times
        .\setup_schedule.ps1 -Remove       # delete the task
#>
[CmdletBinding()]
param(
    [switch] $AlsoAtLogon,   # add an extra "at every login" trigger
    [switch] $Remove         # unregister the task and exit
)

$ErrorActionPreference = 'Stop'
$taskName = 'JobScraper-Runs'
$script   = Join-Path $PSScriptRoot 'logon_scrapers.ps1'

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task '$taskName' (if it existed)."
    return
}

if (-not (Test-Path $script)) { throw "not found: $script" }

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -WindowStyle Minimized -ExecutionPolicy Bypass -File "{0}"' -f $script) `
    -WorkingDirectory $PSScriptRoot

# 4 daily runs in LOCAL time. These sit shortly after the cloud cron
# (07:00/12:00/16:00/20:00 Paris) so the failing-companies list is fresh.
# Edit the times to taste.
$triggers = @(
    New-ScheduledTaskTrigger -Daily -At 08:00
    New-ScheduledTaskTrigger -Daily -At 12:30
    New-ScheduledTaskTrigger -Daily -At 16:30
    New-ScheduledTaskTrigger -Daily -At 20:30
)
if ($AlsoAtLogon) { $triggers += New-ScheduledTaskTrigger -AtLogOn }

# StartWhenAvailable = run a missed scheduled start as soon as possible.
# Battery flags matter on a laptop (Task Scheduler skips runs on battery by
# default). 1h cap so a hung scraper can't run forever.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

# Run as you, only while you're logged on (no stored password needed).
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers `
    -Settings $settings -Principal $principal -Force `
    -Description 'Run local-only + failed-in-CI job scrapers 4x/day; catch up missed runs.' | Out-Null

Write-Host "Registered '$taskName':"
Write-Host "  - runs at 08:00, 12:30, 16:30, 20:30 (local time)"
Write-Host "  - catches up a missed run when the PC comes back on"
if ($AlsoAtLogon) { Write-Host "  - ALSO runs at every login" }

# Retire the login-only Startup shortcut so we don't double-run.
$startupLnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'JobScraper-Logon.lnk'
if (Test-Path $startupLnk) {
    Remove-Item $startupLnk -Force
    Write-Host "Removed the old login-only Startup shortcut (the task covers it now)."
}

Write-Host "`nCheck it any time with:  Get-ScheduledTask -TaskName '$taskName' | Get-ScheduledTaskInfo"
