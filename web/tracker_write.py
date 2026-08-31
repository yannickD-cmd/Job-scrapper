"""Write operations for the Candidatures tab — the "advanced sheet" half.

Everything here exists so the dashboard can do, by hand, exactly what a Cowork
mailbox sweep does in bulk: add a candidature, move a status, record a person
you contacted, log a mail. Cowork holds no privilege this module does not —
it is the same four tables and the same rules, just executed faster.

Separate from web/applications.py, which stays the read side (queries, status
taxonomy, filters). Reads are hot on every page load; writes are rare, and they
carry all the validation. Keeping them apart means the read path never pays for
the write path's checks.

No link to the scraper. The tracker used to carry `applications.job_id` into
`jobs`, which was wrong: most openings are found on LinkedIn, WTTJ, by referral
or by word of mouth, so tying a candidature to a scraped row modelled the
exception as the rule. The two datasets are now independent — the offer is just
a URL you paste.

Validation policy: every value is checked against the CHECK constraints
declared in tracker/schema.sql before it reaches Postgres, so a bad input comes
back as a 400 naming the field instead of a 500 carrying a raw driver error.
Column names are never taken from the request — each writer holds an explicit
allow-list, so an unexpected key is ignored rather than interpolated into SQL.
"""
from __future__ import annotations

from datetime import date

from web.applications import STATUS_META

# Mirrors of the CHECK constraints in tracker/schema.sql. If a constraint moves
# there, it moves here — a mismatch shows up as a 500 from Postgres instead of
# a clean 400, which is the whole thing this module exists to avoid.
STATUSES: frozenset[str] = frozenset(STATUS_META)
EMAIL_STATUSES: frozenset[str] = frozenset(
    {"unknown", "valid", "bounced", "auto_reply_only", "left_company"}
)
DIRECTIONS: frozenset[str] = frozenset({"out", "in"})
CHANNELS: frozenset[str] = frozenset({"email", "linkedin", "phone", "other"})
TOUCH_STATES: frozenset[str] = frozenset({"draft", "sent"})

# Free-text in the schema (no CHECK), but the UI offers a fixed list so the data
# stays groupable. Anything else is still accepted — the point is to make the
# common values one click away, not to police a personal tracker.
SOURCES: tuple[str, ...] = (
    "career_site", "linkedin", "wttj", "indeed", "referral", "cold_email", "other",
)
CLOSE_REASONS: tuple[str, ...] = (
    "their_no", "req_cancelled", "req_frozen", "filled", "seniority", "my_choice",
)
ROLES_IN_PROCESS: tuple[str, ...] = (
    "recruiter", "hiring_manager", "team_member", "referral", "unknown",
)
TOUCH_KINDS: tuple[str, ...] = (
    "cold_email", "relance", "ats_ack", "ats_reject", "human_reply",
    "auto_reply", "bounce", "interview_invite", "assessment", "other",
)

# Statuses that describe an ending. close_reason is only meaningful on these;
# on anything else it is cleared, so a row cannot claim "rejected because
# req_frozen" after being moved back into the funnel.
TERMINAL_STATUSES: frozenset[str] = frozenset({"rejected", "closed", "withdrawn"})


class ValidationError(ValueError):
    """Bad input from the form. Surfaces as HTTP 400 with this message."""


class ConflictError(ValueError):
    """Collides with a UNIQUE index. Surfaces as HTTP 409."""


# --- coercion helpers ------------------------------------------------------
# Forms post strings; the DB wants typed values or NULL. Empty string means
# "not filled in", which is NULL — never the literal "".

def _text(payload: dict, field: str, *, required: bool = False,
          max_len: int = 2000) -> str | None:
    raw = payload.get(field)
    value = ("" if raw is None else str(raw)).strip()
    if not value:
        if required:
            raise ValidationError(f"Le champ « {field} » est obligatoire.")
        return None
    if len(value) > max_len:
        raise ValidationError(
            f"Le champ « {field} » dépasse {max_len} caractères."
        )
    return value


def _enum(payload: dict, field: str, allowed, *, required: bool = False,
          default: str | None = None) -> str | None:
    value = _text(payload, field)
    if value is None:
        if required and default is None:
            raise ValidationError(f"Le champ « {field} » est obligatoire.")
        return default
    if value not in allowed:
        raise ValidationError(
            f"Valeur « {value} » invalide pour « {field} ». "
            f"Attendu : {', '.join(sorted(allowed))}."
        )
    return value


