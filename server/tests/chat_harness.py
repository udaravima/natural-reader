"""Shared plumbing for C1 chat tests.

The chat code opens short pooled connections itself (spec §5.5). Tests hand
every such module ONE transactional test connection through a pool shim, so a
test sees its own writes and everything rolls back afterwards.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from server.auth import deps
from server.auth.users import resolve_or_provision_user, set_status


class PoolShim:
    def __init__(self, conn) -> None:
        self._conn = conn

    @asynccontextmanager
    async def connection(self):
        yield self._conn


def shim_pool(monkeypatch, conn, *modules) -> PoolShim:
    shim = PoolShim(conn)
    for mod in modules:
        monkeypatch.setattr(mod, "get_pool", lambda: shim)
        if hasattr(mod, "is_ready"):
            monkeypatch.setattr(mod, "is_ready", lambda: True)
    return shim


async def member(conn, sub: str, caps=("chat",)) -> deps.Principal:
    u = await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io")
    await set_status(conn, u["id"], "active")
    return deps.Principal(user_id=str(u["id"]), email=u["email"], role="member",
                          capabilities=frozenset(caps))
