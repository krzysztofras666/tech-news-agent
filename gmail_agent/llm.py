"""Use OpenAI to draft a reply for a single email."""

from __future__ import annotations

from openai import OpenAI

from .config import Config
from .gmail_client import EmailMessageView


SYSTEM_PROMPT = """\
You draft email replies on behalf of the user. The user will review and edit
every draft before sending — never assume the draft will be sent as-is.

Rules:
- Write only the body of the reply. No subject line, no "Re:", no headers.
- Match the tone, language, and formality of the incoming email.
- Reply in the same language the email is written in.
- Be concise. Get to the point. Skip filler like "I hope this finds you well".
- If the email asks specific questions, answer them in order.
- If you don't have enough information to answer, write a short draft that
  asks the missing clarifying questions instead of inventing details.
- Never fabricate facts, prices, dates, attachments, or commitments. If the
  user would need to provide a specific value, use a clearly bracketed
  placeholder like [your availability] or [number].
- If a "Knowledge base" is provided, treat it as facts about the user's
  organization. Quote URLs, prices, and concrete dates verbatim from it.
  If the recipient asks for a date or detail that is not in the knowledge
  base, politely refer them to the listed source URL instead of guessing.
- When listing schedule/training dates:
    * Quote EVERY edition relevant to what the recipient asked. Don't
      summarize "only the closest one" or "an example date".
    * A single edition may span multiple months (e.g.
      "20-21 lipca; 3-4 sierpnia 2026" = ONE edition with two sessions).
      If the recipient asks about a month, include every edition whose
      schedule contains that month, even if it also covers other months.
    * Reproduce status labels in parentheses such as "(Brak miejsc)" or
      "(Ostatnie miejsca)" next to the date they apply to.
- If the email is automated / transactional / a notification with no reply
  expected, output exactly the single token: SKIP
- Otherwise, end with a short sign-off on its own line.
"""


def build_user_prompt(
    email: EmailMessageView,
    user_profile: str,
    knowledge: str,
) -> str:
    parts: list[str] = []
    if user_profile:
        parts.append(f"About me (the user replying):\n{user_profile.strip()}")
    if knowledge:
        # The knowledge base may include a static profile plus a live blob
        # fetched from external sources. We deliberately do NOT clip it here:
        # silently dropping the tail of a schedule page is how dates get
        # missed. We clip only the incoming email body, which is bounded.
        parts.append(
            "Knowledge base (facts and source URLs you may use; do not invent "
            "anything outside this):\n"
            f"{knowledge.strip()}"
        )
    parts.append(
        "Incoming email to reply to:\n"
        f"From: {email.from_name} <{email.from_email}>\n"
        f"Subject: {email.subject}\n"
        f"Date: {email.date}\n"
        "---\n"
        f"{_clamp(email.body_text, 6000)}"
    )
    parts.append(
        "Write the reply body now. Remember: output only the body text "
        "(or the single word SKIP if no reply is warranted)."
    )
    return "\n\n".join(parts)


class DraftGenerator:
    def __init__(
        self,
        config: Config,
        *,
        profile: str | None = None,
        knowledge: str | None = None,
    ) -> None:
        config.require_openai()
        self._client = OpenAI(api_key=config.openai_api_key)
        self._model = config.openai_model
        # Per-account profile (if any) overrides the global USER_PROFILE
        # from .env, which is treated as a fallback.
        self._profile = (profile or "").strip() or config.user_profile
        self._knowledge = (knowledge or "").strip()

    def draft_reply(
        self,
        email: EmailMessageView,
        *,
        steering: str | None = None,
    ) -> str | None:
        """Return the reply body, or None if the model says SKIP.

        ``steering`` is optional extra guidance from the user when they
        regenerate a draft (e.g. "make it shorter", "decline politely").
        """

        user_prompt = build_user_prompt(email, self._profile, self._knowledge)
        if steering:
            user_prompt += (
                "\n\nExtra guidance from the user for this regeneration:\n"
                f"{steering}"
            )

        response = self._client.chat.completions.create(
            model=self._model,
            temperature=0.4,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = (response.choices[0].message.content or "").strip()
        if not text or text.strip().upper() == "SKIP":
            return None
        return text


def _clamp(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[...truncated for length...]"
