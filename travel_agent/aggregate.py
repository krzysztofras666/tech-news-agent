"""Aggregate offers across sites: dedupe, pick the best per destination, sort."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from .models import Offer


def drop_past_departures(
    offers: list[Offer],
    *,
    today: date | None = None,
) -> list[Offer]:
    """Remove offers whose trip dates are already in the past.

    Offers with no dates are kept (price-only listings). If only
    ``return_date`` is set and it is before today, the offer is dropped.
    """

    ref = today or date.today()
    out: list[Offer] = []
    for offer in offers:
        if offer.departure_date is not None:
            if offer.departure_date < ref:
                continue
        elif offer.return_date is not None and offer.return_date < ref:
            continue
        out.append(offer)
    return out


def dedupe(offers: list[Offer]) -> list[Offer]:
    """Drop near-duplicate offers across sites.

    Two offers are considered duplicates if they share destination key,
    departure date, return date, and rounded price. We keep the first
    occurrence (which gives the original source priority).
    """

    seen: set[tuple] = set()
    out: list[Offer] = []
    for offer in offers:
        key = (
            offer.destination_key,
            offer.departure_date,
            offer.return_date,
            round(offer.price, 0) if offer.price is not None else None,
            offer.currency,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(offer)
    return out


def group_by_destination(
    offers: list[Offer],
    *,
    max_per_destination: int,
) -> dict[str, list[Offer]]:
    """Group offers by destination and keep only the cheapest N per group.

    Within each group offers are returned sorted by departure date (then
    price), so the CLI can render them in the order the user asked for
    without re-sorting.
    """

    groups: dict[str, list[Offer]] = defaultdict(list)
    for offer in offers:
        groups[offer.destination_key].append(offer)

    # Pick the cheapest N per destination, then re-sort by date for display.
    result: dict[str, list[Offer]] = {}
    for key, items in groups.items():
        picked = _pick_best(items, max_per_destination)
        picked.sort(key=lambda o: (o.sort_date, o.price if o.price is not None else float("inf")))
        result[key] = picked
    return result


def _pick_best(offers: list[Offer], n: int) -> list[Offer]:
    """Return up to ``n`` cheapest offers, dateless ones last.

    Offers without a price are kept only if we don't have ``n`` priced
    offers in this destination yet — they're informative (a real deal
    page link) even when the headline price isn't visible.
    """

    with_price = sorted(
        (o for o in offers if o.price is not None),
        key=lambda o: o.price,  # type: ignore[arg-type]
    )
    without_price = [o for o in offers if o.price is None]
    chosen = with_price[:n]
    if len(chosen) < n:
        chosen.extend(without_price[: n - len(chosen)])
    return chosen


def flatten_sorted_by_date(
    grouped: dict[str, list[Offer]],
) -> list[tuple[str, list[Offer]]]:
    """Return ``[(destination_display, offers...)]`` ordered by earliest date.

    "Display" destination = the casing of the first offer in the group,
    since the grouping key is lowercased.
    """

    items: list[tuple[str, list[Offer]]] = []
    for key, offers in grouped.items():
        if not offers:
            continue
        display = offers[0].destination
        items.append((display, offers))

    items.sort(
        key=lambda kv: (
            kv[1][0].sort_date,
            kv[1][0].price if kv[1][0].price is not None else float("inf"),
            kv[0].lower(),
        )
    )
    return items
