"""Dashboard-side filters. Raw DB stores every scraped row; these predicates
narrow what the dashboard renders. Edit here to widen/narrow scope without
touching scrapers or schema.

`is_idf(location)` — petite couronne only (Paris 75 + Hauts-de-Seine 92 +
Seine-Saint-Denis 93 + Val-de-Marne 94). Multi-location strings pass if ANY
listed location is petite couronne. Bare "Île-de-France" with no city is
kept (user override during scope discussion).

Commune list is the official INSEE one for each department:
- 92 Hauts-de-Seine: 36 communes
- 93 Seine-Saint-Denis: 40 communes
- 94 Val-de-Marne: 47 communes
Sources: fr.wikipedia.org/wiki/Liste_des_communes_des_Hauts-de-Seine
         fr.wikipedia.org/wiki/Liste_des_communes_de_la_Seine-Saint-Denis
         fr.wikipedia.org/wiki/Liste_des_communes_du_Val-de-Marne
"""
from __future__ import annotations

import re
import unicodedata

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


# Commune whitelist. Tokens are written in their _deburr'd form (lowercase,
# no accents, spaces only). Word-boundary regex below means substrings of
# longer city names don't false-match (e.g. 'issy' inside 'poissy').
#
# When a commune has a leading article ("Le/La/Les/L'"), both the prefixed
# form and the bare suffix are listed when the suffix is distinctive enough
# not to cause out-of-IDF collisions ('le bourget' kept WITHOUT a bare
# 'bourget' to avoid matching 'Bourget-du-Lac' in Savoie 73).
_IDF_TOKENS: tuple[str, ...] = (
    # ---- 75 Paris ----
    "paris",
    "la defense", "paris la defense",

    # ---- 92 Hauts-de-Seine (36 communes) ----
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

    # ---- 93 Seine-Saint-Denis (40 communes) ----
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

    # ---- 94 Val-de-Marne (47 communes) ----
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

_IDF_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b" + re.escape(t) + r"\b") for t in _IDF_TOKENS
]

# Department codes in the structured "FR, 75 - ..." format we see from
# Talentsoft / Crédit Agricole. Only petite-couronne codes pass.
# Hyphens are already normalized to spaces in _deburr, so the trailing
# separator is just whitespace.
_DEPT_CODE_RE = re.compile(r"\bfr,\s*(75|92|93|94)\b")

# Bare "Île-de-France" with no city: user kept these during scope review.
_BARE_IDF_RE = re.compile(r"\b(ile de france|idf)\b")


def is_idf(location: str | None) -> bool:
    if not location:
        return False
    norm = _deburr(location)
    if any(p.search(norm) for p in _IDF_PATTERNS):
        return True
    if _DEPT_CODE_RE.search(norm):
        return True
    if _BARE_IDF_RE.search(norm):
        return True
    return False
