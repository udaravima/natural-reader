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

    async def find_user_by_email(self, email: str) -> str | None:
        r = await self._req("GET", "/users", params={"email": email, "exact": "true"})
        if r.status_code != 200:
            raise KCAdminError(f"find_user_by_email {r.status_code}")
        rows = r.json()
        return rows[0]["id"] if rows else None

    async def create_user(self, *, email: str, display_name: str | None = None,
                          email_verified: bool = False) -> str:
        payload = {"email": email, "username": email, "enabled": True,
                   "emailVerified": email_verified}
        if display_name:
            payload["firstName"] = display_name
        r = await self._req("POST", "/users", json=payload)
        if r.status_code == 409:
            raise KCAdminError("user already exists in Keycloak")
        if r.status_code != 201:
            raise KCAdminError(f"create_user {r.status_code}")
        loc = r.headers.get("Location")
        if not loc:
            raise KCAdminError("create_user: 201 without Location header")
        return loc.rstrip("/").rsplit("/", 1)[-1]

    async def set_temp_password(self, sub: str, password: str,
                                *, temporary: bool = True) -> None:
        r = await self._req("PUT", f"/users/{sub}/reset-password",
                            json={"type": "password", "value": password,
                                  "temporary": temporary})
        if r.status_code not in (200, 204):
            raise KCAdminError(f"set_temp_password {r.status_code}")

    async def send_actions_email(self, sub: str, actions: list[str]) -> None:
        r = await self._req("PUT", f"/users/{sub}/execute-actions-email", json=actions)
        if r.status_code not in (200, 204):
            raise KCAdminError(f"send_actions_email {r.status_code}")

    async def set_enabled(self, sub: str, enabled: bool) -> None:
        r = await self._req("PUT", f"/users/{sub}", json={"enabled": enabled})
        if r.status_code not in (200, 204):
            raise KCAdminError(f"set_enabled {r.status_code}")

    async def delete_user(self, sub: str) -> None:
        r = await self._req("DELETE", f"/users/{sub}")
        if r.status_code not in (200, 204, 404):
            raise KCAdminError(f"delete_user {r.status_code}")

    async def _role(self, name: str) -> dict:
        r = await self._req("GET", f"/roles/{name}")
        if r.status_code != 200:
            raise KCAdminError(f"role {name} lookup {r.status_code}")
        return {"id": r.json()["id"], "name": name}

    async def assign_realm_roles(self, sub: str, names: list[str]) -> None:
        if not names:
            return
        body = [await self._role(n) for n in names]
        r = await self._req("POST", f"/users/{sub}/role-mappings/realm", json=body)
        if r.status_code not in (200, 204):
            raise KCAdminError(f"assign_realm_roles {r.status_code}")

    async def remove_realm_roles(self, sub: str, names: list[str]) -> None:
        if not names:
            return
        body = [await self._role(n) for n in names]
        r = await self._req("DELETE", f"/users/{sub}/role-mappings/realm", json=body)
        if r.status_code not in (200, 204):
            raise KCAdminError(f"remove_realm_roles {r.status_code}")

    async def get_user_realm_roles(self, sub: str) -> list[str]:
        r = await self._req("GET", f"/users/{sub}/role-mappings/realm/composite")
        if r.status_code != 200:
            raise KCAdminError(f"get_user_realm_roles {r.status_code}")
        return [x["name"] for x in r.json()]

    async def realm_smtp_configured(self) -> bool:
        r = await self._req("GET", "")
        if r.status_code != 200:
            raise KCAdminError(f"realm read {r.status_code}")
        return bool((r.json().get("smtpServer") or {}).get("host"))


def build_kc_admin(cfg) -> "KeycloakAdmin | None":
    from .config import kc_admin_available
    if not (cfg.oidc_issuer and kc_admin_available(cfg)):
        return None
    return KeycloakAdmin(cfg.oidc_issuer, cfg.kc_admin_client_id,
                         cfg.kc_admin_client_secret)
