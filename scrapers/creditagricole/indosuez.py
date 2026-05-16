"""Indosuez Wealth Management scraper — Talentsoft `jobs.ca-indosuez.com`.

Indosuez is the wealth-management brand of CA, present in 16 territories
(France, Luxembourg, Switzerland, Monaco, Portugal, Asia-Pacific, ME, …).
~68 vacancies. Uses the lowercase /pages/offre/listeoffre.aspx path (note
the different casing from CACIB).

Scope: tech roles only, France-located. The country filter will drop the
Luxembourg/Switzerland/Monaco majority — that's intentional.

See _talentsoft.scrape().
"""
from __future__ import annotations

import sys

from . import _talentsoft

CFG = _talentsoft.TenantConfig(
    company="Indosuez Wealth Management",
    base="https://jobs.ca-indosuez.com",
    listing_path="/pages/offre/listeoffre.aspx",
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