def _date(payload: dict, field: str, *, required: bool = False) -> date | None:
    value = _text(payload, field)
    if value is None:
        if required:
            raise ValidationError(f"Le champ « {field} » est obligatoire.")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationError(
            f"Le champ « {field} » doit être une date AAAA-MM-JJ (reçu « {value} »)."
        ) from None


def _is_unique_violation(exc: Exception, index: str) -> bool:
    """psycopg raises UniqueViolation; we only want to translate OUR indexes."""
    return getattr(exc, "sqlstate", None) == "23505" and index in str(exc)


# --- applications ----------------------------------------------------------

def create_application(cur, payload: dict) -> int:
    """Insert one candidature. Returns its id.

    company and role are the only required fields: an offer you just spotted is
    worth recording before you know the ATS reference or even the date. Status
    defaults to 'applied' as the schema does, but the form offers 'draft' for
    exactly that case — spotted, not sent.
    """
    company = _text(payload, "company", required=True, max_len=200)
    role = _text(payload, "role", required=True, max_len=300)
    status = _enum(payload, "status", STATUSES, default="applied")
    applied_on = _date(payload, "applied_on")
    close_reason = _text(payload, "close_reason", max_len=100)
    if status not in TERMINAL_STATUSES:
        close_reason = None

    # status_since answers "how long has it been in this state". On a fresh row
    # that is the application date when we have one, today otherwise.
    status_since = _date(payload, "status_since") or applied_on or date.today()

    try:
        cur.execute(
            "INSERT INTO applications "
            "  (company, role, req_ref, apply_url, source, applied_on, "
            "   resume_url, status, status_since, close_reason, notes, "
            "   description) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (
                company,
                role,
                _text(payload, "req_ref", max_len=100),
                _text(payload, "apply_url", max_len=1000),
                _text(payload, "source", max_len=50),
                applied_on,
                _text(payload, "resume_url", max_len=1000),
                status,
                status_since,
                close_reason,
                _text(payload, "notes", max_len=_MAX_LEN["notes"]),
                _text(payload, "description", max_len=_MAX_LEN["description"]),
            ),
        )
        return cur.fetchone()[0]
    except Exception as exc:
        # uq_applications_natural is (company, role, COALESCE(applied_on,...)).
        # Re-applying to the same role on a different date is legitimate and
        # allowed; the same date twice is a double submit.
        if _is_unique_violation(exc, "uq_applications_natural"):
            raise ConflictError(
                f"Une candidature « {role} » chez {company} existe déjà à cette "
                f"date. Change la date de candidature pour en enregistrer une "
                f"seconde."
            ) from None
        raise


# Only these may be written, and only through this map — the request never
# names a column.
_APPLICATION_FIELDS: dict[str, str] = {
    "company": "text", "role": "text", "req_ref": "text", "apply_url": "text",
    "source": "text", "resume_url": "text", "notes": "text",
    "description": "text", "close_reason": "text", "status": "status",
    "applied_on": "date", "status_since": "date",
}

# `description` is a snapshot of the offer text, kept because boards take the
# posting down while the process is still running — when a recruiter calls back
# three weeks later, the original ad is gone from the internet but not from
# here. It is pasted in, never fetched: the two datasets are decoupled
# (see the module docstring), and a long ad easily runs past the default cap.
_MAX_LEN: dict[str, int] = {"description": 100_000, "notes": 5_000}
_DEFAULT_MAX_LEN = 2_000


