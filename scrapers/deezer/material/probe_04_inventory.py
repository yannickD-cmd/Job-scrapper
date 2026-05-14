"""Probe 4: list every job from the saved listing with title + category.

Probe 3 confirmed Deezer doesn't expose a structured contract-type field
(no JSON-LD, no jcnt attribute, no badge). The site classifies every
Paris job as "full_time", and the only signal that distinguishes
Permanent vs Internship vs Apprenticeship lives in the job TITLE
("Intern", "Apprentice", "Stage", "Alternance", etc.).

This probe inventories all listings so we can:
- Confirm category is reliably present in <div class="jobdef">
- See which titles look like internships/apprenticeships
- Decide on the exclusion regex used by the scraper
- Confirm cat-XXXXXX class is consistent with the visible category text
"""
from __future__ import annotations

import collections
import pathlib
import re
import sys

from bs4 import BeautifulSoup

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

HTML_FILE = pathlib.Path(__file__).parent / "landing.html"

NON_PERMANENT_PATTERN = re.compile(
    r"\b(intern|internship|apprentice|apprenticeship|stage|stagiaire|alternance|alternant)\b",
    re.IGNORECASE,
)


def main() -> None:
    html = HTML_FILE.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    cards = soup.select("div.openJobs div.lst > a")
    print(f"Total cards: {len(cards)}\n")

    by_category: dict[str, list[tuple[str, str, str]]] = collections.defaultdict(list)
    cat_class_to_text: dict[str, set[str]] = collections.defaultdict(set)

    for a in cards:
        href = a.get("href", "")
        m = re.search(r"jid=(\d+)", href)
        jid = m.group(1) if m else "?"
        title_el = a.find("h3")
        loc_el = a.select_one(".jobloc")
        cat_el = a.select_one(".jobdef")
        title = title_el.get_text(" ", strip=True) if title_el else ""
        location = loc_el.get_text(" ", strip=True) if loc_el else ""
        category = cat_el.get_text(" ", strip=True) if cat_el else ""
        cat_classes = [c for c in (a.get("class") or []) if c.startswith("cat-")]
        cat_class = cat_classes[0] if cat_classes else ""

        by_category[category].append((jid, title, location))
        if cat_class:
            cat_class_to_text[cat_class].add(category)

    print("=== cat-XXX class -> visible category text ===")
    for cls, names in sorted(cat_class_to_text.items()):
        print(f"  {cls}: {sorted(names)}")

    print("\n=== Listings by category ===")
    for cat in sorted(by_category):
        rows = by_category[cat]
        print(f"\n--- {cat} ({len(rows)}) ---")
        for jid, title, location in rows:
            flag = " [NON-PERMANENT?]" if NON_PERMANENT_PATTERN.search(title) else ""
            print(f"  jid={jid}  {title!r}  ({location}){flag}")

    # Focus on Product & Tech.
    pt = by_category.get("Product & Tech", [])
    print(f"\n=== Product & Tech filter result: {len(pt)} total ===")
    kept = [(jid, t, l) for jid, t, l in pt if not NON_PERMANENT_PATTERN.search(t)]
    dropped = [(jid, t, l) for jid, t, l in pt if NON_PERMANENT_PATTERN.search(t)]
    print(f"  permanent (kept) : {len(kept)}")
    print(f"  non-permanent     : {len(dropped)}")
    for jid, t, _ in dropped:
        print(f"    DROP jid={jid}  {t!r}")


if __name__ == "__main__":
    main()
