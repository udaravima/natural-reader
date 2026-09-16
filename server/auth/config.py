"""Auth configuration parsing. Model-free (never imports server.model), so it
stays cheap to import from tests and CI."""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Mapping

_TRUE = {"1", "true", "yes", "on"}

# A signed-cookie secret shorter than this is treated as absent: too little
# entropy to resist forgery of the OIDC-flow session cookie.
MIN_SESSION_SECRET_LEN = 32


@dataclass(frozen=True)
class AuthConfig:
    enabled: bool
    oidc_issuer: str | None
    oidc_client_id: str | None
    oidc_client_secret: str | None
    oidc_redirect_url: str | None
    session_secret: str | None
    session_ttl_hours: int
    cookie_secure: bool
    cookie_domain: str | None
    bootstrap_admin_email: str | None


def load_auth_config(env: Mapping[str, str]) -> AuthConfig:
    def flag(key: str, default: bool) -> bool:
        v = env.get(key)
        return default if v is None else v.strip().lower() in _TRUE

    return AuthConfig(
        enabled=flag("AUTH_ENABLED", True),
        oidc_issuer=env.get("OIDC_ISSUER"),
        oidc_client_id=env.get("OIDC_CLIENT_ID"),
        oidc_client_secret=env.get("OIDC_CLIENT_SECRET"),
        oidc_redirect_url=env.get("OIDC_REDIRECT_URL"),
        session_secret=env.get("SESSION_SECRET"),
        session_ttl_hours=int(env.get("SESSION_TTL_HOURS", "168")),
        cookie_secure=flag("COOKIE_SECURE", True),
        cookie_domain=env.get("COOKIE_DOMAIN"),
        bootstrap_admin_email=env.get("BOOTSTRAP_ADMIN_EMAIL"),
    )


def _is_loopback(bind_host: str) -> bool:
    """True only for a genuine loopback bind address.

    `bind_host` MUST be the server's own bind address (the HOST env / the
    listening socket) — NEVER a client-supplied Host header, which is trivially
    spoofed (`Host: LOCALHOST.`, `Host: [::ffff:127.0.0.1]`, …). We resolve the
    value through `ipaddress` so only genuine loopback addresses qualify.
    """
    h = (bind_host or "").strip().lower().strip("[]")
    if h in {"localhost", "localhost."}:
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def dev_bypass_allowed(cfg: AuthConfig, bind_host: str) -> bool:
    """The AUTH_ENABLED=false bypass is valid ONLY on a loopback bind."""
    return (not cfg.enabled) and _is_loopback(bind_host)


def _weak_session_secret(secret: str | None) -> bool:
    """A missing or too-short secret can't safely sign the session cookie."""
    return not secret or len(secret) < MIN_SESSION_SECRET_LEN


def startup_guard(*, auth_enabled: bool, bind_host: str, session_secret: str | None) -> None:
    """Fail fast on two exposed-server footguns:

    1. The ``AUTH_ENABLED=false`` bypass on a non-loopback bind — it would
       silently become the auth posture of an internet-facing server.
    2. A missing/weak ``SESSION_SECRET`` on a non-loopback bind — the app would
       fall back to an ephemeral (or, historically, a hardcoded) signing key,
       letting anyone forge the OIDC-flow session cookie.

    On a loopback bind both are allowed: the bypass is a local-dev escape hatch,
    and a missing secret degrades to an ephemeral per-process one (see app.py).
    """
    loopback = _is_loopback(bind_host)
    if not auth_enabled and not loopback:
        raise RuntimeError(
            "AUTH_ENABLED=false is only allowed on a loopback bind; "
            f"refusing to start with HOST={bind_host}"
        )
    if not loopback and _weak_session_secret(session_secret):
        raise RuntimeError(
            "SESSION_SECRET must be set to a strong random value "
            f"(>= {MIN_SESSION_SECRET_LEN} chars) for a non-loopback bind "
            f"(HOST={bind_host}); refusing to start with a missing or weak secret"
        )
