"""HTML rendering for the daily LinkedIn-draft email."""

from __future__ import annotations

import base64
import html
from datetime import date
from typing import Literal

from .agent import RunReport
from .image_gen import ILLUSTRATION_CID
from .models import SiteResult, Story

_SUBJECT_LINES = (
    "Your LI post (human edition) — {date}",
    "Morning hot take — {date}",
    "Post this before someone says 'delve' — {date}",
    "Tech news, but make it sarcastic — {date}",
)

_EMAIL_INTROS = (
    "Fresh from the news crawl. Edit freely — the robot doesn't get offended.",
    "Here's today's draft. I tried to make it sound like you, not a press release.",
    "Copy, tweak, post. Or ignore and go back to bed. No judgment.",
)


def _email_copy(generated_at: date) -> tuple[str, str]:
    idx = generated_at.toordinal() % len(_SUBJECT_LINES)
    subject = _SUBJECT_LINES[idx].format(date=generated_at.isoformat())
    intro = _EMAIL_INTROS[idx % len(_EMAIL_INTROS)]
    return subject, intro


def _story_sources(stories: list[Story]) -> list[Story]:
    """Top stories used for the draft — one row per headline."""

    seen: set[str] = set()
    out: list[Story] = []
    for story in stories:
        key = story.headline.lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(story)
    return out


def _portal_sources(sites: list[SiteResult]) -> list[tuple[str, str]]:
    """News portals successfully scanned today."""

    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for site in sites:
        if not site.ok or not site.url:
            continue
        if site.url in seen:
            continue
        seen.add(site.url)
        out.append((site.site_name, site.url))
    return out


def render_email_html(
    report: RunReport,
    *,
    generated_at: date | None = None,
    illustration_png: bytes | None = None,
    image_mode: Literal["cid", "data"] = "cid",
) -> tuple[str, str]:
    generated_at = generated_at or date.today()
    subject, intro = _email_copy(generated_at)
    return subject, _render_body(
        report,
        generated_at=generated_at,
        intro=intro,
        illustration_png=illustration_png,
        image_mode=image_mode,
    )


def render_email_text(
    report: RunReport,
    *,
    generated_at: date | None = None,
    illustration_png: bytes | None = None,
) -> str:
    generated_at = generated_at or date.today()
    subject, intro = _email_copy(generated_at)
    lines = [
        subject,
        "",
        intro,
        "",
    ]
    if illustration_png:
        lines.append("World Retro news — illustration included in the HTML version.")
        lines.append("")
    lines.extend([
        report.linkedin_draft,
        "",
    ])

    stories = _story_sources(report.top_stories)
    if stories:
        lines.append("Sources (stories behind the draft):")
        for story in stories:
            lines.append(f"- {story.headline}")
            if story.source_url:
                lines.append(f"  {story.source_url}")
        lines.append("")

    portals = _portal_sources(report.sites)
    if portals:
        lines.append("News feeds scanned:")
        for name, url in portals:
            lines.append(f"- {name}: {url}")
        lines.append("")

    lines.append("— Kris's news bot (not written by ChatGPT, allegedly)")
    return "\n".join(lines)


def _render_illustration_html(
    illustration_png: bytes | None,
    *,
    image_mode: Literal["cid", "data"],
) -> str:
    if not illustration_png:
        return ""

    if image_mode == "data":
        encoded = base64.b64encode(illustration_png).decode("ascii")
        src = f"data:image/png;base64,{encoded}"
    else:
        src = f"cid:{ILLUSTRATION_CID}"

    return (
        "<tr><td style=\"padding:0 24px 16px;\">"
        "<div style=\"font-size:14px;font-weight:bold;letter-spacing:0.06em;"
        "color:#111827;margin-bottom:10px;\">World Retro news</div>"
        f"<img src=\"{src}\" alt=\"World Retro news\" "
        "style=\"display:block;width:100%;max-width:552px;border-radius:12px;"
        "border:1px solid #e5e7eb;\">"
        "</td></tr>"
    )


