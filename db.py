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
    UNIQUE (company, native_job_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_company    ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_jobs_still_open ON jobs(still_open);

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
_UPSERT_SQL = """
INSERT INTO jobs (
    company, native_job_id, title, description,
    location, category, posted_date, apply_url, raw_payload,
    last_seen_at, still_open
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), TRUE)
ON CONFLICT (company, native_job_id) DO UPDATE SET
    title         = EXCLUDED.title,
    description   = EXCLUDED.description,
    location      = EXCLUDED.location,
    category      = EXCLUDED.category,
    posted_date   = EXCLUDED.posted_date,
    apply_url     = EXCLUDED.apply_url,
    raw_payload   = EXCLUDED.raw_payload,
    last_seen_at  = NOW(),
    still_open    = TRUE
RETURNING (xmax = 0) AS inserted
"""

_MARK_CLOSED_SQL = """
UPDATE jobs
SET still_open = FALSE
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
          "updated_count": int,         # len(jobs) - len(new_jobs)
          "closed_count":  int,         # rows flipped from open → closed this run
          "run_id":        int,         # scraper_runs.id
        }

    Safety: if `jobs` is empty, no jobs are marked closed (a single empty scrape
    would otherwise nuke every open row for that company).
    """
    new_jobs: list[dict] = []
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
            inserted = cur.fetchone()[0]
            if inserted:
                new_jobs.append(job)

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
        "updated_count": len(jobs) - len(new_jobs),
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
