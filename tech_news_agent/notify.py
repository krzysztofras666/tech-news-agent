"""Send the daily LinkedIn-draft digest via Gmail."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Literal

from googleapiclient.discovery import build

from gmail_agent.account import Account, list_accounts
from gmail_agent.auth import load_credentials
from gmail_agent.config import Config as GmailConfig

from .agent import RunReport
from .config import Config
from .image_gen import ILLUSTRATION_CID, generate_illustration
from .render_email import render_email_html, render_email_text


@dataclass(frozen=True)
class SendResult:
    sent: bool
    from_email: str
    to: tuple[str, ...]
    subject: str
    gmail_message_id: str = ""
    error: str = ""
    illustration_saved: str = ""


def _pick_sender_account(
    explicit: str | None,
    accounts_dir: Path,
) -> Account:
    accounts = list_accounts(accounts_dir)
    if not accounts:
        raise RuntimeError(
            f"No authenticated Gmail accounts found under {accounts_dir}. "
            "Run `python -m gmail_agent auth` first."
        )

    if explicit:
        wanted = explicit.strip().lower()
        for account in accounts:
            if account.email == wanted:
                return account
        configured = ", ".join(account.email for account in accounts)
        raise RuntimeError(
            f"--from {explicit!r} is not authenticated. "
            f"Configured accounts: {configured}."
        )

    for account in accounts:
        if account.email == "andalath@gmail.com":
            return account

    if len(accounts) == 1:
        return accounts[0]

    configured = ", ".join(account.email for account in accounts)
    raise RuntimeError(
        "Multiple accounts authenticated but no --from given. "
        f"Pick one with --from <email>. Configured: {configured}"
    )


def prepare_report_email(
    report: RunReport,
    *,
    generated_at: date | None = None,
    image_mode: Literal["cid", "data"] = "cid",
    config: Config | None = None,
) -> tuple[str, str, str, bytes | None, str]:
    """Return (subject, html_body, text_body, illustration_png, illustration_path)."""

    cfg = config or Config.load()
    illustration = generate_illustration(report, cfg)
    illustration_png = illustration.png_bytes if illustration else None
    saved = str(illustration.saved_path) if illustration and illustration.saved_path else ""

    subject, html_body = render_email_html(
        report,
        generated_at=generated_at,
        illustration_png=illustration_png,
        image_mode=image_mode,
    )
    text_body = render_email_text(
        report,
        generated_at=generated_at,
        illustration_png=illustration_png,
    )
    return subject, html_body, text_body, illustration_png, saved


def _build_message(
    *,
    sender: str,
    recipients: list[str],
    subject: str,
    html_body: str,
    text_body: str,
    illustration_png: bytes | None = None,
) -> MIMEMultipart:
    if illustration_png:
        msg: MIMEMultipart = MIMEMultipart("related")
    else:
        msg = MIMEMultipart("alternative")

    msg["From"] = formataddr(("Tech News Agent", sender))
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@", 1)[-1] or "localhost")
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Mailer"] = "tech-news-agent/0.1"
    msg["Precedence"] = "bulk"

    if illustration_png:
        alternative = MIMEMultipart("alternative")
        alternative.attach(MIMEText(text_body, "plain", _charset="utf-8"))
        alternative.attach(MIMEText(html_body, "html", _charset="utf-8"))
        msg.attach(alternative)

        image = MIMEImage(illustration_png, _subtype="png")
        image.add_header("Content-ID", f"<{ILLUSTRATION_CID}>")
        image.add_header(
            "Content-Disposition",
            "inline",
            filename="world_retro_news.png",
        )
        msg.attach(image)
    else:
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
    config: Config | None = None,
) -> SendResult:
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

    subject, html_body, text_body, illustration_png, illustration_saved = (
        prepare_report_email(
            report,
            generated_at=generated_at,
            image_mode="cid",
            config=config,
        )
    )
    message = _build_message(
        sender=account.email,
        recipients=recipients,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        illustration_png=illustration_png,
    )

    if dry_run:
        return SendResult(
            sent=False,
            from_email=account.email,
            to=tuple(recipients),
            subject=subject,
            illustration_saved=illustration_saved,
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
            illustration_saved=illustration_saved,
        )

    return SendResult(
        sent=True,
        from_email=account.email,
        to=tuple(recipients),
        subject=subject,
        gmail_message_id=sent_msg.get("id", ""),
        illustration_saved=illustration_saved,
    )


def default_recipients_from_env(env_value: str | None) -> list[str] | None:
    if not env_value:
        return None
    parts = [piece.strip() for piece in env_value.replace(";", ",").split(",")]
    parts = [piece for piece in parts if piece]
    return parts or None
