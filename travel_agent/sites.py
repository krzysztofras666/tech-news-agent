"""Source sites we scrape and the specific URL on each that lists deals.

The user asked for the homepage of each portal, but homepages of tour
operators are mostly hero banners + JS-rendered search widgets — the
actual list of bookable deals lives on a "last minute" or "promotions"
subpage. We keep the homepage as a fallback and add the better URL as
the primary target.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Site:
    site_id: str
    name: str
    homepage: str
    listing_urls: tuple[str, ...]
    """URLs that actually contain offer cards / titles. Tried in order;
    we keep going until one returns enough content to feed the LLM."""


SITES: tuple[Site, ...] = (
    Site(
        site_id="esky",
        name="esky.pl",
        homepage="https://www.esky.pl/",
        listing_urls=(
            "https://www.esky.pl/lot+hotel/2-wakacje/54-last-minute",
            "https://www.esky.pl/lot+hotel/2-wakacje",
            "https://www.esky.pl/",
        ),
    ),
    Site(
        site_id="coraltravel",
        name="coraltravel.pl",
        homepage="https://www.coraltravel.pl/",
        listing_urls=(
            "https://www.coraltravel.pl/last-minute",
            "https://www.coraltravel.pl/promocje",
            "https://www.coraltravel.pl/",
        ),
    ),
    Site(
        site_id="itaka",
        name="itaka.pl",
        homepage="https://www.itaka.pl/",
        listing_urls=(
            "https://www.itaka.pl/last-minute/",
            "https://www.itaka.pl/wakacje/",
            "https://www.itaka.pl/",
        ),
    ),
    Site(
        site_id="fly",
        name="fly.pl",
        homepage="https://fly.pl/",
        listing_urls=(
            "https://fly.pl/oferta/last-minute/",
            "https://fly.pl/promocje/",
            "https://fly.pl/",
        ),
    ),
    Site(
        site_id="lastminute",
        name="pl.lastminute.com",
        homepage="https://pl.lastminute.com/",
        listing_urls=(
            "https://pl.lastminute.com/deals",
            "https://pl.lastminute.com/wakacje",
            "https://pl.lastminute.com/",
        ),
    ),
    Site(
        site_id="travelplanet",
        name="travelplanet.pl",
        homepage="https://www.travelplanet.pl/",
        listing_urls=(
            "https://www.travelplanet.pl/last-minute/",
            "https://www.travelplanet.pl/promocje/",
            "https://www.travelplanet.pl/",
        ),
    ),
    Site(
        site_id="rpl",
        name="r.pl",
        homepage="https://r.pl/",
        listing_urls=(
            "https://r.pl/last-minute",
            "https://r.pl/promocje",
            "https://r.pl/",
        ),
    ),
    Site(
        site_id="wakacyjnipiraci",
        name="wakacyjnipiraci.pl",
        homepage="https://www.wakacyjnipiraci.pl/",
        listing_urls=(
            "https://www.wakacyjnipiraci.pl/feed",
            "https://www.wakacyjnipiraci.pl/",
        ),
    ),
    Site(
        site_id="superlastminute",
        name="super-last-minute.pl",
        homepage="https://super-last-minute.pl/",
        listing_urls=(
            "https://super-last-minute.pl/wyjazdy",
            "https://super-last-minute.pl/",
        ),
    ),
    Site(
        site_id="kanalwyjazdowy",
        name="kanalwyjazdowy.pl",
        homepage="https://kanalwyjazdowy.pl/",
        listing_urls=(
            "https://kanalwyjazdowy.pl/feed/",
            "https://kanalwyjazdowy.pl/",
        ),
    ),
)


def site_by_id(site_id: str) -> Site | None:
    for s in SITES:
        if s.site_id == site_id:
            return s
    return None
