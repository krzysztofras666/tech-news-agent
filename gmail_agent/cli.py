"""Command-line interface."""

from __future__ import annotations

import argparse
import shutil
import sys
from collections import Counter
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import agent as agent_mod
from .account import Account, account_for, list_accounts
from .auth import (
    TokenExpiredError,
    load_credentials,
    run_oauth_flow,
    save_credentials,
)
from .config import Config
from .gmail_client import EmailMessageView, GmailClient
from .live import build_live_context
from .llm import DraftGenerator
from .tui import RegenerateFn, ReviewDecision, review_draft


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gmail-agent",
        description=(
            "Draft replies to your unread Gmail emails using OpenAI. "
            "Drafts are saved to Gmail; nothing is ever sent automatically."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    auth_p = sub.add_parser(
        "auth",
        help="Authorize a Gmail account and cache its token under accounts/.",
    )
    auth_p.add_argument(
        "--account",
        default=None,
        help=(
            "Email address to authorize. If omitted, the email is detected "
            "from the Google account you sign in with."
        ),
    )

    sub.add_parser("accounts", help="List configured accounts.")

    run_p = sub.add_parser("run", help="Process unread emails for one or all accounts.")
    run_p.add_argument(
        "--account",
        default=None,
        help="Only process this account. If omitted, all configured accounts run.",
    )
    run_p.add_argument(
        "--scope",
        choices=("inbox", "primary"),
        default="inbox",
        help=(
            "Which unread emails to consider. 'inbox' (default) = all unread "
            "in INBOX, including Promotions/Updates/Social tabs. 'primary' = "
            "only the Primary tab."
        ),
    )
    run_p.add_argument(
        "--max", type=int, default=10,
        help="Max emails per account to process (default: 10).",
    )
    run_p.add_argument(
        "--dry-run", action="store_true",
        help="List what would be drafted, but don't call OpenAI or write drafts.",
    )
    run_p.add_argument(
        "--review", action="store_true",
        help=(
            "Interactively review each generated draft before it's saved to "
            "Gmail. Accept, edit in $EDITOR, regenerate, skip, or quit."
        ),
    )

    args = parser.parse_args(argv)
    console = Console()

    try:
        config = Config.load()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Config error:[/red] {exc}")
        return 2

    if args.command == "auth":
        return _cmd_auth(config, console, account_email=args.account)
    if args.command == "accounts":
        return _cmd_accounts(config, console)
    if args.command == "run":
        return _cmd_run(
            config,
            console,
            account_email=args.account,
            max_emails=args.max,
            scope=args.scope,
            dry_run=args.dry_run,
            review=args.review,
        )
    parser.error(f"unknown command {args.command!r}")
    return 2


# ---- subcommands ---------------------------------------------------------


def _cmd_auth(
    config: Config, console: Console, *, account_email: str | None
) -> int:
    try:
        config.require_google_credentials()
        creds = run_oauth_flow(config)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Auth failed:[/red] {exc}")
        return 1

    # Detect the actual email we just authorized so we know where to save.
    try:
        client = GmailClient(creds)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Could not read Gmail profile after auth:[/red] {exc}")
        return 1

    detected = client.address.lower()
    final_email = (account_email or detected).strip().lower()

    if account_email and detected and account_email.lower() != detected:
        console.print(
            f"[yellow]Note:[/yellow] you passed --account {account_email!r} but "
            f"signed in as {detected!r}. Saving under {final_email!r}."
        )

    account = account_for(config.accounts_dir, final_email)
    save_credentials(creds, account.token_file)
    console.print(f"[green]Authorized[/green] {final_email}")
    console.print(f"Token saved to: {account.token_file}")
    _hint_about_extras(console, account)
    return 0


def _cmd_accounts(config: Config, console: Console) -> int:
    _maybe_migrate_legacy_token(config, console)
    accounts = list_accounts(config.accounts_dir)
    if not accounts:
        console.print(
            "No accounts configured. Run "
            "[bold]python -m gmail_agent auth[/bold] to add one."
        )
        return 0
    table = Table(title=f"Configured accounts ({config.accounts_dir})")
    table.add_column("Email")
    table.add_column("Profile")
    table.add_column("Knowledge")
    table.add_column("Live sources")
    for acc in accounts:
        table.add_row(
            acc.email,
            "yes" if acc.profile_file.exists() else "—",
            "yes" if acc.knowledge_file.exists() else "—",
            str(len(acc.read_live_sources())) if acc.live_sources_file.exists() else "—",
        )
    console.print(table)
    return 0


def _cmd_run(
    config: Config,
    console: Console,
    *,
    account_email: str | None,
    max_emails: int,
    scope: str,
    dry_run: bool,
    review: bool,
) -> int:
    _maybe_migrate_legacy_token(config, console)

    accounts = list_accounts(config.accounts_dir)
    if account_email:
        accounts = [a for a in accounts if a.email == account_email.strip().lower()]
        if not accounts:
            console.print(
                f"[red]No configured account matches {account_email!r}.[/red] "
                f"Run `python -m gmail_agent auth --account {account_email}` first."
            )
            return 1
    if not accounts:
        console.print(
            "[red]No accounts configured.[/red] Run "
            "[bold]python -m gmail_agent auth[/bold] first."
        )
        return 1

    if review and not dry_run:
        console.print("[cyan]Review mode:[/cyan] each draft will be shown for approval.")

    overall_status: Counter[str] = Counter()
    failures = 0

    for idx, account in enumerate(accounts, start=1):
        console.rule(f"[bold]{account.email}[/bold]  ({idx}/{len(accounts)})")
        rc = _run_for_account(
            config,
            console,
            account,
            max_emails=max_emails,
            scope=scope,
            dry_run=dry_run,
            review=review,
            status=overall_status,
        )
        if rc != 0:
            failures += 1

    _print_overall(console, overall_status, dry_run=dry_run, failures=failures)
    return 0 if failures == 0 else 1


def _run_for_account(
    config: Config,
    console: Console,
    account: Account,
    *,
    max_emails: int,
    scope: str,
    dry_run: bool,
    review: bool,
    status: Counter[str],
) -> int:
    try:
        creds = load_credentials(account.token_file)
    except TokenExpiredError as exc:
        console.print(
            f"[red]OAuth token for {account.email} has expired or been "
            f"revoked.[/red]\n"
            f"  Reason: {exc}\n"
            f"  Fix: [bold]python -m gmail_agent auth --account "
            f"{account.email}[/bold]\n"
            "  (Google's 'Testing' OAuth consent screen invalidates "
            "refresh tokens after 7 days. Publishing the consent screen "
            "removes that limit — see the README §7 troubleshooting note.)"
        )
        return 1
    if creds is None:
        console.print(
            f"[red]No token for {account.email}.[/red] "
            f"Run `python -m gmail_agent auth --account {account.email}`."
        )
        return 1

    try:
        gmail = GmailClient(creds)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Gmail client failed for {account.email}:[/red] {exc}")
        return 1

    if gmail.address.lower() != account.email:
        console.print(
            f"[yellow]Warning:[/yellow] token under {account.email} is actually "
            f"authorized as {gmail.address}."
        )

    console.print(f"Signed in as [bold]{gmail.address}[/bold]")
    console.print(f"Scope: [bold]{scope}[/bold] (max {max_emails})")

    profile = account.read_profile() or config.user_profile
    knowledge = account.read_knowledge()
    live_urls = account.read_live_sources()
    if live_urls:
        with console.status(f"Fetching {len(live_urls)} live source(s)..."):
            live_blob = build_live_context(live_urls)
        if live_blob:
            knowledge = (knowledge + "\n\n" + live_blob).strip()
            console.print(f"Live sources: [green]{len(live_urls)} fetched[/green]")
        else:
            console.print(
                f"[yellow]Live sources:[/yellow] all {len(live_urls)} fetches failed; "
                "continuing with static knowledge only."
            )

    if dry_run:
        drafter: DraftGenerator | _NullDrafter = _NullDrafter()
    else:
        try:
            drafter = DraftGenerator(config, profile=profile, knowledge=knowledge)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]LLM setup failed for {account.email}:[/red] {exc}")
            return 1

    reviewer = _make_reviewer(console) if (review and not dry_run) else None

    results = agent_mod.run(
        config,
        gmail,
        drafter,  # type: ignore[arg-type]
        max_emails=max_emails,
        scope=scope,
        dry_run=dry_run,
        reviewer=reviewer,
        console=console,
    )

    counts = Counter(r.status for r in results)
    for k, v in counts.items():
        status[k] += v
    _print_account_summary(console, account, counts, dry_run=dry_run)
    return 0


