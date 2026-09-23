"""The app's capability vocabulary and how it is read from OIDC claims.

Realm roles are the source of truth; this module maps the token's
`realm_access.roles` to the app's known capabilities (ignoring Keycloak's
built-in roles like `offline_access`)."""
from __future__ import annotations

KNOWN_CAPABILITIES = frozenset({"reader", "chat", "admin"})
BOOTSTRAP_ADMIN_CAPABILITIES = frozenset({"admin", "reader", "chat"})


def caps_from_claims(claims: dict) -> list[str]:
    roles = ((claims.get("realm_access") or {}).get("roles")) or []
    return sorted(KNOWN_CAPABILITIES.intersection(roles))
