"""LLM-powered headline extraction from scraped tech news pages."""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from openai import OpenAI

from .config import Config
from .models import Story


SYSTEM_PROMPT = """\
You extract tech, AI, and crypto news headlines from messy text scraped
off a news site. Only include stories about technology, artificial
intelligence, startups, chips/semiconductors, crypto, blockchain, DeFi,
or Web3. Skip sports, celebrity gossip, general politics (unless directly
about tech/AI/crypto policy), entertainment, and product deal roundups.

Return STRICT JSON of the form:

{{"stories": [
  {{
    "headline": "Apple finally ships its AI-powered Siri update",
    "summary": "One sentence on why it matters.",
    "topic": "AI",
    "importance": 8,
    "engagement": 9,
    "article_url": "https://example.com/story-path"
  }}
]}}

Hard rules:
- Output ONLY JSON. No prose, no markdown fences.
- Include only real news stories visible on the page. Skip nav links,
  newsletter CTAs, cookie notices, buying guides, deal roundups, and
  sponsored posts unless they are clearly major breaking news.
- headline: concise, factual, in the site's own wording when possible.
- summary: one sentence explaining why a professional audience should care.
- topic: short label such as AI, crypto, blockchain, Web3, DeFi, tech,
  startups, hardware, policy, security, business.
- importance: integer 1-10 for industry/significance impact.
- engagement: integer 1-10 for how likely this is to spark discussion.
- article_url: full https URL to the specific story if visible in the scraped
  text; otherwise null. Do not invent URLs.
- Deduplicate near-identical headlines on the same page.
- If the page is empty or blocked, return {{"stories": []}}.
- Maximum {max_stories} stories per response — prefer the biggest,
  freshest, most widely relevant items.
"""


def _user_prompt(site_name: str, url: str, text: str) -> str:
    return (
        f"Source site: {site_name}\n"
        f"Source URL: {url}\n"
        f"Today: {date.today().isoformat()}\n"
        "Scraped page text (already stripped of HTML):\n"
        "---\n"
        f"{text}\n"
        "---\n"
        "Return the JSON now."
    )


class StoryExtractor:
    REQUEST_TIMEOUT_SECONDS = 60.0
    MAX_RETRIES = 1

    def __init__(self, config: Config) -> None:
        config.require_openai()
        self._client = OpenAI(
            api_key=config.openai_api_key,
            timeout=self.REQUEST_TIMEOUT_SECONDS,
            max_retries=self.MAX_RETRIES,
        )
        self._model = config.openai_model
        self._max_stories = config.max_stories_per_site

    def extract(self, *, site_name: str, url: str, text: str) -> list[Story]:
        if not text.strip():
            return []

        system = SYSTEM_PROMPT.format(max_stories=self._max_stories)
        response = self._client.chat.completions.create(
            model=self._model,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": _user_prompt(site_name, url, text)},
            ],
        )
        raw = (response.choices[0].message.content or "").strip()
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []

        items = data.get("stories") or []
        if not isinstance(items, list):
            return []

        out: list[Story] = []
        for item in items:
            story = _coerce(item, site_name=site_name, url=url)
            if story is not None:
                out.append(story)
        return out


def _coerce(item: Any, *, site_name: str, url: str) -> Story | None:
    if not isinstance(item, dict):
        return None
    headline = _clean_str(item.get("headline"))
    if not headline or len(headline) < 12:
        return None
    summary = _clean_str(item.get("summary"))
    topic = _clean_str(item.get("topic")) or "tech"
    importance = _clamp_score(item.get("importance"))
    engagement = _clamp_score(item.get("engagement"))
    article_url = _clean_str(item.get("article_url"))
    if not article_url.startswith("http"):
        article_url = url
    return Story(
        headline=headline,
        summary=summary,
        topic=topic,
        importance=importance,
        engagement=engagement,
        source_site=site_name,
        source_url=article_url,
    )


def _clean_str(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _clamp_score(value: Any) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError):
        return 5
    return max(1, min(10, score))
