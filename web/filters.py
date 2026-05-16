"""Dashboard-side filters. Raw DB stores every scraped row; these predicates
narrow what the dashboard renders. Edit here to widen/narrow scope without
touching scrapers or schema.

`is_idf(location)` — petite couronne only (Paris 75 + Hauts-de-Seine 92 +
Seine-Saint-Denis 93 + Val-de-Marne 94). Multi-location strings pass if ANY
listed location is petite couronne. Bare "Île-de-France" with no city is
kept (user override during scope discussion).
"""
from __future__ import annotations

import re
import unicodedata


def _deburr(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


# Whitelist tokens. Each is matched with word boundaries against the
# deburred-lowercase location string, so 'issy' does NOT match 'Poissy'.
# Generic tokens that would over-match outside IDF (e.g. bare 'boulogne'
# would catch 'Boulogne-sur-Mer' in Pas-de-Calais) are deliberately omitted —
# only the IDF-specific compound form is listed.
_IDF_TOKENS: tuple[str, ...] = (
    # 75 Paris
    "paris", "la defense", "la-defense",
    # 92 Hauts-de-Seine
    "hauts-de-seine", "hauts de seine",
    "antony", "asnieres", "bagneux", "bois-colombes", "bois colombes",
    "bois colombes cedex",
    "boulogne-billancourt", "boulogne billancourt",
    "chatenay-malabry", "chatillon", "chaville", "clamart", "clichy",
    "colombes", "courbevoie", "fontenay-aux-roses", "garches",
    "garenne-colombes", "gennevilliers",
    "issy-les-moulineaux", "issy les moulineaux",
    "levallois-perret", "levallois",
    "malakoff", "meudon", "montrouge", "nanterre",
    "neuilly-sur-seine", "neuilly",
    "puteaux", "rueil-malmaison", "rueil",
    "saint-cloud", "sceaux", "sevres", "suresnes",
    "vanves", "ville-d'avray", "villeneuve-la-garenne",
    # 93 Seine-Saint-Denis
    "seine-saint-denis", "seine saint denis", "seine-st-denis",
    "aubervilliers", "bagnolet", "bobigny", "bondy", "drancy",
    "epinay-sur-seine", "le blanc-mesnil", "blanc-mesnil",
    "le bourget", "le pre-saint-gervais", "les lilas",
    "livry-gargan", "montreuil", "neuilly-plaisance",
    "noisy-le-grand", "noisy le grand", "noisy-le-sec", "pantin",
    "pierrefitte-sur-seine", "romainville", "rosny-sous-bois",
    "saint-denis", "saint denis", "saint-ouen", "saint ouen",
    "sevran", "stains", "villepinte",
    # 94 Val-de-Marne
    "val-de-marne", "val de marne",
    "alfortville", "arcueil", "boissy-saint-leger",
    "bonneuil-sur-marne", "bry-sur-marne", "cachan",
    "champigny-sur-marne",
    "charenton-le-pont", "charenton le pont", "charenton",
    "chennevieres-sur-marne", "choisy-le-roi",
    "creteil", "fontenay-sous-bois", "fresnes", "gentilly",
    "ivry-sur-seine", "joinville-le-pont", "kremlin-bicetre",
    "limeil-brevannes", "maisons-alfort", "nogent-sur-marne",
    "orly", "le perreux-sur-marne", "rungis",
    "saint-mande", "saint-maur-des-fosses", "saint-maurice",
    "sucy-en-brie", "thiais", "villejuif",
    "villeneuve-saint-georges", "villiers-sur-marne",
    "vincennes", "vitry-sur-seine",
)

_IDF_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b" + re.escape(t) + r"\b") for t in _IDF_TOKENS
]

# Department codes in the structured "FR, 75 - ..." format we see from
# Talentsoft / Crédit Agricole. Only petite-couronne codes pass.
_DEPT_CODE_RE = re.compile(r"\bfr,\s*(75|92|93|94)\s*[-,]")

# Bare "Île-de-France" with no city: user kept these during scope review.
_BARE_IDF_RE = re.compile(r"\b(ile-de-france|ile de france|idf)\b")


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
