"""Dashboard-side filters. Raw DB stores every scraped row; these predicates
narrow what the dashboard renders. Edit here to widen/narrow scope without
touching scrapers or schema.

`REGIONS` / `matches_region(location, region)` — an OPT-IN geographic scope.
Nothing is hidden by default: the dashboard shows every scraped row whatever
its location, and the user picks a region from the dropdown when they want to
narrow. The tiers are cumulative:

    ""                 all locations, no filter at all (default)
    "paris"            Paris 75 + La Défense
    "petite_couronne"  Paris + 92 + 93 + 94 (the old hardcoded scope)
    "idf"              petite couronne + 77 / 78 / 91 / 95 (Yvelines & co)
    "france"           anywhere in France, IDF or not

Matching is done on the free-text `location` string, so it is token-based:
commune names (deburred), "FR, 92 - ..." department codes, and French postal
codes. Multi-location strings pass if ANY listed place qualifies.

Coverage honesty: the petite-couronne lists are the complete INSEE commune
lists for 92 / 93 / 94. The grande-couronne list is NOT exhaustive (1268
communes in IDF) — it is the employment hubs that actually appear on these job
boards, plus department codes and 77/78/91/95 postal codes as a catch-all. A
row in a grande-couronne village with no code and no "Île-de-France" in the
string can slip through; widen `_GRANDE_COURONNE_TOKENS` when one shows up.

`DATE_CHURN_COMPANIES` — boards whose `posted_date` is worthless (below).

Commune sources: fr.wikipedia.org/wiki/Liste_des_communes_des_Hauts-de-Seine
                 fr.wikipedia.org/wiki/Liste_des_communes_de_la_Seine-Saint-Denis
                 fr.wikipedia.org/wiki/Liste_des_communes_du_Val-de-Marne
"""
from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Boards that rewrite the posting date on every crawl (SEO freshness churn).
#
# These sites stamp *every* live offer with today's date, so `posted_date` is
# not a date at all — it's the crawl timestamp wearing a date's clothes. On the
# dashboard that made every row look published today, floated them to the top
# of the sort, and permanently disabled the OPEN-N-MO / hide_old age rules.
#
# For these companies the dashboard uses `first_seen_at` as the base date: the
# first crawl that returned this native_job_id, i.e. the earliest moment we can
# prove the listing was live. It is a lower bound — a role already on the board
# when its scraper was written dates to the backfill run, not to its true
# publication — but unlike the upstream value it never moves.
#
# This does NOT touch the scrapers or the DB: the raw upstream date is still
# stored, so removing a company here restores the old display.
#
# Membership rule: ~every open row carries today's date, on every run.
#   Deloitte France — 80/80 rows stamped today (documented in the scraper).
#   Orange          — SEO-refreshed daily, see project_orange_dateposted_bogus.
# Reposts are unaffected: a real repost is the still_open FALSE->TRUE
# transition (reopened_at), which date churn cannot fake.
DATE_CHURN_COMPANIES: frozenset[str] = frozenset({
    "Deloitte France",
    "Orange",
})

_SEPARATORS_RE = re.compile(r"[-_'`]+")
_WHITESPACE_RE = re.compile(r"\s+")


def _deburr(s: str) -> str:
    """Lowercase, strip diacritics, and replace hyphens / apostrophes with
    spaces so commune names match across scraper formatting variants:
    'Charenton-le-Pont' / 'Charenton le pont' / "L'Haÿ-les-Roses" all
    normalize to 'charenton le pont' / 'l hay les roses'.
    """
    nfkd = unicodedata.normalize("NFKD", s)
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    normalized = _SEPARATORS_RE.sub(" ", no_accents.lower())
    return _WHITESPACE_RE.sub(" ", normalized).strip()


# Tokens are written in their _deburr'd form (lowercase, no accents, spaces
# only). Word-boundary regex below means substrings of longer city names don't
# false-match (e.g. 'issy' inside 'poissy').
#
# When a commune has a leading article ("Le/La/Les/L'"), both the prefixed
# form and the bare suffix are listed when the suffix is distinctive enough
# not to cause out-of-IDF collisions ('le bourget' kept WITHOUT a bare
# 'bourget' to avoid matching 'Bourget-du-Lac' in Savoie 73).

_PARIS_TOKENS: tuple[str, ...] = (
    "paris",
    "la defense", "paris la defense",
)

