"""Dashboard-side filters. Raw DB stores every scraped row; these predicates
narrow what the dashboard renders. Edit here to widen/narrow scope without
touching scrapers or schema.

There is no geographic filter any more. The dashboard used to hide everything
outside the petite couronne (75 / 92 / 93 / 94); it now renders every scraped
row whatever its location, and narrowing by city is done interactively through
the dashboard's location dropdown. The old `is_idf()` predicate and its commune
whitelist were removed in the same change (recoverable from git history).

`DATE_CHURN_COMPANIES` — boards whose `posted_date` is worthless (below).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Boards that rewrite the posting date on every crawl (SEO freshness churn).
#
# These sites stamp *every* live offer with today's date, so `posted_date` is
# not a date at all — it's the crawl timestamp wearing a date's clothes. On the
# dashboard that made every row look published today, floated them to the top
# of the sort, and permanently disabled the OPEN-N-MO / hide_old age rules.
#
# For these companies the dashboard uses `first_seen_at` as the base date: the
# first crawl that returned this native_job_id, i.e. the earliest moment we can
# prove the listing was live. It is a lower bound — a role already on the board
# when its scraper was written dates to the backfill run, not to its true
# publication — but unlike the upstream value it never moves.
#
# This does NOT touch the scrapers or the DB: the raw upstream date is still
# stored, so removing a company here restores the old display.
#
# Membership rule: ~every open row carries today's date, on every run.
#   Deloitte France — 80/80 rows stamped today (documented in the scraper).
#   Orange          — SEO-refreshed daily, see project_orange_dateposted_bogus.
# Reposts are unaffected: a real repost is the still_open FALSE->TRUE
# transition (reopened_at), which date churn cannot fake.
DATE_CHURN_COMPANIES: frozenset[str] = frozenset({
    "Deloitte France",
    "Orange",
})
