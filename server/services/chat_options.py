"""The deployment's chat options (C2.1 Task 13): so far one, how web search asks.

`web_search` has three modes. "ask" (the default): the model may only offer a search, and the user says yes
each time. "auto": the web tools are offered to the model directly. "off": no web tool at all. All of them
still need SEARXNG_URL.

Where the mode comes from, first match wins (the same order as the assistant profile,
services/assistant_profile.py):
1. the admin console (`app_settings` row `chat_options`, `{"web_search": mode}`);
2. the CHAT_WEB_SEARCH environment variable, the deployer's starting value (an invalid one is a warning, once,
   and is ignored);
3. "ask".

The row is read on every turn (one primary-key read, no cache), so a change applies from the next message on
every worker. An admin's reset deletes the row, so 2 and 3 apply again. A change writes an audit line: who, the
mode before, the mode after. Chat options is one row so later chat-wide options can join it.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping

from psycopg.types.json import Jsonb

from ..audit import audit

logger = logging.getLogger(__name__)

KEY = "chat_options"
ENV_VAR = "CHAT_WEB_SEARCH"
WEB_MODES = ("ask", "auto", "off")
DEFAULT_WEB_MODE = "ask"
_warned: set[str] = set()   # invalid env values already reported: once each, not once per turn


@dataclass(frozen=True)
class ChatOptions:
    web_search: str   # one of WEB_MODES
    source: str       # "admin" | "env" | "default": which of the three above decided it


def from_env(env: Mapping[str, str] = os.environ) -> ChatOptions:
    """The mode without the database: the environment, else the default. A turn falls back to this when the
    options row can't be read."""
    raw = (env.get(ENV_VAR) or "").strip()
    if raw:
        mode = raw.lower()
        if mode in WEB_MODES:
            return ChatOptions(mode, "env")
        if raw not in _warned:
            _warned.add(raw)
            logger.warning("%s=%r isn't one of %s: ignored, web search asks first", ENV_VAR, raw,
                           "|".join(WEB_MODES))
    return ChatOptions(DEFAULT_WEB_MODE, "default")


async def load_chat_options(conn, env: Mapping[str, str] = os.environ) -> ChatOptions:
    cur = await conn.execute("SELECT value FROM app_settings WHERE key = %s", (KEY,))
    row = await cur.fetchone()
    if row:
        value = row[0] if isinstance(row[0], dict) else {}
        mode = value.get("web_search")
        if mode in WEB_MODES:
            return ChatOptions(mode, "admin")
        logger.warning("app_settings row %s holds an unusable web_search value: ignored", KEY)
    return from_env(env)


async def save_chat_options(conn, web_search: str | None, *, updated_by: str,
                            env: Mapping[str, str] = os.environ) -> ChatOptions:
    """Save the admin's web search mode and return what applies now. None removes the row, so the environment
    (or the default) applies again; any other value outside WEB_MODES is a ValueError. The audit line names
    the mode that applied before and the one saved (None for a reset): ids and modes only."""
    if web_search is not None and web_search not in WEB_MODES:
        raise ValueError(f"web_search must be one of {', '.join(WEB_MODES)}, or null to reset it.")
    before = await load_chat_options(conn, env)
    if web_search is None:
        await conn.execute("DELETE FROM app_settings WHERE key = %s", (KEY,))
    else:
        await conn.execute(
            "INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES (%s, %s, %s, now()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, "
            "updated_at = now()", (KEY, Jsonb({"web_search": web_search}), updated_by))
    audit("chat_options.web_search", by=updated_by, old=before.web_search, new=web_search)
    return await load_chat_options(conn, env)
