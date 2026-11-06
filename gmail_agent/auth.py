"""Gmail OAuth: get authorized credentials, cache + refresh them locally."""

from __future__ import annotations

from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from .config import GMAIL_SCOPES, Config


class TokenExpiredError(RuntimeError):
    """Refresh token is no longer accepted by Google.

    Common causes:
    - The OAuth consent screen is in 'Testing' mode (refresh tokens expire
      after 7 days).
    - The user revoked the app's access at https://myaccount.google.com.
    - The user changed their password and Google force-rotated tokens.

    The fix is always: re-run `python -m gmail_agent auth --account <email>`.
    """


def load_credentials(token_path: Path) -> Credentials | None:
    """Load credentials from ``token_path``, refreshing if needed.

    Returns:
        - ``None`` if the file doesn't exist (account not authorized yet).
        - A valid :class:`Credentials` instance otherwise.

    Raises:
        TokenExpiredError: if the file exists but the refresh token has been
            invalidated by Google. The caller should prompt the user to
            re-authorize that account.
    """

    if not token_path.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise TokenExpiredError(str(exc)) from exc
        save_credentials(creds, token_path)
        return creds
    # Token file is malformed or has no refresh token — treat as a hard
    # invalidation; the user will need to re-auth.
    raise TokenExpiredError(
        f"Token at {token_path} is unusable (no refresh token). "
        "Re-run the auth command."
    )


def run_oauth_flow(config: Config) -> Credentials:
    """Open a browser, walk the user through OAuth, and return credentials.

    The caller is responsible for deciding where to persist the resulting
    token (typically ``accounts/<email>/token.json``).
    """

    config.require_google_credentials()
    flow = InstalledAppFlow.from_client_secrets_file(
        str(config.credentials_file), GMAIL_SCOPES
    )
    return flow.run_local_server(port=0)


def save_credentials(creds: Credentials, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json())
    try:
        path.chmod(0o600)
    except OSError:
        pass
