"""Runtime configuration for the travel agent.

Reuses the same ``.env`` as gmail_agent so a single ``OPENAI_API_KEY``
covers both. Travel-specific knobs live under the ``TRAVEL_`` prefix.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    openai_api_key: str
    openai_model: str
    http_timeout: float
    fetch_concurrency: int
    max_text_chars: int
    max_offers_per_destination: int
    user_agent: str
    browser_retry_min_chars: int
    """Min chars of cleaned text from the httpx fetch below which the
    agent will retry through a real browser (``browser_mode='auto'``).
    Pages that come back shorter than this are almost certainly bot
    walls or pure JS shells with no offer content."""

    extract_concurrency: int
    """Max number of in-flight OpenAI extraction calls. We cap below the
    "all sites at once" default because tier 1 OpenAI accounts have a
    TPM limit (30k/min for gpt-4o-mini) that 10 simultaneous
    ~3-5k-token prompts can blow through, causing the SDK to silently
    exponential-back-off and the agent to look frozen."""

    @classmethod
    def load(cls) -> "Config":
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            openai_model=(
                os.getenv("TRAVEL_OPENAI_MODEL")
                or os.getenv("OPENAI_MODEL")
                or "gpt-4o-mini"
            ).strip(),
            http_timeout=float(os.getenv("TRAVEL_HTTP_TIMEOUT", "20")),
            fetch_concurrency=int(os.getenv("TRAVEL_FETCH_CONCURRENCY", "6")),
            # Per-site cap on text sent to the LLM. Polish travel pages can
            # be hundreds of kB; gpt-4o-mini can fit much more but we cap
            # for cost. Per-site, not global.
            max_text_chars=int(os.getenv("TRAVEL_MAX_TEXT_CHARS", "18000")),
            max_offers_per_destination=int(
                os.getenv("TRAVEL_MAX_OFFERS_PER_DEST", "3")
            ),
            user_agent=os.getenv(
                "TRAVEL_USER_AGENT",
                # A real-looking desktop UA. Many of these sites return a
                # short error page to obvious bot UAs.
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36",
            ),
            browser_retry_min_chars=int(
                os.getenv("TRAVEL_BROWSER_RETRY_MIN_CHARS", "2000")
            ),
            extract_concurrency=int(
                os.getenv("TRAVEL_EXTRACT_CONCURRENCY", "4")
            ),
        )

    def require_openai(self) -> None:
        if not self.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Add it to .env or your environment."
            )
