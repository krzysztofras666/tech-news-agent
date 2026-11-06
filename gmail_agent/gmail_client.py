"""Thin wrapper around the Gmail API for the operations we need."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from typing import Iterable

from bs4 import BeautifulSoup
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


@dataclass
class EmailMessageView:
    """A normalised view of a Gmail message that's easy to feed to an LLM."""

    id: str
    thread_id: str
    history_id: str
    label_ids: list[str]
    from_name: str
    from_email: str
    to: list[str]
    cc: list[str]
    subject: str
    date: str
    rfc822_message_id: str
    references: str
    snippet: str
    body_text: str
    is_from_me: bool = False
    headers: dict[str, str] = field(default_factory=dict)


class GmailClient:
    def __init__(self, creds: Credentials) -> None:
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        profile = self._service.users().getProfile(userId="me").execute()
        self.address: str = profile["emailAddress"].lower()

    # ---- listing ---------------------------------------------------------

    SCOPES = {
        # All unread mail in the INBOX, including the Promotions/Updates/Social
        # tabs (the per-message skip rules in `agent.py` still filter
        # mailing lists and no-reply senders).
        "inbox": "is:unread in:inbox",
        # Only the Primary tab. Useful if you rely on Gmail categories.
        "primary": "is:unread category:primary in:inbox",
    }

    def list_unread_message_ids(
        self,
        max_results: int = 50,
        *,
        scope: str = "inbox",
        exclude_label_name: str | None = None,
    ) -> list[str]:
        """Return message IDs for unread emails matching ``scope``.

        Args:
            max_results: maximum number of IDs to return (newest first).
            scope: one of :data:`SCOPES`.
            exclude_label_name: if given, messages already carrying that
                Gmail label are filtered out at search time.
        """

        try:
            query = self.SCOPES[scope]
        except KeyError as exc:
            valid = ", ".join(sorted(self.SCOPES))
            raise ValueError(
                f"Unknown scope {scope!r}; expected one of: {valid}"
            ) from exc

        if exclude_label_name:
            # Gmail's search syntax uses dashes for "not". The label name may
            # contain slashes (nested labels) or spaces; quote it.
            query += f' -label:"{exclude_label_name}"'

        ids: list[str] = []
        page_token: str | None = None
        while True:
            resp = (
                self._service.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    maxResults=min(100, max_results - len(ids)),
                    pageToken=page_token,
                )
                .execute()
            )
            for msg in resp.get("messages", []):
                ids.append(msg["id"])
                if len(ids) >= max_results:
                    return ids
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return ids

    # ---- fetching --------------------------------------------------------

    def get_message(self, message_id: str) -> EmailMessageView:
        msg = (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        return self._parse(msg)

    def thread_has_my_reply(self, thread_id: str, after_message_id: str) -> bool:
        """True if, in the thread, I've already sent a message after the given one."""

        thread = (
            self._service.users()
            .threads()
            .get(userId="me", id=thread_id, format="metadata",
                 metadataHeaders=["From"])
            .execute()
        )
        seen_target = False
        for m in thread.get("messages", []):
            if m["id"] == after_message_id:
                seen_target = True
                continue
            if not seen_target:
                continue
            label_ids = m.get("labelIds", []) or []
            if "SENT" in label_ids:
                return True
            from_header = _header(m.get("payload", {}).get("headers", []), "From")
            _, addr = parseaddr(from_header)
            if addr and addr.lower() == self.address:
                return True
        return False

    # ---- labels ----------------------------------------------------------

    def ensure_label(self, name: str) -> str:
        """Return the ID of the label with the given display name, creating it
        if it doesn't exist. ``name`` may contain slashes for nested labels.
        """

        resp = self._service.users().labels().list(userId="me").execute()
        for label in resp.get("labels", []):
            if label.get("name") == name:
                return label["id"]
        created = (
            self._service.users()
            .labels()
            .create(
                userId="me",
                body={
                    "name": name,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
            .execute()
        )
        return created["id"]

    def apply_label(self, message_id: str, label_id: str) -> None:
        self._service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [label_id]},
        ).execute()

    # ---- drafts ----------------------------------------------------------

    def create_reply_draft(
        self,
        *,
        original: EmailMessageView,
        body_text: str,
    ) -> str:
        """Create a Gmail draft replying to `original`. Returns the draft id."""

        reply_to = original.headers.get("Reply-To") or original.from_email
        subject = original.subject or ""
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}".strip()

        message = EmailMessage()
        message["To"] = reply_to
        message["Subject"] = subject
        if original.rfc822_message_id:
            message["In-Reply-To"] = original.rfc822_message_id
            refs = original.references.strip()
            message["References"] = (
                f"{refs} {original.rfc822_message_id}".strip()
                if refs
                else original.rfc822_message_id
            )
        message.set_content(body_text)

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        draft = (
            self._service.users()
            .drafts()
            .create(
                userId="me",
                body={"message": {"raw": raw, "threadId": original.thread_id}},
            )
            .execute()
        )
        return draft["id"]

    # ---- internals -------------------------------------------------------

    def _parse(self, msg: dict) -> EmailMessageView:
        payload = msg.get("payload", {})
        headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
        from_name, from_email = parseaddr(headers.get("From", ""))
        to = [a for _, a in getaddresses([headers.get("To", "")]) if a]
        cc = [a for _, a in getaddresses([headers.get("Cc", "")]) if a]
        label_ids = msg.get("labelIds", []) or []
        is_from_me = "SENT" in label_ids or (
            from_email and from_email.lower() == self.address
        )

        body_text = _extract_body(payload)

        return EmailMessageView(
            id=msg["id"],
            thread_id=msg["threadId"],
            history_id=msg.get("historyId", ""),
            label_ids=label_ids,
            from_name=from_name,
            from_email=from_email,
            to=to,
            cc=cc,
            subject=headers.get("Subject", ""),
            date=headers.get("Date", ""),
            rfc822_message_id=headers.get("Message-ID", "") or headers.get("Message-Id", ""),
            references=headers.get("References", ""),
            snippet=msg.get("snippet", ""),
            body_text=body_text,
            is_from_me=is_from_me,
            headers=headers,
        )


# --------- helpers --------------------------------------------------------


def _header(headers: Iterable[dict], name: str) -> str:
    name_lower = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_lower:
            return h.get("value", "")
    return ""


def _extract_body(payload: dict) -> str:
    """Pull a plaintext body out of a Gmail payload, falling back to HTML→text."""

    text_plain = _find_part(payload, "text/plain")
    if text_plain:
        return _decode(text_plain).strip()

    text_html = _find_part(payload, "text/html")
    if text_html:
        html = _decode(text_html)
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        return soup.get_text("\n").strip()

    data = payload.get("body", {}).get("data")
    if data:
        return _b64(data).strip()
    return ""


def _find_part(part: dict, mime: str) -> dict | None:
    if part.get("mimeType") == mime and part.get("body", {}).get("data"):
        return part
    for sub in part.get("parts", []) or []:
        found = _find_part(sub, mime)
        if found:
            return found
    return None


def _decode(part: dict) -> str:
    return _b64(part["body"]["data"])


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")
