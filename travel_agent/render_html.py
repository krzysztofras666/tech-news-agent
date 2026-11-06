"""HTML rendering for the daily travel-deals email.

The output is a self-contained string with inline styles only (no
<style> tags, no external CSS, no external images) so it renders
reliably in Gmail, Outlook, and on mobile clients.
"""

from __future__ import annotations

import html
from datetime import date

from .agent import RunReport
from .models import Offer, SiteResult


# Polish month names for human-friendly date formatting.
_PL_MONTHS = (
    "stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca",
    "lipca", "sierpnia", "września", "października", "listopada", "grudnia",
)


def render_email_html(
    report: RunReport,
    *,
    generated_at: date | None = None,
) -> tuple[str, str]:
    """Return ``(subject, html_body)`` for the daily deals email.

    The body is a single ``<table>``-based layout because Gmail strips
    most flex/grid CSS. Each destination is a card with its cheapest
    offers; destinations are ordered by earliest departure date.
    """

    generated_at = generated_at or date.today()
    total_offers = sum(len(items) for _, items in report.grouped)
    destination_count = len(report.grouped)

    subject = (
        f"Tanie wakacje — {generated_at.isoformat()} "
        f"({_pl_destinations_label(destination_count)}, "
        f"{_pl_offers_label_inflected(total_offers)})"
    )

    body = _render_body(report, generated_at=generated_at)
    return subject, body


def _pl_destinations_label(n: int) -> str:
    """Return "1 kierunek" / "3 kierunki" / "5 kierunków"."""

    if n == 1:
        return "1 kierunek"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} kierunki"
    return f"{n} kierunków"


def _pl_offers_label_inflected(n: int) -> str:
    """Return "1 oferta" / "3 oferty" / "5 ofert" (also used in subject)."""

    if n == 1:
        return "1 oferta"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} oferty"
    return f"{n} ofert"


def _pl_offers_label(n: int) -> str:
    """Alias kept for backward-compat with diagnostics rendering."""

    return _pl_offers_label_inflected(n)


# ---- private rendering --------------------------------------------------


_COLOR_BG = "#f4f6fa"
_COLOR_CARD = "#ffffff"
_COLOR_BORDER = "#e3e7ee"
_COLOR_TEXT = "#1f2330"
_COLOR_MUTED = "#6c7383"
_COLOR_ACCENT = "#1d6fe1"
_COLOR_PRICE = "#0d8a4a"
_COLOR_DESTINATION = "#0b2a52"


def _render_body(report: RunReport, *, generated_at: date) -> str:
    parts: list[str] = []
    parts.append(_doc_open())
    parts.append(_render_header(report, generated_at=generated_at))

    if not report.grouped:
        parts.append(_render_empty_state(report))
    else:
        for destination, offers in report.grouped:
            parts.append(_render_destination_card(destination, offers))

    parts.append(_render_diagnostics(report))
    parts.append(_render_footer(generated_at))
    parts.append(_doc_close())
    return "".join(parts)


def _doc_open() -> str:
    return (
        '<!DOCTYPE html>'
        '<html lang="pl"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Tanie wakacje</title>'
        '</head>'
        f'<body style="margin:0;padding:0;background:{_COLOR_BG};'
        'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,'
        'Helvetica,Arial,sans-serif;'
        f'color:{_COLOR_TEXT};">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="background:{_COLOR_BG};">'
        '<tr><td align="center" style="padding:24px 12px;">'
        '<table role="presentation" width="640" cellpadding="0" cellspacing="0" '
        'border="0" style="max-width:640px;width:100%;">'
    )


def _doc_close() -> str:
    return "</table></td></tr></table></body></html>"


