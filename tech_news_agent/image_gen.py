"""Generate a funny retro illustration for the daily email."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from openai import OpenAI

from .agent import RunReport
from .config import Config

ILLUSTRATION_CID = "illustration"
DEFAULT_IMAGE_PATH = Path("logs/last_tech_news_illustration.png")


@dataclass(frozen=True)
class IllustrationResult:
    png_bytes: bytes
    prompt: str
    saved_path: Path | None = None


def build_image_prompt(report: RunReport) -> str:
    headlines = "; ".join(
        story.headline for story in report.top_stories[:3]
    ) or "generic tech chaos"
    mood = (report.linkedin_draft or "")[:280].replace("\n", " ")
    return (
        "Single funny retro illustration for a fake news broadcast called "
        "'World Retro news'. "
        f"Visualize today's tech/crypto/AI headlines: {headlines}. "
        f"Tone of the day: {mood}. "
        "Style: 1980s TV news desk or vintage tabloid cover, synthwave neon, "
        "CRT screens, chunky computers, halftone print, exaggerated cartoon "
        "humor, absurd visual metaphor, scanlines, VHS grain. "
        "Make it witty and ridiculous, not corporate. "
        "No logos, no watermarks, no readable text except a stylized retro "
        "title banner that says WORLD RETRO NEWS."
    )


class IllustrationGenerator:
    REQUEST_TIMEOUT_SECONDS = 120.0
    MAX_RETRIES = 1

    def __init__(self, config: Config) -> None:
        config.require_openai()
        self._client = OpenAI(
            api_key=config.openai_api_key,
            timeout=self.REQUEST_TIMEOUT_SECONDS,
            max_retries=self.MAX_RETRIES,
        )
        self._model = config.image_model
        self._size = config.image_size

    def generate(
        self,
        report: RunReport,
        *,
        save_to: Path | None = DEFAULT_IMAGE_PATH,
    ) -> IllustrationResult:
        prompt = build_image_prompt(report)
        response = self._client.images.generate(
            model=self._model,
            prompt=prompt,
            size=self._size,
            n=1,
        )
        item = response.data[0]
        if item.b64_json:
            png_bytes = base64.b64decode(item.b64_json)
        elif item.url:
            import httpx

            png_bytes = httpx.get(item.url, timeout=60).content
        else:
            raise RuntimeError("Image API returned no image data")

        saved_path: Path | None = None
        if save_to is not None:
            save_to.parent.mkdir(parents=True, exist_ok=True)
            save_to.write_bytes(png_bytes)
            saved_path = save_to.resolve()

        return IllustrationResult(
            png_bytes=png_bytes,
            prompt=prompt,
            saved_path=saved_path,
        )


def generate_illustration(
    report: RunReport,
    config: Config | None = None,
    *,
    save_to: Path | None = DEFAULT_IMAGE_PATH,
) -> IllustrationResult | None:
    """Best-effort illustration generation; returns None on failure."""

    cfg = config or Config.load()
    if not cfg.generate_illustrations:
        return None
    try:
        return IllustrationGenerator(cfg).generate(report, save_to=save_to)
    except Exception:  # noqa: BLE001
        return None
