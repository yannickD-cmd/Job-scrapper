"""Probe 15: tally every city that appears across the 211 France jobs.

The user wants to narrow Accenture France to Paris + Île-de-France only.
The API doesn't surface region as a per-row field (regionDescription
came back blank for all rows in probe_14), so we have to whitelist
cities. This probe lists every distinct city in our scope's listing,
plus how many jobs land in each, plus a per-city KEEP-set hit so we
can size the impact on the 59 we currently retain.
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys
import time
from collections import Counter

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

HOST = "https://www.accenture.com"
SEARCH = HOST + "/fr-fr/careers/jobsearch"
API = HOST + "/api/accenture/jobsearch/result"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Referer": SEARCH,
    "From": "yannickarieldossa@gmail.com",
}

# Île-de-France: the 8 departments (75, 77, 78, 91, 92, 93, 94, 95) + Paris.
# We compare lowercased; case- and accent-insensitive would be better but
# the data is consistent in casing so a simple lowered set suffices.
IDF_HINTS = {
    "paris",
    "boulogne-billancourt", "saint-denis", "argenteuil", "montreuil",
    "nanterre", "vitry-sur-seine", "créteil", "versailles", "aubervilliers",
    "asnières-sur-seine", "colombes", "aulnay-sous-bois", "rueil-malmaison",
    "champigny-sur-marne", "saint-maur-des-fossés", "drancy",
    "issy-les-moulineaux", "levallois-perret", "noisy-le-grand", "antony",
    "neuilly-sur-seine", "sarcelles", "saint-ouen", "cergy", "pantin",
    "évry-courcouronnes", "evry-courcouronnes", "evry", "maisons-alfort",
    "bobigny", "meaux", "fontenay-sous-bois", "bondy", "vincennes",
    "clamart", "châtenay-malabry", "suresnes", "bagneux", "massy",
    "clichy", "courbevoie", "puteaux", "la défense", "la defense",
    "ivry-sur-seine", "stains", "athis-mons", "houilles", "sartrouville",
    "rosny-sous-bois", "le kremlin-bicêtre", "kremlin-bicetre",
    "gennevilliers", "garges-lès-gonesse", "savigny-sur-orge",
    "alfortville", "pontault-combault", "saint-germain-en-laye",
    "saint-cloud", "sannois", "le perreux-sur-marne", "vélizy-villacoublay",
    "velizy-villacoublay", "chelles", "torcy", "noisiel", "yerres",
    "lognes", "rambouillet", "vélizy", "velizy", "trappes", "élancourt",
    "elancourt", "guyancourt", "montigny-le-bretonneux",
}

SKILLS_IN_SCOPE = {
    "Software Engineering", "AI & Data", "Security", "Engineering & Networks",
}


def parse_city_list(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if x]
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            v = ast.literal_eval(s)
            if isinstance(v, list):
                return [str(x).strip() for x in v if x]
            return [str(v).strip()]
        except (ValueError, SyntaxError):
            return [s]
    return [str(raw)]


def fetch_all(s: requests.Session) -> list[dict]:
    all_by_id: dict[str, dict] = {}
    total = None
    for page_idx in range(20):
        raw_start = page_idx * 50
        start = raw_start if total is None else raw_start % max(total, 1)
        fields = {
            "startIndex": str(start),
            "maxResultSize": "50",
            "jobKeyword": "",
            "jobLanguage": "fr-fr",
            "countrySite": "fr-fr",
            "jobCountry": "France",
            "jobFilters": "[]",
            "aggregations": "[]",
            "sortBy": "0",
            "componentId": "",
        }
        r = s.post(
            API,
            headers={**HEADERS, "Accept": "*/*", "X-Requested-With": "XMLHttpRequest",
                     "Origin": HOST},
            files={k: (None, v) for k, v in fields.items()},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if total is None:
            total = int(data.get("total") or 0)
        for row in data.get("data") or []:
            jid = row.get("jobId")
            if jid and jid not in all_by_id:
                all_by_id[jid] = row
        print(f"  page {page_idx+1}: start={start}, cumulative={len(all_by_id)}/{total}")
        if total is not None and len(all_by_id) >= total:
            break
        time.sleep(1.5)
    return list(all_by_id.values())


def main() -> None:
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get(SEARCH, timeout=30)
    time.sleep(1.5)

    print("Fetching all France rows...")
    rows = fetch_all(s)
    print(f"\nTotal collected: {len(rows)}\n")

    # Count cities across all rows, and within our kept set
    all_cities: Counter = Counter()
    kept_cities: Counter = Counter()
    cities_in_idf: dict[str, bool] = {}

    for row in rows:
        cities = parse_city_list(row.get("jobCityState"))
        for c in cities:
            all_cities[c] += 1
            cities_in_idf[c] = c.lower() in IDF_HINTS

        # Is this row in our kept scope (Full-time + skill in set)?
        if row.get("employeeType") != "Full-time":
            continue
        if (row.get("skill") or "") not in SKILLS_IN_SCOPE:
            continue
        for c in cities:
            kept_cities[c] += 1

    print("=== ALL CITIES (across 211 jobs) ===")
    print(f"  ({sum(1 for c in all_cities if cities_in_idf.get(c))} "
          f"IDF / {len(all_cities)} total distinct cities)")
    for city, n in all_cities.most_common():
        flag = "IDF" if cities_in_idf.get(city) else "   "
        print(f"  {n:4d}  [{flag}]  {city!r}")

    print("\n=== CITIES IN OUR KEPT SCOPE (Full-time + Tech skills) ===")
    print(f"  total kept jobs: {sum(kept_cities.values())} (city-mentions; "
          f"jobs span multiple cities)")
    for city, n in kept_cities.most_common():
        flag = "IDF" if cities_in_idf.get(city) else "   "
        print(f"  {n:4d}  [{flag}]  {city!r}")

    # Estimate: how many kept jobs are in IDF if we restrict to IDF cities?
    idf_only_kept = 0
    non_idf_kept = 0
    mixed_kept = 0
    for row in rows:
        if row.get("employeeType") != "Full-time":
            continue
        if (row.get("skill") or "") not in SKILLS_IN_SCOPE:
            continue
        cities = parse_city_list(row.get("jobCityState"))
        if not cities:
            continue
        idf_hit = any(c.lower() in IDF_HINTS for c in cities)
        non_idf_hit = any(c.lower() not in IDF_HINTS for c in cities)
        if idf_hit and not non_idf_hit:
            idf_only_kept += 1
        elif idf_hit and non_idf_hit:
            mixed_kept += 1
        else:
            non_idf_kept += 1

    print("\n=== SCOPE PROJECTION ===")
    print(f"  IDF-only kept jobs        : {idf_only_kept}")
    print(f"  mixed (IDF + other) kept  : {mixed_kept}")
    print(f"  non-IDF kept jobs         : {non_idf_kept}")
    print(f"  ─────")
    print(f"  total kept (current)      : {idf_only_kept + mixed_kept + non_idf_kept}")
    print(f"  kept if IDF-required      : {idf_only_kept + mixed_kept}")
    print(f"  kept if IDF-only          : {idf_only_kept}")


if __name__ == "__main__":
    main()
