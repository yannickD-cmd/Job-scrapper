# Voodoo — filters in scope

| Axis | Kept value | Applied where |
|---|---|---|
| Location | **Paris** | client (Lever `categories.location`) |
| Commitment | **Permanent** | client (Lever `categories.commitment`) |
| Team / Department | *all* | no filter — Paris office spans Gaming, BeReal, Engineering & Data, Apps, Strategy & Operations |

Out of scope on the same board: Internship postings, and every non-Paris office (Barcelona, London, Tokyo, Los Angeles, New York, Chicago, Amsterdam, Shanghai).

`workplaceType` (onsite / hybrid / remote) is **not** used for filtering — Paris-listed jobs mix all three and the careers UI also shows the full mix when "Paris" is selected.

**Source URL** (no auth, JSON, single response — Lever public posting API):
- `https://api.lever.co/v0/postings/voodoo?mode=json`

`mode=json` returns the full posting list in one response (~34 postings total board-wide as of 2026-05). No pagination logic needed.

**Native job id**: Lever's posting `id` (UUID) — stable per posting, used for the `(company, native_job_id)` unique key.

**Scraper**: [scrapers/voodoo/voodoo.py](voodoo.py) — see `LOCATIONS_IN_SCOPE`, `COMMITMENTS_IN_SCOPE` constants to change.
