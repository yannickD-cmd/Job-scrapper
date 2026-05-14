"""Email alerts for genuinely new jobs.

Called by run.py after `db.persist_run_results` returns. We email only the
`new_jobs` list — rows where the upsert performed an INSERT, not an UPDATE
(see the `xmax = 0` trick in db.py). That guarantees: when the cron fires
multiple times a day, each new job triggers exactly ONE alert across the
first run that sees it; later runs treat it as a silent update.

Delivery is Gmail SMTP with an App Password — stdlib only, no new pip dep.
Best-effort: missing creds or SMTP errors are logged but don't fail the run.
Persistence is the source of truth; alerts are a notification layer on top.
"""
from __future__ import annotations

import html as _html
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_TIMEOUT = 30


def _build_message(
    sender: str,
    recipient: str,
    company: str,
    new_jobs: list[dict],
) -> EmailMessage:
    n = len(new_jobs)
    plural = "s" if n > 1 else ""
    subject = f"[Job alert] {n} new {company} job{plural}"

    plain_lines = [f"Found {n} new {company} job{plural}:", ""]
    html_items: list[str] = []

    for job in new_jobs:
        ident = job.get("identifier") or job["native_job_id"]
        title = job["title"]
        location = job.get("location") or "n/a"
        posted = job.get("posted_date") or "n/a"
        url = job["apply_url"]

        plain_lines.append(f"- [{ident}] {title}")
        plain_lines.append(f"    Location : {location}")
        plain_lines.append(f"    Posted   : {posted}")
        plain_lines.append(f"    Apply    : {url}")
        plain_lines.append("")

        # Escape job-provided strings before embedding into HTML. URLs we
        # only escape for attribute context; this is plenty for trusted-ish
        # scraper output going into our own inbox.
        html_items.append(
            "<li style='margin-bottom:14px'>"
            f"<strong>{_html.escape(title)}</strong> "
            f"<code>[{_html.escape(ident)}]</code><br>"
            f"<small>Location: {_html.escape(location)} "
            f"&middot; Posted: {_html.escape(posted)}</small><br>"
            f'<a href="{_html.escape(url, quote=True)}">'
            f"{_html.escape(url)}</a>"
            "</li>"
        )

    html_body = (
        f"<p>Found <strong>{n}</strong> new {_html.escape(company)} "
        f"job{plural}:</p>"
        f"<ul style='padding-left:18px'>{''.join(html_items)}</ul>"
    )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content("\n".join(plain_lines))
    msg.add_alternative(html_body, subtype="html")
    return msg


def send_new_jobs_email(company: str, new_jobs: list[dict]) -> bool:
    """Send one email summarising new jobs. Returns True on success.

    No-ops (returns False) if new_jobs is empty or creds are missing —
    callers don't need to guard.
    """
    if not new_jobs:
        return False

    sender = os.environ.get("GMAIL_ADDRESS", "").strip()
    # Google shows app passwords as 'xxxx xxxx xxxx xxxx' — tolerate the spaces.
    password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    recipient = (os.environ.get("ALERT_TO") or sender).strip()

    if not (sender and password and recipient):
        print(
            "  [alerts] GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set — "
            "skipping email (set them in .env to enable)",
            file=sys.stderr,
        )
        return False

    msg = _build_message(sender, recipient, company, new_jobs)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as smtp:
            smtp.starttls()
            smtp.login(sender, password)
            smtp.send_message(msg)
    except Exception as exc:
        print(
            f"  [alerts] email send FAILED: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return False

    n = len(new_jobs)
    print(
        f"  [alerts] sent email to {recipient} "
        f"({n} new job{'s' if n > 1 else ''})",
        flush=True,
    )
    return True
