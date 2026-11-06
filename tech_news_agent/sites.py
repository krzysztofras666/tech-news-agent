"""Tech news sources to scan for headlines."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Site:
    site_id: str
    name: str
    homepage: str
    listing_urls: tuple[str, ...]


SITES: tuple[Site, ...] = (
    Site(
        site_id="wired",
        name="WIRED",
        homepage="https://www.wired.com/",
        listing_urls=("https://www.wired.com/",),
    ),
    Site(
        site_id="cnn",
        name="CNN Tech",
        homepage="https://edition.cnn.com/business/tech",
        listing_urls=("https://edition.cnn.com/business/tech",),
    ),
    Site(
        site_id="bbc",
        name="BBC Technology",
        homepage="https://www.bbc.com/technology",
        listing_urls=("https://www.bbc.com/technology",),
    ),
    Site(
        site_id="techcrunch",
        name="TechCrunch",
        homepage="https://techcrunch.com/",
        listing_urls=("https://techcrunch.com/",),
    ),
    Site(
        site_id="engadget",
        name="Engadget",
        homepage="https://www.engadget.com/",
        listing_urls=("https://www.engadget.com/",),
    ),
    Site(
        site_id="usatoday",
        name="USA Today Tech",
        homepage="https://eu.usatoday.com/tech/",
        listing_urls=("https://eu.usatoday.com/tech/",),
    ),
    Site(
        site_id="digitaltrends",
        name="Digital Trends",
        homepage="https://www.digitaltrends.com/",
        listing_urls=("https://www.digitaltrends.com/",),
    ),
    Site(
        site_id="googlenews",
        name="Google News — Technology",
        homepage=(
            "https://news.google.com/topics/"
            "CAAqJggKIiBDQkFTRWdvSUwyMHZNRGRqTVhZU0FtVnVHZ0pWVXlnQVAB"
            "?hl=en-US&gl=US&ceid=US%3Aen"
        ),
        listing_urls=(
            "https://news.google.com/topics/"
            "CAAqJggKIiBDQkFTRWdvSUwyMHZNRGRqTVhZU0FtVnVHZ0pWVXlnQVAB"
            "?hl=en-US&gl=US&ceid=US%3Aen",
        ),
    ),
    Site(
        site_id="geekwire",
        name="GeekWire",
        homepage="https://www.geekwire.com/",
        listing_urls=("https://www.geekwire.com/",),
    ),
    Site(
        site_id="coindesk",
        name="CoinDesk",
        homepage="https://www.coindesk.com/",
        listing_urls=("https://www.coindesk.com/",),
    ),
    Site(
        site_id="cointelegraph",
        name="Cointelegraph",
        homepage="https://cointelegraph.com/",
        listing_urls=("https://cointelegraph.com/",),
    ),
    Site(
        site_id="theblock",
        name="The Block",
        homepage="https://www.theblock.co/",
        listing_urls=("https://www.theblock.co/",),
    ),
)


def site_by_id(site_id: str) -> Site | None:
    for site in SITES:
        if site.site_id == site_id:
            return site
    return None
