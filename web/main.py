"""Read-only FastAPI dashboard over the jobs table.

Personal tool: no auth, deployed to Render free tier. Reuses db.get_connection()
so it picks up the same Supabase pooler env vars (SUPABASE_PROJECT_REF,
supabasePW, SUPABASE_POOLER_HOST) as the scrapers.

Routes:
  GET  /                                dashboard page with filters
  GET  /api/jobs/{id}/description       JSON payload for the description modal
  POST /api/jobs/{id}/to-apply          toggle the "to apply" flag from the modal
  POST /api/run/{company}               dispatch scrape-one.yml on GitHub Actions
  GET  /api/run/{run_id}/status         poll run status + log tail when finished
  GET    /applications                  application tracker page with filters
  GET    /api/applications/{id}         JSON payload for the tracker detail panel
  POST   /api/applications              create a candidature by hand
  PATCH  /api/applications/{id}         edit any field, including the status
  POST   /api/applications/{id}/contacts       attach (or create) a person
  DELETE /api/applications/{id}/contacts/{cid} detach a person
  PATCH  /api/contacts/{id}             edit a person (notably email_status)
  POST   /api/applications/{id}/touches log an exchange
  PATCH  /api/touches/{id}              edit one (usually draft -> sent)
  DELETE /api/touches/{id}              undo a mis-logged exchange

The tracker tables are edited from two places that hold identical rights: this
dashboard, by hand, and a Cowork session running SQL in the Supabase editor
when there are dozens to process at once. Neither can do more than the other.

The tracker is independent of the scraper: `jobs` and `applications` share no
key, because most openings are found on LinkedIn, WTTJ or by referral rather
than by a scraper. The offer is just a URL on the candidature.
"""
from __future__ import annotations

import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

# Make `import db` work whether we're launched from repo root or web/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402
from run import COMPANY_NAMES  # noqa: E402
from web import applications as apps  # noqa: E402
from web import tracker_write as tw  # noqa: E402
from web.filters import (  # noqa: E402
    DATE_CHURN_COMPANIES,
    REGION_KEYS,
    REGION_LABELS,
    REGIONS,
    matches_region,
)

