"""Shared relevance predicate — is a posting an in-scope tech / data / AI / software role?

This board tracks Data & AI, Software/IT and adjacent engineering roles in France.
Some company boards (notably Schneider Electric on iCIMS) return their *entire*
careers feed — electricians, fitters, warehouse, HR, finance, sales, quality —
because the ATS has no usable "tech only" category facet. Category alone is
unreliable there (relevant roles are scattered across many categories, and those
same categories are full of non-tech roles), so we classify on the TITLE.

`is_tech_role(title)` returns True when the title carries a high-signal
data / AI / software / cyber / cloud / dev keyword. It is built on an ALLOW-list
(you must look like tech to be kept) plus a small HARD-EXCLUDE override for the
few titles where a tech-ish word collides with a clearly non-tech role.

Matching is done on a *deburred* title (lowercased, accents stripped) so that
"Données"/"Donnees", "Système"/"Systemes", "Développeur" all match a single
ASCII pattern — same technique as web/filters.py's IDF matcher.

Used in two places so they can never drift:
  - scrapers that pull a broad feed (Schneider) filter their output through it;
  - the one-off DB junk purge deletes rows that fail it.

Tuned against the live Schneider / Dassault feeds. When junk slips through (or a
real role is wrongly dropped), adjust the keyword lists here and the change
applies everywhere at once.
"""
from __future__ import annotations

import re
import unicodedata


