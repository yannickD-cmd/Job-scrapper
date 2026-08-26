# Groupe iliad — filters

Two boards, two modules. Registered as `iliad.france` and `iliad.scaleway`.

| Entity | Board | Module |
|---|---|---|
| Free, Free Pro, Stancer, Opcore, Itrust | SmartRecruiters tenant `Iliad-Free` | `france.py` |
| Scaleway | Lever tenant `scaleway` | `scaleway.py` |
| iliad italia, Play Polska | out of scope (France-only) | — |

## france.py — SmartRecruiters `Iliad-Free`

`recrutement.iliad.fr` 302s to a **Beekome** career site
(`iliad_career.beekome.com`, a Nuxt SPA). Beekome is only a front-end: its rows
are stamped `jobSourceType: "ATS_SR"` and their `directApplyUrl` points at
`jobs.smartrecruiters.com/Iliad-Free/…`. We hit the ATS of record instead — it
pages 100 at a time rather than Beekome's 10, and keeps the `customField` facets
Beekome flattens away. **Do not go back to the Beekome layer.**

| Axis | Value kept | Applied where |
|---|---|---|
| Country | France (board is 220/224 FR + 4 DOM-TOM) | not filtered — the tenant is French |
| Category | `Métiers` customField | `METIER_KEEP` / `METIER_BLOCK` / content gate |
| Contract | **All** (CDI 197, CDD 20, Freelance 5, Alternance 1, Stage 1) | not gated |
| Field/NOC roles | excluded | `FIELD_OPS_TITLE_RE`, applied first |

**Use `Métiers`, not `department`, and never `function`.** All three disagree:

- `department` merges entity with métier ("Free Pro" is both) and files 3 roles
  under Réseaux & Telecom where `Métiers` files 17.
- SmartRecruiters' standard `function` is recruiter-entered and plainly wrong
  here: *"Marketing Produit Télécom"* and *"Marketing Produit Services Managés"*
  are both `function: Engineering`, while *"Technicien Support Systèmes ou
  Réseaux"* is `function: Administrative`.

| Keep wholesale | Block outright | Content-gated |
|---|---|---|
| Tech & Digital (42) | Boutique (87, retail sales) | Réseaux & Telecom (17) |
| | Relation Abonné (22, call-centre) | Free Pro (14) |
| | | Fonctions Centrales (40), none |

### Three French-language traps in the content gate

Each of these was a live false positive caught in a smoke test:

1. **`développeur` vs `développer`.** The pattern `d[ée]veloppeu?r` also matches
   the *verb* "développer". Every sales ad says *"développer le chiffre
   d'affaires"* / *"développer votre portefeuille"*, so it kept door-to-door
   salespeople and Chefs de secteur. Pin the noun: `\bd[ée]veloppeu(?:r|se)\b`.
2. **`LLM` is a law degree.** *"Juriste M&A"* was kept on `strong=llm` — on a
   French board LLM is the **Master of Laws (LL.M.)** far more often than a
   language model. Bare `\bllms?\b` is removed; `large language model` spelled
   out is unambiguous.
3. **Never match the products iliad sells.** "cybersécurité" and "cloud" run
   through Free Pro's *commercial* ads ("Commercial Sédentaire", "Gestionnaire
   Back Office"), because those are the things being sold. Only practitioner
   vocabulary discriminates: SOC, SIEM, pentest, Ansible, Terraform.

Also: `intelligence artificielle` is SUPPORTING, not STRONG — plenty of
non-technical ads say they "use AI tools", and on its own it kept a forensic
accountant and an HR-systems MOA. A real AI role always names something else.

**Why a content gate rather than a title gate, concretely:** Free Pro's board is
full of *"Ingénieur Commercial"* — French for **account executive**. A title
filter keeps every one of them (they say "Ingénieur") while the same bucket's
real *"Référent Technique SRE / Cloud Linux"* and *"Ingénieur Cybersécurité"*
are indistinguishable by title alone.

**Yield: 54 of 224** (112 detail-fetched; blocked métiers skip the detail call).
The gate rescues Ingénieur Cybersécurité, Référent Technique SRE, Administrateur
TOIP, Ingénieur E2E NetDevOps, E2E Network Performance Analyst, Cloud Ops
Coordinator, Architecte Intégration, and a `Métiers: None` "DevOps (H/F)".

## scaleway.py — Lever `scaleway`

Standard Lever public API; description inline as `descriptionPlain`, no detail
call. `country` is a reliable top-level ISO-2 (FR 33, IT 2, PL 1, SE 1).

| Keep wholesale | Content-gated |
|---|---|
| Engineering (10), GPU Cloud (4), IT (3), Products (2) | Sales, Operations, Marketing, Finance, null |

**Judge the role section, not the company pitch.** Every posting opens with the
same *"OUR STORY: 🇪🇺 … sovereign cloud … bare metal, containerization,
serverless, AI …"* preamble. Gating on the whole ad means gating on Scaleway
rather than the job — it kept an *"Approvisionneur - Stage"* (a supply-chain
intern). `_role_section()` cuts to the first role heading (`WHY WE NEED YOU`,
`YOUR DAILY ROUTINE`, `HARDSKILLS`, …), which then makes infra vocabulary safe to
use and correctly rescued *Hardware Architect* (Open Hardware/OCP, co-design with
vendors) and *Cloud Support Specialist*.

A JD can be pitch-only — *"Presales Solutions Engineer - HPC"* is 1142 chars of
pure boilerplate with no role section at all. There is genuinely nothing to
judge, and it drops.

Commitment is NOT gated: the board carries 4 internships and 1 apprenticeship,
including *"Software Engineer IAM - Internship"*.

**Yield: 25 of 33 France** (37 board).

`posted_date` is Lever `createdAt` — record creation, not recency. Scaleway
leaves reqs open for years (one reads 2021-10-15 and is live). Never read it as
freshness; dedup is by `native_job_id`. See `project_lever_createdat_evergreen`.