def _render_header(report: RunReport, *, generated_at: date) -> str:
    total_offers = sum(len(items) for _, items in report.grouped)
    destinations = len(report.grouped)
    sources_ok = sum(1 for sr in report.sites if sr.ok and sr.offers)
    sources_total = len(report.sites)
    date_label = _format_long_date(generated_at)

    return (
        '<tr><td style="padding-bottom:16px;">'
        f'<div style="font-size:13px;color:{_COLOR_MUTED};letter-spacing:.04em;'
        'text-transform:uppercase;">Codzienne okazje wakacyjne</div>'
        f'<div style="font-size:28px;font-weight:700;color:{_COLOR_DESTINATION};'
        'margin-top:4px;line-height:1.15;">Najtańsze wyjazdy</div>'
        f'<div style="font-size:15px;color:{_COLOR_MUTED};margin-top:6px;">'
        f'{html.escape(date_label)} &nbsp;·&nbsp; '
        f'<b style="color:{_COLOR_TEXT};">{destinations}</b> kierunków, '
        f'<b style="color:{_COLOR_TEXT};">{total_offers}</b> ofert z '
        f'<b style="color:{_COLOR_TEXT};">{sources_ok}/{sources_total}</b> '
        'źródeł'
        '</div>'
        '</td></tr>'
    )


def _render_destination_card(destination: str, offers: list[Offer]) -> str:
    cheapest_priced = next((o for o in offers if o.price is not None), None)
    headline_price = (
        _format_price(cheapest_priced) if cheapest_priced is not None else ""
    )
    headline = (
        f'<span style="color:{_COLOR_PRICE};font-weight:700;'
        f'font-size:18px;">od {html.escape(headline_price)}</span>'
        if headline_price
        else ""
    )

    rows = "".join(_render_offer_row(o) for o in offers)

    return (
        '<tr><td style="padding-bottom:14px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="background:{_COLOR_CARD};border:1px solid {_COLOR_BORDER};'
        'border-radius:12px;overflow:hidden;">'
        # Destination header
        f'<tr><td style="padding:14px 18px;background:{_COLOR_CARD};'
        f'border-bottom:1px solid {_COLOR_BORDER};">'
        '<table width="100%"><tr>'
        f'<td style="font-size:18px;font-weight:700;color:{_COLOR_DESTINATION};'
        'vertical-align:middle;">'
        f'{html.escape(destination)}'
        '</td>'
        f'<td align="right" style="vertical-align:middle;">{headline}</td>'
        '</tr></table>'
        '</td></tr>'
        # Offer rows
        f'<tr><td style="padding:0 18px 6px 18px;">'
        '<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'{rows}'
        '</table>'
        '</td></tr>'
        '</table>'
        '</td></tr>'
    )


