"""Postgres database for the job scraper, via Supabase Transaction pooler.

Two tables — `jobs` and `scraper_runs` — matching the project spec.

Connection string is constructed at runtime from .env values so secrets stay
out of git and special chars in the password are URL-encoded correctly.

Schema choices (vs an earlier SQLite draft):
- BIGSERIAL    for PKs — 64-bit auto-increment, never wraps for personal use
- TIMESTAMPTZ  for first_seen_at / last_seen_at / run_timestamp — proper TZ-aware
- DATE         for posted_date — no time component, sortable
- BOOLEAN      for still_open — real boolean, not 0/1
- JSONB        for raw_payload — queryable with -> / ->> / @> operators in SQL
- CHECK        on scraper_runs.status — guards against typos in the runner

Public API for the scrapers + future runner:
- init_db()                        idempotent schema apply
- persist_run_results(company, jobs, duration_ms)
                                   atomic: upsert jobs, mark missing closed,
                                   log a successful run; returns summary dict
- log_failed_run(company, error_message, duration_ms)
                                   log a run that crashed before upsert
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import quote

import psycopg
from psycopg.types.json import Jsonb
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

_REQUIRED = ("supabasePW", "SUPABASE_PROJECT_REF", "SUPABASE_POOLER_HOST")
_missing = [v for v in _REQUIRED if not os.environ.get(v)]
if _missing:
    raise RuntimeError(
        f"Missing env vars in .env: {', '.join(_missing)}. "
        f"See .env.example for what each one means."
    )

DATABASE_URL = (
    f"postgresql://postgres.{os.environ['SUPABASE_PROJECT_REF']}"
    f":{quote(os.environ['supabasePW'], safe='')}"
    f"@{os.environ['SUPABASE_POOLER_HOST']}:6543/postgres"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              BIGSERIAL PRIMARY KEY,
    company         TEXT NOT NULL,
    native_job_id   TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    location        TEXT,
    category        TEXT,
    posted_date     DATE,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    still_open      BOOLEAN NOT NULL DEFAULT TRUE,
    apply_url       TEXT NOT NULL,
    raw_payload     JSONB,
    to_apply        BOOLEAN NOT NULL DEFAULT FALSE,
    -- Repost tracking. `first_seen_at` is stamped once and never moves, so a
    -- company that pulls a listing and re-publishes it under the SAME
    -- native_job_id would otherwise be invisible (upsert hits the UPDATE
    -- branch, no NEW badge, no alert). We detect a genuine repost as a
    -- still_open FALSE->TRUE transition: the row went absent for >=1 scrape
    -- (marked closed, closed_at stamped) and then reappeared (reopened_at
    -- stamped, reopen_count bumped). This is immune to Orange-style date churn
    -- because a continuously-open row is never marked closed, so it never
    -- transitions — see project_orange_dateposted_bogus / _new_means_first_id.
    closed_at       TIMESTAMPTZ,
    reopened_at     TIMESTAMPTZ,
    reopen_count    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (company, native_job_id)
);

-- Backfill the repost-tracking columns on databases created before they
-- existed (init_db is the idempotent migration path; CREATE TABLE IF NOT
-- EXISTS above is a no-op once the table is there).
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS closed_at    TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS reopened_at  TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS reopen_count INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_jobs_company    ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_jobs_still_open ON jobs(still_open);
CREATE INDEX IF NOT EXISTS idx_jobs_reopened   ON jobs(reopened_at);

CREATE TABLE IF NOT EXISTS scraper_runs (
    id              BIGSERIAL PRIMARY KEY,
    run_timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    company         TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('success', 'partial', 'failed')),
    jobs_found      INTEGER NOT NULL DEFAULT 0,
    new_jobs        INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT,
    duration_ms     BIGINT
);

CREATE INDEX IF NOT EXISTS idx_runs_company_time
    ON scraper_runs(company, run_timestamp DESC);
"""

# (xmax = 0) is TRUE only when ON CONFLICT performed an INSERT (not an UPDATE).
# This is how we distinguish brand-new jobs from rows we've already seen.
#
# The reopen columns detect a genuine repost: `jobs.still_open` inside DO UPDATE
# is the row's value BEFORE this upsert, so `jobs.still_open = FALSE` is TRUE
# only when we're re-finding a row we had marked closed. That's the exact
# FALSE->TRUE transition an Orange-style date refresh never produces (it stays
# continuously open). `reopened_at = NOW()` in RETURNING then reads back TRUE
# only for rows reopened in THIS transaction — NOW() is the transaction start
# time, constant across the whole persist_run_results loop, so a reopen stamped
# in a prior run carries an older timestamp and won't match.
_UPSERT_SQL = """
INSERT INTO jobs (
    company, native_job_id, title, description,
    location, category, posted_date, apply_url, raw_payload,
    last_seen_at, still_open
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), TRUE)
ON CONFLICT (company, native_job_id) DO UPDATE SET
    title         = EXCLUDED.title,
    -- COALESCE the enrichment-only fields: a scraper that keeps a row despite a
    -- transient detail-page failure sends description/posted_date = NULL, and we
    -- must not let that wipe a previously-populated value. NULL falls back to the
    -- stored value; a real new value still overwrites.
    description   = COALESCE(EXCLUDED.description, jobs.description),
    location      = EXCLUDED.location,
    category      = EXCLUDED.category,
    posted_date   = COALESCE(EXCLUDED.posted_date, jobs.posted_date),
    apply_url     = EXCLUDED.apply_url,
    raw_payload   = EXCLUDED.raw_payload,
    last_seen_at  = NOW(),
    still_open    = TRUE,
    reopened_at   = CASE WHEN jobs.still_open = FALSE THEN NOW() ELSE jobs.reopened_at END,
    reopen_count  = jobs.reopen_count + CASE WHEN jobs.still_open = FALSE THEN 1 ELSE 0 END
RETURNING (xmax = 0) AS inserted, (reopened_at = NOW()) AS reopened
"""

