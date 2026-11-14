"""Auth configuration parsing. Model-free (never imports server.model), so it
stays cheap to import from tests and CI."""
from __future__ import annotations

import hashlib
import ipaddress
import logging
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
    # Unused this long, a session ends (0 = never by idleness). The TTL above
    # is the absolute cap from sign-in.
    session_idle_hours: int
    cookie_secure: bool
    cookie_domain: str | None
    bootstrap_admin_email: str | None
    kc_admin_client_id: str | None
    kc_admin_client_secret: str | None


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
        session_idle_hours=max(0, int(env.get("SESSION_IDLE_HOURS", "12"))),
        cookie_secure=flag("COOKIE_SECURE", True),
        cookie_domain=env.get("COOKIE_DOMAIN"),
        bootstrap_admin_email=env.get("BOOTSTRAP_ADMIN_EMAIL"),
        kc_admin_client_id=env.get("KC_ADMIN_CLIENT_ID"),
        kc_admin_client_secret=env.get("KC_ADMIN_CLIENT_SECRET"),
    )


def kc_admin_available(cfg: "AuthConfig") -> bool:
    """True only when both service-account credentials are configured — the
    switch between full Keycloak provisioning and app-only degradation."""
    return bool(cfg.kc_admin_client_id and cfg.kc_admin_client_secret)


# SHA-256 of the two client secrets this repository shipped in deploy/keycloak/realm-export.json up to
# v2.11.0. They are public, so a realm created from that file has a sign-in client and, worse, a
# user-management service account that anyone who can reach Keycloak can use. Hashes, so the values
# themselves are no longer anywhere in the tree.
PUBLISHED_SECRET_HASHES = frozenset({
    "88675de7fe987e6dd4b86353af648998a136b33a0d0355a4e395f24a7db47693",
    "53a4cf38a902cab917d1706c385576aedefe213e0d63a2060dc74e91ab369711",
})


def is_published_secret(value: str | None) -> bool:
    return bool(value) and hashlib.sha256(value.encode()).hexdigest() in PUBLISHED_SECRET_HASHES


def published_secrets(cfg: AuthConfig) -> list[str]:
    """The environment variables still holding a published client secret."""
    return [name for name, value in (("OIDC_CLIENT_SECRET", cfg.oidc_client_secret),
                                     ("KC_ADMIN_CLIENT_SECRET", cfg.kc_admin_client_secret))
            if is_published_secret(value)]


def warn_published_secrets(cfg: AuthConfig) -> list[str]:
    names = published_secrets(cfg)
    for name in names:
        logging.getLogger(__name__).error(
            "%s is a default that was published in this project's repository. Anyone who can reach Keycloak "
            "can use it. Rotate it: regenerate the client's secret in Keycloak, put the new value in .env, "
            "restart (docs/DEPLOYMENT.md, \"Rotating the Keycloak secrets\").", name)
    return names


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
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    # An IPv4-mapped address (::ffff:127.0.0.1) never qualifies. Explicit:
    # Python 3.13 changed ipaddress to call it loopback, and which binds open
    # the auth bypass must not depend on the interpreter's version.
    if getattr(ip, "ipv4_mapped", None) is not None:
        return False
    return ip.is_loopback


def dev_bypass_allowed(cfg: AuthConfig, bind_host: str) -> bool:
    """The AUTH_ENABLED=false bypass is valid ONLY on a loopback bind."""
    return (not cfg.enabled) and _is_loopback(bind_host)


# Headers a reverse proxy adds. One of them on a request means it didn't start on this machine.
_PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-real-ip", "forwarded")


def bypass_request_is_local(headers: Mapping[str, str]) -> bool:
    """With sign-in off, may this request be the seed admin? Only one addressed to this machine by a loopback
    name, with no sign of a proxy in front.

    The bind address alone doesn't say that: behind nginx the backend still listens on 127.0.0.1 while serving
    the internet, and a page on any website can reach a loopback server through DNS rebinding. Both show in the
    request: the proxy passes the public name as Host (or adds its headers), and a rebinding page's Host is the
    attacker's name. Host is client-supplied, so this is never the only check (dev_bypass_allowed is the
    other); it can only take the bypass away."""
    lowered = {k.lower(): v for k, v in headers.items()}
    if any(lowered.get(h) for h in _PROXY_HEADERS):
        return False
    host = (lowered.get("host") or "").strip().lower()
    if host.startswith("["):                      # [::1]:8000
        name = host[1:].split("]", 1)[0]
    else:
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return bool(name) and _is_loopback(name)


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
