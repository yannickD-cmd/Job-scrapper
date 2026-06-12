# Pernod Ricard — Workday CXS API notes

Single global board: `pernodricard.wd3.myworkdayjobs.com/pernod-ricard`

## Endpoints
- Seed cookies: `GET  /wday/cxs/pernodricard/pernod-ricard`
- Listing:      `POST /wday/cxs/pernodricard/pernod-ricard/jobs`
- Detail:       `GET  /wday/cxs/pernodricard/pernod-ricard<externalPath>`

## Session-cookie quirk (the gotcha)
An **empty**-facet POST works cookie-free, but ANY `appliedFacets` payload
returns an empty-body HTTP 400 (`{"errorCode":"HTTP_400",...}`) unless the
caller already holds the CXS session cookies. Fix: `GET` the CXS root once to
seed `PLAY_SESSION` / `wd-browser-id` / `__cf_bm`, then reuse the Session.
Even then, a burst of faceted POSTs within ~2s re-trips the 400 (Cloudflare
rate limit, ~30-60s recovery) — pace requests and back off on 400.

## Facet ids
jobFamilyGroup (Job Category):
- Tech                     = 5c4276c36b5a1001e317a08d36940000  (~34 global)
- Information Technology   = 371688745b57014fe9c19df9ef17a12f  (~4 global)
- Operations 92, Sales 86, Marketing 46, Finance 39, Trade Marketing 20, …

workerSubType (Job Type):
- Regular (CDI)            = 371688745b5701d8d14db11fa6174024  (245 global)
- Temporary (Fixed Term)   = 371688745b5701882a89b11fa6174124
- Trainee / Intern / Apprenticeship / VIE / Contractor — see board

No country/location facet is exposed. Country comes back as a plain name in
each listing row's `bulletFields` (e.g. ["France","JR-053956"]); filter
client-side. Detail confirms via `country.descriptor` == "France" (France id
54c5b6971ffb4bf0b116fe7651ec789a, the Workday-global one).

## Field mapping (detail `jobPostingInfo`)
- native_job_id  <- jobReqId        ("JR-053956")
- title          <- title
- description     <- jobDescription  (HTML, stripped)
- apply_url      <- externalUrl
- location       <- location         (clean city) / jobRequisitionLocation.descriptor
- posted_date    <- startDate        (already ISO YYYY-MM-DD)
- identifier     <- id               (internal hash)
- employment_type = "CDI"            (guaranteed by the Regular facet)
- category       = "Tech / IT"       (coarse — see below)

## One combined faceted POST (token-bucket avoidance)
The faceted POST endpoint is metered by a slow-refilling, ESCALATING token
bucket: after a burst it 400s for tens of minutes (the empty-facet POST and
detail GET stay 200 throughout). To make at most ONE faceted POST per run we
query both families together: `jobFamilyGroup: [Tech, IT]` + Regular. The two
families are disjoint (34 + 4 == 38 combined), so the union can't double-count;
the only cost is we can't tell Tech from IT per row, hence the coarse
`category = "Tech / IT"`. Listing rows don't carry the family at all, and the
detail endpoint has NO family field either — faceting is the only source.

## Scope locked with user (2026-06-12)
France · Tech + Information Technology families · CDI (Regular) only ·
no extra title regex (keep every in-family role).
