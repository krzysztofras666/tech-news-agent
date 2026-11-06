"""LLM-powered structured extraction of travel offers from messy HTML text.

Each source site has wildly different HTML. Rather than write 10 fragile
parsers, we feed the cleaned page text to gpt-4o-mini with a strict JSON
schema instruction and let it normalize the fields.

The model is told to refuse to invent data: if a field is missing on the
page it must return null/empty. We then validate everything in Python
before turning the dicts into :class:`Offer` instances.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from openai import OpenAI

from .config import Config
from .models import Offer


SYSTEM_PROMPT = """\
You extract travel offers from messy text scraped off a Polish travel
portal. Return STRICT JSON of the form:

{"offers": [
  {
    "destination": "Egipt, Hurghada",
    "departure_date": "2026-06-12",   // ISO date or null
    "return_date":    "2026-06-19",   // ISO date or null
    "duration_nights": 7,              // integer or null
    "price": 2199.0,                   // number or null
    "currency": "PLN",                 // ISO-ish, default PLN if amount has no symbol
    "title": "Hotel Sunrise Holidays Resort 5*",
    "notes": "all inclusive, wylot z Warszawy"
  }
]}

Hard rules:
- Output ONLY JSON. No prose, no markdown fences.
- Include only offers that name a real destination AND have either a
  departure date OR a price. Skip pure marketing banners, newsletter
  CTAs, search-widget placeholders, "wybierz kierunek", cookie notices.
- destination: a place a person could travel to. Country and resort/city
  if both are given, e.g. "Egipt, Hurghada" or "Turcja, Antalya".
  Do NOT use category labels like "Last minute", "Promocje", "Wakacje".
- departure_date / return_date: ISO YYYY-MM-DD. If the source shows a
  date range like "12-19.06.2026", departure=2026-06-12, return=2026-06-19.
  If a Polish month name is used ("12 czerwca 2026"), convert it.
  Use null if the date isn't on the page — don't guess.
- NEVER include an offer whose departure_date is before Today (given in
  the user message). Skip stale last-minute blocks from past months.
  If a day/month has no year, pick the nearest future calendar date on
  or after Today; if that day/month already passed this year, use next year.
- price: numeric only. Strip currency symbol and Polish thousands
  separators ("2 199 zł" -> 2199). If the page shows "od 2199 zł", use
  2199. If there's no price at all on the page for that offer, use null.
- currency: "PLN" if the amount is in zł/PLN. "EUR" if €/EUR. "USD" if $.
  Default to "PLN" when ambiguous on Polish portals.
- duration_nights: integer count of nights/dni if visible, else null.
  ("7 dni" -> 7; "8 dni / 7 nocy" -> 7.)
- Deduplicate near-identical entries (same hotel + same dates).
- If the page is empty, a cookie wall, an error page, or has no real
  offers (e.g. only a search form), return {"offers": []}. Do NOT
  invent offers.
- Maximum 25 offers per response — pick the most concretely-described
  and cheapest ones if there are more.
