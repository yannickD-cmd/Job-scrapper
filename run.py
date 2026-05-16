"""Run one or more company scrapers and persist results to Supabase.

Usage:
    python run.py              # runs every scraper in COMPANY_NAMES
    python run.py sanofi       # runs just Sanofi
    python run.py sanofi bnp   # runs Sanofi and BNP

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
    "orange": "Orange",
    "deezer": "Deezer",
    "adobe": "Adobe",
    "thales": "Thales",
    "sg": "Société Générale",
    "databricks": "Databricks",
    "decathlon": "Decathlon Digital",
    "capgemini": "Capgemini",
    "voodoo": "Voodoo",
    "rothschild": "Rothschild & Co",
    "doctolib": "Doctolib",
    "cgi": "CGI",
    "allianz": "Allianz",
    "ibm": "IBM",
    "totalenergies": "TotalEnergies",
    "visa": "Visa",
    "dassault.systemes": "Dassault Systèmes",
    "dassault.aviation": "Dassault Aviation",
    "creditagricole.carecrute": "Crédit Agricole Recrute",
    "creditagricole.amundi": "Amundi",
    "creditagricole.lcl": "LCL",
    "creditagricole.cacib": "Crédit Agricole CIB",
    "creditagricole.caceis": "CACEIS",
    "creditagricole.indosuez": "Indosuez Wealth Management",
    "creditagricole.sofinco": "Crédit Agricole Personal Finance & Mobility",
    "creditagricole.bforbank": "BforBank",
    "creditagricole.assurances": "Crédit Agricole Assurances",
    "orano": "Orano",
    "soprasteria": "Sopra Steria",
}


def run_one(key: str) -> int:
    company = COMPANY_NAMES.get(key, key.title())

    try:
        module = importlib.import_module(f"scrapers.{key}")
    except ModuleNotFoundError:
        print(f"No scraper module: scrapers/{key}.py", file=sys.stderr)
        return 2

    print(f">>> Scraping {company}...")
    started = time.time()
    try:
        jobs = module.scrape()
    except Exception as exc:
        duration_ms = int((time.time() - started) * 1000)
        db.log_failed_run(company, f"{type(exc).__name__}: {exc}", duration_ms)
        print(f"\nFAILED ({company}): {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one or more scrapers and persist to Supabase.")
    parser.add_argument(
        "scrapers",
        nargs="*",
        help="Scraper module names under scrapers/ (e.g. sanofi bnp). Empty = run all.",
    )
    args = parser.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    keys = [s.lower() for s in args.scrapers] if args.scrapers else list(COMPANY_NAMES.keys())

    db.init_db()

    exit_code = 0
    for key in keys:
        rc = run_one(key)
        if rc != 0:
            exit_code = rc
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
