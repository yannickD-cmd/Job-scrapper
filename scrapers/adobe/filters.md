# Adobe — filters in scope

| Axis | Kept value | Applied where |
|---|---|---|
| Country | **France** | server (query param `qcountry=France`) — soft, radius-based |
| Country | **France** (primary OR any multi-location) | client (`country == "France"` OR `isMultiLocation && any multi_location tail == "France"`) |

No category / role-family filter — Adobe's France footprint is tiny (~18 open roles), so we keep every category and let downstream signal pick winners.

The Phenom search endpoint treats `qcountry/qcity/qstate` as a *center* with a 305-mile radius (see `locationData.sliderRadius` in the response). The "leaks" from London / Reading / Amsterdam are not really leaks — they are pan-EMEA roles whose `multi_location` lists Paris as one of the acceptable work sites, so we keep them. We only drop a row if it has no France site at all.

**Source URL**: `https://careers.adobe.com/us/en/search-results?qcountry=France`
**API**: `GET https://content-us.phenompeople.com/api/ADOBUS/eagerLoadRefineSearch` (Phenom tenant `ADOBUS`)
**Scraper**: [scrapers/adobe/adobe.py](adobe.py) — see `COUNTRY` constant to change.
