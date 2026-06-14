# Pernod Ricard — Workday CXS API notes

Single global board: `pernodricard.wd3.myworkdayjobs.com/pernod-ricard`

## Endpoints
- Listing: `POST /wday/cxs/pernodricard/pernod-ricard/jobs`
- Detail:  `GET  /wday/cxs/pernodricard/pernod-ricard<externalPath>`

## The faceted-POST 400 (the gotcha) — what actually matters
Faceted (filtered) POSTs return an empty-body HTTP 400
(`{"errorCode":"HTTP_400","locale":"en-US,en;q=0.9",...}`) for two real reasons,
plus one red herring I chased:
1. **Missing locale header.** Filtered POSTs need `X-Calypso-Selected-Locale:
   en-US` + an `/en-US/...` Referer. Without them the server 400s valid bodies.
   (Same as Rothschild.)
2. **Token bucket.** The faceted POST is metered by a slow-refilling, ESCALATING
   bucket — a burst earns 400s for tens of minutes (the detail GET is NOT
   metered). Probe sparingly; back off on 400.
- RED HERRING: it looks like faceted POSTs "need a seeded session cookie" — they
  do NOT. Run **cookie-free** (`cookies.clear()` before each request). On a
  flagged fingerprint (datacenter IPs / GitHub Actions) a seeded `__cf_bm`
  cookie makes every faceted POST 400 forever — a seeded version failed 100% in
  CI. Cookie-free requests are scored fresh and succeed.

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
- category       = family name from the per-family loop ("Tech" / "Information Technology")

## One family per POST (not combined)
We query each family on its own: `jobFamilyGroup: [<one id>]` + Regular.
Confirmed reliable (Tech+Regular → 28, etc.). The COMBINED two-family payload
`jobFamilyGroup: [Tech, IT]` + Regular was never once observed to return 200
(always 400, regardless of cookies/headers), so it's avoided. Two POSTs per run
(one per family), well spaced. Looping also lets us tag each row with its
family — listing rows don't carry the family, and the detail endpoint has NO
family field either, so faceting is the only source.

## Scope locked with user (2026-06-12)
France · Tech + Information Technology families · CDI (Regular) only ·
no extra title regex (keep every in-family role).