# ---- 92 Hauts-de-Seine (36 communes) ----
_92_TOKENS: tuple[str, ...] = (
    "hauts de seine",
    "antony",
    "asnieres sur seine", "asnieres",
    "bagneux",
    "bois colombes",
    "boulogne billancourt",
    "bourg la reine",
    "chatenay malabry",
    "chatillon",
    "chaville",
    "clamart",
    "clichy",
    "colombes",
    "courbevoie",
    "fontenay aux roses",
    "garches",
    "la garenne colombes", "garenne colombes",
    "gennevilliers",
    "issy les moulineaux",
    "levallois perret", "levallois",
    "malakoff",
    "marnes la coquette",
    "meudon",
    "montrouge",
    "nanterre",
    "neuilly sur seine", "neuilly",
    "le plessis robinson", "plessis robinson",
    "puteaux",
    "rueil malmaison", "rueil",
    "saint cloud",
    "sceaux",
    "sevres",
    "suresnes",
    "vanves",
    "vaucresson",
    "ville d avray",
    "villeneuve la garenne",
)

# ---- 93 Seine-Saint-Denis (40 communes) ----
_93_TOKENS: tuple[str, ...] = (
    "seine saint denis", "seine st denis",
    "aubervilliers",
    "aulnay sous bois",
    "bagnolet",
    "bobigny",
    "le blanc mesnil", "blanc mesnil",
    "bondy",
    "le bourget",   # NOT bare 'bourget' (Bourget-du-Lac is 73 Savoie)
    "clichy sous bois",
    "coubron",
    "la courneuve", "courneuve",
    "drancy",
    "dugny",
    "epinay sur seine",
    "gagny",
    "gournay sur marne",
    "l ile saint denis", "ile saint denis",
    "les lilas", "lilas",
    "livry gargan",
    "montfermeil",
    "montreuil",
    "neuilly plaisance",
    "neuilly sur marne",
    "noisy le grand",
    "noisy le sec",
    "pantin",
    "les pavillons sous bois", "pavillons sous bois",
    "le pre saint gervais", "pre saint gervais",
    "le raincy", "raincy",
    "romainville",
    "rosny sous bois",
    "saint denis",
    "saint ouen sur seine", "saint ouen",
    "sevran",
    "stains",
    "tremblay en france",
    "vaujours",
    "villemomble",
    "villepinte",
    "villetaneuse",
    "pierrefitte sur seine",
)

# ---- 94 Val-de-Marne (47 communes) ----
_94_TOKENS: tuple[str, ...] = (
    "val de marne",
    "ablon sur seine",
    "alfortville",
    "arcueil",
    "boissy saint leger",
    "bonneuil sur marne",
    "bry sur marne",
    "cachan",
    "champigny sur marne",
    "charenton le pont", "charenton",
    "chennevieres sur marne",
    "chevilly larue",
    "choisy le roi",
    "creteil",
    "fontenay sous bois",
    "fresnes",
    "gentilly",
    "l hay les roses", "hay les roses",
    "ivry sur seine",
    "joinville le pont",
    "le kremlin bicetre", "kremlin bicetre",
    "le perreux sur marne", "perreux sur marne",
    "le plessis trevise", "plessis trevise",
    "la queue en brie", "queue en brie",
    "limeil brevannes",
    "maisons alfort",
    "mandres les roses",
    "marolles en brie",
    "nogent sur marne",
    "noiseau",
    "orly",
    "ormesson sur marne",
    "perigny",
    "rungis",
    "saint mande",
    "saint maur des fosses",   # NOT bare 'saint maur' (St-Maur in 36 Indre)
    "saint maurice",
    "santeny",
    "sucy en brie",
    "thiais",
    "valenton",
    "villecresnes",
    "villejuif",
    "villeneuve le roi",
    "villeneuve saint georges",
    "villiers sur marne",
    "vincennes",
    "vitry sur seine",
)

