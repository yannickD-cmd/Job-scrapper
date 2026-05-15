"""Read-only FastAPI dashboard over the jobs table.

Personal tool: no auth, deployed to Render free tier. Reuses db.get_connection()
so it picks up the same Supabase pooler env vars (SUPABASE_PROJECT_REF,
supabasePW, SUPABASE_POOLER_HOST) as the scrapers.

Routes:
  GET /                          dashboard page with filters
  GET /api/jobs/{id}/description JSON payload for the description modal
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

# Make `import db` work whether we're launched from repo root or web/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402

app = FastAPI(title="Jobs dashboard", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _all(cur, sql: str, params: tuple = ()) -> list[tuple]:
    cur.execute(sql, params)
    return cur.fetchall()


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    company: str | None = None,
    location: str | None = None,
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
        where.append("location = %s")
        params.append(location)
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
        ORDER BY first_seen_at DESC
        LIMIT 1000
    """

    with db.get_connection() as conn, conn.cursor() as cur:
        rows = _all(cur, jobs_sql, tuple(params))
        companies = [r[0] for r in _all(
            cur,
            "SELECT DISTINCT company FROM jobs WHERE still_open = TRUE ORDER BY company",
        )]
        locations = [r[0] for r in _all(
            cur,
            "SELECT DISTINCT location FROM jobs "
            "WHERE still_open = TRUE AND location IS NOT NULL AND location <> '' "
            "ORDER BY location",
        )]
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

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "jobs": jobs,
        "companies": companies,
        "locations": locations,
        "stats": {
            "total_open": stats_row[0],
            "companies": stats_row[1],
            "new_this_week": stats_row[2],
        },
        "selected_company": company or "",
        "selected_location": location or "",
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


@app.get("/healthz")
def healthz():
    return {"ok": True}