"""


def _user_prompt(site_name: str, url: str, text: str) -> str:
    return (
        f"Source site: {site_name}\n"
        f"Source URL: {url}\n"
        f"Today: {date.today().isoformat()}\n"
        "Scraped page text (already stripped of HTML):\n"
        "---\n"
        f"{text}\n"
        "---\n"
        "Return the JSON now."
    )


class OfferExtractor:
    # Per-request HTTP timeout. The SDK's default is 10 minutes; one
    # stuck request shouldn't be allowed to hold up the whole daily run.
    # 60s comfortably covers the largest pages we feed in (~18k chars
    # from kanalwyjazdowy.pl); 30s was cutting it too close and we lost
    # 2-3 sites per run to timeouts.
    REQUEST_TIMEOUT_SECONDS = 60.0

    # The SDK retries 5xx/429 by default with exponential backoff
    # (up to ~minutes total). For a daily batch we'd rather fail one
    # site quickly than block the whole run on retries.
    MAX_RETRIES = 1

    def __init__(self, config: Config) -> None:
        config.require_openai()
        self._client = OpenAI(
            api_key=config.openai_api_key,
            timeout=self.REQUEST_TIMEOUT_SECONDS,
            max_retries=self.MAX_RETRIES,
        )
        self._model = config.openai_model

    def extract(
        self,
        *,
        site_name: str,
        url: str,
        text: str,
    ) -> list[Offer]:
        if not text.strip():
            return []

        response = self._client.chat.completions.create(
            model=self._model,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _user_prompt(site_name, url, text)},
            ],
        )
        raw = (response.choices[0].message.content or "").strip()
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []

        items = data.get("offers") or []
        if not isinstance(items, list):
            return []

        out: list[Offer] = []
        for item in items:
            offer = _coerce(item, site_name=site_name, url=url)
            if offer is not None:
                out.append(offer)
        return out


# ---- coercion helpers ---------------------------------------------------


def _coerce(item: Any, *, site_name: str, url: str) -> Offer | None:
    if not isinstance(item, dict):
        return None
    destination = _clean_str(item.get("destination"))
    if not destination or _is_junk_destination(destination):
        return None

    dep = _parse_date(item.get("departure_date"))
    ret = _parse_date(item.get("return_date"))
    # Sanity: if return < departure, swap or drop.
    if dep and ret and ret < dep:
        dep, ret = ret, dep

    today = date.today()
    if dep is not None and dep < today:
        return None
    if dep is None and ret is not None and ret < today:
        return None

    price = _parse_price(item.get("price"))
    currency = _clean_str(item.get("currency")) or "PLN"
    currency = currency.upper()[:4]

    # Require at least one of date/price; the prompt says so but the
    # model occasionally slips. Otherwise we'd surface noise.
    if dep is None and price is None:
        return None

    return Offer(
        destination=destination,
        departure_date=dep,
        return_date=ret,
        price=price,
        currency=currency,
        source_site=site_name,
        source_url=url,
        title=_clean_str(item.get("title")),
        duration_nights=_parse_int(item.get("duration_nights")),
        notes=_clean_str(item.get("notes")),
    )


_JUNK_DESTINATIONS = {
    "last minute",
    "lastminute",
    "promocje",
    "promocja",
    "wakacje",
    "oferta",
    "oferty",
    "wybierz kierunek",
    "wszystkie kierunki",
}


def _is_junk_destination(value: str) -> bool:
    key = value.strip().lower()
    return key in _JUNK_DESTINATIONS or len(key) < 2


def _clean_str(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _parse_price(value: Any) -> float | None:
    """Parse a Polish or English price string into a float.

    Handles spaces as thousand separators ("2 199 zł"), Polish
    "2.199,00" (dot=thousand, comma=decimal), English "2,199.00", and
    bare numbers. Returns ``None`` when no digits are present.
    """

    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value)
    s = re.sub(r"[^0-9,.\-]", "", s)
    if not s:
        return None

    # Treat the rightmost '.' or ',' as the decimal point if it leaves
    # exactly 1-2 digits after it (e.g. "2.199,00", "1499.50"); otherwise
    # both punctuation characters are thousands separators ("1.499" =
    # 1499, "2,199" = 2199).
    last_comma = s.rfind(",")
    last_dot = s.rfind(".")
    decimal_idx = max(last_comma, last_dot)
    if decimal_idx >= 0:
        digits_after = len(s) - decimal_idx - 1
        if 1 <= digits_after <= 2:
            integer_part = s[:decimal_idx].replace(",", "").replace(".", "")
            fractional_part = s[decimal_idx + 1 :]
            s = f"{integer_part}.{fractional_part}" if integer_part else f"0.{fractional_part}"
        else:
            s = s.replace(",", "").replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None
