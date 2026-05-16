"""Read-only FastAPI dashboard over the jobs table.

Personal tool: no auth, deployed to Render free tier. Reuses db.get_connection()
so it picks up the same Supabase pooler env vars (SUPABASE_PROJECT_REF,
supabasePW, SUPABASE_POOLER_HOST) as the scrapers.

Routes:
  GET  /                                dashboard page with filters
  GET  /api/jobs/{id}/description       JSON payload for the description modal
  POST /api/run/{company}               dispatch scrape-one.yml on GitHub Actions
  GET  /api/run/{run_id}/status         poll run status + log tail when finished
"""
from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

# Make `import db` work whether we're launched from repo root or web/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402
from run import COMPANY_NAMES  # noqa: E402

app = FastAPI(title="Jobs dashboard", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

GITHUB_API = "https://api.github.com"
WORKFLOW_FILE = "scrape-one.yml"

# Dashboard rows store the display name ("Sanofi") but the workflow expects
# the scraper key ("sanofi"). Reverse-map at startup.
KEY_BY_DISPLAY = {v: k for k, v in COMPANY_NAMES.items()}


def _all(cur, sql: str, params: tuple = ()) -> list[tuple]:
    cur.execute(sql, params)
    return cur.fetchall()


# Scraped locations are messy: "Paris, Paris, France", "PUTEAUX, Hauts-de-Seine,
# France", multi-location jobs joined by "|" or ";". Reduce to a clean list of
# unique city names for the dropdown.
def _extract_cities(raw_locations: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for loc in raw_locations:
        if not loc:
            continue
        for chunk in re.split(r"[|;]", loc):
            city = chunk.split(",")[0].strip()
            if not city:
                continue
            key = city.lower()
            if key not in seen:
                seen[key] = city.title() if city.isupper() else city
    return sorted(seen.values(), key=str.lower)


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    company: str | None = None,
    location: list[str] = Query(default=[]),
    q: str | None = None,
    show_closed: bool = False,
):
    where: list[str] = []
    params: list = []

    if not show_closed:
        where.append("still_open = TRUE")
    if company:
        where.append("company = %s")
        params.append(company)
    if location:
        where.append("location ILIKE ANY(%s)")
        params.append([f"%{loc}%" for loc in location])
    if q:
        where.append("(title ILIKE %s OR COALESCE(description, '') ILIKE %s)")
        like = f"%{q}%"
        params += [like, like]

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    jobs_sql = f"""
        SELECT id, company, title, location, category, posted_date,
               first_seen_at, still_open, apply_url,
               (first_seen_at >= NOW() - INTERVAL '7 days') AS is_new
        FROM jobs
        {where_sql}
        ORDER BY posted_date DESC NULLS LAST, first_seen_at DESC
        LIMIT 1000
    """

    with db.get_connection() as conn, conn.cursor() as cur:
        rows = _all(cur, jobs_sql, tuple(params))
        companies = [r[0] for r in _all(
            cur,
            "SELECT DISTINCT company FROM jobs WHERE still_open = TRUE ORDER BY company",
        )]
        loc_sql = (
            "SELECT DISTINCT location FROM jobs "
            "WHERE location IS NOT NULL AND location <> ''"
        )
        loc_params: list = []
        if not show_closed:
            loc_sql += " AND still_open = TRUE"
        if company:
            loc_sql += " AND company = %s"
            loc_params.append(company)
        raw_locations = [r[0] for r in _all(cur, loc_sql, tuple(loc_params))]
        locations = _extract_cities(raw_locations)
        stats_row = _all(cur, """
            SELECT
              COUNT(*) FILTER (WHERE still_open),
              COUNT(DISTINCT company) FILTER (WHERE still_open),
              COUNT(*) FILTER (
                WHERE still_open AND first_seen_at >= NOW() - INTERVAL '7 days'
              )
            FROM jobs
        """)[0]

    jobs = [
        {
            "id": r[0],
            "company": r[1],
            "title": r[2],
            "location": r[3],
            "category": r[4],
            "posted_date": r[5],
            "first_seen_at": r[6],
            "still_open": r[7],
            "apply_url": r[8],
            "is_new": r[9],
        }
        for r in rows
    ]

    return templates.TemplateResponse(request, "dashboard.html", {
        "jobs": jobs,
        "companies": companies,
        "locations": locations,
        "stats": {
            "total_open": stats_row[0],
            "companies": stats_row[1],
            "new_this_week": stats_row[2],
        },
        "selected_company": company or "",
        "selected_company_key": KEY_BY_DISPLAY.get(company or ""),
        "selected_locations": location,
        "q": q or "",
        "show_closed": show_closed,
        "result_count": len(jobs),
    })


@app.get("/api/jobs/{job_id}/description")
def job_description(job_id: int):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT title, company, location, category, posted_date, "
            "       description, apply_url, still_open "
            "FROM jobs WHERE id = %s",
            (job_id,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse({
        "title": row[0],
        "company": row[1],
        "location": row[2],
        "category": row[3],
        "posted_date": row[4].isoformat() if row[4] else None,
        "description": row[5] or "",
        "apply_url": row[6],
        "still_open": row[7],
    })


def _gh_session() -> tuple[requests.Session, str]:
    token = os.environ.get("GH_DISPATCH_TOKEN")
    repo = os.environ.get("GH_REPO")
    if not token or not repo:
        raise HTTPException(
            status_code=503,
            detail="Fresh run is disabled (GH_DISPATCH_TOKEN / GH_REPO not set).",
        )
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "job-scrapper-dashboard",
    })
    return s, repo