def _deburr(s: str) -> str:
    """Lowercase + strip diacritics so accent/no-accent spellings match one pattern."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


# --- ALLOW: high-signal tech / data / AI / software keywords (deburred text) --
# High precision on purpose. Bare ambiguous words ("analyste", "systeme",
# "reseaux", "digital", "securite", "test", "qualite", and English bare
# "development") are NOT here — on their own they match non-tech roles. We only
# match them in tech-specific compounds.
_ALLOW_PATTERNS: tuple[str, ...] = (
    # ---- Data ----
    r"\bdata\b", r"\bdatas\b", r"donnee", r"big ?data", r"datavi[sz]",
    r"data ?(?:scien|engineer|analy|architec|steward|lake|ops|warehouse)",
    r"\banalytics?\b", r"\banalytique", r"business intelligence", r"\bbi\b",
    r"base de donnee", r"database", r"knowledge graph",
    # ---- AI / ML ----
    r"\bia\b", r"\bai\b", r"a\.i\b", r"\bml\b", r"\bmlops\b",
    r"intelligence artificielle", r"artificial intelligence",
    r"machine learning", r"deep learning", r"\bnlp\b", r"\bllms?\b", r"llmaas",
    r"generativ", r"gen ?ai", r"foundation models?", r"modele de fondation",
    r"applied scientist", r"research scientist", r"\bgpu\b",
    # ---- Software / dev ----
    r"software", r"logiciel",
    r"developpeur", r"developpeuse", r"developpement", r"\bdeveloper\b", r"\bdev\b",
    r"full ?stack", r"front ?end", r"back ?end", r"micro ?service",
    r"devops", r"devsecops", r"\brelops\b", r"\bsysops\b", r"\bsre\b",
    r"programmeur", r"programming", r"\bapi\b", r"\bsdk\b",
    # ---- Languages / stacks ----
    r"python", r"\bjava\b", r"javascript", r"typescript", r"\bc\+\+", r"\bc#",
    r"golang", r"kotlin", r"\bscala\b", r"\bsql\b", r"\bsap\b", r"\bphp\b",
    r"\b\.net\b", r"react", r"angular",
    # ---- Cloud / infra ----
    r"\bcloud\b", r"infrastructure", r"virtualisation", r"kubernetes", r"docker",
    r"\biaas\b", r"\bpaas\b", r"\bsaas\b", r"\bplatform\b", r"plateforme",
    r"administration systeme", r"administrateur systeme", r"systeme et stockage",
    # ---- Cyber (compound only — not bare French "securite") ----
    r"cyber", r"cybersecurit", r"cybersecurity",
    r"security (?:engineer|architect|analyst|operation|specialist)",
    r"\bsoc\b", r"\biam\b", r"pentest", r"vulnerabilit", r"intrusion",
    # ---- Embedded / systems ----
    r"embarque", r"embedded", r"firmware",
    # ---- Networks (tech, not electrical) ----
    r"wireless", r"\biot\b", r"\biiot\b", r"\bwifi\b", r"\bwpa\d", r"\bvpn\b",
    # ---- QA / test automation (not bare "test") ----
    r"\bqa\b", r"test automation", r"automatisation de test",
    # ---- Architecture (tech) ----
    r"architecte", r"architect",
    # ---- IT end-user computing / interoperability (verified by audit) ----
    r"poste de travail", r"interoperab",
    # ---- Dassault product stacks (all tech roles) ----
    r"\bplm\b", r"3dexperience", r"enovia", r"catia", r"delmia", r"biovia", r"3dexcite",
    # ---- Design (product/UX) ----
    r"\bux\b", r"\bui\b", r"ux/ui", r"\bdesigner\b",
    # ---- Generic IT ----
    r"informatique", r"\bit\b",
    # ---- Data platforms / BI / ETL (added when Safran/Thales/CGI feeds surfaced
    #      real data roles named only after the tool: "Consultant Dataiku",
    #      "Tech Lead Databricks", "Consultant Snowflake/ETL", "Décisionnel"). ----
    r"snowflake", r"databricks", r"dataiku", r"informatica", r"talend",
    r"\bssis\b", r"\bssas\b", r"\betl\b", r"\belt\b", r"\bkafka\b", r"\bspark\b",
    r"hadoop", r"\bhdfs\b", r"bigquery", r"power ?bi", r"\bpowerbi\b",
    r"\btableau\b", r"\bqlik", r"microstrategy", r"decisionn?el",
    r"data ?lake", r"data ?warehouse", r"lakehouse", r"\bdbt\b",
    r"maintenance predictive", r"predictive maintenance",
    r"biostatist", r"statisticien", r"statistician",
    r"web analyst", r"digital analyst",
    # ---- SRE / platform / infra-as-code / observability (spelled-out forms the
    #      bare-acronym patterns above miss: "Site Reliability Engineer",
    #      "Ingénieur CloudOps", "Expert OpenShift", "Splunk Operations"). ----
    r"site reliability", r"cloud ?ops", r"fin ?ops", r"git ?ops",
    r"platform engineer", r"openshift", r"terraform", r"ansible",
    r"observabilit", r"splunk", r"dynatrace", r"elasticsearch", r"opensearch",
    r"grafana", r"prometheus",
    r"\bhpc\b", r"calcul intensif", r"calculs intensifs",
    # ---- Security, hands-on (recover roles the compound "security ..." pattern
    #      misses: forensic/CERT/SOC analysts, Blue/Red Team, Common Criteria). ----
    r"forensi", r"\bcert\b", r"\bcsirt\b", r"\bsiem\b", r"\bedr\b", r"\bdfir\b",
    r"blue team", r"red team", r"purple team", r"security officer",
    r"criteres communs", r"common criteria",
)
_ALLOW = re.compile("|".join(_ALLOW_PATTERNS))

# --- HARD EXCLUDE: override the allow-list for known collisions (deburred) ----
# Fires only when a title matched ALLOW but is unmistakably non-tech.
# Most junk never matches ALLOW at all, so this stays tiny.
_HARD_EXCLUDE_PATTERNS: tuple[str, ...] = (
    r"reseaux? electrique",
    r"securite machine",
    r"\bit\b technicien", r"technicien \bit\b",
    r"business development",       # sales, despite "dev"-ish wording
    r"developpement commercial",
    r"technico.?commercial",       # technical SALES, despite cyber/IA domain words
    r"\bpricing\b", r"monetisation",  # pricing/monetization = commercial, not tech
    # ---- Physical-product / aerospace-defense engineering ---------------------
    # Safran, Thales and Airbus file hardware / systems / mechanical / signal /
    # optronics / safety / V&V / configuration-management roles under the same
    # ATS families as their software & data roles. These are out of scope for a
    # data + software board but collide with a tech keyword above (architecte,
    # logiciel, embarque, ia, developpement, calculateur...), so ALLOW alone
    # keeps them. Drop them here. Terms are aerospace/defense-specific and do not
    # appear in the data/software titles at the other tracked companies (verified
    # against the full open-jobs set). See project_defense_physeng_junk memory.
    #   physical / defense / mechanical domain nouns:
    r"turbomachine", r"turborea?cteur", r"optroniqu", r"avioniqu",
    r"\bmissiles?\b", r"artillerie", r"torpille", r"\bsonar\b", r"\bradar\b",
    r"\bviseurs?\b", r"munition", r"armement", r"aeroport",  # aéroporté(e)
    r"\bcalculateurs?\b", r"\bguidage\b", r"pile a combustible",
    r"propulsi", r"mecaniqu", r"mecatroniqu", r"hydrauliqu", r"aerodynamiqu",
    r"bancs? d.?essai",
    r"energie dirigee", r"navigation aeronautique", r"\binertielles?\b",
    #   methodology: V&V / config mgmt / safety / airworthiness (physical systems):
    r"surete de fonctionnement", r"\bivvqm?\b", r"\bivv\b",
    r"verification.{0,6}validation", r"traitement (?:du|de) signal",
    r"navigabilite", r"airworthiness", r"\bsafety\b",
    r"gestion(?:naire)? de configuration", r"configuration management",
    r"gestion(?:naire)? de modifications?",
    # NB: no bare "architecte systeme" / "ingenieur systeme" exclude — it would
    # also drop cybersecurity/infra architects. Bare "Système" roles with no
    # physical-domain qualifier are left in (few, and genuinely ambiguous).
)
_HARD_EXCLUDE = re.compile("|".join(_HARD_EXCLUDE_PATTERNS))


def is_tech_role(title: str | None, category: str | None = None) -> bool:
    """True if the posting looks like an in-scope tech/data/AI/software role.

    Decides on the title (category is unreliable on broad feeds). `category` is
    accepted for future per-ATS overrides but not currently used — keep the
    signature stable for callers.
    """
    if not title or not title.strip():
        return False
    t = _deburr(title)
    if _HARD_EXCLUDE.search(t):
        return False
    return bool(_ALLOW.search(t))
