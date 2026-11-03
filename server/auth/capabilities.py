"""The app's capability vocabulary and how it is read from OIDC claims.

Realm roles are the source of truth; this module maps the token's
`realm_access.roles` to the app's known capabilities (ignoring Keycloak's
built-in roles like `offline_access`)."""
from __future__ import annotations

KNOWN_CAPABILITIES = frozenset({"reader", "chat", "admin", "docling", "talk"})
# Admins may always convert documents and talk, so the bootstrap admin doesn't need the `docling` or `talk` role.
BOOTSTRAP_ADMIN_CAPABILITIES = frozenset({"admin", "reader", "chat"})


def caps_from_claims(claims: dict) -> list[str]:
    roles = ((claims.get("realm_access") or {}).get("roles")) or []
    return sorted(KNOWN_CAPABILITIES.intersection(roles))


def may_talk(capabilities: frozenset[str]) -> bool:
    """Talk, the spoken conversation in the chat: the `talk` role, which an admin never needs."""
    return "talk" in capabilities or "admin" in capabilities
