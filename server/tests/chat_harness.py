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


# ---- Task 8: a scripted router and the SPA contract fixtures ----

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from server.llm.types import Capabilities, Finish, TextDelta, Usage


def reply(text: str = "Hello there.", prompt: int = 10, completion: int = 3) -> list:
    return [TextDelta(text), Usage(prompt, completion), Finish("stop")]


@dataclass
class FakeRouter:
    """Stands in for server.llm.router.Router. Each stream_chat call plays the
    next scripted step: a list of chunks, or (chunks, exception) to raise after
    them. With no steps left it plays reply()."""
    steps: list = field(default_factory=list)
    caps: Capabilities = field(default_factory=lambda: Capabilities(
        tools=True, thinking=True, vision=True, audio=None, context_window=None))
    allowed: bool = True
    listed: bool = True
    providers: bool = True
    calls: list = field(default_factory=list)

    def has_providers(self) -> bool:
        return self.providers

    def is_allowed(self, model_id: str) -> bool:
        return self.allowed

    async def is_listed(self, model_id: str) -> bool:
        return self.listed

    def canonical_id(self, model_id: str) -> str:
        return model_id if model_id.startswith("ollama:") else f"ollama:{model_id}"

    async def capabilities(self, model_id: str) -> Capabilities:
        return self.caps

    def stream_chat(self, model_id, messages, tools, settings):
        self.calls.append({"model": model_id, "messages": list(messages),
                           "tools": [t.name for t in tools], "settings": settings})
        step = self.steps.pop(0) if self.steps else reply()
        return _play(step)


async def _play(step):
    chunks, exc = step if isinstance(step, tuple) else (step, None)
    for c in chunks:
        yield c
    if exc is not None:
        raise exc


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "chat_events"
_ID_FIELDS = {"runId": "<message-id>", "messageId": "<message-id>",
              "userMessageId": "<user-message-id>", "sessionId": "<session-id>"}


def normalize(events: list[dict]) -> list[dict]:
    """Replace per-run values (ids, wall-clock time) so fixtures are stable."""
    out = []
    for e in events:
        e = {k: (_ID_FIELDS[k] if k in _ID_FIELDS else v) for k, v in e.items()}
        if e.get("type") == "finish" and isinstance(e.get("stats"), dict):
            e["stats"] = {**e["stats"], "totalNs": 0}
        out.append(e)
    return out


def assert_fixture(name: str, events: list[dict]) -> None:
    """The event sequences the backend asserts are the SPA reducer's test input
    (spec §11). A format change fails here first; regenerate with
    UPDATE_CHAT_FIXTURES=1, then make the SPA tests pass against the new files."""
    path = FIXTURE_DIR / f"{name}.jsonl"
    got = normalize(events)
    if os.environ.get("UPDATE_CHAT_FIXTURES") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(e, sort_keys=True, ensure_ascii=False) + "\n" for e in got))
        return
    assert path.exists(), f"{path} missing: run once with UPDATE_CHAT_FIXTURES=1"
    want = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert got == want, f"event format for {name!r} changed: regenerate with UPDATE_CHAT_FIXTURES=1 and update src/lib/chatEvents.js"
