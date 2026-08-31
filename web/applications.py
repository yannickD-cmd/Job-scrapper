"""Application-tracker reads for the "Candidatures" tab.

Deliberately separate from web/filters.py: that module carries the *offers*
logic (geographic tiers, date-churn boards, evergreen age rules) and has
nothing to say about a pipeline of candidatures. Mixing them would make two
unrelated domains share one blast radius.

This is the READ half. Writes live in web/tracker_write.py, which carries the
validation; nothing in this module inserts or updates. The tables are edited
from two places holding identical rights: the dashboard, by hand, and a Cowork
session running SQL in the Supabase editor when dozens need processing at once.

Independent of the scraper. `applications` used to hold a job_id FK into
`jobs`; it was dropped, because most openings are found on LinkedIn, WTTJ or by
referral, so a hard link modelled the exception as the rule. The offer is now
just a URL on the candidature, and no query here joins the two datasets.

Everything derived (staleness, counters, per-contact follow-up) already lives
in the three views declared in tracker/schema.sql - v_pipeline,
v_relance_queue, v_contact_followup. This module queries those rather than
re-deriving the same aggregates in Python, so the definition of "stale" or
"got a human reply" exists exactly once, in SQL.
"""
from __future__ import annotations

from datetime import date

# ---------------------------------------------------------------------------
# Status taxonomy. The ordering here is the ordering of the filter checkboxes.
#
# The distinction this whole tab exists to preserve is `rejected` vs `closed`.
#   rejected  a human read the application and said no      -> a verdict on me
#   closed    the requisition died under it (cancelled,      -> a verdict on
#             frozen, filled by someone else)                   nothing
# Folding them together inflates the apparent rejection rate with 4 outcomes
# that were never rejections (Airbus, Nestle, URW, papernest). They therefore
# get different labels, different colours, and separate stat tiles - never a
# shared "not going anywhere" bucket.
#
#   key -> (label, badge classes, tooltip)
# ---------------------------------------------------------------------------
STATUS_META: dict[str, tuple[str, str, str]] = {
    "draft": (
        "Brouillon",
        "bg-amber-500/15 text-amber-300 border-amber-500/40",
        "Candidature commencée et jamais finalisée. C'est une action à faire, "
        "pas un état mort.",
    ),
    "applied": (
        "Envoyée",
        "bg-zinc-700/40 text-zinc-200 border-zinc-600",
        "Soumise, aucun accusé de réception reçu.",
    ),
    "acked": (
        "Accusé de réception",
        "bg-sky-500/15 text-sky-300 border-sky-500/40",
        "Accusé de réception de l'ATS uniquement, aucun humain.",
    ),
    "screening": (
        "Préqualification",
        "bg-indigo-500/15 text-indigo-300 border-indigo-500/40",
        "Un humain a répondu, ou un test / questionnaire en ligne a été envoyé.",
    ),
    "interview": (
        "Entretien",
        "bg-violet-500/15 text-violet-300 border-violet-500/40",
        "Entretien proposé, planifié ou passé.",
    ),
    "final": (
        "Dernier tour",
        "bg-fuchsia-500/15 text-fuchsia-300 border-fuchsia-500/40",
        "Dernier tour, prise de références, ou discussion d'offre.",
    ),
    "offer": (
        "Offre",
        "bg-emerald-500/20 text-emerald-300 border-emerald-500/50",
        "Offre écrite reçue.",
    ),
    "rejected": (
        "Refusée",
        "bg-rose-500/15 text-rose-300 border-rose-500/40",
        "Ils ont explicitement dit non. C'est un jugement sur la candidature.",
    ),
    "closed": (
        "Req. fermée",
        "bg-slate-500/15 text-slate-300 border-slate-500/40",
        "La réquisition est morte (annulée, gelée, pourvue par quelqu'un "
        "d'autre). Ce n'est PAS un refus : rien n'a été jugé.",
    ),
    "ghosted": (
        "Sans réponse",
        "bg-stone-600/25 text-stone-300 border-stone-600/50",
        "Aucun signal depuis 30 jours après la dernière touche.",
    ),
    "withdrawn": (
        "Retirée",
        "bg-zinc-800 text-zinc-400 border-zinc-700",
        "Retrait volontaire.",
    ),
}

# The funnel states where the application is still moving. This exact set is
# what v_relance_queue filters on in tracker/schema.sql - keep them in step.
# `offer` is deliberately NOT here: it is a terminal *win*, not something to
# chase, and counting it as "vivante" would make the relance queue nag about
# an application that already landed.
LIVE_STATUSES: frozenset[str] = frozenset(
    {"applied", "acked", "screening", "interview", "final"}
)

