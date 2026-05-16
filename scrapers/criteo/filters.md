# Criteo scraper — filters

| axis             | value                                          | applied where         |
|------------------|------------------------------------------------|-----------------------|
| Country          | France                                         | listing pass (cards)  |
| Team / category  | Engineering, Internal IT, Analytics, Product   | listing pass (cards)  |
| Hiring type      | Regular / Permanent (drop Intern + FTC)        | detail pass (HTML)    |

Note: the team labels on the Happydance cards differ from the Workday
`Job_Category` facet (Engineering ≠ R&D, Internal IT ≠ IT). The card labels
are authoritative here because they're what the scraper actually parses.

## Why this shape

Criteo's career site is `careers.criteo.com`, a Happydance front-end on top of
a Workday ATS (`criteo.wd3.myworkdayjobs.com/Criteo_Career_Site`). Both expose
the same 138 listings.

Workday's JSON API (`/wday/cxs/criteo/Criteo_Career_Site/jobs`) accepts the
empty-facets POST and returns the full list, but every filtered POST we
tried (`Country`, `locationCountry`, `country`, plus `Hiring_Type`,
`Job_Category`) is rejected with HTTP 400 / empty body — even after the
Cloudflare cooldown window. The tenant evidently uses a non-standard facet
key. Rather than burn more probes guessing it, we drive from the Happydance
HTML, which is server-rendered and has team + locations on the card and
hiring type on the detail page.

The Workday detail JSON has `startDate` but is reached by `externalPath`
(e.g. `/job/Paris/Senior-Engineer_r12345`), which we don't get from the
Happydance card. We could derive it from the Workday apply URL on the
Happydance detail page, but the marginal value (precise posted_date vs.
"today minus N days") doesn't justify a second per-job request. We parse
"Posted N days ago" off the card instead.