def _render_offer_row(offer: Offer) -> str:
    dates = _format_dates(offer)
    price = _format_price(offer) if offer.price is not None else "—"
    nights = (
        f' · {offer.duration_nights} '
        f'{"noc" if offer.duration_nights == 1 else ("nocy" if offer.duration_nights > 4 or offer.duration_nights < 2 else "noce")}'
        if offer.duration_nights
        else ""
    )
    safe_url = html.escape(offer.source_url, quote=True)

    # Title doubles as the primary click target — it's the most natural
    # thing for a reader to click. If the source page provided no title,
    # fall back to "Zobacz ofertę" so the link is never empty.
    title_text = offer.title.strip() or "Zobacz ofertę"
    title_line = (
        f'<div style="font-size:14px;margin-top:2px;">'
        f'<a href="{safe_url}" '
        f'style="color:{_COLOR_TEXT};text-decoration:none;font-weight:500;">'
        f'{html.escape(title_text)}</a>'
        '</div>'
    )
    notes_line = (
        f'<div style="font-size:12px;color:{_COLOR_MUTED};margin-top:2px;">'
        f'{html.escape(offer.notes)}</div>'
        if offer.notes
        else ""
    )

    # CTA-style link styled like a small button-pill so it's visually
    # distinct from the title link and the user knows where they're
    # headed (which portal).
    cta_link = (
        f'<a href="{safe_url}" '
        f'style="display:inline-block;color:{_COLOR_ACCENT};'
        'text-decoration:none;font-size:12px;font-weight:600;'
        f'padding:4px 10px;border:1px solid {_COLOR_BORDER};'
        'border-radius:6px;background:#f7f9fc;">'
        f'Zobacz na {html.escape(offer.source_site)} →'
        '</a>'
    )

    # Wrap the price in the same anchor so the entire "right side" of
    # the row also clicks through. Gmail honors block-styled <a> here.
    price_html = (
        f'<a href="{safe_url}" '
        f'style="color:{_COLOR_TEXT};text-decoration:none;'
        'font-size:17px;font-weight:700;display:inline-block;">'
        f'{html.escape(price)}</a>'
    )

    return (
        '<tr>'
        f'<td style="padding:12px 0;border-top:1px solid {_COLOR_BORDER};">'
        '<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        '<tr>'
        '<td style="vertical-align:top;padding-right:12px;">'
        f'<div style="font-size:13px;color:{_COLOR_MUTED};">'
        f'{html.escape(dates)}{html.escape(nights)}</div>'
        f'{title_line}'
        f'{notes_line}'
        f'<div style="margin-top:8px;">{cta_link}</div>'
        '</td>'
        '<td align="right" style="vertical-align:top;white-space:nowrap;">'
        f'{price_html}'
        '</td>'
        '</tr>'
        '</table>'
        '</td>'
        '</tr>'
    )


def _render_empty_state(report: RunReport) -> str:
    fail_lines = [
        f"<li>{html.escape(sr.site_name)}: {html.escape(sr.error or 'no offers')}</li>"
        for sr in report.sites
        if not sr.ok or not sr.offers
    ]
    fails_html = (
        f'<ul style="margin:8px 0 0 18px;color:{_COLOR_MUTED};font-size:13px;">'
        f'{"".join(fail_lines)}</ul>'
        if fail_lines
        else ""
    )
    return (
        '<tr><td style="padding-bottom:14px;">'
        f'<div style="background:{_COLOR_CARD};border:1px solid {_COLOR_BORDER};'
        'border-radius:12px;padding:18px;">'
        f'<div style="font-size:16px;font-weight:600;color:{_COLOR_DESTINATION};">'
        'Brak ofert w dzisiejszym przebiegu.</div>'
        f'<div style="font-size:13px;color:{_COLOR_MUTED};margin-top:6px;">'
        'Wszystkie strony zostały sprawdzone, ale żadna nie zwróciła ofert '
        'z konkretną datą lub ceną. Diagnostyka:</div>'
        f'{fails_html}'
        '</div>'
        '</td></tr>'
    )


def _render_diagnostics(report: RunReport) -> str:
    rows = []
    for sr in report.sites:
        status_color = _COLOR_PRICE if sr.ok and sr.offers else _COLOR_MUTED
        status_text = (
            _pl_offers_label(len(sr.offers)) if sr.ok and sr.offers
            else ("brak ofert" if sr.ok else "błąd")
        )
        detail = sr.error or f"{sr.raw_chars:,} znaków"
        engine_badge = (
            f'<span style="font-size:11px;color:{_COLOR_MUTED};'
            'padding:1px 6px;border-radius:8px;border:1px solid '
            f'{_COLOR_BORDER};margin-left:6px;">{html.escape(sr.engine)}</span>'
        )
        # Link the site name to whichever URL we actually scraped — for
        # successful sites this is the "last minute" listing, for
        # failures it's still useful so the reader can investigate.
        name_html = (
            f'<a href="{html.escape(sr.url, quote=True)}" '
            f'style="color:{_COLOR_ACCENT};text-decoration:none;">'
            f'{html.escape(sr.site_name)}</a>'
            if sr.url
            else html.escape(sr.site_name)
        )
        rows.append(
            '<tr>'
            f'<td style="font-size:13px;padding:4px 8px 4px 0;color:{_COLOR_TEXT};">'
            f'{name_html}{engine_badge}'
            '</td>'
            f'<td style="font-size:13px;padding:4px 0;color:{status_color};">'
            f'{html.escape(status_text)}'
            '</td>'
            f'<td style="font-size:12px;padding:4px 0;color:{_COLOR_MUTED};'
            'text-align:right;">'
            f'{html.escape(detail)}'
            '</td>'
            '</tr>'
        )
    return (
        '<tr><td style="padding-top:6px;padding-bottom:8px;">'
        f'<div style="background:{_COLOR_CARD};border:1px solid {_COLOR_BORDER};'
        'border-radius:12px;padding:14px 18px;">'
        f'<div style="font-size:12px;color:{_COLOR_MUTED};letter-spacing:.04em;'
        'text-transform:uppercase;margin-bottom:8px;">Źródła</div>'
        '<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'{"".join(rows)}'
        '</table>'
        '</div>'
        '</td></tr>'
    )


