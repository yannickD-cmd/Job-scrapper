# Accor scraper — filter map

ATS: **Attrax** (careers.accor.com, Azure Front Door CDN). Listing endpoint:

```
https://careers.accor.com/global/en/jobs?options=<facetIds>&page=<n>&ln=&la=0&lo=0&lr=1&li=
```

`options` = comma list of Attrax facet-value IDs. **OR within a facet group, AND
across groups.** IDs are read from each filter checkbox's `data-option-id` /
`addFilterOptionId(<id>)` handler on the jobs page.

| Axis | Value kept | Facet ID | Applied where |
|---|---|---|---|
| Country | France | `405` | server-side (`options`), both passes |
| Contract | Permanent (CDI) | `191` | server-side (`options`), both passes |
| Category | Tech & Digital | `1343` | server-side, category pass |
| Category | Digital Products, IT, Data & Analytics | `271` | server-side, category pass |
| Category | Product Design, IT & Data Analysis | `259` | server-side, category pass |
| Data-adjacent | data / AI titles in *any* family | — | `q=data\|analytics\|intelligence artificielle` + `_is_data_ai_title()` title gate |

## Why two passes

Accor files virtually all Data/AI roles into the three tech categories above, so
the **category pass** (`options=405,191,1343,271,259`) is the core — kept wholesale,
no title filter (the user chose these families deliberately). It returns a stable
28 rows (`tiles/page = [12,12,4,0]`, identical every run).

The **data-adjacent pass** exists because the odd data role gets tagged to a
non-tech family (verified: `Sustainable Performance Data Analyst` sits under
*Corporate Social Responsibility*, and gender-notation variants of a data role can
split across categories). It re-queries France+Permanent with data/AI keywords and
keeps only rows whose **title** passes `_is_data_ai_title()` — so it recovers the
strays without dragging in the rest of the corporate feed.

## Do not do a full unfiltered crawl

`options=405,191` alone (no category/keyword) is **unstable**: it shifts page count
run-to-run (38↔58 pages), overlaps heavily, and silently caps ~300 unique — dropping
jobs a facet query proves exist. Only the narrow facet/keyword queries are stable and
complete. The scraper never enumerates the unfiltered set.

## Category / contract are in the listing tile

Each `div.attrax-vacancy-tile` already carries category, location, contract type and
the Attrax GUID (`__reference-value`). The detail page is fetched **only** for
`description` + `posted_date` (schema.org/JobPosting JSON-LD).

Note: the tile's *displayed* `option-job-category` label is the job's primary
category and can differ from the matched facet (a job with facet `1343` may display
"Information Technology" or even "Rooms"). Filtering is by facet ID server-side, never
by the displayed label.