def update_application(cur, application_id: int, payload: dict) -> bool:
    """Patch the fields present in `payload`. Absent keys are left alone.

    Sending only {"status": "..."} is the inline status change from the list.
    """
    cur.execute("SELECT status FROM applications WHERE id = %s", (application_id,))
    row = cur.fetchone()
    if not row:
        return False
    current_status = row[0]

    sets: list[str] = []
    params: list = []
    for field, kind in _APPLICATION_FIELDS.items():
        if field not in payload:
            continue
        if kind == "date":
            value = _date(payload, field)
        elif kind == "status":
            value = _enum(payload, field, STATUSES, required=True)
        else:
            value = _text(payload, field,
                          max_len=_MAX_LEN.get(field, _DEFAULT_MAX_LEN))
        if field in ("company", "role") and value is None:
            raise ValidationError(f"Le champ « {field} » ne peut pas être vidé.")
        sets.append(f"{field} = %s")
        params.append(value)

    new_status = payload.get("status", current_status)
    if "status" in payload and new_status != current_status:
        # Moving the status restarts its clock, unless the caller set the date
        # explicitly in the same request.
        if "status_since" not in payload:
            sets.append("status_since = %s")
            params.append(date.today())
        # Leaving a terminal state must not keep the reason it ended.
        if new_status not in TERMINAL_STATUSES and "close_reason" not in payload:
            sets.append("close_reason = NULL")

    if not sets:
        return True  # nothing to do is not an error

    sets.append("updated_at = NOW()")
    params.append(application_id)
    try:
        cur.execute(
            f"UPDATE applications SET {', '.join(sets)} WHERE id = %s", params
        )
    except Exception as exc:
        if _is_unique_violation(exc, "uq_applications_natural"):
            raise ConflictError(
                "Une autre candidature a déjà cette société, ce poste et cette date."
            ) from None
        raise
    return cur.rowcount > 0


# There is deliberately NO delete_application. A candidature that went nowhere
# is set to 'rejected' / 'closed' / 'withdrawn' and filtered out of the view —
# it is never destroyed. Keeping the row is what makes the rejection rate, the
# response rate and the history of a company mean anything a year later.
#
# The two deletions that DO exist below (detach a contact, remove a touch) undo
# a mis-entry rather than a candidature: a wrongly logged touch would otherwise
# skew days_stale for good, and nothing else can correct it.


# --- contacts --------------------------------------------------------------

def attach_contact(cur, application_id: int, payload: dict) -> tuple[int, bool]:
    """Link a person to a candidature. Returns (contact_id, created).

    Find-or-create on the email, because `uq_contacts_email` makes the address
    the identity of a person and the same recruiter turns up on several
    openings. Re-entering someone already on file updates the blanks on their
    record rather than failing — that is what a sheet would do.
    """
    email = _text(payload, "email", max_len=320)
    full_name = _text(payload, "full_name", required=True, max_len=200)

    contact_id = None
    if email:
        cur.execute("SELECT id FROM contacts WHERE lower(email) = lower(%s)", (email,))
        found = cur.fetchone()
        contact_id = found[0] if found else None

    created = contact_id is None
    if created:
        cur.execute(
            "INSERT INTO contacts (full_name, email, company, title, "
            "                      linkedin_url, email_status, notes) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (
                full_name,
                email,
                _text(payload, "company", max_len=200),
                _text(payload, "title", max_len=200),
                _text(payload, "linkedin_url", max_len=500),
                _enum(payload, "email_status", EMAIL_STATUSES, default="unknown"),
                _text(payload, "notes", max_len=2000),
            ),
        )
        contact_id = cur.fetchone()[0]
    else:
        # COALESCE so re-adding a known person fills gaps without wiping what
        # the mailbox sweep already learned about them.
        cur.execute(
            "UPDATE contacts SET "
            "  full_name = %s, "
            "  company = COALESCE(%s, company), "
            "  title = COALESCE(%s, title), "
            "  linkedin_url = COALESCE(%s, linkedin_url) "
            "WHERE id = %s",
            (
                full_name,
                _text(payload, "company", max_len=200),
                _text(payload, "title", max_len=200),
                _text(payload, "linkedin_url", max_len=500),
                contact_id,
            ),
        )

    cur.execute(
        "INSERT INTO application_contacts (application_id, contact_id, role_in_process) "
        "VALUES (%s,%s,%s) "
        "ON CONFLICT (application_id, contact_id) DO UPDATE "
        "  SET role_in_process = COALESCE(EXCLUDED.role_in_process, "
        "                                 application_contacts.role_in_process)",
        (application_id, contact_id, _text(payload, "role_in_process", max_len=50)),
    )
    return contact_id, created


def detach_contact(cur, application_id: int, contact_id: int) -> bool:
    """Unlink a person from one candidature. The contact record is kept."""
    cur.execute(
        "DELETE FROM application_contacts "
        "WHERE application_id = %s AND contact_id = %s",
        (application_id, contact_id),
    )
    return cur.rowcount > 0


