"""Per-Gmail-account configuration.

Each account lives in ``accounts/<email>/`` and may contain:

- ``token.json`` (required for `run`) — OAuth refresh token for that account.
- ``profile.md`` (optional) — text describing the user / desired tone,
  overrides the global ``USER_PROFILE`` env var.
- ``knowledge.md`` (optional) — free-form context (product info, FAQs,
  policies) that is injected into the model prompt for every draft.
- ``live_sources.txt`` (optional) — one URL per line; fetched on each run
  (with a small in-process cache) and appended to the knowledge so the
  model has fresh data like current training dates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Account:
    email: str
    directory: Path

    @property
    def token_file(self) -> Path:
        return self.directory / "token.json"

    @property
    def profile_file(self) -> Path:
        return self.directory / "profile.md"

    @property
    def knowledge_file(self) -> Path:
        return self.directory / "knowledge.md"

    @property
    def live_sources_file(self) -> Path:
        return self.directory / "live_sources.txt"

    def read_profile(self) -> str:
        return _read_optional(self.profile_file)

    def read_knowledge(self) -> str:
        return _read_optional(self.knowledge_file)

    def read_live_sources(self) -> list[str]:
        raw = _read_optional(self.live_sources_file)
        urls: list[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line)
        return urls


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def list_accounts(accounts_dir: Path) -> list[Account]:
    """Return all accounts that have a token file, sorted by email."""

    if not accounts_dir.exists():
        return []
    found: list[Account] = []
    for child in sorted(accounts_dir.iterdir()):
        if not child.is_dir():
            continue
        # We only treat directories named like an email as accounts.
        if not _EMAIL_RE.match(child.name):
            continue
        if not (child / "token.json").exists():
            continue
        found.append(Account(email=child.name, directory=child))
    return found


def account_for(accounts_dir: Path, email: str) -> Account:
    """Return the Account object for ``email``, creating its directory."""

    safe = email.strip().lower()
    if not _EMAIL_RE.match(safe):
        raise ValueError(f"Not a valid-looking email: {email!r}")
    directory = accounts_dir / safe
    directory.mkdir(parents=True, exist_ok=True)
    return Account(email=safe, directory=directory)


def _read_optional(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
