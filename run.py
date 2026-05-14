"""Run a single company scraper and persist results to Supabase.

Usage:
    python run.py sanofi

Each scraper module under `scrapers/` must expose a `scrape() -> list[dict]`
returning rows compatible with `db.persist_run_results`.

The DB layer dedupes via UNIQUE (company, native_job_id) + ON CONFLICT UPDATE,
so reruns are idempotent: existing rows have last_seen_at refreshed and any
fields updated; only genuinely new (company, native_job_id) pairs come back
in the `new_jobs` list.
"""
from __future__ import annotations

import argparse
import importlib
import sys
import time

import alerts
import db


COMPANY_NAMES = {
    "sanofi": "Sanofi",
    "bnp": "BNP Paribas",
    "loreal": "L'Oréal",
    "accenture": "Accenture",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a scraper and persist to Supabase.")
    parser.add_argument("scraper", help="Scraper module name under scrapers/ (e.g. sanofi)")
    args = parser.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    key = args.scraper.lower()
    company = COMPANY_NAMES.get(key, key.title())

    try:
        module = importlib.import_module(f"scrapers.{key}")
    except ModuleNotFoundError:
        print(f"No scraper module: scrapers/{key}.py", file=sys.stderr)
        return 2

    db.init_db()

    started = time.time()
    try:
        jobs = module.scrape()
    except Exception as exc:
        duration_ms = int((time.time() - started) * 1000)
        db.log_failed_run(company, f"{type(exc).__name__}: {exc}", duration_ms)
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise

    duration_ms = int((time.time() - started) * 1000)
    summary = db.persist_run_results(company, jobs, duration_ms=duration_ms)

    new_jobs = summary["new_jobs"]
    print()
    print(f"=== Persisted to Supabase ({company}) ===")
    print(f"  total scraped : {len(jobs)}")
    print(f"  new           : {len(new_jobs)}")
    print(f"  updated       : {summary['updated_count']}")
    print(f"  closed        : {summary['closed_count']}")
    print(f"  run_id        : {summary['run_id']}")

    if new_jobs:
        print()
        print("New jobs:")
        for j in new_jobs:
            print(f"  - [{j.get('identifier') or j['native_job_id']}] "
                  f"{j['title']} ({j.get('location') or 'n/a'})")
            print(f"      {j['apply_url']}")

        print()
        alerts.send_new_jobs_email(company, new_jobs)

    return 0


if __name__ == "__main__":
    sys.exit(main())
