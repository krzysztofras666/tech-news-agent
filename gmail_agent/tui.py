"""Interactive draft review UI.

Shown for each generated draft when the user runs with ``--review``.
Lets them accept, edit in $EDITOR, view the full original, regenerate
(with optional steering), skip, or quit the run.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable, Literal

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from .gmail_client import EmailMessageView


Action = Literal["accept", "skip", "quit"]


@dataclass
class ReviewDecision:
    action: Action
    body: str = ""


# A function that given optional steering text returns a fresh draft body
# (or None if the model declined). Used to wire the LLM regenerate call in
# from the caller without making this module depend on it directly.
RegenerateFn = Callable[["str | None"], "str | None"]


def review_draft(
    console: Console,
    *,
    index: int,
    total: int,
    email: EmailMessageView,
    initial_body: str,
    regenerate: RegenerateFn,
) -> ReviewDecision:
    """Show the review UI for a single draft and return the user's decision."""

    body = initial_body
    while True:
        _render(console, index=index, total=total, email=email, body=body)
        choice = _prompt_choice(console)

        if choice == "a":
            return ReviewDecision("accept", body=body)
        if choice == "s":
            return ReviewDecision("skip")
        if choice == "q":
            return ReviewDecision("quit")
        if choice == "v":
            _show_full_original(console, email)
            continue
        if choice == "e":
            edited = _edit_in_editor(body)
            if edited is None:
                console.print("[yellow]Edit aborted; keeping previous draft.[/yellow]")
            else:
                body = edited.rstrip() + "\n"
            continue
        if choice == "r":
            steering = _prompt_steering(console)
            console.print("[dim]Regenerating...[/dim]")
            try:
                new_body = regenerate(steering)
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]Regenerate failed:[/red] {exc}")
                continue
            if new_body is None:
                console.print(
                    "[yellow]Model returned SKIP. Keeping previous draft.[/yellow]"
                )
            else:
                body = new_body
            continue


# --- rendering ------------------------------------------------------------


def _render(
    console: Console,
    *,
    index: int,
    total: int,
    email: EmailMessageView,
    body: str,
) -> None:
    console.print(Rule(f"Draft {index} of {total}", style="cyan"))

    header = Text()
    header.append(f"From:    ", style="bold")
    header.append(f"{email.from_name} <{email.from_email}>\n")
    header.append(f"Subject: ", style="bold")
    header.append(f"{email.subject}\n")
    header.append(f"Date:    ", style="bold")
    header.append(f"{email.date}")
    console.print(Panel(header, border_style="cyan", title="incoming", title_align="left"))

    original = email.body_text.strip() or "(empty)"
    if len(original) > 1200:
        original = original[:1200] + "\n[...truncated; press 'v' to see full original...]"
    console.print(
        Panel(
            Text(original),  # Text() prevents Rich from parsing [..] as markup
            border_style="grey50",
            title="original",
            title_align="left",
        )
    )

    draft = body.strip() or "(empty)"
    console.print(
        Panel(
            Text(draft),
            border_style="green",
            title="draft reply",
            title_align="left",
        )
    )


def _show_full_original(console: Console, email: EmailMessageView) -> None:
    console.print(
        Panel(
            Text(email.body_text or "(empty)"),
            border_style="grey50",
            title=f"full original — {email.subject}",
            title_align="left",
        )
    )


# --- prompts --------------------------------------------------------------


_VALID_CHOICES = {"a", "e", "v", "r", "s", "q"}


def _prompt_choice(console: Console) -> str:
    console.print(
        "[bold green]\\[a][/bold green] accept   "
        "[bold]\\[e][/bold] edit   "
        "[bold]\\[v][/bold] view full original   "
        "[bold]\\[r][/bold] regenerate   "
        "[bold]\\[s][/bold] skip   "
        "[bold red]\\[q][/bold red] quit"
    )
    while True:
        try:
            choice = input("> ").strip().lower()
        except EOFError:
            return "q"
        if choice in _VALID_CHOICES:
            return choice
        console.print(
            f"[red]Unknown choice {choice!r}. Pick one of "
            f"{', '.join(sorted(_VALID_CHOICES))}.[/red]"
        )


def _prompt_steering(console: Console) -> str | None:
    console.print(
        "[dim]Optional guidance for the regenerate "
        "(e.g. 'shorter', 'decline politely'). Enter to skip:[/dim]"
    )
    try:
        text = input("> ").strip()
    except EOFError:
        return None
    return text or None


# --- $EDITOR --------------------------------------------------------------


def _edit_in_editor(body: str) -> str | None:
    """Open ``body`` in the user's $EDITOR and return the edited text.

    Returns None if the editor exited non-zero, the file disappeared, or
    something else went wrong.
    """

    editor = (
        os.environ.get("VISUAL")
        or os.environ.get("EDITOR")
        or ("notepad" if os.name == "nt" else "vi")
    )

    path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".gmail-agent.txt", mode="w", encoding="utf-8", delete=False
        ) as fh:
            fh.write(body)
            path = fh.name

        try:
            rc = subprocess.call([editor, path])
        except FileNotFoundError:
            return None
        if rc != 0:
            return None

        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