# ---- 77 / 78 / 91 / 95 grande couronne — employment hubs, not exhaustive ----
# Deliberate omissions to avoid out-of-IDF collisions:
#   'cesson'  → Cesson-Sévigné (35, Rennes tech cluster) would false-match;
#               only the full 'cesson vert saint denis' is listed.
#   'grigny'  → Grigny 69 (Lyon) collides with Grigny 91.
#   'avon'    → too short / too generic to be safe.
_GRANDE_COURONNE_TOKENS: tuple[str, ...] = (
    # department names
    "seine et marne", "yvelines", "essonne", "val d oise",

    # ---- 78 Yvelines ----
    "versailles", "le chesnay", "chesnay rocquencourt", "rocquencourt",
    "guyancourt", "montigny le bretonneux", "saint quentin en yvelines",
    "trappes", "elancourt", "voisins le bretonneux", "plaisir",
    "les clayes sous bois", "clayes sous bois",
    "velizy villacoublay", "velizy", "buc", "jouy en josas",
    "poissy", "carrieres sous poissy", "orgeval", "chambourcy",
    "les mureaux", "mantes la jolie", "mantes la ville", "limay",
    "aubergenville", "flins sur seine", "epone", "gargenville",
    "saint germain en laye", "le pecq", "chatou", "croissy sur seine",
    "le vesinet", "marly le roi", "louveciennes", "bougival",
    "sartrouville", "houilles", "maisons laffitte", "carrieres sur seine",
    "conflans sainte honorine", "andresy", "acheres", "verneuil sur seine",
    "vernouillet", "triel sur seine", "meulan", "rambouillet", "coignieres",
    "maurepas", "magny les hameaux", "bois d arcy", "fontenay le fleury",
    "viroflay", "saint cyr l ecole",

    # ---- 91 Essonne ----
    "evry", "evry courcouronnes", "courcouronnes", "massy", "palaiseau",
    "saclay", "plateau de saclay", "gif sur yvette", "orsay", "les ulis",
    "courtaboeuf", "villebon sur yvette", "villejust", "nozay", "marcoussis",
    "bievres", "igny", "verrieres le buisson", "wissous", "chilly mazarin",
    "longjumeau", "morangis", "savigny sur orge", "juvisy sur orge",
    "athis mons", "viry chatillon", "ris orangis", "corbeil essonnes",
    "saint michel sur orge", "sainte genevieve des bois", "bretigny sur orge",
    "montlhery", "arpajon", "dourdan", "etampes", "mennecy",
    "vigneux sur seine", "draveil", "epinay sur orge", "limours",
    "brunoy", "yerres", "montgeron", "quincy sous senart",
    "boussy saint antoine", "lisses", "bondoufle", "fleury merogis",

    # ---- 95 Val-d'Oise ----
    "cergy", "cergy pontoise", "pontoise", "saint ouen l aumone", "osny",
    "eragny", "jouy le moutier", "vaureal", "courdimanche",
    "argenteuil", "bezons", "franconville", "ermont", "eaubonne",
    "saint gratien", "enghien les bains", "montmorency",
    "soisy sous montmorency", "taverny", "beauchamp", "herblay",
    "la frette sur seine", "montigny les cormeilles", "cormeilles en parisis",
    "sannois", "sarcelles", "garges les gonesse", "villiers le bel",
    "gonesse", "goussainville", "roissy", "roissy en france",
    "charles de gaulle", "le thillay", "fosses", "louvres", "domont",
    "ezanville", "saint brice sous foret", "persan", "beaumont sur oise",
    "l isle adam", "isle adam", "pierrelaye", "bessancourt", "marly la ville",
    "survilliers", "saint witz", "moisselles",

    # ---- 77 Seine-et-Marne ----
    "marne la vallee", "val d europe", "chessy", "serris",
    "bussy saint georges", "bailly romainvilliers", "magny le hongre",
    "montevrain", "collegien", "ferrieres en brie", "torcy", "noisiel",
    "champs sur marne", "lognes", "croissy beaubourg", "emerainville",
    "roissy en brie", "pontault combault", "ozoir la ferriere",
    "gretz armainvilliers", "tournan en brie", "servon",
    "chelles", "vaires sur marne", "brou sur chantereine", "courtry",
    "villeparisis", "mitry mory", "claye souilly", "othis",
    "dammartin en goele", "lagny sur marne", "saint thibault des vignes",
    "meaux", "melun", "dammarie les lys", "vaux le penil",
    "le mee sur seine", "savigny le temple", "cesson vert saint denis",
    "combs la ville", "moissy cramayel", "lieusaint", "senart",
    "brie comte robert", "nangis", "rozay en brie", "provins",
    "coulommiers", "la ferte sous jouarre", "fontainebleau", "nemours",
    "montereau fault yonne", "montereau",
)

# Bare "Île-de-France" with no city.
_BARE_IDF_TOKENS: tuple[str, ...] = (
    "ile de france", "idf", "region parisienne", "greater paris", "paris area",
)


def _compile(*token_groups: tuple[str, ...]) -> list[re.Pattern[str]]:
    tokens = {t for group in token_groups for t in group}
    return [re.compile(r"\b" + re.escape(t) + r"\b") for t in sorted(tokens)]


