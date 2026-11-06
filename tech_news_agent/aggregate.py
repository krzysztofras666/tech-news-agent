"""Deduplicate, filter, and rank stories across sources."""

from __future__ import annotations

import re

from .models import Story

FOCUS_TOPIC_KEYWORDS = (
    "ai",
    "artificial intelligence",
    "machine learning",
    "llm",
    "chatgpt",
    "openai",
    "anthropic",
    "crypto",
    "cryptocurrency",
    "bitcoin",
    "ethereum",
    "blockchain",
    "web3",
    "defi",
    "nft",
    "token",
    "stablecoin",
    "tech",
    "startup",
    "chip",
    "semiconductor",
    "robot",
    "autonomous",
)

FOCUS_TOPICS = {
    "ai",
    "crypto",
    "cryptocurrency",
    "blockchain",
    "web3",
    "defi",
    "bitcoin",
    "ethereum",
    "tech",
    "startups",
    "startup",
    "hardware",
    "policy",
    "security",
    "business",
}


def is_focus_story(story: Story) -> bool:
    topic = story.topic.lower().strip()
    if topic in FOCUS_TOPICS:
        return True
    blob = f"{story.headline} {story.summary} {story.topic}".lower()
    return any(keyword in blob for keyword in FOCUS_TOPIC_KEYWORDS)


def filter_focus_stories(stories: list[Story]) -> list[Story]:
    return [story for story in stories if is_focus_story(story)]


def dedupe_stories(stories: list[Story]) -> list[Story]:
    best_by_key: dict[str, Story] = {}
    for story in stories:
        key = _headline_key(story.headline)
        existing = best_by_key.get(key)
        if existing is None or story.score > existing.score:
            best_by_key[key] = story
    return list(best_by_key.values())


def pick_top_stories(stories: list[Story], *, limit: int) -> list[Story]:
    focused = filter_focus_stories(stories)
    ranked = sorted(
        dedupe_stories(focused),
        key=lambda story: (story.score, story.importance, story.engagement),
        reverse=True,
    )
    return ranked[:limit]


def _headline_key(headline: str) -> str:
    text = headline.lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    words = text.split()
    return " ".join(words[:8])
