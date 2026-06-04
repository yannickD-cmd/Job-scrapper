"""Wavestone job scraper — Paris office, permanent (CDI), Data & AI / tech only.

Wavestone's public careers board at https://www.wavestone.com/en/careers/our-offers/
is a thin front-end over the SmartRecruiters ATS (tenant `Wavestone1`). The board's
filter chips (`?contract-type=permanent&city=paris`) map one-to-one onto SmartRecruiters
postings + their `customField[]` facets, so we go straight to the documented public API:

  listing : https://api.smartrecruiters.com/v1/companies/Wavestone1/postings?limit=100&offset=N
  detail  : https://api.smartrecruiters.com/v1/companies/Wavestone1/postings/{id}

The listing endpoint returns ~256 postings across every geography/contract type, each
carrying the facets the website filters on inside `customField[]`:

  "Office"                     -> "Paris" / "Lyon" / "London" / ...   (the city= filter)
  "Permanents / internships"   -> "Permanent" / "Internship"          (the contract-type= filter)
  "Practice / Function"        -> "Artificial Intelligence" / "CTO Advisory" / ...

`Office == "Paris"` AND `Permanents/internships == "Permanent"` reproduces the website's
"105 results match your filters" exactly, so we trust those two facets rather than
`location.city` (which is the postal commune, e.g. "Puteaux") or `typeOfEmployment`
(which encodes Full-time/Part-time, not CDI/CDD).

SCOPE (locked with the user):
  - Geography      : Paris office only.
  - Contract       : Permanent (CDI) only.
  - Families        : "Data & AI / tech". Data/AI/tech roles are scattered across practices,
                      not a single facet, so we keep:
                        * whole families  -> Artificial Intelligence, CTO Advisory, Wivoo
                          (CTO Advisory = Wavestone's technology/IT-advisory practice; user
                           chose to keep it wholesale incl. its IT-strategy/sourcing roles)
                        * cross-family catch -> any title matching a Data/AI/tech keyword,
                          which pulls the data strays out of Financial Services / Digital
                          Customer / Public Sector / Transportation.
                      Cybersecurity is explicitly EXCLUDED (separate security practice).

The listing payload has no description, so we make one detail call per kept job to pull
the HTML job-ad sections (and the canonical postingUrl). To widen scope, edit
OFFICES_IN_SCOPE / CONTRACTS_IN_SCOPE / KEEP_FAMILIES / TITLE_KEYWORDS below.
"""
from __future__ import annotations

import html
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests

COMPANY_ID = "Wavestone1"
API_BASE = f"https://api.smartrecruiters.com/v1/companies/{COMPANY_ID}/postings"
# Public posting page; the ATS resolves the bare-id form to the full slug URL.
PUBLIC_URL_TEMPLATE = f"https://jobs.smartrecruiters.com/{COMPANY_ID}/{{job_id}}"

# --- scope predicate -------------------------------------------------------
OFFICES_IN_SCOPE = {"Paris"}
CONTRACTS_IN_SCOPE = {"Permanent"}
# Families kept in full (every posting in them is in scope).
KEEP_FAMILIES = {"Artificial Intelligence", "CTO Advisory", "Wivoo"}
# Families never kept, even on a keyword hit.
EXCLUDE_FAMILIES = {"Cybersecurity"}
# Cross-family catch: keep a posting in any other family if its title matches.
# NB: deliberately omits bare "developer" — it false-matches "Business Developer"
# (a sales role); the families above already keep their software-dev postings.
TITLE_KEYWORDS = re.compile(
    r"\b(data|donn[ée]es|IA|AI|ML|machine learning|genai|llm|llmops|agentic|"
    r"analytics|analytique|cloud|devops|sre|software|architect|architecte|"
    r"plateforme|platform)\b",
    re.IGNORECASE,
)

# SmartRecruiters customField labels (stable per tenant).
CF_OFFICE = "Office"
CF_CONTRACT = "Permanents / internships"
CF_FUNCTION = "Practice / Function"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; personal-job-tracker/0.1; "
        "contact yannickarieldossa@gmail.com)"
    ),
    "Accept": "application/json",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

PAGE_SIZE = 100
MAX_PAGES = 20            # defensive cap: 256 postings / 100 = 3 pages today
REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.0


@dataclass
class Job:
    native_job_id: str              # SmartRecruiters posting id (stable numeric string)
    title: str
    location: str                   # location.fullLocation, e.g. "Puteaux, IDF, France"
    category: str | None            # Practice / Function facet, e.g. "Artificial Intelligence"
    apply_url: str                  # canonical jobs.smartrecruiters.com posting page
    employment_type: str            # "Permanent" (the CDI/internship facet)
    description: str | None = None
    posted_date: str | None = None  # releasedDate, normalised to YYYY-MM-DD
    identifier: str | None = None   # refNumber, e.g. "REF196H"
    raw_payload: dict | None = None  # listing posting doc, for forensics


def _custom_field(doc: dict, label: str) -> str | None:
    for c in doc.get("customField") or []:
        if c.get("fieldLabel") == label:
            return c.get("valueLabel")
    return None


