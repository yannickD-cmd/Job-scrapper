"""LCL scraper — Talentsoft tenant `offres-emploi.lcl.com`.

LCL (Le Crédit Lyonnais, ex Crédit Lyonnais) is the urban retail-bank arm of
Crédit Agricole Group. ~450 offers in their Talentsoft tenant; the bulk are
branch-network roles (Conseiller / Directeur d'agence) which the tech-keyword
filter strips out before we burn detail fetches.

Scope: tech roles only, France-located. See _talentsoft.scrape().
"""
from __future__ import annotations

import sys

from . import _talentsoft

CFG = _talentsoft.TenantConfig(
    company="LCL",
    base="https://offres-emploi.lcl.com",
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
