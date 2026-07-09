<#
    refresh_failing_companies.ps1

    Rebuilds the list of company scrapers that FAILED on the last GitHub Actions
    `scrape` run. Writes it to logs\failing_companies.txt (one company key per
    line) and prints a coloured summary. Meant to fire at logon so the moment you
    open your PC you can see what broke overnight while you were asleep.

    The repo is PUBLIC, so this needs no GitHub token - it makes 2 unauthenticated
    GETs against the REST API, far under the 60/hr anonymous limit. (Set
    $env:GITHUB_TOKEN if you ever want the higher authenticated limit; optional.)

    NOTE: bnp / safran / pernodricard are deliberately NOT in the CI matrix (their
    WAFs IP-block GitHub's datacenter ranges), so they never appear here. Run those
    with run_local_scrapers.ps1. THIS script surfaces the *in-CI* scrapers that
    broke - a site changed its markup, an endpoint moved, a transient 5xx, etc.

    Usage:
        .\refresh_failing_companies.ps1          # rebuild + print the list
        .\refresh_failing_companies.ps1 -Run     # also re-run the failing ones
                                                 #   locally through run.py
        .\refresh_failing_companies.ps1 -Open    # open the run on github.com
#>
[CmdletBinding()]
param(
    [switch] $Run,      # after listing, re-run the failing scrapers via .venv python
    [switch] $Open      # open the workflow run in the browser
)

$ErrorActionPreference = 'Stop'
# Windows PowerShell 5.1 may default to TLS 1.0; GitHub requires 1.2.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$owner = 'yannickD-cmd'
$repo  = 'Job-scrapper'
$wf    = 'scrape.yml'          # workflow file name = the API key for its runs

$proj   = $PSScriptRoot
$logDir = Join-Path $proj 'manual-runs\logs'
$out    = Join-Path $logDir 'failing_companies.txt'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$headers = @{
    'Accept'               = 'application/vnd.github+json'
    'X-GitHub-Api-Version' = '2022-11-28'
    'User-Agent'           = 'job-scrapper-failwatch'
}
if ($env:GITHUB_TOKEN) { $headers['Authorization'] = "Bearer $env:GITHUB_TOKEN" }

# GET with a few retries - at logon the network may not be up yet.
function Invoke-GH([string] $Url) {
    for ($i = 1; $i -le 4; $i++) {
        try { return Invoke-RestMethod -Uri $Url -Headers $headers }
        catch {
            if ($i -eq 4) { throw }
            Write-Host "  network not ready (try $i/4), retrying in 10s..." -ForegroundColor DarkYellow
            Start-Sleep -Seconds 10
        }
    }
}

# --- 1) latest COMPLETED run of the scrape workflow -----------------------
try {
    $runsUrl = "https://api.github.com/repos/$owner/$repo/actions/workflows/$wf/runs?status=completed&per_page=1"
    $latestRun = (Invoke-GH $runsUrl).workflow_runs | Select-Object -First 1
}
catch {
    # Don't clobber a good list just because we booted offline.
    Write-Host "Could not reach GitHub ($($_.Exception.Message))." -ForegroundColor Yellow
    if (Test-Path $out) { Write-Host "Keeping previous list: $out" }
    exit 0
}
if (-not $latestRun) { throw "No completed runs found for $wf" }

# --- 2) its jobs (paged), keep the ones that failed -----------------------
$jobs = @()
$page = 1
do {
    $jobsUrl = "https://api.github.com/repos/$owner/$repo/actions/runs/$($latestRun.id)/jobs?per_page=100&page=$page"
    $resp = Invoke-GH $jobsUrl
    $jobs += $resp.jobs
    $page++
} while ($resp.jobs.Count -eq 100)

# Matrix job names look like "scrape (allianz)" / "scrape (dassault.systemes)".
# Treat both hard failures and timeouts as "failing".
$bad = @('failure', 'timed_out')
$failed = foreach ($j in $jobs) {
    if ($bad -contains $j.conclusion -and $j.name -match '^\s*scrape \((.+)\)\s*$') {
        $Matches[1]
    }
}
$failed = @($failed | Sort-Object -Unique)

# --- 3) write the list ----------------------------------------------------
$stamp  = Get-Date -Format o
$header = "# scrape run #$($latestRun.run_number)  ($($latestRun.created_at))  rebuilt $stamp"
if ($failed.Count -gt 0) {
    @($header) + $failed | Set-Content -Path $out -Encoding utf8
} else {
    @($header, '# (none - every in-CI scraper passed)') | Set-Content -Path $out -Encoding utf8
}

# --- 4) report ------------------------------------------------------------
$total = @($jobs | Where-Object { $_.name -match '^\s*scrape \(' }).Count
Write-Host ""
Write-Host "scrape run #$($latestRun.run_number)  [$($latestRun.event)]  $($latestRun.created_at)"
Write-Host "  $($latestRun.html_url)"
Write-Host ("  {0} company jobs - {1} failed" -f $total, $failed.Count)
if ($failed.Count -gt 0) {
    Write-Host "FAILED:" -ForegroundColor Red
    $failed | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
} else {
    Write-Host "All in-CI scrapers passed." -ForegroundColor Green
}
Write-Host "List written to: $out"

if ($Open) { Start-Process $latestRun.html_url }

# --- 5) optional: re-run the failures locally -----------------------------
if ($Run -and $failed.Count -gt 0) {
    $py = Join-Path $proj '.venv\Scripts\python.exe'
    if (-not (Test-Path $py)) { throw "venv python not found: $py" }

    # Never re-run the CI-excluded local-only scrapers here. They're already
    # driven by run_local_scrapers.ps1 (with its own throttle), and re-running
    # pernodricard in particular feeds its token-bucket ban. This also guards
    # against a STALE CI run — one that executed before a company was pulled
    # from the matrix — still listing them as "failed".
    $localOnly = @('bnp', 'safran.group', 'pernodricard')
    $toRun = @($failed | Where-Object { $_ -notin $localOnly })

    if ($toRun.Count -eq 0) {
        Write-Host "`nNothing to re-run (all failures are local-only, already handled)." -ForegroundColor DarkGray
    } else {
        Write-Host "`nRe-running failing scrapers locally: $($toRun -join ', ')" -ForegroundColor Cyan
        # Absolute run.py path so this works under the scheduled task, whose
        # working directory is C:\WINDOWS\system32 — NOT the project. A bare
        # 'run.py' there failed with "can't open file ...\system32\run.py".
        & $py (Join-Path $proj 'run.py') @toRun
        Write-Host "Local re-run exit: $LASTEXITCODE"
    }
}

exit 0
