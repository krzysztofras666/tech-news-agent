"""Runtime configuration for the tech news agent."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_DEFAULT_AUTHOR_PROFILE = (
    Path(__file__).resolve().parent / "author_profile.md"
)


@dataclass(frozen=True)
class Config:
    openai_api_key: str
    openai_model: str
    http_timeout: float
    fetch_concurrency: int
    max_text_chars: int
    user_agent: str
    browser_retry_min_chars: int
    extract_concurrency: int
    max_stories_per_site: int
    max_top_stories: int
    author_profile_path: Path
    image_model: str
    image_size: str
    generate_illustrations: bool

    @classmethod
    def load(cls) -> "Config":
        profile_env = os.getenv("TECH_NEWS_AUTHOR_PROFILE", "").strip()
        profile_path = Path(profile_env) if profile_env else _DEFAULT_AUTHOR_PROFILE
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            openai_model=(
                os.getenv("TECH_NEWS_OPENAI_MODEL")
                or os.getenv("OPENAI_MODEL")
                or "gpt-4o-mini"
            ).strip(),
            http_timeout=float(os.getenv("TECH_NEWS_HTTP_TIMEOUT", "20")),
            fetch_concurrency=int(os.getenv("TECH_NEWS_FETCH_CONCURRENCY", "6")),
            max_text_chars=int(os.getenv("TECH_NEWS_MAX_TEXT_CHARS", "18000")),
            user_agent=os.getenv(
                "TECH_NEWS_USER_AGENT",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36",
            ),
            browser_retry_min_chars=int(
                os.getenv("TECH_NEWS_BROWSER_RETRY_MIN_CHARS", "2000")
            ),
            extract_concurrency=int(os.getenv("TECH_NEWS_EXTRACT_CONCURRENCY", "4")),
            max_stories_per_site=int(os.getenv("TECH_NEWS_MAX_STORIES_PER_SITE", "10")),
            max_top_stories=int(os.getenv("TECH_NEWS_MAX_TOP_STORIES", "5")),
            author_profile_path=profile_path,
            image_model=os.getenv("TECH_NEWS_IMAGE_MODEL", "gpt-image-1").strip(),
            image_size=os.getenv("TECH_NEWS_IMAGE_SIZE", "1024x1024").strip(),
            generate_illustrations=os.getenv(
                "TECH_NEWS_GENERATE_ILLUSTRATIONS", "1"
            ).strip().lower() not in {"0", "false", "no", "off"},
        )

    def require_openai(self) -> None:
        if not self.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Add it to .env or your environment."
            )