def _in_scope(doc: dict) -> bool:
    if _custom_field(doc, CF_OFFICE) not in OFFICES_IN_SCOPE:
        return False
    if _custom_field(doc, CF_CONTRACT) not in CONTRACTS_IN_SCOPE:
        return False
    family = _custom_field(doc, CF_FUNCTION)
    if family in EXCLUDE_FAMILIES:
        return False
    if family in KEEP_FAMILIES:
        return True
    return bool(TITLE_KEYWORDS.search(doc.get("name") or ""))


def _normalise_date(released: str | None) -> str | None:
    if isinstance(released, str) and len(released) >= 10:
        return released[:10]
    return None


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]*\n[ \t]*")


def _html_to_text(fragment: str | None) -> str:
    if not fragment:
        return ""
    # Turn block boundaries into newlines before stripping tags so the text
    # keeps paragraph/list structure.
    text = re.sub(r"(?i)</(p|div|li|ul|ol|h[1-6])>", "\n", fragment)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _WS_RE.sub("\n", text)
    return text.strip()


def _build_description(detail: dict) -> str | None:
    sections = (detail.get("jobAd") or {}).get("sections") or {}
    parts: list[str] = []
    # Skip companyDescription — it is identical boilerplate on every posting.
    for key in ("jobDescription", "qualifications", "additionalInformation"):
        sec = sections.get(key)
        if isinstance(sec, dict):
            body = _html_to_text(sec.get("text"))
            if body:
                title = (sec.get("title") or "").strip()
                parts.append(f"## {title}\n{body}" if title else body)
    joined = "\n\n".join(parts)
    return joined or None


def _fetch_listing(session: requests.Session) -> list[dict]:
    docs: list[dict] = []
    for page in range(MAX_PAGES):
        offset = page * PAGE_SIZE
        resp = session.get(
            API_BASE,
            params={"limit": PAGE_SIZE, "offset": offset},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        content = payload.get("content") or []
        total = payload.get("totalFound", 0)
        docs.extend(content)
        print(
            f"  page {page} offset={offset}: +{len(content)} "
            f"(running {len(docs)}/{total})",
            flush=True,
        )
        if not content or len(docs) >= total:
            break
        time.sleep(REQUEST_DELAY_SECONDS)
    else:
        print(f"  WARNING: hit MAX_PAGES={MAX_PAGES} cap", flush=True)
    return docs


def _fetch_detail(session: requests.Session, job_id: str) -> dict | None:
    resp = session.get(f"{API_BASE}/{job_id}", timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _doc_to_job(doc: dict, detail: dict | None) -> Job:
    job_id = str(doc.get("id") or "").strip()
    if not job_id:
        raise RuntimeError(f"posting missing id (title={doc.get('name')!r})")

    location = doc.get("location") or {}
    apply_url = PUBLIC_URL_TEMPLATE.format(job_id=job_id)
    description = None
    if detail:
        apply_url = detail.get("postingUrl") or apply_url
        description = _build_description(detail)

    return Job(
        native_job_id=job_id,
        title=(doc.get("name") or "").strip(),
        location=(location.get("fullLocation") or location.get("city") or "").strip(),
        category=_custom_field(doc, CF_FUNCTION),
        apply_url=apply_url,
        employment_type=_custom_field(doc, CF_CONTRACT) or "",
        description=description,
        posted_date=_normalise_date(doc.get("releasedDate")),
        identifier=(doc.get("refNumber") or None),
        raw_payload=doc,
    )


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    started = time.time()
    print("Listing phase...", flush=True)
    docs = _fetch_listing(session)

    print("Filter phase...", flush=True)
    candidates = [d for d in docs if _in_scope(d)]
    print(
        f"  kept {len(candidates)} (dropped {len(docs) - len(candidates)} out-of-scope)",
        flush=True,
    )

    print(
        f"Enrichment phase: fetching {len(candidates)} detail pages "
        f"(~{int(len(candidates) * REQUEST_DELAY_SECONDS / 60) + 1} min)...",
        flush=True,
    )

    kept: dict[str, Job] = {}
    failed = 0
    for i, doc in enumerate(candidates, 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        job_id = str(doc.get("id") or "")
        try:
            detail = _fetch_detail(session, job_id)
        except Exception as exc:
            # Description/apply_url enrichment is non-essential — keep the row.
            print(
                f"  [{i}/{len(candidates)}] {job_id} detail fetch FAILED: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            detail = None
            failed += 1

        job = _doc_to_job(doc, detail)
        if job.native_job_id in kept:
            continue
        kept[job.native_job_id] = job
        print(
            f"  [{i}/{len(candidates)}] {job.native_job_id} {job.title!r} -> KEEP",
            flush=True,
        )

    elapsed = time.time() - started
    print(
        f"\n  -> {len(kept)} jobs in {elapsed:.1f}s "
        f"({failed} detail fetches failed)\n",
        flush=True,
    )
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
        desc = (j["description"] or "")
        desc = desc[:200] + ("..." if len(desc) > 200 else "")
        print(f"[{j['native_job_id']}] {j['title']}")
        print(f"  Category   : {j['category']}")
        print(f"  Type       : {j['employment_type']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Ref        : {j['identifier']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
