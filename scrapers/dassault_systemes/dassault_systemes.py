"""Dassault Systèmes (3DS) job scraper — France, broad Data/AI scope.

The public careers page at https://www.3ds.com/careers/jobs is a Nuxt SPA
backed by 3DS's in-house Exalead search platform. The page calls:

  GET https://www.3ds.com/apisearch/card_search_api?q=...&b=<offset>&hf=<page_size>&output_format=json

with the Exalead-style `q` string built from the brick config exposed at
`/contents/brick/jobs_listing/<uuid>` — in particular `field_special_query`
which constrains results to `card_content_type="career"`.

Each hit's `metas` is a flat list of `{name, value}` pairs. Names of interest:
  - card_id                  : stable Exalead card id (our native_job_id)
  - content_title            : posting title
  - content_summary          : HTML description
  - content_info_1_value     : employment type ("Internship" / "Permanent" / "Apprenticeship" / ...)
  - content_info_2_value     : location string ("Country, State, City")
  - content_start_datetime   : "YYYY/MM/DD HH:MM:SS"
  - content_type_display_text: 3DS category ("Research & Development", "Information Technology", ...)
  - content_cta_1_url        : public detail page (also the apply landing)
  - content_funnel           : same as card_id, kept as `identifier`

Plus repeated `meta_cat` rows for `Category/<x>`, `Country/<x>`, `City/<x>`,
`Type/<x>`, `Products/<x>`, `Year/<x>` — that's how we country-filter.

3DS has ~390 unique active postings worldwide (~170 in France). The Exalead
result set occasionally repeats the same card_id across pages, so we
deduplicate after fetching every page.

Filter (broad Data/AI incl. SWE adjacent, per user scope):
  - CORE_CATEGORIES kept entirely for France (R&D and IT cover all SWE/data/AI roles)
  - Other categories (Services, Industry, Strategy, …) only if the title
    matches AI_KEYWORDS_RE

To widen scope, edit SCOPE_COUNTRY, CORE_CATEGORIES, or AI_KEYWORDS_RE.
"""
from __future__ import annotations

import html
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests

BASE_URL = "https://www.3ds.com/apisearch/card_search_api"
PAGE_SIZE = 100
MAX_PAGES = 20  # safety cap; real total ≈ 5 pages of 100

LANG_CODE = "en"
SPECIAL_QUERY = 'card_content_type="career"'
SORT_KEY = "score_with_card_update_timestamp"

SCOPE_COUNTRY = "France"

# Categories whose every France posting we keep — R&D and IT are where 3DS
# places all software, data, AI, ML, infra, security and platform roles.
CORE_CATEGORIES: set[str] = {
    "Research & Development",
    "Information Technology",
}

# Other categories sometimes carry data/AI-adjacent roles (e.g. Services has
# "Consultant Logiciel Services, Sciences de données et IA") — keep those
# only when the title matches.
AI_KEYWORDS_RE = re.compile(
    r"\b("
    r"AI|IA|ML|MLOps|NLP|LLM|LLMs|GenAI"
    r"|Machine\s+Learning|Deep\s+Learning|Generative\s+AI|Foundation\s+Models?"
    r"|Data\s+(?:Scientist|Engineer|Analyst|Architect|Science|Engineering|Analytics)"
    r"|Données|Donnees"
    r"|Intelligence\s+Artificielle"
    r"|Applied\s+Scientist|Research\s+Scientist"
    r"|Analytics|Analytique"
    r"|Sciences?\s+de\s+données|Sciences?\s+de\s+donnees"
    r")\b",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.3ds.com/careers/jobs",
    "From": "yannickarieldossa@gmail.com",
}

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.0


@dataclass
class Job:
    native_job_id: str         # 3DS Exalead card_id (integer string)
    title: str
    location: str              # "Country, State, City" as exposed by content_info_2_value
    category: str | None       # 3DS category (Research & Development, Information Technology, …)
    apply_url: str             # public detail page (which contains the Apply CTA)
    employment_type: str       # raw content_info_1_value (Internship / Permanent / Apprenticeship / …)
    description: str | None = None
    posted_date: str | None = None    # YYYY-MM-DD
    identifier: str | None = None     # content_funnel (same as card_id for 3DS)
    raw_payload: dict | None = None


def _build_query(offset: int, page_size: int) -> str:
    """Reproduce the formatQuery() string the page JS builds.

    Translated 1:1 from /core/CPkP2NEG.js (function s). With no keyword and
    no facets, the result is:

        q=#all <langCode-clause> (<special>) &s=desc(<sort>)&b=<offset>&hf=<page_size>&output_format=json
    """
    lang_clause = f"card_content_lang:{LANG_CODE} "
    special_clause = f" ({SPECIAL_QUERY}) " if SPECIAL_QUERY else " "
    inner = f"{lang_clause}{special_clause}"
    # mirror the encoder's URL-encoding behaviour: only the inside of q is encoded
    from urllib.parse import quote
    return (
        f"q=%23all%20{quote(inner)}"
        f"&s=desc({SORT_KEY})"
        f"&b={offset}"
        f"&hf={page_size}"
        f"&output_format=json"
    )


