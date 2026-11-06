"""Command-line entrypoint for the travel agent."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
from datetime import date

from rich.console import Console
from rich.table import Table
from rich.text import Text

import os

from .agent import BrowserMode, RunReport, run
from .browser_fetcher import browser_available
from .config import Config
from .models import Offer, SiteResult
from .notify import (
    SendResult,
    default_recipients_from_env,
    send_report_email,
)
from .render_html import render_email_html
from .sites import SITES, Site, site_by_id


# Hard-coded default recipients per the user's request. Overridable via
# the TRAVEL_EMAIL_TO env var or --to flag.
DEFAULT_RECIPIENTS = (
    "andalath@gmail.com",
    "goniaras@gmail.com",
    "katarzyna.dyngosz@gmail.com",
)


def _format_price(offer: Offer) -> str:
    if offer.price is None:
        return "—"
    if offer.price.is_integer():
        return f"{int(offer.price):,} {offer.currency}".replace(",", " ")
    return f"{offer.price:,.2f} {offer.currency}".replace(",", " ")


def _format_dates(offer: Offer) -> str:
    if offer.departure_date and offer.return_date:
        return f"{offer.departure_date.isoformat()} → {offer.return_date.isoformat()}"
    if offer.departure_date:
        return offer.departure_date.isoformat()
    if offer.return_date:
        return f"→ {offer.return_date.isoformat()}"
    return "—"


def _render_table(report: RunReport, console: Console) -> None:
    table = Table(
        title=f"Travel deals — {date.today().isoformat()}",
        show_lines=True,
        title_style="bold",
    )
    table.add_column("Destination", style="bold cyan", no_wrap=False)
    table.add_column("Dates", style="white", no_wrap=False)
    table.add_column("Price", style="bold green", justify="right", no_wrap=True)
    table.add_column("Nights", justify="right", style="white")
    table.add_column("Source", style="dim", no_wrap=False)
    table.add_column("Notes", style="white", no_wrap=False)

    if not report.grouped:
        console.print(
            "[yellow]No offers extracted. "
            "Check the per-site diagnostics above.[/yellow]"
        )
        return

    for destination, offers in report.grouped:
        for idx, offer in enumerate(offers):
            label = destination if idx == 0 else ""
            table.add_row(
                Text(label),
                _format_dates(offer),
                _format_price(offer),
                str(offer.duration_nights) if offer.duration_nights else "—",
                f"{offer.source_site}\n{offer.source_url}",
                (offer.title + ("\n" + offer.notes if offer.notes else "")).strip() or "—",
            )

    console.print(table)


def _render_diagnostics(sites: list[SiteResult], console: Console) -> None:
    table = Table(
        title="Per-site scrape diagnostics",
        show_lines=False,
        title_style="bold",
        caption=(
            "engine: which fetcher produced the text — 'http' = plain "
            "httpx (fast), 'browser' = headless Chromium fallback."
        ),
    )
    table.add_column("Site", style="bold")
    table.add_column("Engine", justify="center")
    table.add_column("Status", justify="right")
    table.add_column("Chars", justify="right")
    table.add_column("Offers", justify="right")
    table.add_column("URL", style="dim", no_wrap=False)
    table.add_column("Error", style="red", no_wrap=False)

    for sr in sites:
        status_style = "green" if sr.ok else "red"
        status_text = "ok" if sr.ok else "fail"
        engine_style = "blue" if sr.engine == "browser" else "white"
        table.add_row(
            sr.site_name,
            f"[{engine_style}]{sr.engine}[/{engine_style}]",
            f"[{status_style}]{status_text}[/{status_style}]",
            f"{sr.raw_chars:,}",
            str(len(sr.offers)),
            sr.url,
            sr.error or "",
        )
    console.print(table)


def _as_json(report: RunReport) -> str:
    def offer_dict(o: Offer) -> dict:
        return {
            "destination": o.destination,
            "departure_date": o.departure_date.isoformat() if o.departure_date else None,
            "return_date": o.return_date.isoformat() if o.return_date else None,
            "duration_nights": o.duration_nights,
            "price": o.price,
            "currency": o.currency,
            "title": o.title,
            "notes": o.notes,
            "source_site": o.source_site,
            "source_url": o.source_url,
        }

    data = {
        "generated_at": date.today().isoformat(),
        "sites": [
            {
                "site": sr.site_name,
                "url": sr.url,
                "ok": sr.ok,
                "error": sr.error,
                "raw_chars": sr.raw_chars,
                "offers": [offer_dict(o) for o in sr.offers],
            }
            for sr in report.sites
        ],
        "grouped": [
            {
                "destination": destination,
                "offers": [offer_dict(o) for o in offers],
            }
            for destination, offers in report.grouped
        ],
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="travel_agent",
        description=(
            "Scrape Polish travel portals, extract cheap offers, and group "
            "them by destination ordered by departure date."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser(
        "run",
        help="Scrape all sites and print grouped deals.",
    )
    run_p.add_argument(
        "--site",
        action="append",
        default=[],
        metavar="ID",
        help=(
            "Limit to a specific site (repeatable). Valid IDs: "
            + ", ".join(s.site_id for s in SITES)
        ),
    )
    run_p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a rendered table (machine-readable).",
    )
    run_p.add_argument(
        "--no-diagnostics",
        action="store_true",
        help="Hide the per-site scrape diagnostics table.",
    )
    run_p.add_argument(
        "--max-per-destination",
        type=int,
        default=None,
        help=(
            "Override TRAVEL_MAX_OFFERS_PER_DEST: how many offers per "
            "destination to keep (cheapest wins, default 3)."
        ),
    )
    browser = run_p.add_mutually_exclusive_group()
    browser.add_argument(
        "--no-browser",
        dest="browser_mode",
        action="store_const",
        const="off",
        help=(
            "Disable the headless-Chromium fallback; use only plain "
            "httpx. Faster, but sites behind anti-bot walls or JS will "
            "return 0 offers."
        ),
    )
    browser.add_argument(
        "--browser-all",
        dest="browser_mode",
        action="store_const",
        const="all",
        help=(
            "Skip httpx entirely; fetch every site through a real "
            "browser. Slower (~3-6s/site) but matches what a human "
            "would see. Default is 'auto': httpx first, browser only "
            "for sites that came back empty or blocked."
        ),
    )
    run_p.set_defaults(browser_mode="auto")

    # `send`: run the scrape and email the result.
    send_p = sub.add_parser(
        "send",
        help=(
            "Scrape all sites and email the digest to the configured "
            "recipients. By default sends from andalath@gmail.com to "
            f"{', '.join(DEFAULT_RECIPIENTS)}."
        ),
    )
    send_p.add_argument(
        "--to",
        action="append",
        default=[],
        help=(
            "Override default recipients (repeatable). Also accepts a "
            "comma-separated list. If omitted, falls back to TRAVEL_EMAIL_TO "
            "env var, then to the hard-coded default."
        ),
    )
    send_p.add_argument(
        "--from",
        dest="from_email",
        default=None,
        help=(
            "Sender Gmail account. Must already be authenticated via "
            "`python -m gmail_agent auth`. Default: andalath@gmail.com "
            "(or TRAVEL_EMAIL_FROM env var)."
        ),
    )
    send_p.add_argument(
        "--site", action="append", default=[],
        help="Limit scraping to specific site IDs (repeatable).",
    )
    send_p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run the scrape and render the email but do NOT call Gmail's "
            "send endpoint. The HTML is written to logs/last_email.html "
            "for inspection."
        ),
    )
    send_p.add_argument(
        "--max-per-destination", type=int, default=None,
        help="Override TRAVEL_MAX_OFFERS_PER_DEST.",
    )
    send_browser = send_p.add_mutually_exclusive_group()
    send_browser.add_argument(
        "--no-browser", dest="browser_mode",
        action="store_const", const="off",
    )
    send_browser.add_argument(
        "--browser-all", dest="browser_mode",
        action="store_const", const="all",
    )
    send_p.set_defaults(browser_mode="auto")

    # `preview-email`: render an HTML email without sending it.
    preview_p = sub.add_parser(
        "preview-email",
        help=(
            "Run the scrape and write the rendered HTML email to a file "
            "(default: logs/last_email.html) without sending it."
        ),
    )
    preview_p.add_argument(
        "--out", default="logs/last_email.html",
        help="Path to write the HTML preview to.",
    )
    preview_p.add_argument(
        "--site", action="append", default=[],
        help="Limit scraping to specific site IDs (repeatable).",
    )
    preview_p.add_argument(
        "--max-per-destination", type=int, default=None,
        help="Override TRAVEL_MAX_OFFERS_PER_DEST.",
    )
    preview_browser = preview_p.add_mutually_exclusive_group()
    preview_browser.add_argument(
        "--no-browser", dest="browser_mode",
        action="store_const", const="off",
    )
    preview_browser.add_argument(
        "--browser-all", dest="browser_mode",
        action="store_const", const="all",
    )
    preview_p.set_defaults(browser_mode="auto")

    sub.add_parser("list-sites", help="Print the list of configured sites.")
    return parser.parse_args(argv)


def _select_sites(ids: list[str]) -> list:
    if not ids:
        return list(SITES)
    out = []
    for sid in ids:
        site = site_by_id(sid)
        if site is None:
            raise SystemExit(
                f"Unknown site id: {sid!r}. Valid: "
                + ", ".join(s.site_id for s in SITES)
            )
        out.append(site)
    return out


def _scrape(args, console: Console) -> tuple[Config, RunReport] | int:
    """Shared scraping pipeline used by run / send / preview-email.

    Returns ``(config, report)`` on success, or an int exit code on
    setup error (so the caller can ``return`` it immediately).
    """

    config = Config.load()
    if getattr(args, "max_per_destination", None) is not None:
        config = dataclasses.replace(
            config, max_offers_per_destination=args.max_per_destination
        )

    try:
        config.require_openai()
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2

    selected = _select_sites(args.site)
    browser_mode: BrowserMode = args.browser_mode

    if browser_mode != "off" and not browser_available():
        console.print(
            "[yellow]Playwright not installed — falling back to "
            "httpx-only mode. To enable the browser fallback:[/yellow]\n"
            "  [bold]pip install playwright && playwright install chromium[/bold]"
        )
        browser_mode = "off"

    console.print(
        f"[bold]Scraping {len(selected)} site(s) with model "
        f"{config.openai_model} (browser={browser_mode})…[/bold]"
    )

    def progress(sr: SiteResult) -> None:
        engine_tag = (
            f" [dim](via {sr.engine})[/dim]"
            if sr.engine != "http"
            else ""
        )
        if sr.ok:
            console.print(
                f"  [green]✓[/green] {sr.site_name}: "
                f"{len(sr.offers)} offer(s) "
                f"({sr.raw_chars:,} chars){engine_tag}"
            )
        else:
            console.print(
                f"  [red]✗[/red] {sr.site_name}: {sr.error}{engine_tag}"
            )

    def engine_switch(site: Site, engine: str) -> None:
        console.print(
            f"  [dim]… retrying {site.name} via {engine} "
            "(up to ~20s)[/dim]"
        )

    def browser_done(site: Site, fr) -> None:
        if fr.error:
            console.print(
                f"  [dim]  · {site.name} browser: {fr.error}[/dim]"
            )
            return
        if 200 <= fr.status < 300 and len(fr.text) > 0:
            console.print(
                f"  [dim]  · {site.name} browser: {len(fr.text):,} chars "
                f"from {fr.url}[/dim]"
            )
        else:
            console.print(
                f"  [dim]  · {site.name} browser: HTTP {fr.status}, "
                f"{len(fr.text)} chars[/dim]"
            )

    def extracting(site: Site) -> None:
        console.print(f"  [dim]⋯ extracting {site.name}…[/dim]")

    report = asyncio.run(
        run(
            config,
            selected_sites=selected,
            on_site_done=progress,
            browser_mode=browser_mode,
            on_engine_switch=engine_switch,
            on_browser_fetch_done=browser_done,
            on_extraction_start=extracting,
        )
    )
    return config, report


def _resolve_recipients(cli_to: list[str]) -> list[str]:
    """Pick final recipient list: CLI > env > hard-coded default."""

    if cli_to:
        recipients: list[str] = []
        for entry in cli_to:
            for piece in entry.replace(";", ",").split(","):
                piece = piece.strip()
                if piece:
                    recipients.append(piece)
        return recipients
    env_list = default_recipients_from_env(os.getenv("TRAVEL_EMAIL_TO"))
    if env_list:
        return env_list
    return list(DEFAULT_RECIPIENTS)


def _write_preview(report: RunReport, out_path: str) -> str:
    from pathlib import Path

    _, html_body = render_email_html(report)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_body, encoding="utf-8")
    return str(path.resolve())


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    console = Console()

    if args.command == "list-sites":
        table = Table(title="Configured sites")
        table.add_column("ID", style="bold")
        table.add_column("Name")
        table.add_column("Listing URLs", style="dim")
        for s in SITES:
            table.add_row(s.site_id, s.name, "\n".join(s.listing_urls))
        console.print(table)
        return 0

    if args.command == "run":
        result = _scrape(args, console)
        if isinstance(result, int):
            return result
        _config, report = result

        if args.json:
            print(_as_json(report))
            return 0

        console.print()
        _render_table(report, console)
        if not args.no_diagnostics:
            console.print()
            _render_diagnostics(report.sites, console)
        return 0

    if args.command == "preview-email":
        result = _scrape(args, console)
        if isinstance(result, int):
            return result
        _config, report = result
        out = _write_preview(report, args.out)
        console.print(f"\n[green]HTML preview written to[/green] {out}")
        console.print(
            "[dim]Open it in a browser to see exactly what gets emailed.[/dim]"
        )
        return 0

    if args.command == "send":
        result = _scrape(args, console)
        if isinstance(result, int):
            return result
        _config, report = result

        recipients = _resolve_recipients(args.to)
        from_email = args.from_email or os.getenv("TRAVEL_EMAIL_FROM") or None

        # Always drop the rendered HTML to disk for postmortem inspection.
        try:
            preview_path = _write_preview(report, "logs/last_email.html")
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


def _report_send(result: SendResult, console: Console) -> int:
    to_line = ", ".join(result.to)
    if result.sent:
        console.print(
            f"\n[green]✉ Sent[/green] from [bold]{result.from_email}[/bold] "
            f"to [bold]{to_line}[/bold]"
        )
        console.print(f"  Subject: {result.subject}")
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
    console.print(
        "  [dim]No email was sent. Inspect logs/last_email.html to see the "
        "rendered HTML.[/dim]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
