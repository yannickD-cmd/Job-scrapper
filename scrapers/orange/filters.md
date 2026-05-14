# Orange scope

Three filters, applied in order:

1. **Country** — JSON-LD `jobLocation.address.addressCountry == "FRANCE"`
   (Orange uses uppercase country names: FRANCE, BELGIUM, POLAND…)

2. **Category** — JSON-LD `occupationalCategory == "Data & AI"`
   Other observed values we exclude: IT & Engineering, Business & Sales,
   Customer Experience & Support, Cybersecurity, Finance, HR, Operations,
   Consulting, Project Management, Other.

   Why only Data & AI: the goal is ML / data engineering / data
   science / GenAI roles. "IT & Engineering" turned out to be mostly
   sysadmins, service delivery, and Azure infra — adjacent but noisy
   for a data-role-focused tracker.

3. **Permanent (CDI) only** — title must NOT match
   `alternance | alternant | stage | stagiaire | internship |
    apprenti | apprentissage | cdd | contrat de professionnalisation |
    vie | professional contract`
   (case-insensitive, word-boundary). Reason: Orange's JSON-LD
   `employmentType` is unreliable for distinguishing CDI from
   alternance/CDD — same field shows `Full-time`, `FULL_TIME`, or
   `OTHER` across postings. Title markers are far more consistent.

## Data source

Orange runs Phenom People (career-site ID `OYVOCZGB`). The job-search
page is fully client-side rendered; the typical Phenom `/api/jobs`
endpoint returns 500 for orange.jobs. Instead we read the public
sitemap, which links every job detail page:

- https://orange.jobs/sitemap.xml → sitemapindex pointing to
  sitemap1.xml, sitemap2.xml, sitemap3.xml (under /gb/en/)
- Each detail page exposes a fully-populated schema.org/JobPosting
  JSON-LD block (same shape we already consume on Sanofi and BNP).

## Slug pre-filter

Before any HTTP fetch, drop URLs whose slug matches the non-permanent
regex above. ~156/1094 jobs today carry an explicit ALTERNANCE / Stage
marker in the URL slug — skipping their detail page saves several
minutes per run with no false negatives (slug = page title).
