# Kering filters

Kering publishes the same requisitions on two surfaces and the scraper reads
**both**, unioning them on the requisition number (`R…`, which is also
`native_job_id` — the corporate feed's internal `jobId` is re-issued whenever a
req is edited, so it is not a stable key).

| surface | source | what it is | fields used |
|---|---|---|---|
| corporate | `www.kering.com/en/talent/job-offers/?page=N` | Next.js `__NEXT_DATA__ → sections[type=job-search] → props.jobList[*]`; filters are client-side checkboxes (query params ignored) so we walk every page | `locationCountry`, `jobFamilyId`, `workerSubType`, `description`, `publishedAt` |
| ATS | `careers.kering.com/api/pcsx/search` + `/api/pcsx/position_details` | Eightfold "PCSX", open JSON, 10 rows/page, `sort_by=timestamp` | `locations` / `standardizedLocations`, `department`, then `efcustomTextWorkerSubtype`, `efcustomTextJobFamily`, `jobDescription` |

Neither surface contains the other (measured 2026-07-31: 976 reqs on both, 389
corporate-only, 54 ATS-only). The ATS is the system of record and publishes
first; the corporate feed lags it by hours but carries more rows overall.

## Scope, per surface

A requisition is kept if **either** surface says it is in scope, so a taxonomy
disagreement can only include, never drop.

| axis | corporate field | ATS field | value(s) kept | notes |
|---|---|---|---|---|
| country | `locationCountry` | `locations` ends with `France` / `standardizedLocations` ends with `, FR` | `France` | corporate is an exact string; ATS location is free text + a normalised variant |
| job family | `jobFamilyId` | `department` (listing) | `Information_&_Digital_Technologies` / `Tech & Digital` | same family under two taxonomies; they agree 1:1 on France rows. `efcustomTextJobFamily` on the ATS detail is a *sub*-family ("Data & Analytics", "Infrastructure") — displayed, never gated on |
| worker subtype | `workerSubType` | `efcustomTextWorkerSubtype` (detail only) | `Regular` | excludes Agency, Fixed Term, Trainee, Student (Fixed Term), Apprenticeship — i.e. CDI-equivalent |
| brand / house | `houseName` | `efcustomTextHouse` | *all 13 houses* (no filter) | per scope decision; accept future overlap if a brand gets its own scraper |

## Request budget

- Corporate: `totalPages` off page 1 (~115 × 12 jobs), capped by `MAX_PAGES = 200`,
  2.0 s apart. Its `totalJobNumber` runs slightly ahead of the rows actually
  served — that gap is the publish backlog (a search index ahead of a cached
  listing), not a pagination bug.
- ATS: `count` off the first response (~103 pages × 10), capped by
  `MAX_PCSX_PAGES = 400`, 1.0 s apart. Detail fetches only for positions that
  already pass France + `Tech & Digital` (~22/run), since the contract type
  lives only in the detail payload.
- Total ≈ 240 requests / ≈ 8 min per run.

`/api/apply/v2/jobs` on the ATS host is closed (`Not authorized for PCSX`) —
use `/api/pcsx/*`. Both endpoints accept the project's polite User-Agent; no
browser fingerprint needed.
