"""Google Cloud credentials for the optional speech engines (spec 2026-11-03-google-speech).

They are found the standard way, by google.auth.default(): GOOGLE_APPLICATION_CREDENTIALS naming a
service-account key file, gcloud's application-default login, or a Google host's metadata server. google-auth is
synchronous, so loading and refreshing run in a thread; a token is kept until google-auth says it's no longer
valid (it refreshes a few minutes before expiry). Nothing here logs or returns a credential, and an error names
what to fix in words, never the key file's path or contents."""
from __future__ import annotations

import asyncio
import os

SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)

_NOT_A_KEY = ("The Google key file isn't a service-account key. In Google Cloud, open IAM & Admin → Service "
              "accounts → the account → Keys → Add key → JSON; an OAuth client ID file won't work.")
_NONE = ("No Google credentials: set GOOGLE_APPLICATION_CREDENTIALS to a service-account key file the backend "
         "can read.")
_UNREADABLE = "The Google key file can't be read by the backend."
_REFRESH = "Google refused this server's key (the token couldn't be refreshed)."


class GoogleAuthError(Exception):
    """Credentials missing, of the wrong kind, or refused. The message is safe to show an admin."""


_credentials = None
_project: str | None = None
_lock: asyncio.Lock | None = None


def _load():
    import google.auth
    from google.auth.exceptions import DefaultCredentialsError
    try:
        return google.auth.default(scopes=list(SCOPES))
    except DefaultCredentialsError as e:
        # google-auth's own words name the file and the type it found: say it without the path.
        if "valid type" in str(e):
            raise GoogleAuthError(_NOT_A_KEY) from None
        if "was not found" in str(e) or "No such file" in str(e):
            raise GoogleAuthError(_UNREADABLE) from None
        raise GoogleAuthError(_NONE) from None
    except (OSError, ValueError):
        raise GoogleAuthError(_UNREADABLE) from None


def _refresh(credentials) -> None:
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    try:
        credentials.refresh(Request())
    except RefreshError:
        raise GoogleAuthError(_REFRESH) from None


async def access_token() -> tuple[str, str | None]:
    """(a bearer token, the project to bill). The project is the key's own, else GOOGLE_CLOUD_PROJECT."""
    global _credentials, _project, _lock
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        if _credentials is None:
            _credentials, _project = await asyncio.to_thread(_load)
        if not _credentials.valid:
            await asyncio.to_thread(_refresh, _credentials)
        return _credentials.token, _project or os.environ.get("GOOGLE_CLOUD_PROJECT") or None


def service_off(response) -> str | None:
    """For a 403, Google's word that the API is switched off in the project it bills, as a sentence for the admin
    (which API, which project); None when the 403 is something else, such as a missing role."""
    try:
        error = response.json().get("error") or {}
    except (ValueError, AttributeError):
        return None
    for detail in error.get("details") or ():
        if isinstance(detail, dict) and detail.get("reason") == "SERVICE_DISABLED":
            meta = detail.get("metadata") or {}
            api = meta.get("serviceTitle") or meta.get("service") or "Google API"
            project = str(meta.get("consumer") or "").removeprefix("projects/") or "the project Google bills"
            return f"The {api} is off in project {project}: enable it there (APIs & Services → Library)"
    return None


def reset_for_tests() -> None:
    global _credentials, _project, _lock
    _credentials, _project, _lock = None, None, None
