"""Data structures for tech news stories."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Story:
    headline: str
    summary: str
    topic: str
    importance: int
    engagement: int
    source_site: str
    source_url: str = ""

    @property
    def score(self) -> float:
        return (self.importance * 0.6) + (self.engagement * 0.4)


@dataclass
class SiteResult:
    site_id: str
    site_name: str
    url: str
    ok: bool
    stories: list[Story] = field(default_factory=list)
    error: str = ""
    raw_chars: int = 0
    engine: str = "http"


@dataclass
class DigestReport:
    sites: list[SiteResult]
    top_stories: list[Story]
    linkedin_draft: str