# ---- helpers -------------------------------------------------------------


class _NullDrafter:
    """Stub used in dry-run; the orchestrator never calls it."""

    def draft_reply(self, _email, **_kwargs):  # pragma: no cover - never called
        raise RuntimeError("drafter called in dry-run")


def _make_reviewer(console: Console):
    def _reviewer(
        email: EmailMessageView,
        initial_body: str,
        index: int,
        total: int,
        regenerate: RegenerateFn,
    ) -> ReviewDecision:
        return review_draft(
            console,
            index=index,
            total=total,
            email=email,
            initial_body=initial_body,
            regenerate=regenerate,
        )

    return _reviewer


def _print_account_summary(
    console: Console, account: Account, counts: Counter, *, dry_run: bool
) -> None:
    if not counts:
        return
    table = Table(title=f"Summary — {account.email}")
    table.add_column("Status")
    table.add_column("Count", justify="right")
    for status in ("drafted", "skipped", "error"):
        if counts.get(status):
            table.add_row(status, str(counts[status]))
    console.print(table)
    if dry_run:
        console.print("[yellow]Dry-run:[/yellow] no drafts created for this account.")
    elif counts.get("drafted"):
        console.print(
            f"[green]Open Gmail ({account.email}) → Drafts[/green] to review "
            f"{counts['drafted']} new draft(s)."
        )