# Sort groups for the default ordering. Live first (that is the working list),
# then an offer, then drafts - which are an action to take, so they must not
# sink to the bottom among the dead - then everything terminal.
_LIVE_RANK, _OFFER_RANK, _DRAFT_RANK, _DEAD_RANK = 0, 1, 2, 3

# Default staleness threshold for "a relancer", matching v_relance_queue.
DEFAULT_STALE_DAYS = 7
STALE_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "Relance : toutes"),
    ("7", "À relancer (≥ 7 j)"),
    ("14", "À relancer (≥ 14 j)"),
    ("30", "À relancer (≥ 30 j)"),
)
# The route validates against this set rather than str.isdigit(), for the same
# reason the offers tab checks `region not in REGION_KEYS`: a value must be one
# the dropdown can echo back. isdigit() would accept "3" — filtering the table
# while the select still reads "Relance : toutes" — and also accepts superscript
# digits like "²", which int() then refuses with a ValueError and a 500.
STALE_VALUES: frozenset[str] = frozenset(v for v, _ in STALE_CHOICES if v)

# Contact address states worth shouting about in the UI. `bounced` above all:
# it means the outreach never reached a human at all, so a silent contact is
# not a snub, it is an address that does not exist.
EMAIL_STATUS_META: dict[str, tuple[str, str, str]] = {
    "unknown": ("", "", ""),
    "valid": (
        "OK", "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
        "Adresse confirmée : un envoi a atteint la boîte.",
    ),
    "bounced": (
        "BOUNCED", "bg-rose-500/20 text-rose-300 border-rose-500/50",
        "Le mail n'a jamais atteint un humain. Le silence de ce contact ne "
        "veut rien dire : l'adresse est fausse.",
    ),
    "auto_reply_only": (
        "AUTO-REPLY", "bg-amber-500/15 text-amber-300 border-amber-500/40",
        "Seules des réponses automatiques sont revenues de cette adresse.",
    ),
    "left_company": (
        "A QUITTÉ", "bg-zinc-700/50 text-zinc-400 border-zinc-600",
        "La personne a quitté l'entreprise.",
    ),
}

# Touch kinds, for the timeline. Anything unmapped falls back to the raw value.
KIND_LABELS: dict[str, str] = {
    "cold_email": "Cold email",
    "relance": "Relance",
    "ats_ack": "Accusé ATS",
    "ats_reject": "Refus ATS",
    "human_reply": "Réponse humaine",
    "auto_reply": "Réponse automatique",
    "bounce": "Bounce",
    "interview_invite": "Invitation entretien",
    "assessment": "Test / évaluation",
    "other": "Autre",
}

# One row per application: the pipeline view, the underlying row for the fields
# the view does not carry, and a per-application rollup of the contact view.
#
# The contact rollup counts (application, contact) pairs, not humans: the same
# recruiter contacted about two openings is two outreach attempts and should
# count twice, which is also how the response-rate denominator is defined.
#
# `contacts_no_reply` requires emails_sent > 0 on purpose. A contact who was
# never written to has not failed to answer - they were never asked - so
# counting them would turn "outreach that fell flat" into "contacts I have not
# used yet", which is a different question.
_PIPELINE_SQL = """
    SELECT p.id, p.company, p.role, a.req_ref, p.applied_on, p.status,
           p.close_reason, p.last_touch_on, p.days_stale, p.emails_sent,
           p.human_replies, p.contact_count, a.apply_url,
           a.source, a.notes, a.status_since,
           COALESCE(f.contacts_no_reply, 0),
           COALESCE(f.contacts_replied, 0),
           COALESCE(f.pending_drafts, 0),
           COALESCE(f.bounced_contacts, 0)
    FROM v_pipeline p
    JOIN applications a ON a.id = p.id
    LEFT JOIN (
        SELECT application_id,
               count(*) FILTER (WHERE emails_sent > 0
                                  AND NOT got_human_reply) AS contacts_no_reply,
               count(*) FILTER (WHERE got_human_reply)      AS contacts_replied,
               count(*) FILTER (WHERE has_pending_draft)    AS pending_drafts,
               count(*) FILTER (WHERE email_status = 'bounced') AS bounced_contacts
        FROM v_contact_followup
        GROUP BY application_id
    ) f ON f.application_id = p.id
"""


