"""End-to-end pipeline: fetch -> extract -> rank -> LinkedIn draft."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Literal

from travel_agent.agent import BrowserMode
from travel_agent.browser_fetcher import (
    browser_available,
    needs_browser_retry,
    open_session,
)
from travel_agent.fetcher import FetchResult, fetch_all

from .aggregate import pick_top_stories
from .config import Config
from .extract import StoryExtractor
from .linkedin import LinkedInDrafter
from .models import DigestReport, SiteResult, Story
from .sites import SITES, Site


@dataclass
class RunReport:
    sites: list[SiteResult]
    all_stories: list[Story]
    top_stories: list[Story]
    linkedin_draft: str


async def run(
    config: Config,
    *,
    selected_sites: list[Site] | None = None,
    on_site_done: Callable[[SiteResult], None] | None = None,
    browser_mode: BrowserMode = "auto",
    on_engine_switch: Callable[[Site, str], None] | None = None,
    on_browser_fetch_done: Callable[[Site, FetchResult], None] | None = None,
    on_extraction_start: Callable[[Site], None] | None = None,
    write_linkedin: bool = True,
) -> RunReport:
    config.require_openai()
    sites = list(selected_sites or SITES)

    fetches: dict[str, FetchResult] = {}
    engine_used: dict[str, str] = {}

    if browser_mode != "all":
        fetch_targets = [(site.site_id, site.listing_urls) for site in sites]
        fetches = await fetch_all(
            fetch_targets,
            user_agent=config.user_agent,
            timeout=config.http_timeout,
            concurrency=config.fetch_concurrency,
            max_chars=config.max_text_chars,
        )
        for site_id in fetches:
            engine_used[site_id] = "http"

    retry_sites: list[Site] = []
    if browser_mode == "all":
        retry_sites = list(sites)
    elif browser_mode == "auto":
        for site in sites:
            result = fetches.get(site.site_id)
            if result is None or needs_browser_retry(
                result, min_useful_chars=config.browser_retry_min_chars
            ):
                retry_sites.append(site)

    if retry_sites and browser_mode != "off":
        if not browser_available():
            for site in retry_sites:
                result = fetches.get(site.site_id)
                if result is None:
                    fetches[site.site_id] = FetchResult(
                        url=site.homepage,
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
                for site in retry_sites:
                    if on_engine_switch:
                        on_engine_switch(site, "browser")
                    result = await _browser_fetch_first_usable(
                        browser, site, min_chars=config.browser_retry_min_chars
                    )
                    fetches[site.site_id] = result
                    engine_used[site.site_id] = "browser"
                    if on_browser_fetch_done:
                        on_browser_fetch_done(site, result)

    extractor = StoryExtractor(config)
    loop = asyncio.get_running_loop()
    sem = asyncio.Semaphore(max(1, config.extract_concurrency))
    site_results: list[SiteResult | None] = [None] * len(sites)

    async def process(idx: int, site: Site) -> None:
        result: FetchResult = fetches.get(site.site_id) or FetchResult(
            url=site.homepage, status=0, text="", error="not fetched"
        )
        engine = engine_used.get(site.site_id, "http")
        site_result = _build_site_result(site, result, engine=engine)

        if not site_result.ok or not result.text.strip():
            site_results[idx] = site_result
            if on_site_done:
                on_site_done(site_result)
            return

        async with sem:
            if on_extraction_start:
                on_extraction_start(site)
            try:
                stories = await loop.run_in_executor(
                    None,
                    lambda: extractor.extract(
                        site_name=site.name, url=result.url, text=result.text
                    ),
                )
                site_result.stories = stories
            except Exception as exc:  # noqa: BLE001
                site_result.ok = False
                site_result.error = f"extract error: {exc}"

        site_results[idx] = site_result
        if on_site_done:
            on_site_done(site_result)

    await asyncio.gather(*(process(idx, site) for idx, site in enumerate(sites)))
    final_results: list[SiteResult] = [result for result in site_results if result]

    all_stories: list[Story] = []
    for site_result in final_results:
        all_stories.extend(site_result.stories)

    top_stories = pick_top_stories(
        all_stories, limit=config.max_top_stories
    )

    linkedin_draft = ""
    if write_linkedin:
        drafter = LinkedInDrafter(config)
        linkedin_draft = await loop.run_in_executor(
            None, lambda: drafter.draft(top_stories)
        )

    return RunReport(
        sites=final_results,
        all_stories=all_stories,
        top_stories=top_stories,
        linkedin_draft=linkedin_draft,
    )


def _build_site_result(site: Site, result: FetchResult, *, engine: str) -> SiteResult:
    if result.error:
        return SiteResult(
            site_id=site.site_id,
            site_name=site.name,
            url=result.url or site.homepage,
            ok=False,
            error=result.error,
            raw_chars=len(result.text),
            engine=engine,
        )
    if result.status == 0 or result.status >= 400 or not result.text.strip():
        return SiteResult(
            site_id=site.site_id,
            site_name=site.name,
            url=result.url or site.homepage,
            ok=False,
            error=f"HTTP {result.status}, {len(result.text)} chars of text",
            raw_chars=len(result.text),
            engine=engine,
        )
    return SiteResult(
        site_id=site.site_id,
        site_name=site.name,
        url=result.url,
        ok=True,
        raw_chars=len(result.text),
        engine=engine,
    )


async def _browser_fetch_first_usable(browser, site: Site, *, min_chars: int) -> FetchResult:
    from travel_agent.browser_fetcher import looks_like_anti_bot

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
        if result.status in (401, 403, 429) or looks_like_anti_bot(result.text):
            return result
        if "timeout" in (result.error or "").lower():
            return result
    return last or FetchResult(url=site.homepage, status=0, text="", error="no urls")
