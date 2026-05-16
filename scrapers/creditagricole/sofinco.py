"""Sofinco / CA Personal Finance & Mobility scraper.

Sofinco is the consumer-credit commercial brand of Crédit Agricole Consumer
Finance, recently rebranded "Crédit Agricole Personal Finance & Mobility"
(CACF). Their own ATS (cacf.talentview.io) is a Talentview SPA whose JSON
API is undocumented and tenant-scoped; the public groupecreditagricole.jobs
listing under the new brand slug is server-rendered HTML with full per-card
metadata, so we scrape there instead.

Scope: tech roles only, France-located. See _groupeca.scrape().
"""
from __future__ import annotations

import sys

from . import _groupeca

CFG = _groupeca.BrandConfig(
    company="Crédit Agricole Personal Finance & Mobility (Sofinco)",
    brand_slug="credit-agricole-personal-finance-mobility",
    entity_match="Personal Finance",
)


def scrape() -> list[dict]:
    return _groupeca.scrape(CFG)


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    jobs = scrape()
    print(f"\n=== {len(jobs)} jobs final ===\n")
    for j in jobs[:20]:
        print(f"[{j['native_job_id']}] {j['title']} ({j['location']})")
