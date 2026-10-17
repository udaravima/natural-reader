"""What a scoped personal access token may do.

A token's scopes narrow its owner's capabilities; they never add to them, so a
member's token with the `admin` scope still can't reach admin routes, and a
user who loses `reader` loses read-aloud on every token at once. A token with
no scopes (None) has full access, as every token did before migration 019.

    read_aloud  the two TTS routes            needs `reader`
    documents   everything `reader` gates     needs `reader` (includes read_aloud)
    chat        everything `chat` gates       needs `chat`
    admin       everything `admin` gates      needs `admin`
"""
from __future__ import annotations

SCOPES = ("read_aloud", "documents", "chat", "admin")

# The account capability each scope draws on.
REQUIRES = {"read_aloud": "reader", "documents": "reader", "chat": "chat", "admin": "admin"}

# The capabilities a scope lets through to require_capability. read_aloud lets
# none through: only require_read_aloud honours it.
_PASSES = {"documents": "reader", "chat": "chat", "admin": "admin"}


def narrow(capabilities: frozenset[str], scopes: frozenset[str] | None) -> frozenset[str]:
    if scopes is None:
        return capabilities
    passed = {_PASSES[s] for s in scopes if s in _PASSES}
    return frozenset(c for c in capabilities if c in passed)


def allows_read_aloud(account_capabilities: frozenset[str], scopes: frozenset[str] | None) -> bool:
    if "reader" not in account_capabilities:
        return False
    return scopes is None or bool(scopes & {"read_aloud", "documents"})
