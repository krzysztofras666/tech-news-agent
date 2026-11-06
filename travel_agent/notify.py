"""Send the daily travel-deals digest as an HTML email.

This module is the one place in the entire repo that actually CALLS a
``send`` endpoint. ``gmail_agent`` deliberately avoids ``send`` and only
creates drafts; here we send because the user has explicitly opted in
to a scheduled outbound digest.

Sending uses the OAuth token previously created with
``python -m gmail_agent auth`` (the scope ``gmail.modify`` already
permits sending). No new credentials are needed.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

from googleapiclient.discovery import build

# Reuse the auth + account discovery from gmail_agent so we have a single
# source of truth for token storage and refresh.
from gmail_agent.account import Account, account_for, list_accounts
from gmail_agent.auth import load_credentials
from gmail_agent.config import Config as GmailConfig

from .agent import RunReport
from .render_html import render_email_html, render_email_text


@dataclass(frozen=True)
class SendResult:
    sent: bool
    from_email: str
    to: tuple[str, ...]
    subject: str
    gmail_message_id: str = ""
    error: str = ""


def _pick_sender_account(
    explicit: str | None,
    accounts_dir: Path,
) -> Account:
    """Resolve which authenticated Gmail account should send the digest.

    Order of preference:
      1. Explicit ``--from`` argument (if its token.json exists).
      2. ``andalath@gmail.com`` if authenticated (default for this user).
      3. The single account if exactly one is configured.
    """

    accounts = list_accounts(accounts_dir)
    if not accounts:
        raise RuntimeError(
            f"No authenticated Gmail accounts found under {accounts_dir}. "
            "Run `python -m gmail_agent auth` first."
        )

    if explicit:
        wanted = explicit.strip().lower()
        for acc in accounts:
            if acc.email == wanted:
                return acc
        configured = ", ".join(a.email for a in accounts)
        raise RuntimeError(
            f"--from {explicit!r} is not authenticated. "
            f"Configured accounts: {configured}. "
            "Run `python -m gmail_agent auth` to add it."
        )

    for acc in accounts:
        if acc.email == "andalath@gmail.com":
            return acc

    if len(accounts) == 1:
        return accounts[0]

    configured = ", ".join(a.email for a in accounts)
    raise RuntimeError(
        "Multiple accounts authenticated but no --from given. "
        f"Pick one with --from <email>. Configured: {configured}"
    )


def _build_message(
    *,
    sender: str,
    recipients: list[str],
    subject: str,
    html_body: str,
    text_body: str,
) -> MIMEMultipart:
    """Assemble a multipart/alternative MIME message (text + HTML).

    We always include a plaintext part so mail clients without HTML
    rendering (and corporate spam filters that distrust HTML-only mail)
    still see a useful body.
    """

    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr(("Travel Agent", sender))
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@", 1)[-1] or "localhost")
    # Identifies this as automated; Gmail uses it to suppress vacation
    # auto-responders firing back at us.
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Mailer"] = "travel-agent/0.1"
    msg["Precedence"] = "bulk"

    msg.attach(MIMEText(text_body, "plain", _charset="utf-8"))
    msg.attach(MIMEText(html_body, "html", _charset="utf-8"))
    return msg


def send_report_email(
    report: RunReport,
    *,
    recipients: list[str],
    from_email: str | None = None,
    accounts_dir: Path | None = None,
    generated_at: date | None = None,
    dry_run: bool = False,
) -> SendResult:
    """Render the digest and send it to ``recipients`` via the Gmail API.

    Returns a :class:`SendResult` with the outcome. Raises only for
    setup-time problems (missing token, etc.). Transient send errors
    are captured in :attr:`SendResult.error`.
    """

    if not recipients:
        raise ValueError("At least one recipient is required.")

    gmail_config = GmailConfig.load()
    accounts_dir = accounts_dir or gmail_config.accounts_dir
    account = _pick_sender_account(from_email, accounts_dir)

    creds = load_credentials(account.token_file)
    if creds is None:
        raise RuntimeError(
            f"OAuth token for {account.email} is missing or unrefreshable. "
            f"Re-run `python -m gmail_agent auth` for this account."
        )

    subject, html_body = render_email_html(report, generated_at=generated_at)
    text_body = render_email_text(report, generated_at=generated_at)
    message = _build_message(
        sender=account.email,
        recipients=recipients,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )

    if dry_run:
        return SendResult(
            sent=False,
            from_email=account.email,
            to=tuple(recipients),
            subject=subject,
        )

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    try:
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        sent_msg = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        return SendResult(
            sent=False,
            from_email=account.email,
            to=tuple(recipients),
            subject=subject,
            error=str(exc),
        )

    return SendResult(
        sent=True,
        from_email=account.email,
        to=tuple(recipients),
        subject=subject,
        gmail_message_id=sent_msg.get("id", ""),
    )


def default_recipients_from_env(env_value: str | None) -> list[str] | None:
    """Parse a comma/space-separated recipient list from an env var."""

    if not env_value:
        return None
    parts = [p.strip() for p in env_value.replace(";", ",").split(",")]
    parts = [p for p in parts if p]
    return parts or None
