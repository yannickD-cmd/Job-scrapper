"""Phase 3 - best-effort link of `applications.job_id` to the scraped `jobs` row.

Run by hand, not from the dashboard and not from CI:

    python -m tracker.link_jobs            # dry run, prints what it would link
    python -m tracker.link_jobs --apply    # writes applications.job_id

Why best effort. `applications` was seeded from a Gmail sweep, so it carries
what the confirmation mails carried: a company name as the recruiter spells it,
a deburred role title, and an ATS reference about a third of the time.
`apply_url` is NULL on every seeded row, so there is no url to join on. Only
about half the companies applied to have a scraper at all (Alstom, Pennylane,
papernest, Goldman Sachs and friends were never scraped). A NULL job_id is the
normal outcome, not a failure - it only means the detail panel shows no
scraped description for that application.

Three tiers, each linking only when the candidate set holds exactly ONE row. A
wrong link is worse than no link: it would render another job's description
under the application, which is the single thing this panel must never do.

  1. req_ref == jobs.native_job_id (normalised)
     The strong signal. ATS acknowledgement mails quote the requisition id
     verbatim and it is also the scraper's dedup key, so an equality here is
     the same requisition by construction. Scoped to the aliased company when
     one is known; otherwise global, and then only if unique across the whole
     table and long enough not to collide by accident.

  2. company alias + normalised title equality.

  3. company alias + token overlap >= _FUZZY_MIN on the title, accepted only if
     the best candidate beats the runner-up. This is what catches the
     application's "Junior Data Scientist / AI Engineer" against Airbus's
     "Junior Data Scientist / AI engineer (m/w/d) in the field of ...".

Anything ambiguous is reported as SKIP with its candidates and left NULL.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402

# Applications store the company as the recruiter writes it; `jobs` stores the
# display name from run.COMPANY_NAMES. Only unambiguous same-legal-entity or
# same-board pairs belong here.
#
# Deliberately NOT aliased: the three Credit Agricole subsidiaries applied to
# (CAMCA, CA Business Digital, CA Technologies et Services) each run their own
# Talentsoft tenant, and the scraped "Credit Agricole Recrute" board is a
# different surface - see project_creditagricole_ats_map. Mapping them would
# invent links. SPIRICA is aliased because a req_ref hit proved the pairing.
COMPANY_ALIASES: dict[str, str] = {
    "amazon": "Amazon / AWS",
    "axa france": "AXA",
    "bcg x": "BCG",
    "deloitte": "Deloitte France",
    "iliad free": "Groupe iliad",
    "mckinsey quantumblack": "McKinsey & Company",
    "publicis sapient": "Publicis Groupe",
    "sopra steria next": "Sopra Steria",
    "spirica groupe credit agricole": "Credit Agricole Assurances",
    "ubisoft paris": "Ubisoft",
    "vinci airports": "VINCI",
}

# A req_ref matched globally (no company alias) must be distinctive enough that
# equality is not a coincidence. "12" would collide, "JR10433316" will not.
# Shorter refs are only honoured inside a known company's pool.
_MIN_GLOBAL_REF_LEN = 5

# Token overlap needed in tier 3. High on purpose: lower, and the three Ubisoft
# "Machine Learning Engineer" applications start pulling in unrelated rows.
_FUZZY_MIN = 0.72

# Stripped from a title before comparing: gender markers, and the
# "(2e candidature)" bookkeeping the seed added to tell re-applications to the
# same requisition apart.
#
# Only that bookkeeping, not every parenthetical. Stripping all of them costs
# more than it pays: "(Anthropic/Claude)" and "(GCP)" are exactly the tokens
# that separate one Sopra Steria or IBM opening from the next, and removing
# them turns confident matches into ties.
_PAREN_RE = re.compile(
    r"\(\s*\d+\s*(?:re|e|eme|ème)\s+candidature\s*\)", re.IGNORECASE
)
_GENDER_RE = re.compile(
    r"\b(?:h\s*f|f\s*h|m\s*f|f\s*m|m\s*w\s*d|w\s*m\s*d|h\s*f\s*n|f\s*m\s*x)\b"
)
# Words carrying no discriminating power between two openings at one company,
# so they must not inflate the overlap score.
_STOPWORDS = frozenset({
    "de", "des", "du", "la", "le", "les", "et", "en", "a", "au", "aux",
    "d", "l", "the", "of", "and", "for", "cdi", "cdd", "stage", "alternance",
})


def _deburr(value: str | None) -> str:
    """Lowercase, strip accents, collapse the rest to single spaces.

    Applications were seeded from mail text and are pure ASCII ("Ingenieur IA",
    "Societe Generale") while `jobs` keeps the upstream accents. Both sides go
    through this so "L'Oreal" and "L'Oreal" with an accent compare equal.
    """
    decomposed = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", stripped.lower()).strip()


def _norm_ref(value: str | None) -> str:
    """Requisition ids compare on alphanumerics only.

    The same req appears as "2026-131466" in a mail and "2026131466" in an API
    payload, or "JR-053956" against "JR053956".
    """
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _norm_title(value: str | None) -> str:
    # Parentheticals go first, while the brackets still exist: _deburr flattens
    # every non-alphanumeric to a space, so running _PAREN_RE after it would
    # never match anything and the "(2e candidature)" bookkeeping would survive
    # as two extra tokens, dragging every fuzzy score down.
    text = _PAREN_RE.sub(" ", value or "")
    text = _deburr(text)
    text = _GENDER_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: str | None) -> frozenset[str]:
    return frozenset(
        t for t in _norm_title(value).split() if t and t not in _STOPWORDS
    )


def _score(applied: frozenset[str], posting: frozenset[str]) -> tuple[float, int]:
    """(coverage, -extra tokens) for one posting against one application.

    Coverage is directional on purpose: the share of the APPLICATION's tokens
    the posting accounts for. Airbus posts "Junior Data Scientist / AI engineer
    (m/w/d) in the field of Flight Physics" for an application recorded as
    "Junior Data Scientist / AI Engineer" — full coverage, and plain Jaccard
    would have punished that to ~0.5 for the crime of being verbose.

    Scoring against the shorter side instead would be worse than either: a bare
    "Data Engineer" posting would score a perfect 1.0 against an application for
    "Data Engineer PySpark Data Factory", because the generic title is entirely
    contained in the specific one. Directional coverage puts that at 0.5 and
    drops it.

    The second element breaks ties between postings that all cover the
    application, preferring the one carrying the fewest unexplained words.
    Without it Ubisoft's "Machine Learning Engineer" and "Senior Machine
    Learning Engineer" are indistinguishable at 1.0 and both get discarded,
    when one of them is obviously the right row.
    """
    if not applied or not posting:
        return (0.0, 0)
    return (len(applied & posting) / len(applied), -len(posting - applied))


class Matcher:
    """Indexes the scraped `jobs` table once, then answers per application."""

    def __init__(self, jobs: list[tuple]) -> None:
        # rows are (id, company, native_job_id, title)
        self.jobs = jobs
        self.by_company: dict[str, list[tuple]] = {}
        self.by_ref: dict[str, list[tuple]] = {}
        for job in jobs:
            self.by_company.setdefault(_deburr(job[1]), []).append(job)
            ref = _norm_ref(job[2])
            if ref:
                self.by_ref.setdefault(ref, []).append(job)

    def company_pool(self, company: str) -> list[tuple]:
        """Scraped rows for an application's company, through the alias table."""
        key = _deburr(company)
        alias = COMPANY_ALIASES.get(key)
        if alias:
            return self.by_company.get(_deburr(alias), [])
        return self.by_company.get(key, [])

    def match(self, company: str, role: str, req_ref: str | None) -> tuple:
        """-> (job_id | None, tier, note). A None job_id means leave it NULL."""
        pool = self.company_pool(company)

        # --- tier 1: requisition id -----------------------------------------
        ref = _norm_ref(req_ref)
        if ref:
            scoped = [j for j in pool if _norm_ref(j[2]) == ref]
            if len(scoped) == 1:
                return scoped[0][0], "req_ref", scoped[0][3]
            if len(scoped) > 1:
                return None, "skip", f"req_ref {req_ref} hits {len(scoped)} rows"
            # Not in this company's pool: either we hold no alias for the
            # company, or the recruiter's spelling differs from the board's.
            if not pool and len(ref) >= _MIN_GLOBAL_REF_LEN:
                globally = self.by_ref.get(ref, [])
                if len(globally) == 1:
                    job = globally[0]
                    return job[0], "req_ref/global", f"{job[1]} - {job[3]}"
                if len(globally) > 1:
                    return None, "skip", f"req_ref {req_ref} ambiguous globally"

        if not pool:
            return None, "no_scraper", ""

        # --- tier 2: exact title --------------------------------------------
        wanted = _norm_title(role)
        if wanted:
            exact = [j for j in pool if _norm_title(j[3]) == wanted]
            if len(exact) == 1:
                return exact[0][0], "title", exact[0][3]
            if len(exact) > 1:
                return None, "skip", f"{len(exact)} rows share this exact title"

        # --- tier 3: token overlap ------------------------------------------
        wanted_tokens = _tokens(role)
        if not wanted_tokens:
            return None, "no_match", ""
        scored = sorted(
            ((_score(wanted_tokens, _tokens(j[3])), j) for j in pool),
            key=lambda pair: pair[0],
            reverse=True,
        )
        best_score, best = scored[0]
        if best_score[0] < _FUZZY_MIN:
            return None, "no_match", ""
        # A tie on the full (coverage, -extras) score means we genuinely cannot
        # tell which opening it was. Kering carries two requisitions under the
        # identical title "KERING Data Engineer"; only the req_ref separates
        # them, and tier 1 already had its chance.
        runner_up = scored[1][0] if len(scored) > 1 else (0.0, 0)
        if runner_up >= best_score:
            return None, "skip", f"tie at {best_score[0]:.2f} between >=2 rows"
        return best[0], f"fuzzy {best_score[0]:.2f}", best[3]


