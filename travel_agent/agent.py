"""End-to-end pipeline: fetch -> (browser fallback) -> extract -> aggregate."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Literal

from .aggregate import dedupe, drop_past_departures, flatten_sorted_by_date, group_by_destination
from .browser_fetcher import (
    BrowserSession,
    browser_available,
    looks_like_anti_bot,
    needs_browser_retry,
    open_session,
)
from .config import Config
from .extract import OfferExtractor
from .fetcher import FetchResult, fetch_all
from .models import Offer, SiteResult
from .sites import SITES, Site


BrowserMode = Literal["auto", "all", "off"]
"""How the agent uses Playwright:

- ``"auto"`` (default): try httpx first, fall back to a real browser
  for sites that came back empty / blocked / anti-bot.
- ``"all"``: skip httpx entirely; fetch every site through the browser.
- ``"off"``: never use the browser; httpx only.
"""


@dataclass
class RunReport:
    sites: list[SiteResult]
    """Per-site outcome, in the order we processed them."""

    all_offers: list[Offer]
    """Flat list of every extracted offer across all sites (post-dedupe)."""

    grouped: list[tuple[str, list[Offer]]]
    """Offers grouped by destination, ordered by earliest departure date."""


async def run(
    config: Config,
    *,
    selected_sites: list[Site] | None = None,
    on_site_done: Callable[[SiteResult], None] | None = None,
    browser_mode: BrowserMode = "auto",
    on_engine_switch: Callable[[Site, str], None] | None = None,
    on_browser_fetch_done: Callable[[Site, FetchResult], None] | None = None,
    on_extraction_start: Callable[[Site], None] | None = None,
) -> RunReport:
    """Execute the full pipeline and return a :class:`RunReport`.

    Callbacks (all optional) — used by the CLI for live progress output:

    - ``on_engine_switch(site, engine)`` fires before a site is retried
      through the browser fallback.
    - ``on_browser_fetch_done(site, FetchResult)`` fires after each
      browser fetch completes (success or failure).
    - ``on_extraction_start(site)`` fires before each LLM call so the
      caller can show "extracting…" while the OpenAI request is in
      flight (these run in parallel).
    - ``on_site_done(SiteResult)`` fires once per site after extraction
      completes (or is skipped). Order is whichever site finishes
      first, not original-list order.
    """

    config.require_openai()
    sites = list(selected_sites or SITES)

    # Step 1: gather initial fetches (HTTP unless mode==all).
    fetches: dict[str, FetchResult] = {}
    engine_used: dict[str, str] = {}

    if browser_mode != "all":
        fetch_targets = [(s.site_id, s.listing_urls) for s in sites]
        fetches = await fetch_all(
            fetch_targets,
            user_agent=config.user_agent,
            timeout=config.http_timeout,
            concurrency=config.fetch_concurrency,
            max_chars=config.max_text_chars,
        )
        for sid in fetches:
            engine_used[sid] = "http"

    # Step 2: decide which sites need the browser.
    retry_sites: list[Site] = []
    if browser_mode == "all":
        retry_sites = list(sites)
    elif browser_mode == "auto":
        for s in sites:
            fr = fetches.get(s.site_id)
            if fr is None or needs_browser_retry(
                fr, min_useful_chars=config.browser_retry_min_chars
            ):
                retry_sites.append(s)

    # Step 3: if any sites need the browser, fire up a single shared
    # Chromium and refetch them. We skip silently if Playwright isn't
    # installed.
    if retry_sites and browser_mode != "off":
        if not browser_available():
            for s in retry_sites:
                fr = fetches.get(s.site_id)
                if fr is None:
                    fetches[s.site_id] = FetchResult(
                        url=s.homepage,
                        status=0,
                        text="",
                        error=(
                            "browser fallback requested but Playwright not "
                            "installed (run: pip install playwright && "
                            "playwright install chromium)"
                        ),
                    )
        else:
            async with open_session(
                user_agent=config.user_agent,
                max_chars=config.max_text_chars,
            ) as browser:
                for s in retry_sites:
                    if on_engine_switch:
                        on_engine_switch(s, "browser")
                    fr = await _browser_fetch_first_usable(
                        browser, s, min_chars=config.browser_retry_min_chars
                    )
                    fetches[s.site_id] = fr
                    engine_used[s.site_id] = "browser"
                    if on_browser_fetch_done:
                        on_browser_fetch_done(s, fr)

    # Step 4: extract per site, in bounded parallel.
    #
    # The sync OpenAI client runs in a thread executor. A semaphore
    # caps the in-flight calls (default 4) so we stay under the gpt-4o-
    # mini TPM limit on tier 1 accounts — bursting 10 simultaneous
    # ~3-5k-token prompts triggers 429s + exponential backoff which
    # makes the run look frozen.
    extractor = OfferExtractor(config)
    loop = asyncio.get_running_loop()
    sem = asyncio.Semaphore(max(1, config.extract_concurrency))
    # Reserve the result slots in original order so the diagnostics
    # table reads in the same order the user sees in `list-sites`.
    site_results: list[SiteResult | None] = [None] * len(sites)

    async def process(idx: int, site: Site) -> None:
        fr: FetchResult = fetches.get(site.site_id) or FetchResult(
            url=site.homepage, status=0, text="", error="not fetched"
        )
        engine = engine_used.get(site.site_id, "http")
        sr = _build_site_result(site, fr, engine=engine)

        if not sr.ok or not fr.text.strip():
            site_results[idx] = sr
            if on_site_done:
                on_site_done(sr)
            return

        async with sem:
            if on_extraction_start:
                on_extraction_start(site)
            try:
                offers = await loop.run_in_executor(
                    None,
                    lambda: extractor.extract(
                        site_name=site.name, url=fr.url, text=fr.text
                    ),
                )
                sr.offers = offers
            except Exception as exc:  # noqa: BLE001
                sr.ok = False
                sr.error = f"extract error: {exc}"

        site_results[idx] = sr
        if on_site_done:
            on_site_done(sr)

    await asyncio.gather(*(process(i, s) for i, s in enumerate(sites)))
    # By construction every slot is filled, but cast to satisfy the type
    # checker.
    final_results: list[SiteResult] = [sr for sr in site_results if sr is not None]

    all_offers: list[Offer] = []
    for sr in final_results:
        all_offers.extend(sr.offers)
    all_offers = dedupe(all_offers)
    all_offers = drop_past_departures(all_offers)

    grouped_map = group_by_destination(
        all_offers, max_per_destination=config.max_offers_per_destination
    )
    grouped = flatten_sorted_by_date(grouped_map)

    return RunReport(sites=final_results, all_offers=all_offers, grouped=grouped)


def _build_site_result(
    site: Site, fr: FetchResult, *, engine: str
) -> SiteResult:
    """Translate a raw ``FetchResult`` into a ``SiteResult`` shell.

    Offer extraction happens later; we just populate the success/error
    fields here based on the fetch outcome.
    """

    if fr.error:
        return SiteResult(
            site_id=site.site_id,
            site_name=site.name,
            url=fr.url or site.homepage,
            ok=False,
            error=fr.error,
            raw_chars=len(fr.text),
            engine=engine,
        )
    if fr.status == 0 or fr.status >= 400 or not fr.text.strip():
        return SiteResult(
            site_id=site.site_id,
            site_name=site.name,
            url=fr.url or site.homepage,
            ok=False,
            error=f"HTTP {fr.status}, {len(fr.text)} chars of text",
            raw_chars=len(fr.text),
            engine=engine,
        )
    return SiteResult(
        site_id=site.site_id,
        site_name=site.name,
        url=fr.url,
        ok=True,
        raw_chars=len(fr.text),
        engine=engine,
    )


async def _browser_fetch_first_usable(
    browser: BrowserSession, site: Site, *, min_chars: int
) -> FetchResult:
    """Try ``site.listing_urls`` in order through the browser.

    Returns the first 2xx response with at least ``min_chars`` of text,
    or the last attempt otherwise so the caller still has diagnostics.

    Bails immediately if the first response indicates a hard block
    (anti-bot interstitial or a 403) — those mean the entire host is
    refusing us, so retrying neighbouring URLs is just wasted time.
    """

    last: FetchResult | None = None
    for url in site.listing_urls:
        result = await browser.fetch(url)
        last = result

        if (
            200 <= result.status < 300
            and len(result.text) >= min_chars
            and not result.error
        ):
            return result

        # Host-wide blocks: don't bother with the remaining URLs.
        if result.status in (401, 403, 429) or looks_like_anti_bot(result.text):
            return result
        if "timeout" in (result.error or "").lower():
            # A single hard-timeout already burned 20s; trying two more
            # URLs would push the whole run past a minute on one site.
            return result

    return last or FetchResult(url=site.homepage, status=0, text="", error="no urls")
