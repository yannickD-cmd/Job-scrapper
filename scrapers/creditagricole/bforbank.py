"""BforBank scraper — Crédit Agricole's online-only bank.

BforBank's own careers landing point at `bforbank.com/nous-rejoindre` redirects
candidates to Welcome to the Jungle, which sits behind Cloudflare bot mitigation
that blocks server-side fetches. The same offers are mirrored on
`groupecreditagricole.jobs/fr/nos-marques/bforbank/nos-offres/`, server-rendered
with all per-card metadata — so we scrape there.

Scope: tech roles only, France-located. See _groupeca.scrape().
"""
from __future__ import annotations

import sys

from . import _groupeca

CFG = _groupeca.BrandConfig(
    company="BforBank",
    brand_slug="bforbank",
    entity_match="BforBank",
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
