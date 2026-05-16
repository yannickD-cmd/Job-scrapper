"""CACEIS scraper — Talentsoft tenant `jobs.caceis.com`.

CACEIS is the asset-servicing arm of CA Group (custody, fund administration,
depositary banking). Talentsoft tenant uses the same listing path as Amundi /
LCL: /offre-de-emploi/liste-offres.aspx.

Scope: tech roles only, France-located. See _talentsoft.scrape().
"""
from __future__ import annotations

import sys

from . import _talentsoft

CFG = _talentsoft.TenantConfig(
    company="CACEIS",
    base="https://jobs.caceis.com",
    listing_path="/offre-de-emploi/liste-offres.aspx",
    lcid=1036,
    scope_country="France",
)


def scrape() -> list[dict]:
    return _talentsoft.scrape(CFG)


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    jobs = scrape()
    print(f"\n=== {len(jobs)} jobs final ===\n")
    for j in jobs[:20]:
        print(f"[{j['native_job_id']}] {j['title']} ({j['location']})")
