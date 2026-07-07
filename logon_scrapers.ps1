<#
    logon_scrapers.ps1

    The single "run this every time my PC turns on" entry point. So you never have
    to remember the daily manual run. It does two complementary things:

      1) run_local_scrapers.ps1
         The scrapers GitHub Actions CANNOT run at all - bnp, safran, pernodricard.
         Their WAFs / token-buckets IP-block GitHub's datacenter ranges but clear
         from your home (residential) IP. These are NOT in the CI matrix, so they
         would otherwise simply never run. Throttled to once / 4h, so opening your
         PC several times a day doesn't re-hammer them.

      2) refresh_failing_companies.ps1 -Run
         Asks GitHub which IN-CI scrapers failed on the last scheduled run,
         rebuilds logs\failing_companies.txt, and re-runs those locally too - a
         residential IP often clears a transient / WAF failure that the cloud hit.

    Net effect: anything that isn't green in the cloud gets a local run on your
    machine. All DB writes are idempotent and new-job email alerts only fire on
    genuinely new job ids, so running again the same day is safe and silent.

    Everything printed here is also saved to logs\logon_YYYYMMDD_HHmmss.log.
#>
[CmdletBinding()]
param()

$proj   = $PSScriptRoot
$logDir = Join-Path $proj 'manual-runs\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# One-time migration: older runs wrote to <proj>\logs. Sweep any leftovers into
# manual-runs\logs and remove the old folder. Skips files still in use, so a run
# that's mid-flight when this fires is left alone and cleaned on the next pass.
$oldLogs = Join-Path $proj 'logs'
if ((Test-Path $oldLogs) -and
    ((Resolve-Path $oldLogs).Path -ne (Resolve-Path $logDir).Path)) {
    Get-ChildItem $oldLogs -Force -ErrorAction SilentlyContinue | ForEach-Object {
        try { Move-Item $_.FullName -Destination $logDir -Force -ErrorAction Stop } catch { }
    }
    if (-not (Get-ChildItem $oldLogs -Force -ErrorAction SilentlyContinue)) {
        Remove-Item $oldLogs -Force -ErrorAction SilentlyContinue
    }
}

$log = Join-Path $logDir ("logon_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))

try { Start-Transcript -Path $log | Out-Null } catch { }

Write-Host "===== logon scrapers @ $(Get-Date -Format o) ====="

# 1) The CI-impossible scrapers.
#    - bnp + safran: robust, 4h throttle (the run_local default set).
#    - pernodricard: a ban-sensitive Workday token bucket. Run it in its OWN
#      invocation at most once/~day, throttled on ATTEMPT, so a transient ban can
#      never trigger repeated same-day retries (that's what banned it before).
Write-Host "`n----- [1/2] local-only scrapers -----" -ForegroundColor Cyan
& (Join-Path $proj 'run_local_scrapers.ps1')
& (Join-Path $proj 'run_local_scrapers.ps1') -Companies pernodricard -MinHoursBetweenRuns 20 -ThrottleOnAttempt

# 2) Whatever else broke in the cloud on the last run - list it and re-run it.
Write-Host "`n----- [2/2] github-actions failures -----" -ForegroundColor Cyan
& (Join-Path $proj 'refresh_failing_companies.ps1') -Run

Write-Host "`n===== done @ $(Get-Date -Format o) ====="

# keep ~30 days of logon logs
Get-ChildItem $logDir -Filter 'logon_*.log' -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

try { Stop-Transcript | Out-Null } catch { }

# keep a stable "latest.log" = a copy of this run, so there's always one
# obvious file to open in the JobScraper Logs folder.
try { Copy-Item -Path $log -Destination (Join-Path $logDir 'latest.log') -Force } catch { }