def _render_footer(generated_at: date) -> str:
    return (
        '<tr><td style="padding:14px 4px 0 4px;">'
        f'<div style="font-size:12px;color:{_COLOR_MUTED};line-height:1.5;">'
        'Ten e-mail został wygenerowany automatycznie przez '
        '<b>travel-agent</b>. Ceny i dostępność mogą się zmienić od czasu '
        'pobrania danych — zawsze potwierdzaj na stronie źródłowej przed rezerwacją.'
        '<br>'
        f'Wygenerowano: {html.escape(generated_at.isoformat())}.'
        '</div>'
        '</td></tr>'
    )


# ---- formatting helpers -------------------------------------------------


def _format_long_date(d: date) -> str:
    return f"{d.day} {_PL_MONTHS[d.month - 1]} {d.year}"


def _format_dates(offer: Offer) -> str:
    if offer.departure_date and offer.return_date:
        return f"{_format_short_date(offer.departure_date)} – {_format_short_date(offer.return_date)}"
    if offer.departure_date:
        return _format_short_date(offer.departure_date)
    if offer.return_date:
        return f"do {_format_short_date(offer.return_date)}"
    return "termin niepodany"


def _format_short_date(d: date) -> str:
    return f"{d.day} {_PL_MONTHS[d.month - 1]}"


def _format_price(offer: Offer) -> str:
    if offer.price is None:
        return "—"
    if offer.price.is_integer():
        amount = f"{int(offer.price):,}".replace(",", " ")
    else:
        amount = f"{offer.price:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")
    return f"{amount} {offer.currency}"


def render_email_text(report: RunReport, *, generated_at: date | None = None) -> str:
    """Plaintext fallback (always included as multipart/alternative)."""

    generated_at = generated_at or date.today()
    lines = [
        f"Tanie wakacje — {generated_at.isoformat()}",
        "=" * 56,
        "",
    ]
    if not report.grouped:
        lines.append("Brak ofert w dzisiejszym przebiegu.")
    else:
        for destination, offers in report.grouped:
            lines.append(destination)
            for o in offers:
                lines.append(
                    f"  · {_format_dates(o)} — "
                    f"{_format_price(o) if o.price is not None else 'cena niepodana'} "
                    f"({o.source_site})"
                )
                if o.title:
                    lines.append(f"      {o.title}")
                lines.append(f"      {o.source_url}")
            lines.append("")

    lines.append("Diagnostyka:")
    for sr in report.sites:
        if sr.ok:
            offers_text = (
                _pl_offers_label(len(sr.offers)) if sr.offers else "brak ofert"
            )
        else:
            offers_text = "błąd"
        lines.append(
            f"  - {sr.site_name} [{sr.engine}]: {offers_text} ({sr.raw_chars} znaków) {sr.error}".rstrip()
        )
    lines.append("")
    lines.append("— travel-agent (automat). Potwierdź ceny przed rezerwacją.")
    return "\n".join(lines)
