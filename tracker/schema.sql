-- ============================================================================
-- Application tracker schema.
--
-- Lives alongside the scraper tables (jobs, scraper_runs) in the same Supabase
-- Postgres. Idempotent: safe to re-run.
--
-- Why this exists: a spreadsheet cannot model one-to-many. One application has
-- N contacts and N email exchanges. That was the whole pain point.
--
--   applications          one row per submitted candidature
--   contacts              one row per human, reused across applications
--   application_contacts  N contacts on one application
--   touches               every email sent or received, in or out
--
-- No Gmail sync table on purpose: statuses are updated by hand, in batches,
-- from a Cowork mailbox sweep. See tracker/updates/README.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Status taxonomy.
--
-- Two axes were conflated in the old sheet: "where is this in the funnel" and
-- "who killed it". Splitting them matters: a cancelled req is not a rejection
-- of the candidate, and lumping them together makes the real rejection rate
-- look worse than it is (Airbus, Nestle, URW and papernest were all dead reqs).
--
--   draft      offer identified, not applied yet
--   applied    submitted, no acknowledgement received
--   acked      ATS acknowledgement only, no human
--   screening  a human replied, or an online test / questionnaire was sent
--   interview  interview proposed, scheduled or done
--   final      last round, references, or offer discussion
--   offer      written offer received
--   rejected   they explicitly said no to YOU
--   closed     the req died: cancelled, frozen, filled by someone else
--   ghosted    no signal for GHOST_AFTER_DAYS since the last touch
--   withdrawn  YOU pulled out
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS applications (
    id             BIGSERIAL PRIMARY KEY,
    -- No FK to jobs on purpose. The tracker is fully decoupled from the scraper:
    -- most applications come from LinkedIn, WTTJ or referrals and have no
    -- scraped row, and a hard link would rot as the jobs table churns.
    -- apply_url below carries the offer link, as plain text.
    company        TEXT NOT NULL,
    role           TEXT NOT NULL,
    -- ATS reference (2026-131466, R169232, JR10433316...). This is the join key
    -- the Gmail sync uses first, because ATS mails quote it verbatim.
    req_ref        TEXT,
    apply_url      TEXT,
    source         TEXT,                       -- career_site / linkedin / wttj / referral / cold_email
    applied_on     DATE,
    resume_url     TEXT,
    status         TEXT NOT NULL DEFAULT 'applied' CHECK (status IN (
                       'draft','applied','acked','screening','interview',
                       'final','offer','rejected','closed','ghosted','withdrawn')),
    status_since   DATE,
    -- Only meaningful for rejected / closed / withdrawn.
    close_reason   TEXT,                       -- their_no / req_cancelled / req_frozen / filled / seniority / my_choice
    notes          TEXT,
    -- Snapshot of the ad, pasted in when the candidature is recorded. Boards
    -- take a posting down while the process is still running, so by the time a
    -- recruiter calls back this is routinely the only copy left anywhere. Not
    -- fetched from `jobs`: the two datasets are decoupled, and most offers were
    -- never scraped in the first place.
    description    TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- A company can be applied to several times for different roles, and the same
-- role can be re-applied to later (Kering R162893 then R169232). The natural key
-- is company + role + applied_on.
CREATE UNIQUE INDEX IF NOT EXISTS uq_applications_natural
    ON applications (company, role, COALESCE(applied_on, DATE '1900-01-01'));
CREATE INDEX IF NOT EXISTS idx_applications_status  ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_company ON applications(company);
CREATE INDEX IF NOT EXISTS idx_applications_req_ref ON applications(req_ref);

CREATE TABLE IF NOT EXISTS contacts (
    id            BIGSERIAL PRIMARY KEY,
    full_name     TEXT NOT NULL,
    email         TEXT,
    company       TEXT,
    title         TEXT,
    linkedin_url  TEXT,
    -- Learned from the mailbox. 'bounced' and 'left_company' are worth storing:
    -- 5 of 43 outreach attempts in the first audit never reached a human at all,
    -- and that is invisible unless you record it.
    email_status  TEXT NOT NULL DEFAULT 'unknown' CHECK (email_status IN (
                      'unknown','valid','bounced','auto_reply_only','left_company')),
    notes         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_contacts_email
    ON contacts (lower(email)) WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company);

CREATE TABLE IF NOT EXISTS application_contacts (
    application_id  BIGINT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    contact_id      BIGINT NOT NULL REFERENCES contacts(id)     ON DELETE CASCADE,
    role_in_process TEXT,   -- recruiter / hiring_manager / team_member / referral / unknown
    PRIMARY KEY (application_id, contact_id)
);

-- Every interaction, in or out. This is the table the spreadsheet could not be.
CREATE TABLE IF NOT EXISTS touches (
    id               BIGSERIAL PRIMARY KEY,
    application_id   BIGINT REFERENCES applications(id) ON DELETE CASCADE,
    contact_id       BIGINT REFERENCES contacts(id)     ON DELETE SET NULL,
    direction        TEXT NOT NULL CHECK (direction IN ('out','in')),
    channel          TEXT NOT NULL DEFAULT 'email' CHECK (channel IN ('email','linkedin','phone','other')),
    occurred_on      DATE NOT NULL,
    gmail_thread_id  TEXT,
    subject          TEXT,
    excerpt          TEXT,
    kind             TEXT,   -- cold_email / relance / ats_ack / ats_reject / human_reply
                             -- auto_reply / bounce / interview_invite / assessment / other
    -- 'draft' = a Gmail draft exists but Yannick has not sent it yet. Cowork
    -- writes drafts into Gmail AND a matching row here, so a planned relance is
    -- visible in the dashboard before it goes out. Flipped to 'sent' by hand,
    -- or by the next mailbox sweep.
    state            TEXT NOT NULL DEFAULT 'sent' CHECK (state IN ('draft','sent')),
    -- For a relance that is planned but not yet drafted.
    due_on           DATE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_touches_app     ON touches(application_id, occurred_on DESC);
CREATE INDEX IF NOT EXISTS idx_touches_contact ON touches(contact_id, occurred_on DESC);
CREATE INDEX IF NOT EXISTS idx_touches_thread  ON touches(gmail_thread_id);

-- ---------------------------------------------------------------------------
-- Pipeline view: what is alive, how stale, and who owes the next move.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_pipeline AS
SELECT
    a.id,
    a.company,
    a.role,
    a.status,
    a.applied_on,
    a.close_reason,
    -- state = 'sent' everywhere below, matching v_contact_followup.
    --
    -- A draft is a mail written in Gmail and NOT sent, so it is not a signal
    -- and must not move the silence clock. Without this filter, drafting a
    -- relance stamps today's date on the application, days_stale drops to 0,
    -- and the row falls out of v_relance_queue and out of the dashboard's
    -- "À relancer" filter -- the application disappears from the working list
    -- precisely because a relance was prepared and never sent, while
    -- last_touch_on advertises a date on which nothing left the mailbox.
    -- Drafts stay visible through v_contact_followup.has_pending_draft.
    (SELECT max(t.occurred_on) FROM touches t
       WHERE t.application_id = a.id AND t.state = 'sent')                            AS last_touch_on,
    (CURRENT_DATE - COALESCE(
        (SELECT max(t.occurred_on) FROM touches t
           WHERE t.application_id = a.id AND t.state = 'sent'),
        a.applied_on))                                                                AS days_stale,
    (SELECT count(*) FROM touches t
       WHERE t.application_id = a.id AND t.direction = 'out'
         AND t.state = 'sent')                                                        AS emails_sent,
    (SELECT count(*) FROM touches t
       WHERE t.application_id = a.id AND t.direction = 'in'
         AND t.kind IN ('human_reply','interview_invite'))                            AS human_replies,
    (SELECT count(*) FROM application_contacts ac WHERE ac.application_id = a.id)     AS contact_count
FROM applications a;

-- ---------------------------------------------------------------------------
-- Relance queue: live applications where a human owes you nothing yet and you
-- have gone quiet. Ordered by staleness. This is the daily working list.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_relance_queue AS
SELECT *
FROM v_pipeline
WHERE status IN ('applied','acked','screening','interview','final')
  AND days_stale >= 7
ORDER BY days_stale DESC;

-- ---------------------------------------------------------------------------
-- Contact-level follow-up. One row per (application, contact): when he last
-- wrote, how many times, whether a human ever answered, how stale it is.
-- This is the view the "Contacts" panel of an application renders from, and
-- the one that answers "who do I need to relaunch today".
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_contact_followup AS
SELECT
    ac.application_id,
    a.company,
    a.role,
    a.status                                       AS application_status,
    c.id                                           AS contact_id,
    c.full_name,
    c.email,
    c.title,
    c.email_status,
    ac.role_in_process,
    (SELECT min(t.occurred_on) FROM touches t
       WHERE t.contact_id = c.id AND t.application_id = ac.application_id
         AND t.direction = 'out' AND t.state = 'sent')          AS first_contacted_on,
    (SELECT max(t.occurred_on) FROM touches t
       WHERE t.contact_id = c.id AND t.application_id = ac.application_id
         AND t.direction = 'out' AND t.state = 'sent')          AS last_contacted_on,
    (SELECT count(*) FROM touches t
       WHERE t.contact_id = c.id AND t.application_id = ac.application_id
         AND t.direction = 'out' AND t.state = 'sent')          AS emails_sent,
    (SELECT count(*) FROM touches t
       WHERE t.contact_id = c.id AND t.application_id = ac.application_id
         AND t.direction = 'out' AND t.kind = 'relance'
         AND t.state = 'sent')                                  AS relances_sent,
    EXISTS (SELECT 1 FROM touches t
       WHERE t.contact_id = c.id AND t.application_id = ac.application_id
         AND t.direction = 'in' AND t.kind = 'human_reply')     AS got_human_reply,
    (SELECT max(t.occurred_on) FROM touches t
       WHERE t.contact_id = c.id AND t.application_id = ac.application_id
         AND t.direction = 'in' AND t.kind = 'human_reply')     AS replied_on,
    -- A draft sitting in Gmail, written but not sent.
    EXISTS (SELECT 1 FROM touches t
       WHERE t.contact_id = c.id AND t.application_id = ac.application_id
         AND t.state = 'draft')                                 AS has_pending_draft,
    (CURRENT_DATE - (SELECT max(t.occurred_on) FROM touches t
       WHERE t.contact_id = c.id AND t.application_id = ac.application_id
         AND t.direction = 'out' AND t.state = 'sent'))         AS days_since_last_contact
FROM application_contacts ac
JOIN applications a ON a.id = ac.application_id
JOIN contacts     c ON c.id = ac.contact_id;