@app.post("/api/run/{company}")
def trigger_run(company: str):
    if company not in COMPANY_NAMES:
        raise HTTPException(status_code=404, detail=f"Unknown company key: {company}")

    s, repo = _gh_session()
    # Record the dispatch moment to filter the run list and avoid grabbing
    # an older run for the same workflow. Small back-buffer for clock skew.
    dispatch_at = datetime.now(timezone.utc) - timedelta(seconds=10)

    r = s.post(
        f"{GITHUB_API}/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/dispatches",
        json={"ref": "main", "inputs": {"company": company}},
        timeout=15,
    )
    if r.status_code != 204:
        raise HTTPException(
            status_code=502,
            detail=f"GitHub dispatch failed ({r.status_code}): {r.text[:300]}",
        )

    # workflow_dispatch doesn't return a run id — find it by listing recent runs
    # of this workflow created after dispatch_at. The new run usually appears
    # within 1-3 seconds.
    created_filter = dispatch_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    runs_url = (
        f"{GITHUB_API}/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs"
        f"?event=workflow_dispatch&created=>={created_filter}&per_page=5"
    )
    for _ in range(8):
        time.sleep(1)
        rr = s.get(runs_url, timeout=15)
        if rr.status_code != 200:
            continue
        runs = rr.json().get("workflow_runs", [])
        if runs:
            run = max(runs, key=lambda x: x["created_at"])
            return JSONResponse({
                "run_id": run["id"],
                "html_url": run["html_url"],
                "status": run["status"],
            })

    # Dispatch succeeded but the run didn't show up in time. Hand back the
    # Actions page so the user can still follow it.
    return JSONResponse(
        status_code=202,
        content={
            "run_id": None,
            "html_url": f"https://github.com/{repo}/actions/workflows/{WORKFLOW_FILE}",
            "status": "queued",
            "detail": "Dispatched. Run id not visible yet — check the Actions page.",
        },
    )


@app.get("/api/run/{run_id}/status")
def run_status(run_id: int):
    s, repo = _gh_session()

    r = s.get(f"{GITHUB_API}/repos/{repo}/actions/runs/{run_id}", timeout=15)
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Run not found")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"GitHub error {r.status_code}")
    run = r.json()

    payload = {
        "run_id": run["id"],
        "status": run["status"],
        "conclusion": run["conclusion"],
        "html_url": run["html_url"],
        "log_tail": None,
    }

    # Logs are only available after the run completes. Fetch the first job's
    # plain-text log and keep the tail — the Persisted summary block is the
    # last interesting thing run.py prints, so it's always near the bottom.
    if run["status"] == "completed":
        jobs_resp = s.get(
            f"{GITHUB_API}/repos/{repo}/actions/runs/{run_id}/jobs",
            timeout=15,
        )
        jobs = jobs_resp.json().get("jobs", []) if jobs_resp.status_code == 200 else []
        if jobs:
            job_id = jobs[0]["id"]
            log_resp = s.get(
                f"{GITHUB_API}/repos/{repo}/actions/jobs/{job_id}/logs",
                timeout=20,
                allow_redirects=True,
            )
            if log_resp.status_code == 200:
                lines = log_resp.text.splitlines()
                payload["log_tail"] = "\n".join(lines[-60:])

    return JSONResponse(payload)


@app.get("/healthz")
def healthz():
    return {"ok": True}