def _sort_rank(status: str) -> int:
    if status in LIVE_STATUSES:
        return _LIVE_RANK
    if status == "offer":
        return _OFFER_RANK
    if status == "draft":
        return _DRAFT_RANK
    return _DEAD_RANK


def fetch_applications(cur) -> list[dict]:
    """Every application, already sorted the way the page renders them.

    Default order: live applications first, most stale first - that is the
    working list, the one that answers "who have I not heard from the
    longest". Then the offer, then drafts, then everything terminal. Within
    the terminal block, most recently applied first, since an old rejection is
    not more interesting than a fresh one.

    NULL days_stale (no touches and no applied_on) sorts as -1 rather than
    being dropped: an unknown staleness is not an urgent one.
    """
    cur.execute(_PIPELINE_SQL)
    rows = [
        {
            "id": r[0],
            "company": r[1],
            "role": r[2],
            "req_ref": r[3],
            "applied_on": r[4],
            "status": r[5],
            "close_reason": r[6],
            "last_touch_on": r[7],
            "days_stale": r[8],
            "emails_sent": r[9],
            "human_replies": r[10],
            "contact_count": r[11],
            "apply_url": r[12],
            "source": r[13],
            "notes": r[14],
            "status_since": r[15],
            "contacts_no_reply": r[16],
            "contacts_replied": r[17],
            "pending_drafts": r[18],
            "bounced_contacts": r[19],
            "rank": _sort_rank(r[5]),
            "is_live": r[5] in LIVE_STATUSES,
        }
        for r in cur.fetchall()
    ]
    def order(a: dict) -> tuple:
        # Staleness only ranks the live block. Applying it everywhere would
        # order the terminal block oldest-first — surfacing a March rejection
        # above last week's — so below the live block the key is the
        # application date, newest first.
        stale = a["days_stale"] if a["days_stale"] is not None else -1
        recency = -(a["applied_on"].toordinal() if a["applied_on"] else 0)
        return (
            a["rank"],
            -stale if a["rank"] == _LIVE_RANK else recency,
            recency,
            a["company"].lower(),
        )

    rows.sort(key=order)
    return rows


def compute_stats(rows: list[dict], contact_totals: tuple[int, int]) -> dict:
    """Bandeau numbers, computed over the WHOLE tracker, not the filtered list.

    Same convention as the offers tab, whose stats describe the universe while
    the result counter under the filter bar describes the selection. A stat
    that moved every time a filter changed would stop being a stat.

    `rejected` and `closed` are counted apart and never summed: see
    STATUS_META. The response rate is per outreach attempt - (application,
    contact) pairs - which is what `contact_totals` carries.
    """
    replied, total_contacts = contact_totals
    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    return {
        "total": len(rows),
        "live": sum(1 for r in rows if r["is_live"]),
        "rejected": by_status.get("rejected", 0),
        "ghosted": by_status.get("ghosted", 0),
        "closed": by_status.get("closed", 0),
        "draft": by_status.get("draft", 0),
        "offer": by_status.get("offer", 0),
        "withdrawn": by_status.get("withdrawn", 0),
        "contacts_total": total_contacts,
        "contacts_replied": replied,
        "reply_rate": (100.0 * replied / total_contacts) if total_contacts else None,
        "by_status": by_status,
    }


def fetch_contact_totals(cur) -> tuple[int, int]:
    """(outreach attempts that got a human reply, total outreach attempts)."""
    cur.execute(
        "SELECT count(*) FILTER (WHERE got_human_reply), count(*) "
        "FROM v_contact_followup"
    )
    row = cur.fetchone()
    return (row[0] or 0, row[1] or 0)


def apply_filters(
    rows: list[dict],
    *,
    statuses: list[str],
    company: str,
    date_from: date | None,
    date_to: date | None,
    stale_days: int | None,
    awaiting_reply: bool,
) -> list[dict]:
    """Narrow the list in Python.

    93 rows is small enough that filtering here beats building the predicates
    into SQL: the stats above stay computed on the untouched universe from the
    same single query, so the two can never disagree about what the data is.

    Both date bounds are inclusive and apply to `applied_on`. A row with no
    applied_on drops out of a bounded range, same rule as the offers tab:
    asking for a period is asking for rows that have a date in it.
    """
    out = rows
    if statuses:
        wanted = set(statuses)
        out = [r for r in out if r["status"] in wanted]
    if company:
        out = [r for r in out if r["company"] == company]
    if date_from:
        out = [r for r in out if r["applied_on"] and r["applied_on"] >= date_from]
    if date_to:
        out = [r for r in out if r["applied_on"] and r["applied_on"] <= date_to]
    if stale_days is not None:
        # "A relancer" is only meaningful for an application still in play -
        # chasing a rejection is not a follow-up. Same predicate as
        # v_relance_queue: live AND days_stale >= N.
        out = [
            r for r in out
            if r["is_live"]
            and r["days_stale"] is not None
            and r["days_stale"] >= stale_days
        ]
    if awaiting_reply:
        out = [r for r in out if r["contacts_no_reply"] > 0]
    return out