def _hit_to_dict(hit: dict) -> dict:
    """Flatten the metas list into a dict; collapse repeated meta_cat into a list."""
    out: dict = {"meta_cat": []}
    for m in hit.get("metas") or []:
        name = m.get("name")
        value = m.get("value")
        if name == "meta_cat":
            if isinstance(value, str):
                out["meta_cat"].append(value)
        elif name:
            out[name] = value
    return out


def _country_of(card: dict) -> str | None:
    for c in card.get("meta_cat") or []:
        if isinstance(c, str) and c.startswith("Country/"):
            return c[len("Country/"):]
    return None


def _category_of(card: dict) -> str | None:
    """Prefer the explicit `content_type_display_text`; fall back to meta_cat `Category/`."""
    cat = card.get("content_type_display_text")
    if cat:
        return cat
    for c in card.get("meta_cat") or []:
        if isinstance(c, str) and c.startswith("Category/"):
            return c[len("Category/"):]
    return None


def _in_scope(card: dict) -> bool:
    if _country_of(card) != SCOPE_COUNTRY:
        return False
    cat = _category_of(card) or ""
    if cat in CORE_CATEGORIES:
        return True
    title = card.get("content_title") or ""
    return bool(AI_KEYWORDS_RE.search(title))


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")


def _html_to_text(s: str | None) -> str | None:
    if not s:
        return None
    # turn block boundaries into line breaks before stripping tags
    s = re.sub(r"</p\s*>", "\n\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</li\s*>", "\n", s, flags=re.IGNORECASE)
    s = _TAG_RE.sub("", s)
    s = html.unescape(s)
    s = _WS_RE.sub(" ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip() or None


def _parse_posted(s: str | None) -> str | None:
    """3DS uses 'YYYY/MM/DD HH:MM:SS' — take the date, convert slashes to dashes."""
    if not s or len(s) < 10:
        return None
    head = s[:10].replace("/", "-")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", head):
        return head
    return None


def _card_to_job(card: dict) -> Job:
    card_id = str(card.get("card_id") or "").strip()
    if not card_id:
        raise RuntimeError(
            f"3DS hit missing card_id (title={card.get('content_title')!r})"
        )

    apply_url = card.get("content_cta_1_url") or card.get("content_cta_1_url_id") or ""

    return Job(
        native_job_id=card_id,
        title=(card.get("content_title") or "").strip(),
        location=(card.get("content_info_2_value") or "").strip(),
        category=_category_of(card),
        apply_url=apply_url,
        employment_type=(card.get("content_info_1_value") or "").strip(),
        description=_html_to_text(card.get("content_summary")),
        posted_date=_parse_posted(card.get("content_start_datetime")),
        identifier=str(card.get("content_funnel") or card_id),
        raw_payload=card,
    )


def _fetch_page(session: requests.Session, offset: int) -> tuple[list[dict], int]:
    url = f"{BASE_URL}?{_build_query(offset, PAGE_SIZE)}"
    print(f"  fetching b={offset} hf={PAGE_SIZE}...", flush=True)
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    hits = payload.get("hits") or []
    nhits = int(payload.get("nhits") or 0)
    print(f"    {len(hits)} hits (nhits={nhits})", flush=True)
    return hits, nhits


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Listing phase...", flush=True)

    all_cards: dict[str, dict] = {}
    offset = 0
    nhits = 0
    for _ in range(MAX_PAGES):
        hits, nhits = _fetch_page(session, offset)
        if not hits:
            break
        for h in hits:
            card = _hit_to_dict(h)
            cid = str(card.get("card_id") or "")
            if cid and cid not in all_cards:
                all_cards[cid] = card
        offset += PAGE_SIZE
        if offset >= nhits:
            break
        time.sleep(REQUEST_DELAY_SECONDS)

    print(
        f"  collected {len(all_cards)} unique postings (nhits={nhits})",
        flush=True,
    )

    print(
        f"Filter phase: country={SCOPE_COUNTRY!r}, "
        f"core={sorted(CORE_CATEGORIES)}, title-fallback=AI/Data keywords...",
        flush=True,
    )
    kept: dict[str, Job] = {}
    for card in all_cards.values():
        if not _in_scope(card):
            continue
        job = _card_to_job(card)
        if job.native_job_id in kept:
            continue
        kept[job.native_job_id] = job
        print(
            f"  {job.native_job_id} [{job.category}] {job.title!r} -> KEEP",
            flush=True,
        )

    elapsed = time.time() - started
    print(flush=True)
    print(f"  -> {len(kept)} jobs in {elapsed:.1f}s\n", flush=True)
    return [asdict(j) for j in kept.values()]


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    started = time.time()
    try:
        jobs = scrape()
    except Exception as exc:
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise

    elapsed = time.time() - started
    print(f"=== {len(jobs)} jobs final (total runtime {elapsed:.1f}s) ===\n")

    for j in jobs:
        desc = (j["description"] or "")[:200]
        desc = desc + ("..." if len(j["description"] or "") > 200 else "")
        print(f"[{j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
