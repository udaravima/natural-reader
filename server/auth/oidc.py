"""Authlib OIDC Relying Party. Keycloak (or any OIDC provider) is configured via
its discovery document, so no per-provider code is needed."""
from __future__ import annotations

from authlib.integrations.starlette_client import OAuth

from .config import AuthConfig


def build_oauth(cfg: AuthConfig) -> OAuth:
    oauth = OAuth()
    oauth.register(
        name="oidc",
        client_id=cfg.oidc_client_id,
        client_secret=cfg.oidc_client_secret,
        server_metadata_url=(
            f"{cfg.oidc_issuer.rstrip('/')}/.well-known/openid-configuration"
        ),
        client_kwargs={
            "scope": "openid email profile",
            "code_challenge_method": "S256",  # PKCE
        },
    )
    return oauth