def fetch_application(cur, application_id: int) -> dict | None:
    """Header block for the detail panel."""
    cur.execute(
        "SELECT a.id, a.company, a.role, a.req_ref, a.apply_url, a.source, "
        "       a.applied_on, a.resume_url, a.status, a.status_since, "
        "       a.close_reason, a.notes "
        "FROM applications a WHERE a.id = %s",
        (application_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    label, badge, tip = STATUS_META.get(row[8], (row[8], "", ""))
    return {
        "id": row[0],
        "company": row[1],
        "role": row[2],
        "req_ref": row[3],
        # The offer is a URL you pasted, nothing more. No join to `jobs`:
        # most openings are found on LinkedIn, WTTJ or by referral, so tying a
        # candidature to a scraped row modelled the exception as the rule.
        "apply_url": row[4],
        "source": row[5],
        "applied_on": row[6].isoformat() if row[6] else None,
        "resume_url": row[7],
        "status": row[8],
        "status_label": label,
        "status_badge": badge,
        "status_tip": tip,
        "status_since": row[9].isoformat() if row[9] else None,
        "close_reason": row[10],
        "notes": row[11],
    }


def fetch_contacts(cur, application_id: int) -> list[dict]:
    """The contacts panel, straight out of v_contact_followup.

    Ordered so the ones that need a decision surface first: a pending draft
    (written, not sent), then whoever has been silent longest.
    """
    cur.execute(
        "SELECT contact_id, full_name, email, title, email_status, "
        "       role_in_process, first_contacted_on, last_contacted_on, "
        "       emails_sent, relances_sent, got_human_reply, replied_on, "
        "       has_pending_draft, days_since_last_contact "
        "FROM v_contact_followup WHERE application_id = %s",
        (application_id,),
    )
    contacts = []
    for r in cur.fetchall():
        badge, badge_class, badge_tip = EMAIL_STATUS_META.get(
            r[4], (r[4].upper(), "bg-zinc-800 text-zinc-400 border-zinc-700", "")
        )
        contacts.append({
            "contact_id": r[0],
            "full_name": r[1],
            "email": r[2],
            "title": r[3],
            "email_status": r[4],
            "email_badge": badge,
            "email_badge_class": badge_class,
            "email_badge_tip": badge_tip,
            "role_in_process": r[5],
            "first_contacted_on": r[6].isoformat() if r[6] else None,
            "last_contacted_on": r[7].isoformat() if r[7] else None,
            "emails_sent": r[8],
            "relances_sent": r[9],
            "got_human_reply": r[10],
            "replied_on": r[11].isoformat() if r[11] else None,
            "has_pending_draft": r[12],
            "days_since_last_contact": r[13],
        })
    contacts.sort(
        key=lambda c: (
            not c["has_pending_draft"],
            -(c["days_since_last_contact"] or 0),
            (c["full_name"] or "").lower(),
        )
    )
    return contacts


def fetch_timeline(cur, application_id: int) -> list[dict]:
    """Every touch on the application, most recent first.

    Ties on occurred_on break on id descending, so two events recorded for the
    same day keep the order the sweep wrote them in rather than shuffling
    between requests.
    """
    cur.execute(
        "SELECT t.id, t.direction, t.channel, t.occurred_on, t.subject, "
        "       t.excerpt, t.kind, t.state, t.due_on, c.full_name, c.email "
        "FROM touches t "
        "LEFT JOIN contacts c ON c.id = t.contact_id "
        "WHERE t.application_id = %s "
        "ORDER BY t.occurred_on DESC, t.id DESC",
        (application_id,),
    )
    return [
        {
            "id": r[0],
            "direction": r[1],
            "channel": r[2],
            "occurred_on": r[3].isoformat() if r[3] else None,
            "subject": r[4],
            "excerpt": r[5],
            "kind": r[6],
            "kind_label": KIND_LABELS.get(r[6] or "", r[6] or ""),
            "state": r[7],
            "due_on": r[8].isoformat() if r[8] else None,
            "contact_name": r[9],
            "contact_email": r[10],
        }
        for r in cur.fetchall()
    ]
