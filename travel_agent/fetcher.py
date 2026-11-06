"""Async HTTP fetching + HTML-to-text cleaning.

We fetch all sites concurrently with realistic browser headers; tour
operator sites tend to return a tiny anti-bot stub if the UA looks
automated. The fetched HTML is cleaned to a compact text stream that's
cheap to send to the LLM.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup, Comment


@dataclass
class FetchResult:
    url: str
    status: int
    text: str
    """Cleaned plain-text snippet of the page body."""
    error: str = ""


def _default_headers(user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        # Polish-first; many of these portals geo-redirect based on this.
        "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.6,en;q=0.5",
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Connection": "keep-alive",
    }


async def fetch_one(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_chars: int,
) -> FetchResult:
    try:
        resp = await client.get(url, follow_redirects=True)
    except httpx.HTTPError as exc:
        return FetchResult(url=url, status=0, text="", error=str(exc))

    body = resp.text or ""
    cleaned = html_to_text(body, max_chars=max_chars)
    return FetchResult(url=str(resp.url), status=resp.status_code, text=cleaned)


async def fetch_first_usable(
    client: httpx.AsyncClient,
    urls: tuple[str, ...],
    *,
    max_chars: int,
    min_useful_chars: int = 500,
) -> FetchResult:
    """Try ``urls`` in order; return the first 2xx response with enough text.

    Falls back to the last attempt even if it didn't meet the threshold,
    so the caller still has *something* to inspect.
    """

    last: FetchResult | None = None
    for url in urls:
        result = await fetch_one(client, url, max_chars=max_chars)
        last = result
        if 200 <= result.status < 300 and len(result.text) >= min_useful_chars:
            return result
    return last or FetchResult(url=urls[0] if urls else "", status=0, text="")


async def fetch_all(
    site_urls: list[tuple[str, tuple[str, ...]]],
    *,
    user_agent: str,
    timeout: float,
    concurrency: int,
    max_chars: int,
) -> dict[str, FetchResult]:
    """Fetch all sites concurrently.

    ``site_urls`` is a list of ``(site_id, urls)``. Returns a dict
    keyed by ``site_id``.
    """

    headers = _default_headers(user_agent)
    limits = httpx.Limits(
        max_connections=concurrency, max_keepalive_connections=concurrency
    )
    sem = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        limits=limits,
        http2=False,
    ) as client:

        async def worker(site_id: str, urls: tuple[str, ...]) -> tuple[str, FetchResult]:
            async with sem:
                res = await fetch_first_usable(client, urls, max_chars=max_chars)
                return site_id, res

        tasks = [worker(sid, urls) for sid, urls in site_urls]
        results = await asyncio.gather(*tasks)
    return dict(results)


# ---- HTML cleaning ------------------------------------------------------


# Tags that almost never carry offer content; dropping them shrinks the
# text we ship to the LLM by an order of magnitude.
_STRIP_TAGS = (
    "script",
    "style",
    "noscript",
    "svg",
    "iframe",
    "header",
    "footer",
    "nav",
    "form",
    "input",
    "button",
)


def html_to_text(html: str, *, max_chars: int) -> str:
    """Strip a page down to compact plain text suitable for an LLM prompt.

    For RSS/Atom feeds (used by wakacyjnipiraci.pl, kanalwyjazdowy.pl) we
    skip the heavy DOM stripping and just collapse whitespace, because the
    XML structure itself is already compact and useful.
    """

    if not html:
        return ""

    if _looks_like_feed(html):
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)
        return _clip(text.strip(), max_chars)

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(_STRIP_TAGS)):
        tag.decompose()
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()

    text = soup.get_text("\n", strip=True)
    # Collapse runs of blank lines so we don't waste tokens on whitespace.
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return _clip(text, max_chars)


def _looks_like_feed(html: str) -> bool:
    head = html.lstrip()[:200].lower()
    return head.startswith("<?xml") or "<rss" in head or "<feed" in head


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[... truncated ...]"
