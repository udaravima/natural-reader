"""Keycloak Admin REST client (service-account, client-credentials grant).

Derives all URLs from OIDC_ISSUER (`{base}/realms/{realm}`): the token endpoint
is `{issuer}/protocol/openid-connect/token`, the admin base is
`{base}/admin/realms/{realm}`. Caches the admin access token. All unexpected
responses raise KCAdminError so callers map them to a clear HTTP error."""
from __future__ import annotations

import time
from urllib.parse import urlsplit

import httpx


class KCAdminError(RuntimeError):
    pass


class KeycloakAdmin:
    def __init__(self, issuer: str, client_id: str, client_secret: str,
                 *, http: httpx.AsyncClient | None = None):
        issuer = issuer.rstrip("/")
        self._issuer = issuer
        parts = urlsplit(issuer)
        base = f"{parts.scheme}://{parts.netloc}"
        realm = issuer.rsplit("/realms/", 1)[-1]
        self._token_url = f"{issuer}/protocol/openid-connect/token"
        self._admin = f"{base}/admin/realms/{realm}"
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or httpx.AsyncClient(timeout=15.0)
        self._tok: str | None = None
        self._tok_exp: float = 0.0

    async def _token(self) -> str:
        if self._tok and time.monotonic() < self._tok_exp:
            return self._tok
        try:
            r = await self._http.post(self._token_url, data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            })
        except httpx.HTTPError as e:
            raise KCAdminError(f"token request failed: {e}") from e
        if r.status_code != 200:
            raise KCAdminError(f"token endpoint returned {r.status_code}")
        body = r.json()
        self._tok = body["access_token"]
        self._tok_exp = time.monotonic() + max(30, int(body.get("expires_in", 60)) - 30)
        return self._tok

    async def _req(self, method: str, path: str, **kw) -> httpx.Response:
        tok = await self._token()
        headers = {**kw.pop("headers", {}), "Authorization": f"Bearer {tok}"}
        try:
            r = await self._http.request(method, f"{self._admin}{path}",
                                         headers=headers, **kw)
        except httpx.HTTPError as e:
            raise KCAdminError(f"{method} {path} failed: {e}") from e
        if r.status_code == 401:  # token might have expired early — retry once
            self._tok = None
            tok = await self._token()
            headers["Authorization"] = f"Bearer {tok}"
            r = await self._http.request(method, f"{self._admin}{path}",
                                         headers=headers, **kw)
        return r


def build_kc_admin(cfg) -> "KeycloakAdmin | None":
    from .config import kc_admin_available
    if not (cfg.oidc_issuer and kc_admin_available(cfg)):
        return None
    return KeycloakAdmin(cfg.oidc_issuer, cfg.kc_admin_client_id,
                         cfg.kc_admin_client_secret)
