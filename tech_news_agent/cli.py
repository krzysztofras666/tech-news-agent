"""Command-line entrypoint for the tech news agent."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from travel_agent.agent import BrowserMode

from .agent import run
from .config import Config
from .models import SiteResult, Story
from .notify import SendResult, default_recipients_from_env, prepare_report_email, send_report_email
from .sites import SITES, site_by_id

DEFAULT_RECIPIENTS = ("andalath@gmail.com",)


def _render_top_stories(stories: list[Story], console: Console) -> None:
    table = Table(
        title=f"Top tech/crypto/AI stories — {date.today().isoformat()}",
        show_lines=True,
        title_style="bold",
    )
    table.add_column("#", justify="right", style="dim")
    table.add_column("Headline", style="bold")
    table.add_column("Topic", style="cyan")
    table.add_column("Score", justify="right")
    table.add_column("Source", style="dim")

    if not stories:
        console.print("[yellow]No stories extracted.[/yellow]")
        return

    for idx, story in enumerate(stories, start=1):
        table.add_row(
            str(idx),
            story.headline,
            story.topic,
            f"{story.score:.1f}",
            story.source_site,
        )
    console.print(table)


def _render_diagnostics(sites: list[SiteResult], console: Console) -> None:
    table = Table(title="Per-site scrape diagnostics", title_style="bold")
    table.add_column("Site", style="bold")
    table.add_column("Engine", justify="center")
    table.add_column("Status", justify="right")
    table.add_column("Chars", justify="right")
    table.add_column("Stories", justify="right")
    table.add_column("Error", style="red")

    for site_result in sites:
        status_style = "green" if site_result.ok else "red"
        status_text = "ok" if site_result.ok else "fail"
        engine_style = "blue" if site_result.engine == "browser" else "white"
        table.add_row(
            site_result.site_name,
            f"[{engine_style}]{site_result.engine}[/{engine_style}]",
            f"[{status_style}]{status_text}[/{status_style}]",
            f"{site_result.raw_chars:,}",
            str(len(site_result.stories)),
            site_result.error or "",
        )
    console.print(table)


def _as_json(report) -> str:
    return json.dumps(
        {
            "generated_at": date.today().isoformat(),
            "top_stories": [
                {
                    "headline": story.headline,
                    "summary": story.summary,
                    "topic": story.topic,
                    "importance": story.importance,
                    "engagement": story.engagement,
                    "score": story.score,
                    "source_site": story.source_site,
                    "source_url": story.source_url,
                }
                for story in report.top_stories
            ],
            "linkedin_draft": report.linkedin_draft,
            "sites": [
                {
                    "site": site_result.site_name,
                    "url": site_result.url,
                    "ok": site_result.ok,
                    "error": site_result.error,
                    "raw_chars": site_result.raw_chars,
                    "stories": len(site_result.stories),
                }
                for site_result in report.sites
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def _shared_parser_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--site", action="append", default=[], metavar="ID")
    browser = parser.add_mutually_exclusive_group()
    browser.add_argument(
        "--no-browser", dest="browser_mode", action="store_const", const="off"
    )
    browser.add_argument(
        "--browser-all", dest="browser_mode", action="store_const", const="all"
    )
    parser.set_defaults(browser_mode="auto")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tech_news_agent",
        description=(
            "Scan tech/crypto/AI news, rank the top stories, draft a short "
            "LinkedIn post, and optionally email it."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Scan sites and print top stories.")
    _shared_parser_args(run_parser)
    run_parser.add_argument("--json", action="store_true")
    run_parser.add_argument("--no-diagnostics", action="store_true")
    run_parser.add_argument("--no-linkedin", action="store_true")

    draft_parser = sub.add_parser(
        "draft",
        help="Scan sites and print a LinkedIn post draft.",
    )
    _shared_parser_args(draft_parser)
    draft_parser.add_argument(
        "--out",
        default="logs/linkedin_draft.txt",
        help="Write the LinkedIn draft to this file.",
    )

    send_parser = sub.add_parser(
        "send",
        help="Scan sites and email the LinkedIn draft.",
    )
    _shared_parser_args(send_parser)
    send_parser.add_argument(
        "--to",
        action="append",
        default=[],
        help="Recipient email (repeatable). Defaults to TECH_NEWS_EMAIL_TO "
        "or andalath@gmail.com.",
    )
    send_parser.add_argument(
        "--from",
        dest="from_email",
        default=None,
        help="Sender Gmail account (must be authenticated via gmail_agent auth).",
    )
    send_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render the email but do not send it.",
    )

    sub.add_parser("list-sites", help="Print configured news sources.")
    return parser.parse_args(argv)


def _select_sites(ids: list[str]):
    if not ids:
        return list(SITES)
    selected = []
    for site_id in ids:
        site = site_by_id(site_id)
        if site is None:
            raise SystemExit(
                f"Unknown site id: {site_id!r}. Valid: "
                + ", ".join(site.site_id for site in SITES)
            )
        selected.append(site)
    return selected


def _resolve_recipients(cli_to: list[str]) -> list[str]:
    if cli_to:
        recipients: list[str] = []
        for entry in cli_to:
            for piece in entry.replace(";", ",").split(","):
                piece = piece.strip()
                if piece:
                    recipients.append(piece)
        return recipients
    env_list = default_recipients_from_env(os.getenv("TECH_NEWS_EMAIL_TO"))
    if env_list:
        return env_list
    return list(DEFAULT_RECIPIENTS)


def _scrape(args, console: Console, *, write_linkedin: bool):
    from travel_agent.browser_fetcher import browser_available

    config = Config.load()
    try:
        config.require_openai()
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2

    selected = _select_sites(args.site)
    browser_mode: BrowserMode = args.browser_mode
    if browser_mode != "off" and not browser_available():
        console.print(
            "[yellow]Playwright not installed — falling back to httpx-only mode.[/yellow]"
        )
        browser_mode = "off"

    console.print(
        f"[bold]Scanning {len(selected)} source(s) with model "
        f"{config.openai_model} (browser={browser_mode})…[/bold]"
    )

    def progress(site_result: SiteResult) -> None:
        if site_result.ok:
            console.print(
                f"  [green]✓[/green] {site_result.site_name}: "
                f"{len(site_result.stories)} story/stories "
                f"({site_result.raw_chars:,} chars)"
            )
        else:
            console.print(
                f"  [red]✗[/red] {site_result.site_name}: {site_result.error}"
            )

    def extracting(site) -> None:
        console.print(f"  [dim]⋯ extracting {site.name}…[/dim]")

    report = asyncio.run(
        run(
            config,
            selected_sites=selected,
            on_site_done=progress,
            browser_mode=browser_mode,
            on_extraction_start=extracting,
            write_linkedin=write_linkedin,
        )
    )
    return report


def _write_preview(report, out_path: str) -> str:
    _, html_body, _, _, illustration_saved = prepare_report_email(
        report, image_mode="data"
    )
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_body, encoding="utf-8")
    if illustration_saved:
        return f"{path.resolve()} (illustration: {illustration_saved})"
    return str(path.resolve())


def _report_send(result: SendResult, console: Console) -> int:
    to_line = ", ".join(result.to)
    if result.sent:
        console.print(
            f"\n[green]✉ Sent[/green] from [bold]{result.from_email}[/bold] "
            f"to [bold]{to_line}[/bold]"
        )
        console.print(f"  Subject: {result.subject}")
        if result.illustration_saved:
            console.print(
                f"  World Retro news: [dim]{result.illustration_saved}[/dim]"
            )
        if result.gmail_message_id:
            console.print(
                f"  Gmail message id: [dim]{result.gmail_message_id}[/dim]"
            )
        return 0

    if result.error:
        console.print(
            f"\n[red]✗ Send failed[/red] from {result.from_email} "
            f"to {to_line}\n  {result.error}"
        )
        return 1

    console.print(
        f"\n[yellow]Dry run[/yellow] — would send from "
        f"[bold]{result.from_email}[/bold] to [bold]{to_line}[/bold]"
    )
    console.print(f"  Subject: {result.subject}")
    if result.illustration_saved:
        console.print(
            f"  World Retro news: [dim]{result.illustration_saved}[/dim]"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    console = Console()

    if args.command == "list-sites":
        table = Table(title="Configured tech news sources")
        table.add_column("ID", style="bold")
        table.add_column("Name")
        table.add_column("URL", style="dim")
        for site in SITES:
            table.add_row(site.site_id, site.name, site.homepage)
        console.print(table)
        return 0

    if args.command in {"run", "draft", "send"}:
        write_linkedin = args.command != "run" or not args.no_linkedin
        report = _scrape(args, console, write_linkedin=write_linkedin)
        if isinstance(report, int):
            return report

    if args.command == "run":
        if args.json:
            print(_as_json(report))
            return 0

        console.print()
        _render_top_stories(report.top_stories, console)
        if write_linkedin and report.linkedin_draft:
            console.print()
            console.print(
                Panel(
                    report.linkedin_draft,
                    title="LinkedIn draft",
                    border_style="green",
                )
            )
        if not args.no_diagnostics:
            console.print()
            _render_diagnostics(report.sites, console)
        return 0

    if args.command == "draft":
        console.print()
        console.print(
            Panel(report.linkedin_draft, title="LinkedIn draft", border_style="green")
        )

        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report.linkedin_draft + "\n", encoding="utf-8")
        console.print(f"\n[green]Draft written to[/green] {out_path.resolve()}")
        return 0

    if args.command == "send":
        recipients = _resolve_recipients(args.to)
        from_email = args.from_email or os.getenv("TECH_NEWS_EMAIL_FROM") or None

        try:
            preview_path = _write_preview(report, "logs/last_tech_news_email.html")
            console.print(f"[dim]HTML written to {preview_path}[/dim]")
        except OSError as exc:
            console.print(f"[yellow]Could not write HTML preview: {exc}[/yellow]")

        try:
            send_result = send_report_email(
                report,
                recipients=recipients,
                from_email=from_email,
                dry_run=args.dry_run,
            )
        except RuntimeError as exc:
            console.print(f"[red]{exc}[/red]")
            return 2

        return _report_send(send_result, console)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
