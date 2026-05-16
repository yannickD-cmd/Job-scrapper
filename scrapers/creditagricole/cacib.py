"""CACIB scraper — Talentsoft tenant `jobs.ca-cib.com`.

Crédit Agricole Corporate & Investment Bank (CACIB) is the CIB arm of CA, with
~30 worldwide offices. The Talentsoft tenant lives at a slightly different
listing path than the asset-management/retail tenants:
  /Pages/Offre/listeoffre.aspx   (capital P, no `-emploi`)
LCID=2057 (English) gives the broadest titles; LCID=1036 returns French-only
content so we stick with French here to match the rest of the group.

Scope: tech roles only, France-located. See _talentsoft.scrape().
"""
from __future__ import annotations

import sys

from . import _talentsoft

CFG = _talentsoft.TenantConfig(
    company="Crédit Agricole CIB",
    base="https://jobs.ca-cib.com",
    listing_path="/Pages/Offre/listeoffre.aspx",
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
