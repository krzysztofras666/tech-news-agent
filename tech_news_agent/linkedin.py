"""Generate a short, personalized LinkedIn post draft from ranked stories."""

from __future__ import annotations

import json
from datetime import date

from openai import OpenAI

from .config import Config
from .models import Story
from .profile import load_author_profile


SYSTEM_PROMPT = """\
You ghostwrite a SHORT LinkedIn post for the author below. Your #1 job:
sound human, wry, and slightly funny — NOT like generic ChatGPT slop.

Return STRICT JSON:
{{"linkedin_draft": "post text ready to paste"}}

Rules:
- First person (I, my, me). Pick 2-3 stories from the input — tech/crypto/AI only.
- Tone: dry humor, light sarcasm, practitioner who's seen too much. Like texting
  a smart friend, not publishing a white paper.
- ONE casual personal aside from the profile when it fits — never as a brag.
- Short punchy sentences. Contractions. Fragments OK.
- Under 600 characters. Plain text. No hashtags, no bold, no emojis.
- End with wit — a punchline, shrug, or cheeky question. NOT "I'd love to hear
  your thoughts" or "what do you think?" engagement bait.

NEVER write like this (instant fail):
- "The rapid evolution of..."
- "As someone who has..."
- "strike a balance" / "at scale" / "in today's landscape"
- "both promise and peril" / "game-changer" / "exciting times"
- Polished consultant voice or fake humility

If the news is absurd, say so. If you've lived it before (crypto winter, etc.),
mention it with eye-roll energy. Be specific, not profound.

Only use facts from the stories + profile. Output ONLY JSON.

AUTHOR PROFILE:
{author_profile}
"""


def _user_prompt(stories: list[Story]) -> str:
    payload = [
        {
            "headline": story.headline,
            "summary": story.summary,
            "topic": story.topic,
            "source": story.source_site,
        }
        for story in stories
    ]
    return (
        f"Today: {date.today().isoformat()}\n"
        "Top stories to synthesize:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
        "Return the JSON now."
    )


class LinkedInDrafter:
    REQUEST_TIMEOUT_SECONDS = 60.0
    MAX_RETRIES = 1

    def __init__(self, config: Config) -> None:
        config.require_openai()
        self._client = OpenAI(
            api_key=config.openai_api_key,
            timeout=self.REQUEST_TIMEOUT_SECONDS,
            max_retries=self.MAX_RETRIES,
        )
        self._model = config.openai_model
        self._author_profile = load_author_profile(config.author_profile_path)

    def draft(self, stories: list[Story]) -> str:
        if not stories:
            return (
                "Slow news day in tech. Either that or everyone's finally "
                "touching grass. Unlikely."
            )

        system = SYSTEM_PROMPT.format(author_profile=self._author_profile)
        response = self._client.chat.completions.create(
            model=self._model,
            temperature=0.7,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": _user_prompt(stories)},
            ],
        )
        raw = (response.choices[0].message.content or "").strip()
        if not raw:
            return _fallback_draft(stories)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return _fallback_draft(stories)
        draft = data.get("linkedin_draft")
        if isinstance(draft, str) and draft.strip():
            return draft.strip()
        return _fallback_draft(stories)


def _fallback_draft(stories: list[Story]) -> str:
    lines = ["Morning scroll:"]
    for story in stories[:3]:
        lines.append(f"• {story.headline}")
    lines.append("")
    lines.append("Anyway. Coffee's getting cold.")
    return "\n".join(lines).strip()
