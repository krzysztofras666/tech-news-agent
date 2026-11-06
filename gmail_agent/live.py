"""Fetch external URLs and turn them into plain-text knowledge for prompts.

Used to augment a per-account ``knowledge.md`` with up-to-date data such as
the current training schedule on a public website. Results are cached in
memory per-process so multiple emails in one run cost a single fetch.

For known structured sources (e.g. the Leaders Island schedule page) the
output is normalized into a per-training schedule table that's easy for the
model to quote — flattened text would otherwise hide which date entries
belong to which course, especially for multi-session editions like
"20-21 lipca; 3-4 sierpnia 2026 / Kraków".
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from typing import Iterable

from bs4 import BeautifulSoup


_USER_AGENT = "gmail-agent/0.1 (+https://github.com)"
_CACHE: dict[str, str] = {}

# Generous: gpt-4o-mini has 128k context, so we'd rather show too much than
# silently drop dates. Combined knowledge is later passed through without
# further truncation.
_DEFAULT_MAX_CHARS = 40000

_POLISH_MONTHS = (
    "stycznia",
    "lutego",
    "marca",
    "kwietnia",
    "maja",
    "czerwca",
    "lipca",
    "sierpnia",
    "września",
    "października",
    "listopada",
    "grudnia",
)
_MONTH_RE = re.compile(
    r"\b(?:" + "|".join(_POLISH_MONTHS) + r")\b", re.IGNORECASE
)
_PRICE_RE = re.compile(r"\d[\d\s.,]*\s*(?:PLN|USD|EUR)\b", re.IGNORECASE)


def fetch_url(url: str, *, timeout: float = 15.0) -> str:
    """Fetch ``url`` and return raw HTML/text, or empty string on failure."""

    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return ""
    return data.decode("utf-8", errors="replace")


def html_to_text(html: str, *, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[... truncated ...]"
    return text


def fetch_url_text(
    url: str, *, timeout: float = 15.0, max_chars: int = _DEFAULT_MAX_CHARS
) -> str:
    """Return the page body as collapsed plain text (cached)."""

    if url in _CACHE:
        return _CACHE[url]
    html = fetch_url(url, timeout=timeout)
    text = html_to_text(html, max_chars=max_chars) if html else ""
    _CACHE[url] = text
    return text


def fetch_leadersisland_schedule(
    url: str = "https://leadersisland.com/szkolenia/",
    *,
    timeout: float = 15.0,
) -> str:
    """Return a clean per-training schedule table parsed from the live page.

    Falls back to plain-text extraction if the page structure changes and
    we can't find any cards.
    """

    cache_key = f"__schedule::{url}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    html = fetch_url(url, timeout=timeout)
    if not html:
        _CACHE[cache_key] = ""
        return ""

    parsed = parse_leadersisland_schedule(html)
    if not parsed:
        parsed = html_to_text(html)
    _CACHE[cache_key] = parsed
    return parsed


def parse_leadersisland_schedule(html: str) -> str:
    """Parse the leadersisland.com/szkolenia/ HTML into a clean schedule.

    Returns an empty string if the expected card structure isn't found.
    """

    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.e-loop-item")
    # Detail cards (the ones with actual schedules) include "Należy do obszaru"
    detail = [c for c in cards if "Należy do obszaru" in c.get_text()]
    if not detail:
        return ""

    out: list[str] = []
    for card in detail:
        entry = _parse_card_text(card.get_text("\n", strip=True))
        if entry:
            out.append(entry)
    if not out:
        return ""
    header = (
        "Aktualny harmonogram szkoleń Leaders Island\n"
        "(źródło: https://leadersisland.com/szkolenia/, dane na żywo)\n"
        "Reguły czytania:\n"
        "  * Każdy blok = jedno szkolenie. Pod 'Terminy:' są kolejne edycje.\n"
        "  * Pojedyncza edycja może mieć kilka dat oddzielonych ';' lub ','\n"
        "    — wszystkie te dni należą do TEJ SAMEJ edycji szkolenia\n"
        "    (np. '20-21 lipca; 3-4 sierpnia 2026' = jedna edycja, dwa zjazdy).\n"
        "  * Etykiety w nawiasach (np. '(Brak miejsc)', '(Ostatnie miejsca)')\n"
        "    dotyczą daty poprzedzającej je w tej samej linii bloku.\n"
        "  * 'zapisy: biuro@leadersisland.com' = brak otwartej daty,\n"
        "    klient ustala termin mailowo.\n"
    )
    return header + "\n" + "\n\n".join(out)


def _parse_card_text(text: str) -> str | None:
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if "Należy do obszaru" not in lines:
        return None

    name_lines: list[str] = []
    i = 0
    while i < len(lines) and lines[i] != "Należy do obszaru":
        name_lines.append(lines[i])
        i += 1
    name = " ".join(name_lines).strip()
    if not name:
        return None
    i += 1  # skip "Należy do obszaru"

    area = lines[i] if i < len(lines) else ""
    i += 1

    description_lines: list[str] = []
    dates: list[str] = []
    price_parts: list[str] = []

    while i < len(lines):
        line = lines[i]
        if line == "Zapisz się":
            break
        if line == "/cena za osobę":
            i += 1
            continue
        if _looks_like_date(line):
            entry = line
            # Pull in an immediately-following status label like "(Brak miejsc)".
            while i + 1 < len(lines) and lines[i + 1].startswith("("):
                entry += " " + lines[i + 1]
                i += 1
            dates.append(_normalize_whitespace(entry))
            i += 1
            continue
        if _looks_like_price(line):
            price_parts.append(_normalize_whitespace(line))
            i += 1
            continue
        # Anything else is description (only first card lines before dates).
        if not dates and not price_parts:
            description_lines.append(line)
        i += 1

    description = _normalize_whitespace(" ".join(description_lines))
    price = " / ".join(price_parts) if price_parts else ""

    out_lines = [f"Szkolenie: {name}"]
    if area:
        out_lines.append(f"  Obszar: {area}")
    if description:
        out_lines.append(f"  Opis: {description}")
    if price:
        out_lines.append(f"  Cena: {price}")
    if dates:
        out_lines.append("  Terminy:")
        for d in dates:
            out_lines.append(f"    - {d}")
    else:
        out_lines.append("  Terminy: brak otwartej rekrutacji, pytania na biuro@leadersisland.com")
    return "\n".join(out_lines)


def _looks_like_date(line: str) -> bool:
    if _MONTH_RE.search(line):
        return True
    lowered = line.lower()
    if lowered.startswith("piąty moduł") or lowered.startswith("zapisy:"):
        return True
    if lowered.strip() in ("on-line", "online"):
        return True
    return False


def _looks_like_price(line: str) -> bool:
    return bool(_PRICE_RE.search(line))


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ---- public composition -------------------------------------------------


def build_live_context(urls: Iterable[str]) -> str:
    """Return a single blob containing the fetched content of each URL.

    For known structured sources we use a hand-tuned parser; for everything
    else we fall back to a cleaned-up plain-text dump.
    """

    sections: list[str] = []
    for url in urls:
        body = ""
        if _is_leadersisland_schedule(url):
            body = fetch_leadersisland_schedule(url)
        if not body:
            body = fetch_url_text(url)
        if not body:
            continue
        sections.append(f"Source: {url}\n\n{body}")
    return "\n\n---\n\n".join(sections)


def _is_leadersisland_schedule(url: str) -> bool:
    return "leadersisland.com" in url and "/szkolenia" in url