_PARIS_PATTERNS = _compile(_PARIS_TOKENS)
_PETITE_COURONNE_PATTERNS = _compile(
    _PARIS_TOKENS, _92_TOKENS, _93_TOKENS, _94_TOKENS,
)
_GRANDE_COURONNE_PATTERNS = _compile(_GRANDE_COURONNE_TOKENS)
_BARE_IDF_PATTERNS = _compile(_BARE_IDF_TOKENS)
_IDF_PATTERNS = _compile(
    _PARIS_TOKENS, _92_TOKENS, _93_TOKENS, _94_TOKENS, _BARE_IDF_TOKENS,
    _GRANDE_COURONNE_TOKENS,
)

# Department codes in the structured "FR, 75 - ..." format we see from
# Talentsoft / Crédit Agricole. Hyphens are already normalized to spaces in
# _deburr, so the trailing separator is just whitespace.
_DEPT_RE = {
    "paris": re.compile(r"\bfr,\s*75\b"),
    "petite_couronne": re.compile(r"\bfr,\s*(75|92|93|94)\b"),
    "idf": re.compile(r"\bfr,\s*(75|77|78|91|92|93|94|95)\b"),
}

# French postal codes ("92130 Issy-les-Moulineaux", "Cergy 95000"). Only
# trusted when the string also looks French, so a US ZIP like "Austin, TX
# 78701" can't be read as a Yvelines address.
_POSTAL_RE = {
    "paris": re.compile(r"\b75\d{3}\b"),
    "petite_couronne": re.compile(r"\b(75|92|93|94)\d{3}\b"),
    "idf": re.compile(r"\b(75|77|78|91|92|93|94|95)\d{3}\b"),
}

_FRENCH_RE = re.compile(r"\b(france|fr)\b")

_TIER_PATTERNS = {
    "paris": _PARIS_PATTERNS,
    "petite_couronne": _PETITE_COURONNE_PATTERNS,
    "idf": _IDF_PATTERNS,
}

# Ordered for the dashboard dropdown: key -> label. "" is the default and
# means no filtering at all.
REGIONS: tuple[tuple[str, str, str], ...] = (
    ("", "Everywhere",
     "No geographic filter — every scraped row, including jobs abroad."),
    ("france", "France",
     "Anywhere in France: an explicit France/FR marker, or a recognised "
     "Île-de-France place name."),
    ("idf", "Île-de-France",
     "All 8 departments — petite couronne plus Yvelines (78), Essonne (91), "
     "Val-d'Oise (95) and Seine-et-Marne (77). Grande-couronne coverage is "
     "the employment hubs plus dept/postal codes, not every commune."),
    ("petite_couronne", "Paris + petite couronne",
     "Paris 75 plus the complete commune lists of Hauts-de-Seine 92, "
     "Seine-Saint-Denis 93 and Val-de-Marne 94."),
    ("paris", "Paris only",
     "Paris 75 and La Défense."),
)
REGION_KEYS = frozenset(k for k, _, _ in REGIONS)
REGION_LABELS = {k: label for k, label, _ in REGIONS}


def matches_region(location: str | None, region: str) -> bool:
    """True if `location` falls inside `region`. Unknown region or "" => True
    (no filtering). A row with no location is only kept by the "" default —
    an empty string can't be proven to be anywhere.
    """
    if not region or region not in REGION_KEYS:
        return True
    if not location:
        return False

    norm = _deburr(location)
    looks_french = bool(_FRENCH_RE.search(norm))

    if region == "france":
        # Any explicit France marker, or an IDF place name (some boards store
        # a bare "Paris" / "Courbevoie" with no country at all).
        return looks_french or any(p.search(norm) for p in _IDF_PATTERNS)

    if any(p.search(norm) for p in _TIER_PATTERNS[region]):
        return True
    if _DEPT_RE[region].search(norm):
        return True
    if looks_french and _POSTAL_RE[region].search(norm):
        return True
    # A bare "Île-de-France" with no commune is kept in the petite-couronne
    # tier (user override from the original scope review — most such rows are
    # Paris HQ roles). But only when no commune is named at all: once the
    # string says "Guyancourt, Île-de-France" we know it is grande couronne,
    # and the region word must not smuggle it into the petite couronne.
    if (
        region == "petite_couronne"
        and any(p.search(norm) for p in _BARE_IDF_PATTERNS)
        and not any(p.search(norm) for p in _GRANDE_COURONNE_PATTERNS)
    ):
        return True
    return False
