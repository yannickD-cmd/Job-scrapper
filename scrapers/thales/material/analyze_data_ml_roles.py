"""One-shot analysis: which Phenom category buckets actually hold the
Thales France data / AI / ML / research roles? Reuses thales.py helpers
to fetch every detail page, classifies each France / Permanent job by
title keywords, then prints per-category counts + the matching titles
so we can pick a real scope from data instead of guessing.

Run from repo root:
    .venv/Scripts/python.exe scrapers/thales/material/analyze_data_ml_roles.py
"""
from __future__ import annotations

import random
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Make `scrapers.thales.thales` importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import requests

from scrapers.thales.thales import (
    HEADERS,
    JITTER_MAX,
    JITTER_MIN,
    MAX_WORKERS,
    REQUEST_TIMEOUT,
    SCOPE_COUNTRY,
    SCOPE_WORKER_SUBTYPE,
    _collect_job_urls,
    _extract_phapp_ddo,
    _pick_category,
)


# Keywords that signal a data / AI / ML / research / FDE-style role.
# FR + EN. `\b` boundaries avoid false positives like "available" matching "ai".
DATA_KW = re.compile(
    r"\b("
    r"data\s*(scientist|engineer|analyst|architect|manager|ops|engineering|science)|"
    r"machine\s*learning|deep\s*learning|ml\s*(engineer|ops)|mlops|"
    r"artificial\s*intelligence|"
    r"nlp|natural\s*language|computer\s*vision|"
    r"signal\s*process(ing)?|image\s*process(ing)?|"
    r"big\s*data|databricks|airflow|spark|kafka|hadoop|"
    r"research\s*scientist|research\s*engineer|"
    r"forward\s*deployed|customer\s*engineer|solutions?\s*engineer|"
    r"chercheur|scientifique|"
    r"ing[ée]nieur\s*(data|ia|machine\s*learning|ml|recherche)|"
    r"intelligence\s*artificielle|apprentissage\s*automatique|"
    r"traitement\s*(du\s*)?signal|traitement\s*(d'?)?image|"
    r"vision\s*par\s*ordinateur"
    r")\b",
    re.IGNORECASE,
)

# Standalone tokens that need word-boundary matching but can't go in the
# big alternation without polluting it with too many `\b`.
TOKEN_KW = re.compile(r"\b(ai|ml|cv)\b", re.IGNORECASE)


def matches_data_ml(title: str, teaser: str) -> bool:
    """Title is the primary signal; teaser used as a fallback so a role
    titled 'Ingénieur F/H' with 'machine learning' in the teaser still
    surfaces.
    """
    if DATA_KW.search(title) or TOKEN_KW.search(title):
        return True
    if DATA_KW.search(teaser):
        return True
    return False


def fetch_and_classify(url: str) -> dict | None:
    """Fetch detail page, return classified row or None.

    None means: failed / not France / not Permanent. We only keep rows
    that pass the country + permanent filter — the analysis is about
    *which categories* the in-scope roles fall into.
    """
    time.sleep(random.uniform(JITTER_MIN, JITTER_MAX))
    session = requests.Session()
    try:
        response = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if response.status_code == 404:
            return None
        response.raise_for_status()
    except Exception:
        return None

    ddo = _extract_phapp_ddo(response.text)
    if not ddo:
        return None
    job = ddo.get("jobDetail", {}).get("data", {}).get("job")
    if not isinstance(job, dict):
        return None
    if job.get("country") != SCOPE_COUNTRY:
        return None
    if job.get("workerSubType") != SCOPE_WORKER_SUBTYPE:
        return None

    title = job.get("title") or ""
    teaser = job.get("descriptionTeaser") or ""
    return {
        "url": url,
        "title": title,
        "category": _pick_category(job.get("multi_category")) or "(unknown)",
        "city": job.get("city") or "",
        "state": job.get("state") or "",
        "matches": matches_data_ml(title, teaser),
        "teaser": teaser[:200],
    }


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    session = requests.Session()
    session.headers.update(HEADERS)

    print("Pulling sitemap...", flush=True)
    urls = _collect_job_urls(session)
    print(f"  → {len(urls)} URLs to classify\n", flush=True)

    print(f"Fetching detail pages with {MAX_WORKERS} workers...", flush=True)
    started = time.time()
    rows: list[dict] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_and_classify, u): u for u in urls}
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            if result is not None:
                rows.append(result)
            if i % 250 == 0:
                kept = len(rows)
                print(
                    f"  ...{i}/{len(urls)} fetched, {kept} France/Permanent kept, "
                    f"{time.time() - started:.0f}s",
                    flush=True,
                )

    elapsed = time.time() - started
    matches = [r for r in rows if r["matches"]]

    print("\n" + "=" * 60)
    print("ANALYSIS — Thales France · Permanent")
    print("=" * 60)
    print(f"  total France/Permanent jobs : {len(rows)}")
    print(f"  data/AI/ML keyword matches  : {len(matches)}")
    print(f"  fetch runtime               : {elapsed:.0f}s")

    print("\n--- Category distribution (ALL France/Permanent) ---")
    cat_total = Counter(r["category"] for r in rows)
    for c, n in cat_total.most_common():
        print(f"  {n:4d}  {c}")

    print("\n--- Category distribution (TITLES matching data/AI/ML/research) ---")
    cat_match = Counter(r["category"] for r in matches)
    for c, n in cat_match.most_common():
        denom = cat_total[c] or 1
        ratio = f"{100 * n / denom:.0f}%"
        print(f"  {n:4d}  ({ratio:>4} of category) {c}")

    print("\n--- Matching titles, grouped by category ---")
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in matches:
        by_cat[r["category"]].append(r)
    for cat in sorted(by_cat.keys(), key=lambda c: -len(by_cat[c])):
        print(f"\n  ### {cat} ({len(by_cat[cat])} matches) ###")
        for r in sorted(by_cat[cat], key=lambda r: r["city"]):
            loc = r["city"] or r["state"] or "?"
            print(f"    [{loc}] {r['title']}")

    # Persist the full classified set so we can re-analyse without
    # re-fetching the 3569 pages.
    out_path = Path(__file__).parent / "analyze_data_ml_roles_output.tsv"
    with out_path.open("w", encoding="utf-8") as f:
        f.write("matches\tcategory\tcity\tstate\ttitle\turl\n")
        for r in rows:
            f.write(
                f"{int(r['matches'])}\t{r['category']}\t{r['city']}\t"
                f"{r['state']}\t{r['title']}\t{r['url']}\n"
            )
    print(f"\nFull classified rows written to: {out_path.name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
