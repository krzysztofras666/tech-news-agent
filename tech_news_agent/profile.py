"""Load the author profile used to personalize LinkedIn drafts."""

from __future__ import annotations

from pathlib import Path

_DEFAULT_PROFILE = Path(__file__).resolve().parent / "author_profile.md"


def load_author_profile(path: Path | None = None) -> str:
    profile_path = path or _DEFAULT_PROFILE
    if not profile_path.is_file():
        return ""
    return profile_path.read_text(encoding="utf-8").strip()