def _render_body(
    report: RunReport,
    *,
    generated_at: date,
    intro: str,
    illustration_png: bytes | None = None,
    image_mode: Literal["cid", "data"] = "cid",
) -> str:
    draft_html = html.escape(report.linkedin_draft).replace("\n", "<br>\n")
    sources_html = _render_sources_html(report)
    intro_html = html.escape(intro)
    illustration_html = _render_illustration_html(
        illustration_png, image_mode=image_mode
    )

    return f"""\
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" \
style="background:#f4f6fa;padding:24px 0;font-family:Arial,Helvetica,sans-serif;">
  <tr><td align="center">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0" \
style="background:#ffffff;border-radius:12px;overflow:hidden;">
      <tr><td style="background:#111827;color:#ffffff;padding:20px 24px;">
        <div style="font-size:18px;font-weight:bold;">Your LI post</div>
        <div style="font-size:13px;opacity:0.85;margin-top:4px;">
          Tech, AI &amp; crypto — {html.escape(generated_at.isoformat())}
        </div>
      </td></tr>
      <tr><td style="padding:20px 24px 8px;color:#6b7280;font-size:14px;\
line-height:1.5;font-style:italic;">{intro_html}</td></tr>
      {illustration_html}
      <tr><td style="padding:8px 24px 24px;color:#111827;font-size:15px;line-height:1.6;">
        <div style="font-size:12px;text-transform:uppercase;letter-spacing:0.08em;\
color:#6b7280;margin-bottom:10px;">Copy &amp; paste</div>
        <div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;\
padding:16px;white-space:normal;">{draft_html}</div>
      </td></tr>
      {sources_html}
      <tr><td style="padding:0 24px 24px;color:#9ca3af;font-size:12px;">
        Opinions yours to own.
      </td></tr>
    </table>
  </td></tr>
</table>"""


def _render_sources_html(report: RunReport) -> str:
    stories = _story_sources(report.top_stories)
    portals = _portal_sources(report.sites)
    if not stories and not portals:
        return ""

    parts: list[str] = []

    if stories:
        items = "".join(_story_source_item(story) for story in stories)
        parts.append(
            "<div style=\"font-size:12px;text-transform:uppercase;letter-spacing:0.08em;"
            "color:#6b7280;margin:0 0 12px;\">Sources</div>"
            f"<ul style=\"margin:0;padding:0 0 0 18px;\">{items}</ul>"
        )

    if portals:
        portal_items = "".join(_portal_item(name, url) for name, url in portals)
        margin = "24px 0 12px" if stories else "0 0 12px"
        parts.append(
            f"<div style=\"font-size:12px;text-transform:uppercase;letter-spacing:0.08em;"
            f"color:#6b7280;margin:{margin};\">News feeds scanned</div>"
            f"<ul style=\"margin:0;padding:0 0 0 18px;\">{portal_items}</ul>"
        )

    body = "".join(parts)
    return (
        "<tr><td style=\"padding:0 24px 24px;color:#111827;font-size:14px;"
        "line-height:1.6;border-top:1px solid #e5e7eb;\">"
        f"{body}"
        "</td></tr>"
    )


def _story_source_item(story: Story) -> str:
    headline = html.escape(story.headline)
    site = html.escape(story.source_site)
    if story.source_url.startswith("http"):
        url = html.escape(story.source_url, quote=True)
        title = (
            f"<a href=\"{url}\" style=\"color:#2563eb;text-decoration:none;\">"
            f"{headline}</a>"
        )
    else:
        title = headline
    return (
        "<li style=\"margin-bottom:10px;\">"
        f"{title}"
        f"<span style=\"color:#9ca3af;font-size:12px;\"> — {site}</span>"
        "</li>"
    )


def _portal_item(name: str, url: str) -> str:
    safe_name = html.escape(name)
    safe_url = html.escape(url, quote=True)
    return (
        "<li style=\"margin-bottom:6px;\">"
        f"<a href=\"{safe_url}\" style=\"color:#2563eb;text-decoration:none;\">"
        f"{safe_name}</a>"
        "</li>"
    )