_MARK_CLOSED_SQL = """
UPDATE jobs
SET still_open = FALSE,
    closed_at  = NOW()
WHERE company = %s
  AND still_open = TRUE
  AND native_job_id <> ALL(%s)
"""

_LOG_RUN_SQL = """
INSERT INTO scraper_runs (
    company, status, jobs_found, new_jobs, error_message, duration_ms
)
VALUES (%s, %s, %s, %s, %s, %s)
RETURNING id
"""


def get_connection() -> psycopg.Connection:
    # `prepare_threshold=None` disables psycopg3's automatic prepared
    # statements. Required for the Supabase Transaction pooler (port 6543):
    # pgbouncer multiplexes client sessions over a smaller real-Postgres
    # connection pool, so a prepared-statement name set up by one client
    # collides with the next client that lands on the same underlying
    # connection (psycopg.errors.DuplicatePreparedStatement). For ~tens
    # of upserts per run the cost of skipping prepare is negligible.
    return psycopg.connect(DATABASE_URL, prepare_threshold=None)


def init_db() -> None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA)


def persist_run_results(
    company: str,
    jobs: list[dict],
    *,
    duration_ms: int,
) -> dict:
    """Atomically: upsert each job, mark missing as closed, log the run.

    `jobs` is a list of dicts with keys:
        native_job_id, title, apply_url   (required)
        description, location, category, posted_date, raw_payload  (optional)

    `raw_payload` may be a dict (preferred) or None. Anything else is treated
    as NULL.

    Returns:
        {
          "new_jobs":      list[dict],  # subset of `jobs` that didn't exist before
          "reopened_jobs": list[dict],  # existing rows that were closed and came back
          "updated_count": int,         # len(jobs) - len(new_jobs) - len(reopened_jobs)
          "closed_count":  int,         # rows flipped from open → closed this run
          "run_id":        int,         # scraper_runs.id
        }

    A reopened job is an existing row whose `still_open` was FALSE and is now
    re-found (a genuine repost). It is NOT counted in `new_jobs`, so the email
    alert path is unaffected — reopens surface on the dashboard only.

    Safety: if `jobs` is empty, no jobs are marked closed (a single empty scrape
    would otherwise nuke every open row for that company).
    """
    new_jobs: list[dict] = []
    reopened_jobs: list[dict] = []
    seen_ids: list[str] = [j["native_job_id"] for j in jobs]

    with get_connection() as conn, conn.cursor() as cur:
        for job in jobs:
            raw = job.get("raw_payload")
            cur.execute(_UPSERT_SQL, (
                company,
                job["native_job_id"],
                job["title"],
                job.get("description"),
                job.get("location"),
                job.get("category"),
                job.get("posted_date"),
                job["apply_url"],
                Jsonb(raw) if isinstance(raw, dict) else None,
            ))
            inserted, reopened = cur.fetchone()
            if inserted:
                new_jobs.append(job)
            elif reopened:
                reopened_jobs.append(job)

        if seen_ids:
            cur.execute(_MARK_CLOSED_SQL, (company, seen_ids))
            closed_count = cur.rowcount
        else:
            closed_count = 0

        cur.execute(_LOG_RUN_SQL, (
            company, "success", len(jobs), len(new_jobs), None, duration_ms,
        ))
        run_id = cur.fetchone()[0]

    return {
        "new_jobs": new_jobs,
        "reopened_jobs": reopened_jobs,
        "updated_count": len(jobs) - len(new_jobs) - len(reopened_jobs),
        "closed_count": closed_count,
        "run_id": run_id,
    }


def log_failed_run(company: str, error_message: str, duration_ms: int) -> int:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(_LOG_RUN_SQL, (
            company, "failed", 0, 0, error_message, duration_ms,
        ))
        return cur.fetchone()[0]


def _describe_tables() -> list[tuple[str, str, str, str]]:
    query = """
        SELECT table_name, column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name IN ('jobs', 'scraper_runs')
        ORDER BY table_name, ordinal_position
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(query)
        return cur.fetchall()


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    host = os.environ["SUPABASE_POOLER_HOST"]
    print(f"Connecting to {host}:6543 …", flush=True)
    init_db()
    print("Schema applied.\n")

    current = None
    for tbl, col, dtype, nullable in _describe_tables():
        if tbl != current:
            print(f"\n  {tbl}")
            print("  " + "-" * len(tbl))
            current = tbl
        suffix = "" if nullable == "NO" else "  (nullable)"
        print(f"    {col:18s} {dtype}{suffix}")