_CONTACT_FIELDS: dict[str, str] = {
    "full_name": "text", "email": "text", "company": "text", "title": "text",
    "linkedin_url": "text", "notes": "text", "email_status": "email_status",
}


def update_contact(cur, contact_id: int, payload: dict) -> bool:
    """Patch a person. The field that earns its keep here is `email_status`:
    marking an address `bounced` is what stops you reading their silence as a
    snub."""
    sets: list[str] = []
    params: list = []
    for field, kind in _CONTACT_FIELDS.items():
        if field not in payload:
            continue
        if kind == "email_status":
            value = _enum(payload, field, EMAIL_STATUSES, required=True)
        else:
            value = _text(payload, field, max_len=2000)
        if field == "full_name" and value is None:
            raise ValidationError("Le nom du contact ne peut pas être vidé.")
        sets.append(f"{field} = %s")
        params.append(value)

    if not sets:
        return True
    params.append(contact_id)
    try:
        cur.execute(f"UPDATE contacts SET {', '.join(sets)} WHERE id = %s", params)
    except Exception as exc:
        if _is_unique_violation(exc, "uq_contacts_email"):
            raise ConflictError("Cette adresse email est déjà sur un autre contact.") from None
        raise
    return cur.rowcount > 0


# --- touches ---------------------------------------------------------------

def create_touch(cur, application_id: int, payload: dict) -> int:
    """Record one exchange, in or out.

    `state` matters more than it looks: 'draft' is a mail written and NOT sent.
    v_pipeline ignores drafts when computing staleness, so logging a prepared
    relance does not make the candidature look freshly contacted.
    """
    contact_id = payload.get("contact_id")
    contact_id = int(contact_id) if contact_id not in (None, "", "null") else None
    if contact_id is not None:
        # The touch must belong to someone actually on this candidature,
        # otherwise v_contact_followup silently drops it.
        cur.execute(
            "SELECT 1 FROM application_contacts "
            "WHERE application_id = %s AND contact_id = %s",
            (application_id, contact_id),
        )
        if not cur.fetchone():
            raise ValidationError(
                "Ce contact n'est pas rattaché à cette candidature."
            )

    cur.execute(
        "INSERT INTO touches (application_id, contact_id, direction, channel, "
        "                     occurred_on, gmail_thread_id, subject, excerpt, "
        "                     kind, state, due_on) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (
            application_id,
            contact_id,
            _enum(payload, "direction", DIRECTIONS, required=True),
            _enum(payload, "channel", CHANNELS, default="email"),
            _date(payload, "occurred_on") or date.today(),
            _text(payload, "gmail_thread_id", max_len=200),
            _text(payload, "subject", max_len=500),
            _text(payload, "excerpt", max_len=5000),
            _text(payload, "kind", max_len=50),
            _enum(payload, "state", TOUCH_STATES, default="sent"),
            _date(payload, "due_on"),
        ),
    )
    return cur.fetchone()[0]


_TOUCH_FIELDS: dict[str, str] = {
    "subject": "text", "excerpt": "text", "kind": "text",
    "gmail_thread_id": "text",
    "direction": "direction", "channel": "channel", "state": "state",
    "occurred_on": "date", "due_on": "date",
}


def update_touch(cur, touch_id: int, payload: dict) -> bool:
    """Patch a touch. The common call is {"state": "sent"} — the draft went
    out, so it now counts against staleness."""
    sets: list[str] = []
    params: list = []
    for field, kind in _TOUCH_FIELDS.items():
        if field not in payload:
            continue
        if kind == "date":
            value = _date(payload, field)
            if field == "occurred_on" and value is None:
                raise ValidationError("La date de la touche est obligatoire.")
        elif kind == "direction":
            value = _enum(payload, field, DIRECTIONS, required=True)
        elif kind == "channel":
            value = _enum(payload, field, CHANNELS, required=True)
        elif kind == "state":
            value = _enum(payload, field, TOUCH_STATES, required=True)
        else:
            value = _text(payload, field, max_len=5000)
        sets.append(f"{field} = %s")
        params.append(value)

    if not sets:
        return True
    params.append(touch_id)
    cur.execute(f"UPDATE touches SET {', '.join(sets)} WHERE id = %s", params)
    return cur.rowcount > 0


def delete_touch(cur, touch_id: int) -> bool:
    cur.execute("DELETE FROM touches WHERE id = %s", (touch_id,))
    return cur.rowcount > 0
