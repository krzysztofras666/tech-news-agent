"""Data structures for travel offers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Offer:
    """A single travel offer extracted from a source page.

    Fields are intentionally permissive — many source pages omit one or
    more of these, and we'd rather keep a partial offer than drop it.
    """

    destination: str
    """Human-readable destination, normalized lowercase-stripped for grouping
    via :attr:`destination_key`. Original casing is preserved here for
    display."""

    departure_date: date | None
    """Start of the trip. ``None`` if the source didn't specify one."""

    return_date: date | None
    """End of the trip. ``None`` if not specified."""

    price: float | None
    """Total price per person (or per offer) in :attr:`currency`."""

    currency: str
    """ISO-ish currency code, e.g. ``PLN``, ``EUR``, ``USD``."""

    source_site: str
    """Friendly site name, e.g. ``itaka.pl``."""

    source_url: str
    """URL the offer was extracted from."""

    title: str = ""
    """Short marketing title from the source, optional."""

    duration_nights: int | None = None
    """Trip length in nights, if the source mentioned it."""

    notes: str = ""
    """Anything else worth surfacing (board type, departure city, etc.)."""

    @property
    def destination_key(self) -> str:
        """Lower-cased, whitespace-collapsed key used for grouping."""

        return " ".join(self.destination.lower().split())

    @property
    def sort_date(self) -> date:
        """Date used for chronological ordering.

        Falls back to ``date.max`` so offers without a date sink to the
        bottom rather than crashing the sort.
        """

        return self.departure_date or date.max


@dataclass
class SiteResult:
    """Outcome of scraping a single source site."""

    site_id: str
    site_name: str
    url: str
    ok: bool
    offers: list[Offer] = field(default_factory=list)
    error: str = ""
    raw_chars: int = 0
    """Number of characters of cleaned text we sent to the LLM. Useful
    for debugging sites that come back almost empty (JS-rendered)."""

    engine: str = "http"
    """Which fetcher actually produced the text we extracted from:
    ``"http"`` (plain httpx) or ``"browser"`` (Playwright Chromium).
    Set automatically by :func:`travel_agent.agent.run`."""