app = FastAPI(title="Jobs dashboard", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

GITHUB_API = "https://api.github.com"
WORKFLOW_FILE = "scrape-one.yml"

# Dashboard rows store the display name ("Sanofi") but the workflow expects
# the scraper key ("sanofi"). Reverse-map at startup.
KEY_BY_DISPLAY = {v: k for k, v in COMPANY_NAMES.items()}

# Dashboard-side "old posting" rule (raw DB keeps every row): a still-open
# posting first published more than ~6 months ago is an evergreen/pipeline
# requisition — real and applyable, just not fresh. Those rows get an
# "OPEN N MO" badge, and the hide_old checkbox removes them from the list.
# Rows with NULL posted_date are never hidden (unknown age is not old age).
OLD_AFTER_DAYS = 183

# Every date column on `jobs`, exposed as a "filter on this date" dropdown so
# the dashboard can answer the questions that used to need a Supabase query:
# what did we first see last week, what closed in July, what got reposted.
#   key -> (dropdown label, SQL expression, tooltip)
# The SQL expression is interpolated into the query, so it MUST come from this
# dict and never from the query string — the key is validated against it.
# Timestamps are cast to ::date so a plain YYYY-MM-DD bound is inclusive on
# both ends.
DATE_FIELDS: dict[str, tuple[str, str, str]] = {
    "effective": (
        "Date: effective", "effective_date",
        "The upstream posted_date, except on date-churn boards "
        "(Deloitte, Orange) where it is first_seen_at. Default sort.",
    ),
    "posted_date": (
        "Date: posted", "posted_date",
        "Raw posted_date as the company published it. Worthless on churn "
        "boards, which restamp it to today on every crawl.",
    ),
    "first_seen_at": (
        "Date: first seen", "first_seen_at::date",
        "First scrape that ever returned this job id — what NEW is based on.",
    ),
    "last_seen_at": (
        "Date: last seen", "last_seen_at::date",
        "Most recent scrape that still found the job on the board.",
    ),
    "closed_at": (
        "Date: closed", "closed_at::date",
        "When the job stopped appearing on the board. Only closed rows have "
        "one — pair with Show closed.",
    ),
    "reopened_at": (
        "Date: reposted", "reopened_at::date",
        "When a closed job came back under the same id (the REPOSTED badge).",
    ),
    "date_bumped_at": (
        "Date: bumped upstream", "date_bumped_at::date",
        "When a still-open job's posted_date moved forward upstream. Rarely "
        "populated so far.",
    ),
}
DEFAULT_DATE_FIELD = "effective"

# The date every age rule and the default sort run on. Normally the upstream
# posted_date, but for DATE_CHURN_COMPANIES that value is rewritten to "today"
# on every crawl, so we substitute first_seen_at — the first run that returned
# this native_job_id. See web/filters.py for why those companies are listed.
# Computed once in a CTE so WHERE and ORDER BY can both reference it.
_EFFECTIVE_DATE_CTE = """
    WITH j AS (
        SELECT *,
               CASE WHEN company = ANY(%s)
                    THEN first_seen_at::date
                    ELSE posted_date
               END AS effective_date
        FROM jobs
    )
"""
_CHURN_PARAM = sorted(DATE_CHURN_COMPANIES)

# Strip ISO8601 timestamps that GitHub prefixes to every raw log line.
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z ?")
# Drop GitHub Actions log directives like ##[group]/##[endgroup]/##[debug].
_GROUP_RE = re.compile(r"^##\[[a-z]+\].*$")


def _clean_run_log(raw: str) -> str:
    """Cut the Actions noise so the panel shows only what run.py printed.

    Mobile-friendly: from `>>> Scraping <Company>...` (run.py's first log
    line) to end-of-file, no timestamps, no setup steps, no pip output.
    Falls back to the raw tail if the marker is missing (rare — scraper
    crashed before importing).
    """
    cleaned: list[str] = []
    for line in raw.splitlines():
        line = _TS_RE.sub("", line)
        if _GROUP_RE.match(line):
            continue
        cleaned.append(line)

    for i, line in enumerate(cleaned):
        if line.startswith(">>> Scraping"):
            cleaned = cleaned[i:]
            break

    return "\n".join(cleaned[-120:])


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


def _parse_date(value: str | None) -> date | None:
    """Query-string date bound. Anything unparseable is treated as absent
    rather than a 422 — a hand-edited URL should degrade to "no bound", not
    to an error page.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    company: str | None = None,
    location: list[str] = Query(default=[]),
    region: str = "",
    q: str | None = None,
    show_closed: bool = False,
    to_apply: bool = False,
    hide_old: bool = False,
    date_field: str = DEFAULT_DATE_FIELD,
    date_from: str | None = None,
    date_to: str | None = None,
):
    old_cutoff = date.today() - timedelta(days=OLD_AFTER_DAYS)

    if region not in REGION_KEYS:
        region = ""
    # Validated against the dict, never interpolated raw — see DATE_FIELDS.
    if date_field not in DATE_FIELDS:
        date_field = DEFAULT_DATE_FIELD
    date_col = DATE_FIELDS[date_field][1]
    d_from, d_to = _parse_date(date_from), _parse_date(date_to)

    where: list[str] = []
    params: list = []

    if not show_closed:
        where.append("still_open = TRUE")
    if to_apply:
        where.append("to_apply = TRUE")
    if hide_old:
        where.append("(effective_date IS NULL OR effective_date >= %s)")
        params.append(old_cutoff)
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
    # Both bounds inclusive. A NULL in the chosen column fails the comparison
    # and drops out, which is the intent: filtering on "Closed" is asking for
    # rows that actually closed.
    if d_from:
        where.append(f"{date_col} >= %s")
        params.append(d_from)
    if d_to:
        where.append(f"{date_col} <= %s")
        params.append(d_to)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    # Sort on whatever date the user is filtering by, so the range they asked
    # for reads in order; effective_date stays the tiebreaker-free default.
    order_sql = f"{date_col} DESC NULLS LAST, first_seen_at DESC"

    # No LIMIT: the page renders every matching row. The old 2000/1000 caps
    # silently truncated a company's tail, which is the opposite of what this
    # dashboard is for. If the table ever grows past what one page can carry,
    # add pagination — don't put back a cap that hides rows without saying so.
    #
    # A repost is a row we had marked closed that reappeared under the same
    # native_job_id (reopened_at stamped by the upsert). It won't refresh
    # first_seen_at, so is_new stays FALSE — is_reopened is the separate signal
    # that earns the REPOSTED badge. Immune to Orange-style date churn: a
    # continuously-open row never closes, so reopened_at is never set.
    jobs_sql = f"""
        {_EFFECTIVE_DATE_CTE}
        SELECT id, company, title, location, category, posted_date,
               first_seen_at, still_open, apply_url, to_apply,
               (first_seen_at >= NOW() - INTERVAL '7 days') AS is_new,
               (reopened_at IS NOT NULL
                AND reopened_at >= NOW() - INTERVAL '7 days') AS is_reopened,
               reopened_at, closed_at, reopen_count, effective_date
        FROM j
        {where_sql}
        ORDER BY {order_sql}
    """

    with db.get_connection() as conn, conn.cursor() as cur:
        # The CTE placeholder comes first in the SQL text, so its param leads.
        rows = _all(cur, jobs_sql, tuple([_CHURN_PARAM, *params]))
        # Projection over the whole table for stats + dropdowns. Only the
        # region scope is applied to it (identically to the rows below), so
        # totals, counts, and option lists stay consistent with what's visible.
        # is_recent counts a reopened row as recent too, so the NEW (7D) stat
        # includes reposts (the badge itself stays distinct).
        universe_rows = _all(cur, """
            SELECT company, location, still_open, to_apply,
                   (first_seen_at >= NOW() - INTERVAL '7 days'
                    OR (reopened_at IS NOT NULL
                        AND reopened_at >= NOW() - INTERVAL '7 days')) AS is_recent
            FROM jobs
        """)

    # Geography is opt-in: region == "" (the default) keeps every row, whatever
    # its location. Picking Paris / petite couronne / IDF / France narrows both
    # the rows and the stats universe with the same predicate, so the counters
    # never disagree with the list. See web/filters.py for the tiers.
    if region:
        rows = [r for r in rows if matches_region(r[3], region)]
        universe = [r for r in universe_rows if matches_region(r[1], region)]
    else:
        universe = universe_rows
    open_universe = [r for r in universe if r[2]]

    companies = sorted({r[0] for r in open_universe})

    loc_source = universe if show_closed else open_universe
    if company:
        loc_source = [r for r in loc_source if r[0] == company]
    locations = _extract_cities([r[1] for r in loc_source])

    stats = {
        "total_open": len(open_universe),
        "companies": len(companies),
        "new_this_week": sum(1 for r in open_universe if r[4]),
        "to_apply": sum(1 for r in universe if r[3]),
    }

    jobs = []
    for r in rows:
        # Base date for the age badges and the "Posted / First tracked" line.
        # For a churn board this is first_seen_at and the upstream posted_date
        # is deliberately not shown — it is today's date on every row.
        base = r[15].date() if isinstance(r[15], datetime) else r[15]
        churned = r[1] in DATE_CHURN_COMPANIES
        # Days the row spent off the board between closing and coming back.
        # A genuine repost sits idle for weeks; a gap of 0-1 day is almost
        # always a partial scrape that closed the row and a later run that
        # found it again (scraper_runs.jobs_found dips then recovers, while
        # the run still logs 'success' because the empty-scrape guard only
        # catches a *fully* empty return). Surfaced so the badge can be
        # trusted or discounted at a glance.
        reopened_at, closed_at = r[12], r[13]
        repost_gap_days = (
            (reopened_at.date() - closed_at.date()).days
            if reopened_at and closed_at else None
        )
        jobs.append({
            "id": r[0],
            "company": r[1],
            "title": r[2],
            "location": r[3],
            "category": r[4],
            "posted_date": r[5],
            "first_seen_at": r[6],
            "still_open": r[7],
            "apply_url": r[8],
            "to_apply": r[9],
            "is_new": r[10],
            "is_reopened": r[11],
            "reopened_at": reopened_at,
            "closed_at": closed_at,
            "reopen_count": r[14],
            "repost_gap_days": repost_gap_days,
            "base_date": base,
            "date_churned": churned,
            "is_old": bool(base and base < old_cutoff),
            "age_months": (date.today() - base).days // 30 if base else None,
        })

    return templates.TemplateResponse(request, "dashboard.html", {
        "jobs": jobs,
        "companies": companies,
        "locations": locations,
        "stats": stats,
        "selected_company": company or "",
        "selected_company_key": KEY_BY_DISPLAY.get(company or ""),
        "selected_locations": location,
        "q": q or "",
        "show_closed": show_closed,
        "to_apply_filter": to_apply,
        "hide_old": hide_old,
        "result_count": len(jobs),
        "regions": REGIONS,
        "selected_region": region,
        "region_label": REGION_LABELS[region],
        "date_fields": [(k, v[0], v[2]) for k, v in DATE_FIELDS.items()],
        "selected_date_field": date_field,
        "date_field_label": DATE_FIELDS[date_field][0].replace("Date: ", ""),
        "date_from": date_from or "",
        "date_to": date_to or "",
        "date_filtered": bool(d_from or d_to),
    })


def _job_description_payload(cur, job_id: int) -> dict | None:
    """The description-modal payload for one scraped job, or None if absent.

    Factored out so the tracker's detail panel renders a linked offer through
    exactly the same query, the same churn-date rule and the same field names
    as the offers tab — one description mechanism, two callers.
    """
    cur.execute(
        "SELECT title, company, location, category, posted_date, "
        "       description, apply_url, still_open, to_apply, "
        "       first_seen_at::date "
        "FROM jobs WHERE id = %s",
        (job_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    # Same base-date rule as the list: a churn board's posted_date is today's
    # crawl date, so the modal shows when we first tracked the listing instead.
    churned = row[1] in DATE_CHURN_COMPANIES
    base = row[9] if churned else row[4]
    return {
        "title": row[0],
        "company": row[1],
        "location": row[2],
        "category": row[3],
        "posted_date": row[4].isoformat() if row[4] else None,
        "base_date": base.isoformat() if base else None,
        "date_churned": churned,
        "description": row[5] or "",
        "apply_url": row[6],
        "still_open": row[7],
        "to_apply": row[8],
    }


@app.get("/api/jobs/{job_id}/description")
def job_description(job_id: int):
    with db.get_connection() as conn, conn.cursor() as cur:
        payload = _job_description_payload(cur, job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(payload)


@app.post("/api/jobs/{job_id}/to-apply")
def toggle_to_apply(job_id: int):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET to_apply = NOT to_apply "
            "WHERE id = %s RETURNING to_apply",
            (job_id,),
        )
        row = cur.fetchone()
        conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse({"to_apply": row[0]})


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
                payload["log_tail"] = _clean_run_log(log_resp.text)

    return JSONResponse(payload)


# ---------------------------------------------------------------------------
# Candidatures — the application tracker. Read only, always: these tables are
# maintained from a Gmail sweep run outside this repo. See web/applications.py.
# ---------------------------------------------------------------------------
@app.get("/applications", response_class=HTMLResponse)
def applications_page(
    request: Request,
    status: list[str] = Query(default=[]),
    company: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    stale: str = "",
    awaiting: bool = False,
):
    # Unknown status keys are dropped rather than 422'd — a hand-edited URL
    # should degrade to "no filter", same rule as _parse_date on the offers tab.
    statuses = [s for s in status if s in apps.STATUS_META]
    stale = stale if stale in apps.STALE_VALUES else ""
    stale_days = int(stale) if stale else None
    d_from, d_to = _parse_date(date_from), _parse_date(date_to)

    with db.get_connection() as conn, conn.cursor() as cur:
        rows = apps.fetch_applications(cur)
        contact_totals = apps.fetch_contact_totals(cur)

    # Stats describe the whole tracker, the result counter describes the
    # selection — the same split the offers tab uses.
    stats = apps.compute_stats(rows, contact_totals)
    companies = sorted({r["company"] for r in rows}, key=str.lower)
    visible = apps.apply_filters(
        rows,
        statuses=statuses,
        company=company or "",
        date_from=d_from,
        date_to=d_to,
        stale_days=stale_days,
        awaiting_reply=awaiting,
    )

    return templates.TemplateResponse(request, "applications.html", {
        "applications": visible,
        "companies": companies,
        "stats": stats,
        "status_meta": apps.STATUS_META,
        "status_order": list(apps.STATUS_META),
        "selected_statuses": statuses,
        "selected_company": company or "",
        "date_from": date_from or "",
        "date_to": date_to or "",
        "stale_choices": apps.STALE_CHOICES,
        "selected_stale": stale,
        "awaiting": awaiting,
        "result_count": len(visible),
        "live_count": sum(1 for r in visible if r["is_live"]),
        # Vocabularies for the add/edit forms, straight from the write layer so
        # a dropdown can never offer a value the validator would reject.
        "sources": tw.SOURCES,
        "close_reasons": tw.CLOSE_REASONS,
        "roles_in_process": tw.ROLES_IN_PROCESS,
        "touch_kinds": tw.TOUCH_KINDS,
        "channels": sorted(tw.CHANNELS),
        "email_statuses": sorted(tw.EMAIL_STATUSES),
        "today": date.today().isoformat(),
    })


@app.get("/api/applications/{application_id}")
def application_detail(application_id: int):
    with db.get_connection() as conn, conn.cursor() as cur:
        application = apps.fetch_application(cur, application_id)
        if application is None:
            raise HTTPException(status_code=404, detail="Application not found")
        contacts = apps.fetch_contacts(cur, application_id)
        timeline = apps.fetch_timeline(cur, application_id)

    return JSONResponse({
        "application": application,
        "contacts": contacts,
        "timeline": timeline,
    })


# ---------------------------------------------------------------------------
# Tracker writes — the "advanced sheet" half of the Candidatures tab.
#
# The dashboard is no longer read-only on these four tables: adding a
# candidature, moving a status and recording who was contacted all happen here,
# by hand. A Cowork mailbox sweep does the same things in bulk through SQL and
# holds no privilege these endpoints do not.
#
# There is no DELETE on an application on purpose — a dead candidature is
# marked rejected/closed/withdrawn and filtered out, never destroyed. See
# web/tracker_write.py.
# ---------------------------------------------------------------------------
def _write(fn, *args, **kwargs):
    """Run one write in its own transaction and translate the failure modes.

    ValidationError -> 400 with the offending field named.
    ConflictError   -> 409 (a UNIQUE index, e.g. the same company+role+date).
    Anything else propagates as a 500, because it is a bug, not bad input.
    """
    try:
        with db.get_connection() as conn, conn.cursor() as cur:
            result = fn(cur, *args, **kwargs)
            conn.commit()
            return result
    except tw.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except tw.ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.post("/api/applications")
async def create_application(request: Request):
    payload = await request.json()
    new_id = _write(tw.create_application, payload)
    return JSONResponse({"id": new_id}, status_code=201)


@app.patch("/api/applications/{application_id}")
async def update_application(application_id: int, request: Request):
    payload = await request.json()
    if not _write(tw.update_application, application_id, payload):
        raise HTTPException(status_code=404, detail="Application not found")
    return JSONResponse({"ok": True})


@app.post("/api/applications/{application_id}/contacts")
async def add_contact(application_id: int, request: Request):
    payload = await request.json()
    contact_id, created = _write(tw.attach_contact, application_id, payload)
    return JSONResponse({"contact_id": contact_id, "created": created},
                        status_code=201)


@app.delete("/api/applications/{application_id}/contacts/{contact_id}")
def remove_contact(application_id: int, contact_id: int):
    """Detach only: the person stays on file for other candidatures."""
    if not _write(tw.detach_contact, application_id, contact_id):
        raise HTTPException(status_code=404, detail="Contact not linked")
    return JSONResponse({"ok": True})


@app.patch("/api/contacts/{contact_id}")
async def update_contact(contact_id: int, request: Request):
    payload = await request.json()
    if not _write(tw.update_contact, contact_id, payload):
        raise HTTPException(status_code=404, detail="Contact not found")
    return JSONResponse({"ok": True})


@app.post("/api/applications/{application_id}/touches")
async def add_touch(application_id: int, request: Request):
    payload = await request.json()
    touch_id = _write(tw.create_touch, application_id, payload)
    return JSONResponse({"id": touch_id}, status_code=201)


@app.patch("/api/touches/{touch_id}")
async def update_touch(touch_id: int, request: Request):
    """Mostly {"state": "sent"} — a drafted relance actually went out."""
    payload = await request.json()
    if not _write(tw.update_touch, touch_id, payload):
        raise HTTPException(status_code=404, detail="Touch not found")
    return JSONResponse({"ok": True})


@app.delete("/api/touches/{touch_id}")
def remove_touch(touch_id: int):
    """Undo a mis-logged exchange — it would skew days_stale permanently."""
    if not _write(tw.delete_touch, touch_id):
        raise HTTPException(status_code=404, detail="Touch not found")
    return JSONResponse({"ok": True})


@app.get("/healthz")
def healthz():
    return {"ok": True}
