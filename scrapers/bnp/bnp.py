"""BNP Paribas job scraper — scaffold.

Source: https://group.bnpparibas/en/careers

Not implemented yet. Site structure (listing format, pagination, JSON
endpoints, category/sub-category taxonomy) still to be probed; raw
samples and exploration notes will land in ./material/ as we go.

The final shape must match the contract every scraper in this repo
follows:

    scrape() -> list[dict]

with each dict containing at minimum:
    native_job_id  (str)   stable per-job ID from BNP's own system
    title          (str)
    apply_url      (str)

and optionally:
    description, location, category, posted_date,
    identifier, raw_payload   (see db.py / alerts.py for usage)
"""
from __future__ import annotations


def scrape() -> list[dict]:
    raise NotImplementedError(
        "BNP Paribas scraper not implemented yet. "
        "Probe https://group.bnpparibas/en/careers first."
    )
