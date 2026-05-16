# Kering filters

The Kering careers site exposes filters as client-side checkboxes only —
query-string params are ignored by the server, so we walk every page and
filter in Python on fields inside `__NEXT_DATA__ → sections[type=job-search]
→ props.jobList[*]`.

| axis            | field             | value(s) kept                                  | notes                                                                                              |
|-----------------|-------------------|------------------------------------------------|----------------------------------------------------------------------------------------------------|
| country         | `locationCountry` | `France`                                       | exact string match, case-sensitive (Kering serves consistent values)                               |
| job family      | `jobFamilyId`     | `Information_&_Digital_Technologies`           | UI label is "Tech & Digital" — id is more stable                                                   |
| worker subtype  | `workerSubType`   | `Regular`                                      | excludes Agency, Fixed Term, Trainee, Student (Fixed Term), Apprenticeship — i.e. CDI-equivalent  |
| brand / house   | `houseName`       | *all 13 houses* (no filter)                    | per scope decision; accept future overlap if a brand gets its own scraper (e.g. Gucci, Saint Laurent) |

Pagination upper bound: read from `sections[0].props.totalPages` on page 1,
capped by `MAX_PAGES = 200` defensively.
