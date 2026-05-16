"""Crédit Agricole Assurances scraper (Predica, Pacifica, Spirica, La Médicale).

CA Assurances is Europe's leading bancassurer and France's #1 insurer. The
group brings together several insurance brands (Predica = life, Pacifica =
non-life, Spirica = life via brokers/IFAs, La Médicale = health-care
professionals). All of them post to the central groupecreditagricole.jobs
portal under the `credit-agricole-assurances` brand slug.

Scope: tech roles only, France-located. See _groupeca.scrape().
"""
from __future__ import annotations

import sys

from . import _groupeca

CFG = _groupeca.BrandConfig(
    company="Crédit Agricole Assurances",
    brand_slug="credit-agricole-assurances",
    entity_match="Assurances",
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
