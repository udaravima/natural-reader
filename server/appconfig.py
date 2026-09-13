"""
App-wide configuration parsing that must stay cheap to import.

Kept separate from `app.py` because importing `app.py` pulls in the TTS
endpoints, which load the ~325 MB Kokoro model at import time. Helpers here can
be unit-tested (and used in CI) without that cost.
"""
from __future__ import annotations


def parse_cors_origins(raw: str | None) -> list[str]:
    """
    Turn a comma-separated FRONTEND_ORIGIN env value into a CORS allowlist.

    Blank/unset stays permissive (`["*"]`) on purpose: the read-aloud extension
    fetches TTS from a content script, so its requests carry an arbitrary page
    origin that can't be allowlisted. Deployments pin origins by setting
    FRONTEND_ORIGIN, which also flips on credentials — see cors_allow_credentials.
    """
    if not raw or not raw.strip():
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def cors_allow_credentials(origins: list[str]) -> bool:
    """
    Cookies (sub-project B) require an explicit origin allowlist. A wildcard
    origin combined with credentials is both invalid per the CORS spec (browsers
    refuse it) and the SEC-3 footgun, so credentials are enabled only once
    origins are pinned.
    """
    return "*" not in origins