def main() -> int:
    parser = argparse.ArgumentParser(description="Link applications to scraped jobs.")
    parser.add_argument(
        "--apply", action="store_true",
        help="write the links (default is a dry run that changes nothing)",
    )
    args = parser.parse_args()

    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, company, role, req_ref FROM applications "
            "ORDER BY company, applied_on"
        )
        applications = cur.fetchall()
        cur.execute("SELECT id, company, native_job_id, title FROM jobs")
        matcher = Matcher(cur.fetchall())

        linked: list[tuple[int, int]] = []
        skipped: list[tuple[str, str, str]] = []
        for app_id, company, role, req_ref in applications:
            job_id, tier, note = matcher.match(company, role, req_ref)
            if job_id:
                linked.append((app_id, job_id))
                print(f"  LINK [{tier:>14}] {company} - {role[:44]}")
                print(f"                      -> jobs#{job_id} {note[:64]}")
            elif tier == "skip":
                skipped.append((company, role, note))

        if skipped:
            print("\n  ambiguous, left NULL:")
            for company, role, note in skipped:
                print(f"    {company} - {role[:40]}: {note}")

        total = len(applications)
        print(
            f"\n{len(linked)} of {total} applications matched a scraped job "
            f"({total - len(linked)} left NULL: no scraper, no match, or ambiguous)."
        )

        if not args.apply:
            print("Dry run - nothing written. Re-run with --apply to persist.")
            return 0

        cur.executemany(
            "UPDATE applications SET job_id = %s, updated_at = NOW() WHERE id = %s",
            [(job_id, app_id) for app_id, job_id in linked],
        )
        conn.commit()
        print(f"Wrote {len(linked)} job_id links.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