def _print_overall(
    console: Console, status: Counter, *, dry_run: bool, failures: int
) -> None:
    if not status:
        return
    table = Table(title="Overall")
    table.add_column("Status")
    table.add_column("Count", justify="right")
    for s in ("drafted", "skipped", "error"):
        if status.get(s):
            table.add_row(s, str(status[s]))
    console.print(table)
    if failures:
        console.print(f"[red]{failures} account(s) failed to run.[/red]")


def _maybe_migrate_legacy_token(config: Config, console: Console) -> None:
    """If a single legacy `token.json` is present, move it into accounts/."""

    legacy = config.legacy_token_file
    if not legacy.exists():
        return
    try:
        creds = load_credentials(legacy)
    except TokenExpiredError:
        # Legacy token is dead; nothing useful to migrate. Leave it alone
        # so the user can see it and decide what to do.
        return
    if creds is None:
        return
    try:
        client = GmailClient(creds)
        email = client.address.lower()
    except Exception:  # noqa: BLE001
        return
    account = account_for(config.accounts_dir, email)
    if account.token_file.exists():
        # Already migrated; remove the stray legacy file.
        try:
            legacy.unlink()
        except OSError:
            pass
        return
    try:
        shutil.move(str(legacy), str(account.token_file))
        try:
            account.token_file.chmod(0o600)
        except OSError:
            pass
        console.print(
            f"[dim]Migrated legacy token.json → {account.token_file}[/dim]"
        )
    except OSError as exc:
        console.print(f"[yellow]Could not migrate legacy token.json: {exc}[/yellow]")


def _hint_about_extras(console: Console, account) -> None:
    extras = [
        ("profile.md", account.profile_file),
        ("knowledge.md", account.knowledge_file),
        ("live_sources.txt", account.live_sources_file),
    ]
    missing = [name for name, p in extras if not p.exists()]
    if not missing:
        return
    console.print(
        "[dim]Optional files you can add to "
        f"{account.directory} to tailor drafts: "
        + ", ".join(missing)
        + "[/dim]"
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
