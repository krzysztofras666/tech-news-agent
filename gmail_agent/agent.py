"""Orchestration: fetch unread → decide → draft → (review) → save → label."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

from rich.console import Console

from .config import Config
from .gmail_client import EmailMessageView, GmailClient
from .llm import DraftGenerator
from .tui import RegenerateFn, ReviewDecision


# Addresses we never reply to. Substring match on the local part.
_NO_REPLY_PATTERNS = re.compile(
    r"(?:^|[._\-])(?:no[-_.]?reply|do[-_.]?not[-_.]?reply|mailer[-_.]?daemon|"
    r"notifications?|postmaster|bounces?|noreply)(?:[._\-]|@)",
    re.IGNORECASE,
)


# A reviewer is a callable that, given the email, the LLM-generated draft body,
# and a regenerate hook, returns the user's decision. ``None`` means "no
# interactive review; just save whatever the LLM produced".
ReviewerFn = Callable[
    [EmailMessageView, str, int, int, RegenerateFn],
    ReviewDecision,
]


@dataclass
class ProcessResult:
    message_id: str
    subject: str
    from_email: str
    status: str  # "drafted" | "skipped" | "error"
    reason: str = ""
    draft_id: str | None = None


def _is_auto_or_no_reply(email: EmailMessageView) -> str | None:
    """Return a skip reason if this looks like an automated / no-reply email."""

    local_at = email.from_email.lower()
    if _NO_REPLY_PATTERNS.search(local_at):
        return f"no-reply sender ({email.from_email})"

    headers = {k.lower(): v for k, v in email.headers.items()}
    if headers.get("auto-submitted", "").lower() not in ("", "no"):
        return "Auto-Submitted header set"
    if "list-unsubscribe" in headers:
        return "mailing list (List-Unsubscribe present)"
    if headers.get("precedence", "").lower() in ("bulk", "list", "junk"):
        return f"Precedence: {headers.get('precedence')}"
    return None


def run(
    config: Config,
    gmail: GmailClient,
    drafter: DraftGenerator,
    *,
    max_emails: int = 25,
    scope: str = "inbox",
    dry_run: bool = False,
    reviewer: Optional[ReviewerFn] = None,
    on_event: Callable[[str], None] | None = None,
    console: Console | None = None,
) -> list[ProcessResult]:
    console = console or Console()
    log = on_event or (lambda msg: console.log(msg))

    # Ensure our marker label exists (skipped for dry-run so dry-run is
    # truly side-effect-free).
    label_name = config.agent_label_name
    label_id: str | None = None
    if not dry_run:
        try:
            label_id = gmail.ensure_label(label_name)
        except Exception as exc:  # noqa: BLE001
            log(f"[red]Could not ensure label {label_name!r}: {exc}[/red]")
            label_id = None

    ids = gmail.list_unread_message_ids(
        max_results=max_emails,
        scope=scope,
        # In dry-run we still want to mirror what the real run would see, so
        # we filter by the label if we know its name.
        exclude_label_name=label_name,
    )
    if not ids:
        log(
            f"No unread {scope.upper()} emails found "
            "(after excluding already-drafted)."
        )
        return []

    log(f"Found {len(ids)} unread {scope.upper()} email(s) to consider.")
    results: list[ProcessResult] = []

    for idx, mid in enumerate(ids, start=1):
        try:
            email = gmail.get_message(mid)
        except Exception as exc:  # noqa: BLE001
            results.append(ProcessResult(mid, "", "", "error", f"fetch failed: {exc}"))
            log(f"[{idx}/{len(ids)}] fetch failed for {mid}: {exc}")
            continue

        header = f"[{idx}/{len(ids)}] {email.from_email} — {email.subject!r}"

        if label_id and label_id in email.label_ids:
            results.append(ProcessResult(mid, email.subject, email.from_email,
                                         "skipped", "already labeled by agent"))
            log(f"{header}: skip (already drafted previously)")
            continue

        if email.is_from_me:
            results.append(ProcessResult(mid, email.subject, email.from_email,
                                         "skipped", "message was sent by me"))
            log(f"{header}: skip (sent by me)")
            continue

        skip_reason = _is_auto_or_no_reply(email)
        if skip_reason:
            results.append(ProcessResult(mid, email.subject, email.from_email,
                                         "skipped", skip_reason))
            log(f"{header}: skip ({skip_reason})")
            continue

        try:
            if gmail.thread_has_my_reply(email.thread_id, email.id):
                results.append(ProcessResult(mid, email.subject, email.from_email,
                                             "skipped", "already replied in thread"))
                log(f"{header}: skip (already replied)")
                continue
        except Exception as exc:  # noqa: BLE001
            log(f"{header}: thread check failed ({exc}) — continuing anyway")

        if dry_run:
            results.append(ProcessResult(mid, email.subject, email.from_email,
                                         "skipped", "dry-run"))
            log(f"{header}: would draft (dry-run)")
            continue

        try:
            body = drafter.draft_reply(email)
        except Exception as exc:  # noqa: BLE001
            results.append(ProcessResult(mid, email.subject, email.from_email,
                                         "error", f"LLM failed: {exc}"))
            log(f"{header}: LLM failed — {exc}")
            continue

        if body is None:
            results.append(ProcessResult(mid, email.subject, email.from_email,
                                         "skipped", "model said no reply warranted"))
            log(f"{header}: skip (model chose SKIP)")
            continue

        # Optional interactive review before anything is saved.
        if reviewer is not None:
            def _regen(steering: str | None) -> str | None:
                return drafter.draft_reply(email, steering=steering)

            decision = reviewer(email, body, idx, len(ids), _regen)
            if decision.action == "quit":
                results.append(ProcessResult(mid, email.subject, email.from_email,
                                             "skipped", "user quit during review"))
                log(f"{header}: user quit — stopping run.")
                break
            if decision.action == "skip":
                results.append(ProcessResult(mid, email.subject, email.from_email,
                                             "skipped", "user skipped in review"))
                log(f"{header}: user skipped in review.")
                continue
            body = decision.body or body

        try:
            draft_id = gmail.create_reply_draft(original=email, body_text=body)
        except Exception as exc:  # noqa: BLE001
            results.append(ProcessResult(mid, email.subject, email.from_email,
                                         "error", f"draft create failed: {exc}"))
            log(f"{header}: draft creation failed — {exc}")
            continue

        if label_id:
            try:
                gmail.apply_label(mid, label_id)
            except Exception as exc:  # noqa: BLE001
                log(f"{header}: draft saved but applying label failed — {exc}")

        results.append(ProcessResult(mid, email.subject, email.from_email,
                                     "drafted", "", draft_id=draft_id))
        log(f"{header}: draft created ({draft_id})")

    return results
