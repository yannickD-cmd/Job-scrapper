"""Groupe iliad (France) job scraper — Free, Free Pro, Stancer, Opcore, Itrust.

All of iliad's French entities publish through ONE SmartRecruiters tenant:

    https://api.smartrecruiters.com/v1/companies/Iliad-Free/postings

Found by probing, not guessing: `recrutement.iliad.fr` 302s to a Beekome career
site (`iliad_career.beekome.com`, a Nuxt SPA). Beekome is only a front-end — its
own API (`api.beekome.com/public/companies/{id}/career-sites/{id}/jobs`) returns
rows stamped `jobSourceType: "ATS_SR"` whose `directApplyUrl` points straight at
`jobs.smartrecruiters.com/Iliad-Free/...`. So we skip the proxy and hit the ATS
of record: it pages 100 at a time instead of Beekome's 10, and carries the
`customField` facets Beekome flattens away. Beekome probe artifacts are kept in
`material/` for reference — do not go back to that layer.

Scaleway is NOT here: it is the one iliad entity on its own ATS (Lever), scraped
by `scrapers/iliad/scaleway.py`. iliad italia and Play Polska are out of scope
(France-only, locked with the user).

Entity coverage on this tenant (`customField` "Brands", 224 postings today):
Iliad - Free 178, Free Pro 40, Stancer 4, Itrust 1, OPCORE 1.

Scope
-----
France (the board is 220/224 France + 4 DOM-TOM), all contract types — CDI 197,
CDD 20, Freelance 5, Alternance 1, Stage 1 — because alternance/stage are wanted
(`feedback_include_data_adjacent_ai_roles`).

The category axis is the **`Métiers` customField**, not `department` and not
SmartRecruiters' standard `function`. All three disagree, and only Métiers is
consistently right:

  * `department` collapses entity and métier together ("Free Pro" is a
    department AND a brand), and files only 3 roles under Réseaux & Telecom
    where Métiers files 17.
  * `function` is entered by the recruiter and is plainly wrong on this board:
    "Marketing Produit Télécom" and "Marketing Produit Services Managés" are both
    filed `function: Engineering`, while "Technicien Support Systèmes ou Réseaux"
    is filed `Administrative`.

  KEEP wholesale   Tech & Digital (42) — Data Scientist, Développeur Go, Python
                   Backend, Network DevOps, Cloud RAN, Ops Engineer. Uniformly
                   technical, including its Junior/Alternance rows.
  BLOCK outright   Boutique (87, retail shop sales) and Relation Abonné (22,
                   "Conseiller Free" call-centre). Blocked before the content
                   gate: a Conseiller JD does mention "box", "fibre", "réseau".
  CONTENT gate     Réseaux & Telecom (17), Free Pro (14), Fonctions Centrales
                   (40) and anything uncategorised — all genuinely mixed.

**Why a content gate and never a title gate here:** Free Pro's board is full of
"Ingénieur Commercial" — French for *account executive*. A title filter keeps
every one of them because they say "Ingénieur", while dropping nothing. The same
bucket holds a real "Référent Technique SRE / Cloud Linux" and an "Ingénieur
Cybersécurité". Only the description separates them.

Field/NOC technician roles are excluded by explicit scope decision (consistent
with the datacentre-technician exclusions at Microsoft and Google) — see
FIELD_OPS_TITLE_RE. That list is a *scope* exclusion, not a relevance heuristic:
it is applied before every other gate and nothing rescues it.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass

import requests

TENANT = "Iliad-Free"
API_ROOT = "https://api.smartrecruiters.com/v1/companies"
POSTINGS_URL = f"{API_ROOT}/{TENANT}/postings"
DETAIL_URL = f"{API_ROOT}/{TENANT}/postings/{{posting_id}}"

METIER_FIELD = "Métiers"          # note: the fieldLabel carries a trailing space
BRAND_FIELD = "Brands"
CONTRACT_FIELD = "Type de contrat"
REGION_FIELD = "Région"

# --- gate 1: the Métiers facet ----------------------------------------------
METIER_KEEP: frozenset[str] = frozenset({
    "Tech & Digital",
})
METIER_BLOCK: frozenset[str] = frozenset({
    "Boutique",           # 87 — retail shop sales
    "Relation Abonné",    # 22 — "Conseiller Free" call-centre
})
# Everything else (Réseaux & Telecom, Free Pro, Fonctions Centrales, None) falls
# through to the content gate.

# --- gate 0: explicit scope exclusion, applied first -------------------------
# Hands-on fibre-rollout and NOC shift roles, scoped out by the user in line with
# the Microsoft/Google datacentre-technician decision. Deliberately narrow: it
# names the four field roles this board actually carries rather than matching
# "technicien" broadly, so a desk-based "Technicien Support Systèmes ou Réseaux"
# still reaches the content gate on its own merits.
FIELD_OPS_TITLE_RE = re.compile(
    r"technicien\s+(?:d[ée]ploiement|supervision|itin[ée]rant)"
    r"|supervision\s+nuit",
    re.IGNORECASE,
)

# --- gate 2: content signal, read off the FULL description -------------------
# French + English. A bare "ingénieur" is absent on purpose: on this board it
# reads "Ingénieur Commercial" far more often than it reads engineer.
# PRACTITIONER terms only — never the products iliad sells. Two traps cost a
# smoke-test round here and both are French-specific:
#
#   * `d[ée]veloppeu?r` also matches the VERB "développer". Every sales ad on
#     this board says "développer le chiffre d'affaires" / "développer votre
#     portefeuille", so it kept door-to-door salespeople and Chefs de secteur.
#     The noun form must be pinned: "développeur"/"développeuse".
#   * "cybersécurité" and "cloud" are PRODUCTS Free Pro sells, so they appear
#     throughout its commercial ads ("Commercial Sédentaire", "Gestionnaire Back
#     Office"). Only practitioner vocabulary (SOC, SIEM, pentest) discriminates.
_STRONG_PATTERNS: tuple[str, ...] = (
    r"\bd[ée]veloppeu(?:r|se)\b", r"software (?:engineer|develop)",
    r"d[ée]veloppement logiciel", r"ing[ée]nieur logiciel",
    r"data scien(?:ce|tist)", r"data engineer", r"ing[ée]nieur donn[ée]es",
    r"machine learning", r"deep learning", r"\bmlops\b", r"\bnlp\b",
    # NOT `\bllms?\b`: on a French board "LLM" is the **Master of Laws** degree
    # far more often than a language model — it kept a "Juriste M&A" (a lawyer)
    # in the smoke test. "large language model" spelled out is unambiguous.
    r"large language model",
    r"\bdevops\b", r"\bsre\b", r"site reliability", r"\bkubernetes\b",
    r"\bterraform\b", r"\bansible\b", r"ci/cd", r"infrastructure as code",
    r"micro-?services?", r"\bpentest", r"\bsiem\b", r"analyste soc\b",
    r"architecte (?:cloud|logiciel|syst[èe]me|r[ée]seau|technique|"
    r"int[ée]gration|donn[ée]es)",
    r"administrateur (?:syst[èe]me|r[ée]seau|base)", r"\bsysadmin\b",
    r"network automation", r"automatisation r[ée]seau", r"\bnetdevops\b",
    r"c(?:œ|oe)ur de r[ée]seau", r"\btoip\b", r"\bvoip\b", r"rest api",
)
# Concrete tools/languages. A technical JD names several; a commercial one that
# happens to mention "cloud" names none. Generic words ("production",
# "architecture", "cloud", "supervision", "API", "agile") are excluded on
# purpose — they are what made the first pass keep back-office roles.
_SUPPORTING_PATTERNS: tuple[str, ...] = (
    r"\bpython\b", r"\bgolang\b", r"\bjava\b", r"\bc\+\+\b", r"\bc#\b",
    r"\bphp\b", r"\bjavascript\b", r"\btypescript\b", r"\bsql\b", r"\bnosql\b",
    r"\blinux\b", r"\bunix\b", r"\bdocker\b", r"\bgit\b", r"\bansible\b",
    r"\bspark\b", r"\bkafka\b", r"\bgrafana\b", r"\bprometheus\b",
    r"\bpostgres", r"\bmysql\b", r"\bredis\b", r"\belasticsearch\b",
    r"\bkibana\b", r"\bjenkins\b", r"\bgitlab\b", r"\bbash\b",
    r"\balgorithm", r"\bscripting\b", r"\bcodebase\b",
    # SUPPORTING, not STRONG: plenty of non-technical ads here say they "use AI
    # tools" — on its own it kept a forensic accountant and an HR-systems MOA.
    # A real AI role always names something else as well (python, ML, data).
    r"intelligence artificielle", r"\bia g[ée]n[ée]rative\b",
)
_STRONG = re.compile("|".join(_STRONG_PATTERNS), re.I)
_SUPPORTING = re.compile("|".join(_SUPPORTING_PATTERNS), re.I)
MIN_SUPPORTING = 2

HEADERS = {
    "User-Agent": (
        "Job-scrapper/1.0 (+https://github.com/yannickD-cmd; "
        "yannickarieldossa@gmail.com) python-requests"
    ),
    "Accept": "application/json",
}

PAGE_SIZE = 100                 # SmartRecruiters public API max
MAX_PAGES = 40                  # defensive: 4k rows vs 224 today
MAX_DETAIL_FETCHES = 400        # defensive: ~115 non-blocked today
REQUEST_DELAY_SECONDS = 1.0     # JSON API
REQUEST_TIMEOUT = 45
MAX_ATTEMPTS = 4
RETRY_BACKOFF_SECONDS = 5

_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class Job:
    native_job_id: str
    title: str
    apply_url: str
    description: str | None = None
    location: str | None = None
    category: str | None = None
    posted_date: str | None = None
    employment_type: str | None = None
    identifier: str | None = None
    raw_payload: dict | None = None


def _request(session: requests.Session, url: str, params: dict | None = None) -> dict:
    """GET with linear-backoff retry. Fails closed after MAX_ATTEMPTS."""
    last: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if response.status_code == 429 or response.status_code >= 500:
                raise requests.HTTPError(f"HTTP {response.status_code}",
                                         response=response)
            response.raise_for_status()
            return response.json() or {}
        except (requests.Timeout, requests.ConnectionError,
                requests.HTTPError, ValueError) as exc:
            last = exc
            if attempt == MAX_ATTEMPTS:
                break
            wait = RETRY_BACKOFF_SECONDS * attempt
            print(f"    {type(exc).__name__}: {exc} — retry {attempt}/"
                  f"{MAX_ATTEMPTS - 1} in {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"iliad: {url} failed after {MAX_ATTEMPTS} attempts: {last}")


def _custom_field(posting: dict, label: str) -> str | None:
    """Read a customField by label. Labels carry stray trailing spaces upstream."""
    target = label.strip().casefold()
    for field in posting.get("customField") or []:
        if (field.get("fieldLabel") or "").strip().casefold() == target:
            return field.get("valueLabel")
    return None


def _plain_text(html: str | None) -> str:
    if not html:
        return ""
    text = _TAG_RE.sub(" ", html)
    return re.sub(r"\s+", " ", text).strip()


def _crawl(session: requests.Session) -> list[dict]:
    """Page the whole tenant. Returns every posting."""
    postings: list[dict] = []
    advertised: int | None = None

    print(f"Crawl phase: SmartRecruiters {TENANT} ({PAGE_SIZE}/page)...", flush=True)
    for page in range(MAX_PAGES):
        data = _request(session, POSTINGS_URL,
                        {"limit": PAGE_SIZE, "offset": page * PAGE_SIZE})
        content = data.get("content") or []
        if advertised is None:
            advertised = data.get("totalFound")
            print(f"  totalFound: {advertised}", flush=True)
        postings.extend(content)
        if not content or len(postings) >= (advertised or 0):
            break
        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"  -> {len(postings)} postings", flush=True)
    # A pagination bug loses whole pages. Aborting is mandatory: a partial board
    # is non-empty, so it slips past the empty-guard in db.persist_run_results
    # and retires every row that fell past the cut.
    if advertised and len(postings) < advertised:
        raise RuntimeError(
            f"iliad: crawled {len(postings)} of {advertised} advertised postings "
            f"— aborting rather than persisting a partial board."
        )
    print(flush=True)
    return postings


def _fetch_detail(session: requests.Session, posting_id: str) -> tuple[str, str]:
    """(full JD, canonical apply URL).

    SmartRecruiters splits the JD across four jobAd sections. The listing's
    `ref` is the *API* URL, not a human one — the candidate-facing link is
    `postingUrl` on the detail payload.
    """
    detail = _request(session, DETAIL_URL.format(posting_id=posting_id))
    sections = ((detail.get("jobAd") or {}).get("sections") or {})
    parts: list[str] = []
    for key in ("jobDescription", "qualifications",
                "additionalInformation", "companyDescription"):
        section = sections.get(key) or {}
        text = _plain_text(section.get("text"))
        if text:
            parts.append(f"{section.get('title') or key}\n{text}")

    # The slug-less form resolves (HTTP 200) and is stable if the slug changes.
    apply_url = (detail.get("postingUrl")
                 or f"https://jobs.smartrecruiters.com/{TENANT}/{posting_id}")
    return "\n\n".join(parts), apply_url


def _content_signals(text: str) -> tuple[list[str], list[str]]:
    strong = sorted({m.group(0).lower() for m in _STRONG.finditer(text)})
    supporting = sorted({m.group(0).lower() for m in _SUPPORTING.finditer(text)})
    return strong, supporting


def _is_in_scope(metier: str | None, title: str, body: str) -> tuple[bool, str]:
    """Scope exclusion, then Métier, then mission. The title decides nothing else."""
    if FIELD_OPS_TITLE_RE.search(title):
        return False, "excluded: field/NOC technician role"
    if metier in METIER_BLOCK:
        return False, f"blocked métier: {metier}"
    if metier in METIER_KEEP:
        return True, f"métier: {metier}"

    strong, supporting = _content_signals(body)
    if strong:
        return True, f"content:strong={','.join(strong[:4])}"
    if len(supporting) >= MIN_SUPPORTING:
        return True, f"content:supporting={','.join(supporting[:4])}"
    return False, f"off-scope métier ({metier or 'none'}), no content signal"


def _location(posting: dict) -> str:
    loc = posting.get("location") or {}
    bits = [loc.get("city"), loc.get("region"), (loc.get("country") or "").upper()]
    return ", ".join(b for b in bits if b)


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    postings = _crawl(session)
    if not postings:
        return []

    # Blocked métiers never need a detail call — that is 109 of 224 saved.
    candidates = [
        p for p in postings
        if _custom_field(p, METIER_FIELD) not in METIER_BLOCK
        and not FIELD_OPS_TITLE_RE.search(p.get("name") or "")
    ]
    print(f"Pre-filter: {len(candidates)} of {len(postings)} postings need a "
          f"detail fetch (~{int(len(candidates) * REQUEST_DELAY_SECONDS)}s)\n",
          flush=True)
    if len(candidates) > MAX_DETAIL_FETCHES:
        raise RuntimeError(
            f"iliad: {len(candidates)} detail fetches exceeds the "
            f"{MAX_DETAIL_FETCHES} cap — refusing to hammer the API."
        )

    kept: list[Job] = []
    dropped = 0
    failed = 0

    for i, posting in enumerate(candidates, 1):
        posting_id = str(posting.get("id"))
        title = posting.get("name") or ""
        metier = _custom_field(posting, METIER_FIELD)

        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            body, apply_url = _fetch_detail(session, posting_id)
        except Exception as exc:
            print(f"  [{i}/{len(candidates)}] {posting_id} FAILED: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            failed += 1
            continue

        in_scope, reason = _is_in_scope(metier, title, body)
        if not in_scope:
            dropped += 1
            print(f"  [{metier}] {title[:52]!r} -> drop ({reason})", flush=True)
            continue

        strong, supporting = _content_signals(body)
        brand = _custom_field(posting, BRAND_FIELD)
        contract = _custom_field(posting, CONTRACT_FIELD)
        kept.append(Job(
            native_job_id=posting_id,
            title=title,
            apply_url=apply_url,
            description=body or None,
            location=_location(posting),
            # Entity first, then the scope axis — both matter when reading a row.
            category=" / ".join(x for x in (brand, metier) if x) or None,
            posted_date=(posting.get("releasedDate") or "")[:10] or None,
            employment_type=contract or (posting.get("typeOfEmployment") or {}).get("label"),
            identifier=posting.get("refNumber"),
            raw_payload={
                "posting_id": posting_id,
                "ref_number": posting.get("refNumber"),
                "uuid": posting.get("uuid"),
                "brand": brand,
                "metier": metier,
                "department": (posting.get("department") or {}).get("label"),
                "sr_function": (posting.get("function") or {}).get("label"),
                "contract_type": contract,
                "type_of_employment": (posting.get("typeOfEmployment") or {}).get("label"),
                "experience_level": (posting.get("experienceLevel") or {}).get("label"),
                "region": _custom_field(posting, REGION_FIELD),
                "location": posting.get("location"),
                "released_date": posting.get("releasedDate"),
                "scope_reason": reason,
                "content_strong": strong,
                "content_supporting": supporting,
            },
        ))
        print(f"  [{metier}] {title[:52]!r} -> KEEP ({reason})", flush=True)

    if candidates and failed == len(candidates):
        raise RuntimeError(
            f"iliad: 0 of {len(candidates)} detail fetches succeeded — aborting "
            f"to avoid false-closing DB rows."
        )

    print(flush=True)
    print("Gate: Métiers facet (Tech & Digital wholesale; Boutique / Relation "
          "Abonné blocked) OR technical mission in the full JD. Field/NOC "
          "technician roles excluded by scope.", flush=True)
    print(f"  board          : {len(postings)}", flush=True)
    print(f"  detail-fetched : {len(candidates)}", flush=True)
    print(f"  kept           : {len(kept)}", flush=True)
    print(f"  off-scope      : {dropped}", flush=True)
    print(f"  failed         : {failed}", flush=True)

    return [asdict(j) for j in kept]


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    started = time.time()
    try:
        jobs = scrape()
    except Exception as exc:
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise

    print(f"\n=== {len(jobs)} jobs final "
          f"(total runtime {time.time() - started:.1f}s) ===\n")

    for j in jobs:
        desc = (j["description"] or "").strip().replace("\n", " ")
        desc = desc[:180] + ("…" if len(desc) > 180 else "")
        raw = j["raw_payload"]
        print(f"[{j['identifier'] or j['native_job_id']}] {j['title']}")
        print(f"  Entity     : {raw['brand']}   Métier: {raw['metier']}")
        print(f"  Contract   : {j['employment_type']}   Level: {raw['experience_level']}")
        print(f"  Location   : {j['location']}")
        print(f"  Posted     : {j['posted_date']}")
        print(f"  Why        : {raw['scope_reason']}")
        print(f"  Apply      : {j['apply_url']}")
        print(f"  Description: {desc}")
        print()
