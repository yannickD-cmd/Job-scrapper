# Wavestone — applied filters

ATS: **SmartRecruiters** (tenant `Wavestone1`). Source of truth = the postings
`customField[]` facets, which mirror the website's filter chips.

| Axis | Value kept | Applied where |
|---|---|---|
| Office | `Paris` | `customField "Office"` == Paris (mirrors `?city=paris`) |
| Contract | `Permanent` (CDI) | `customField "Permanents / internships"` == Permanent (mirrors `?contract-type=permanent`) |
| Family (kept whole) | Artificial Intelligence, CTO Advisory, Wivoo | `customField "Practice / Function"` allowlist |
| Family (excluded) | Cybersecurity | `customField "Practice / Function"` blocklist |
| Title keyword (cross-family catch) | `data, IA, AI, ML, GenAI, LLM, agentic, analytics, cloud, devops, sre, software, architect, plateforme, ...` | regex on `name`, for families outside the allowlist |

`Office=Paris` + `Permanent` == the website's "105 results"; the family +
keyword predicate narrows that to **~55** Data & AI / tech roles.

Note: bare `developer` is intentionally NOT a keyword — it false-matches
"Business Developer" (a sales role). The allowlisted families already keep their
software-dev postings.
