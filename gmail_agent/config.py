"""Runtime configuration loaded from environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# gmail.modify is enough to read messages and create drafts, but cannot send.
# That's intentional — the agent must never send mail on its own.
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


@dataclass(frozen=True)
class Config:
    openai_api_key: str
    openai_model: str
    credentials_file: Path
    legacy_token_file: Path
    accounts_dir: Path
    user_profile: str
    agent_label_name: str

    @classmethod
    def load(cls) -> "Config":
        openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
        credentials_file = Path(
            os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
        ).expanduser()
        legacy_token_file = Path(
            os.getenv("GOOGLE_TOKEN_FILE", "token.json")
        ).expanduser()
        accounts_dir = Path(os.getenv("AGENT_ACCOUNTS_DIR", "accounts")).expanduser()
        user_profile = os.getenv("USER_PROFILE", "").strip()
        agent_label_name = (
            os.getenv("AGENT_LABEL_NAME", "agent/drafted").strip() or "agent/drafted"
        )

        return cls(
            openai_api_key=openai_api_key,
            openai_model=openai_model,
            credentials_file=credentials_file,
            legacy_token_file=legacy_token_file,
            accounts_dir=accounts_dir,
            user_profile=user_profile,
            agent_label_name=agent_label_name,
        )

    def require_openai(self) -> None:
        if not self.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Add it to .env or your environment."
            )

    def require_google_credentials(self) -> None:
        if not self.credentials_file.exists():
            raise RuntimeError(
                "Google OAuth client file not found at "
                f"{self.credentials_file.resolve()}.\n\n"
                "You need to create a Desktop-app OAuth client in Google Cloud "
                "Console and download its JSON. One-time setup:\n"
                "  1. https://console.cloud.google.com/ — create or pick a project.\n"
                "  2. APIs & Services → Library → enable 'Gmail API'.\n"
                "  3. APIs & Services → OAuth consent screen → User Type 'External',\n"
                "     add yourself under 'Test users'. (No need to publish.)\n"
                "  4. APIs & Services → Credentials → + Create Credentials →\n"
                "     OAuth client ID → Application type 'Desktop app' → Create.\n"
                "  5. Click 'Download JSON' next to the new client.\n"
                f"  6. Save the file as: {self.credentials_file.resolve()}\n"
                "     (or set GOOGLE_CREDENTIALS_FILE in .env to its path).\n"
                "Then re-run `python -m gmail_agent auth`."
            )
