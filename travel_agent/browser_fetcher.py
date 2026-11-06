"""Headless-Chromium fallback for sites that don't return offers over plain HTTP.

Used by :mod:`agent` when ``httpx`` returns either a near-empty page, an
anti-bot interstitial, or a non-2xx response. Reuses a single browser
process across all fallback fetches to keep cost reasonable.

Playwright is an optional dependency: if it isn't installed (or the
matching Chromium hasn't been downloaded), :func:`browser_available`
returns False and the agent silently degrades to httpx-only.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from .fetcher import FetchResult, html_to_text


# Markers in cleaned text that mean "the server gave us a bot challenge,
# not real content". Used to decide a httpx fetch was wasted and the
# browser should retry.
_ANTI_BOT_MARKERS = (
    "pardon our interruption",
    "access denied",
    "are you a human",
    "checking your browser",
    "press & hold",
    "enable javascript",
    "cf-ray",  # Cloudflare challenge HTML
    "captcha",
)

# Common Polish cookie-consent button labels. We click them so that the
# real content underneath is visible — many sites lazy-load offer cards
# only after consent is given.
_CONSENT_SELECTORS = (
    "#didomi-notice-agree-button",
    "button#onetrust-accept-btn-handler",
    "button[aria-label*='Akceptuj']",
    "button[aria-label*='akceptuj']",
    "button:has-text('Akceptuj wszystkie')",
    "button:has-text('Akceptuję')",
    "button:has-text('Zaakceptuj')",
    "button:has-text('Zgadzam się')",
    "button:has-text('OK, rozumiem')",
    "button:has-text('Accept all')",
    "button:has-text('Allow all')",
)


def browser_available() -> bool:
    """Return True iff Playwright + a runnable Chromium are installed."""

    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except ImportError:
        return False
    return True


def looks_like_anti_bot(text: str) -> bool:
    """Heuristic: does this cleaned page text look like a bot challenge?"""

    if not text:
        return False
    sample = text[:1500].lower()
    return any(marker in sample for marker in _ANTI_BOT_MARKERS)


def needs_browser_retry(
    result: FetchResult,
    *,
    min_useful_chars: int,
) -> bool:
    """Decide whether to re-fetch ``result.url`` through a real browser.

    True if: the httpx fetch errored, returned a 4xx/5xx, returned too
    little usable text, or returned an anti-bot interstitial.
    """

    if result.error:
        return True
    if result.status == 0:
        return True
    if not 200 <= result.status < 300:
        return True
    if len(result.text) < min_useful_chars:
        return True
    if looks_like_anti_bot(result.text):
        return True
    return False


class BrowserSession:
    """Long-lived headless Chromium context.

    Designed to be entered once per ``run`` and reused across all
    fallback fetches. Construction is lazy: the actual browser only
    launches on first :meth:`fetch` call, so users who don't need the
    fallback never pay for a launch.
    """

    def __init__(
        self,
        *,
        user_agent: str,
        navigation_timeout_ms: int = 15_000,
        post_load_settle_ms: int = 1500,
        max_chars: int = 18_000,
    ) -> None:
        self._user_agent = user_agent
        self._nav_timeout = navigation_timeout_ms
        self._settle_ms = post_load_settle_ms
        self._max_chars = max_chars
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "BrowserSession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def _ensure_started(self) -> None:
        if self._context is not None:
            return
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        # Headless shell is what `playwright install chromium` ships by
        # default and is enough for read-only scraping. We pass a
        # realistic UA so anti-bot rules that key on the default
        # "HeadlessChrome" string don't trip.
        self._browser = await self._pw.chromium.launch(headless=True)
        self._context = await self._browser.new_context(
            user_agent=self._user_agent,
            locale="pl-PL",
            timezone_id="Europe/Warsaw",
            viewport={"width": 1366, "height": 900},
            extra_http_headers={
                "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.6,en;q=0.5",
            },
        )

    async def close(self) -> None:
        try:
            if self._context is not None:
                await self._context.close()
            if self._browser is not None:
                await self._browser.close()
        finally:
            self._context = None
            self._browser = None
            if self._pw is not None:
                await self._pw.stop()
                self._pw = None

    # Hard cap on the entire fetch for one URL — nav + settle + text
    # extraction included. Without this, a single stuck page (e.g.
    # Incapsula) holds up the rest of the queue.
    _PER_URL_HARD_TIMEOUT_S = 20.0

    async def fetch(self, url: str) -> FetchResult:
        """Navigate to ``url`` and return the cleaned HTML body."""

        async with self._lock:  # serialize pages on the shared context
            await self._ensure_started()
            try:
                return await asyncio.wait_for(
                    self._fetch_one(url),
                    timeout=self._PER_URL_HARD_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                return FetchResult(
                    url=url,
                    status=0,
                    text="",
                    error=f"timeout after {self._PER_URL_HARD_TIMEOUT_S:.0f}s",
                )

    async def _fetch_one(self, url: str) -> FetchResult:
        assert self._context is not None
        page = await self._context.new_page()
        try:
            try:
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self._nav_timeout,
                )
            except Exception as exc:  # noqa: BLE001
                return FetchResult(
                    url=url, status=0, text="", error=f"goto: {exc}"
                )

            await self._dismiss_consent(page)

            await _wait_for_content(
                page,
                min_text_chars=600,
                settle_ms=self._settle_ms,
            )

            final_url = page.url
            status = response.status if response is not None else 200

            try:
                rendered = await page.evaluate(
                    "() => document.body && document.body.innerText"
                ) or ""
            except Exception:  # noqa: BLE001
                html_doc = await page.content()
                rendered = html_to_text(html_doc, max_chars=self._max_chars)

            rendered = _collapse_whitespace(rendered)
            if len(rendered) > self._max_chars:
                rendered = rendered[: self._max_chars] + "\n[... truncated ...]"

            return FetchResult(url=final_url, status=status, text=rendered)
        finally:
            # CancelledError is a BaseException in 3.8+; we must catch
            # that too, otherwise a wait_for-triggered cancellation will
            # tear down the surrounding pipeline.
            try:
                await page.close()
            except BaseException:  # noqa: BLE001
                pass

    async def _dismiss_consent(self, page: Any) -> None:
        for sel in _CONSENT_SELECTORS:
            try:
                btn = await page.query_selector(sel)
            except Exception:  # noqa: BLE001
                continue
            if btn is None:
                continue
            try:
                await btn.click(timeout=2000)
                # Give the DOM a beat to remove the overlay.
                await asyncio.sleep(0.4)
                return
            except Exception:  # noqa: BLE001
                continue


async def _wait_for_content(
    page: Any, *, min_text_chars: int, settle_ms: int
) -> None:
    """Race body-text-grew vs. networkidle vs. a hard cap.

    Many travel SPAs fire DOMContentLoaded with an empty `<body>` and
    then hydrate offers asynchronously. networkidle isn't reliable on
    sites with ad/analytics pings. So we wait for either signal up to a
    cap of ``settle_ms + ~3s``.

    Important: this function MUST swallow ``asyncio.CancelledError`` from
    its inner racing tasks. Re-raising would tear down the parent's
    ``asyncio.wait_for`` budget and trip the entire fetcher pipeline.
    """

    cap_seconds = (settle_ms + 3000) / 1000

    async def grew_task() -> None:
        await page.wait_for_function(
            f"() => document.body && document.body.innerText.length >= {min_text_chars}",
            timeout=settle_ms + 3000,
        )

    async def idle_task() -> None:
        await page.wait_for_load_state("networkidle", timeout=settle_ms + 3000)

    grew = asyncio.create_task(grew_task())
    idle = asyncio.create_task(idle_task())

    try:
        _, pending = await asyncio.wait(
            {grew, idle},
            return_when=asyncio.FIRST_COMPLETED,
            timeout=cap_seconds,
        )
    except BaseException:  # noqa: BLE001 — propagate after cleaning up
        for task in (grew, idle):
            task.cancel()
        raise

    for task in pending:
        task.cancel()
    # Drain both tasks. CancelledError is a BaseException in 3.8+, so we
    # need to catch BaseException, not just Exception.
    for task in (grew, idle):
        try:
            await task
        except BaseException:  # noqa: BLE001
            pass


def _collapse_whitespace(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


@asynccontextmanager
async def open_session(
    *,
    user_agent: str,
    max_chars: int,
) -> AsyncIterator[BrowserSession]:
    """Async context manager that always closes the browser cleanly."""

    session = BrowserSession(user_agent=user_agent, max_chars=max_chars)
    try:
        yield session
    finally:
        await session.close()
