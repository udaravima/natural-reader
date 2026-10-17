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

    Blank/unset allows no cross-origin browser caller: the SPA is same-origin,
    and the read-aloud extension calls from its service worker, which CORS
    doesn't gate. (Until extension 0.2.0 it called from the page, which is why
    this used to default to "*" and let any website drive Kokoro.) Set
    FRONTEND_ORIGIN to allow other origins, which also flips on credentials —
    see cors_allow_credentials; "*" is still accepted as an explicit opt-in.
    """
    if not raw or not raw.strip():
        return []
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def cors_allow_credentials(origins: list[str]) -> bool:
    """
    Cookies (sub-project B) require an explicit origin allowlist. A wildcard
    origin combined with credentials is both invalid per the CORS spec (browsers
    refuse it) and the SEC-3 footgun, so credentials are enabled only once
    origins are pinned.
    """
    return "*" not in origins
