<#
    run_local_scrapers.ps1

    Runs the scrapers that GitHub Actions CANNOT run — their WAFs / rate-limiters
    punish datacenter ranges but clear from a residential IP. Today that's three:
        bnp          -> BNP Paribas    (Akamai Bot Manager)
        safran.group -> Safran         (WAF IP-reputation block)
        pernodricard -> Pernod Ricard  (Workday faceted-POST token bucket,
                                        escalating + far worse on datacenter ASNs)
    (Everything else runs 4x/day in .github/workflows/scrape.yml. If you ever
     add another CI-excluded scraper, just add its key to -Companies below or
     to the default list.)

    Meant to fire at logon so you don't have to remember. Safe to run often:
    db upserts are idempotent and new-job email alerts only fire on genuinely
    new native_job_ids, so a second run the same day sends no duplicate mail.

    Usage:
        .\run_local_scrapers.ps1                 # default: bnp + safran, 4h throttle
        .\run_local_scrapers.ps1 -Force          # ignore the throttle, run now
        .\run_local_scrapers.ps1 -Companies bnp  # just one
        .\run_local_scrapers.ps1 -MinHoursBetweenRuns 0   # never throttle
#>
[CmdletBinding()]
param(
    # pernodricard is intentionally OUT of the default set right now. Its Workday
    # faceted POST returns empty-message HTTP 400 on every residential attempt
    # (under investigation — likely the cookie-free rewrite regressed on
    # residential; see scrapers/pernodricard/pernodricard.py). Two reasons to
    # pause it here:
    #   1) each attempt makes ~3 escalating POSTs that only deepen a token-bucket
    #      ban, and it recovers nothing;
    #   2) because it never exits 0, it kept the marker below from ever being
    #      written, which DEFEATED this whole script's throttle for bnp/safran
    #      (they were re-running on every trigger too).
    # Run it deliberately once the scraper is fixed:
    #   .\run_local_scrapers.ps1 -Companies pernodricard
    [string[]] $Companies = @('bnp', 'safran.group'),
    # Skip if a run already SUCCEEDED within this many hours (0 = always run).
    # Stops repeated logons in one day from re-hammering the WAFs needlessly.
    [double]   $MinHoursBetweenRuns = 4,
    # Bump the throttle marker even when the run FAILS. For ban-sensitive
    # endpoints (pernodricard): a failed attempt still "spends" the interval, so
    # a transient ban can't trigger 4x/day retries that only deepen it. Default
    # (off) keeps the retry-on-failure behaviour that suits bnp/safran.
    [switch]   $ThrottleOnAttempt,
    [switch]   $Force
)

$proj   = $PSScriptRoot
$py     = Join-Path $proj '.venv\Scripts\python.exe'
$logDir = Join-Path $proj 'manual-runs\logs'
# Per-company-set marker so different -Companies invocations throttle
# independently (bnp+safran on 4h; pernodricard on its own ~daily interval).
$slug   = (@($Companies) | Sort-Object) -join '_'
$marker = Join-Path $logDir ".last_run_$slug"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# --- throttle -------------------------------------------------------------
if (-not $Force -and $MinHoursBetweenRuns -gt 0 -and (Test-Path $marker)) {
    $ageH = ((Get-Date) - (Get-Item $marker).LastWriteTime).TotalHours
    if ($ageH -lt $MinHoursBetweenRuns) {
        Write-Host ("Skip [{0}]: last run {1:N1}h ago (< {2}h). Use -Force to run now." -f ($Companies -join ','), $ageH, $MinHoursBetweenRuns)
        return
    }
}

if (-not (Test-Path $py)) { throw "venv python not found: $py  (create it or fix the path)" }

# --- run ------------------------------------------------------------------
# run.py loops the companies and continues past a failing one; its exit code is
# non-zero if ANY scraper failed. It loads .env itself for the Supabase / Gmail
# creds, so nothing secret is handled here.
$stamp  = Get-Date -Format 'yyyyMMdd_HHmmss'
$log    = Join-Path $logDir "local_scrapers_$stamp.log"
$outTmp = "$log.out"
$errTmp = "$log.err"

$p = Start-Process -FilePath $py -ArgumentList (@('run.py') + $Companies) `
    -WorkingDirectory $proj -NoNewWindow -Wait -PassThru `
    -RedirectStandardOutput $outTmp -RedirectStandardError $errTmp
$code = $p.ExitCode

# merge stdout + stderr into one readable, timestamped log
"=== $(Get-Date -Format o)  companies: $($Companies -join ', ')  exit: $code ===" | Set-Content -Path $log -Encoding utf8
Get-Content $outTmp -ErrorAction SilentlyContinue | Add-Content -Path $log -Encoding utf8
$err = Get-Content $errTmp -ErrorAction SilentlyContinue
if ($err) {
    "`n----- stderr -----" | Add-Content -Path $log -Encoding utf8
    $err | Add-Content -Path $log -Encoding utf8
}
Remove-Item $outTmp, $errTmp -ErrorAction SilentlyContinue

# Bump the throttle marker on success; also on failure when -ThrottleOnAttempt
# (ban-sensitive endpoints must not retry a failed run on every trigger).
if ($code -eq 0 -or $ThrottleOnAttempt) { Set-Content -Path $marker -Value (Get-Date -Format o) -Encoding utf8 }

# keep ~30 days of logs
Get-ChildItem $logDir -Filter 'local_scrapers_*.log' -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

Write-Host "Done (exit $code). Log: $log"
exit $code
