"""The deployment's chat options: how web search asks (C2.1 Task 13), and whether voice input is on.

`web_search` has three modes. "ask" (the default): the model may only offer a search, and the user says yes
each time. "auto": web_search and read_web_pages are offered to the model directly. "off": no web tool at
all. All of them still need SEARXNG_URL.

`voice_input` is a boolean, off by default so an upgrade never starts a model download or uses CPU unasked.
It comes from the admin console, else CHAT_VOICE_INPUT (on|off), else off.

The two settings share one row, `{"web_search": ..., "voice_input": ...}`, and are independent: each key is read
and written on its own, a missing key falls through to the environment without a warning, and a save or reset
changes only its own key, in one statement, so two admins saving different settings can't undo each other.

Where the web search mode comes from, first match wins (the same order as the assistant profile,
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
VOICE_ENV_VAR = "CHAT_VOICE_INPUT"
WEB_MODES = ("ask", "auto", "off")
DEFAULT_WEB_MODE = "ask"
_warned: set[str] = set()   # invalid env values already reported: once each, not once per turn


@dataclass(frozen=True)
class ChatOptions:
    web_search: str   # one of WEB_MODES
    source: str       # "admin" | "env" | "default": which of the three above decided web_search
    voice_input: bool = False
    voice_source: str = "default"   # the same three, for voice_input


def _web_from_env(env: Mapping[str, str]) -> tuple[str, str]:
    raw = (env.get(ENV_VAR) or "").strip()
    if raw:
        mode = raw.lower()
        if mode in WEB_MODES:
            return mode, "env"
        if raw not in _warned:
            _warned.add(raw)
            logger.warning("%s=%r isn't one of %s: ignored, web search asks first", ENV_VAR, raw,
                           "|".join(WEB_MODES))
    return DEFAULT_WEB_MODE, "default"


def _voice_from_env(env: Mapping[str, str]) -> tuple[bool, str]:
    raw = (env.get(VOICE_ENV_VAR) or "").strip()
    if raw:
        if raw.lower() in ("on", "off"):
            return raw.lower() == "on", "env"
        if f"voice:{raw}" not in _warned:
            _warned.add(f"voice:{raw}")
            logger.warning("%s=%r isn't on or off: ignored, voice input stays off", VOICE_ENV_VAR, raw)
    return False, "default"


def from_env(env: Mapping[str, str] = os.environ) -> ChatOptions:
    """The options without the database: the environment, else the defaults. A turn falls back to this when the
    options row can't be read."""
    web, web_source = _web_from_env(env)
    voice, voice_source = _voice_from_env(env)
    return ChatOptions(web, web_source, voice, voice_source)


async def load_chat_options(conn, env: Mapping[str, str] = os.environ) -> ChatOptions:
    """Each key on its own: a key the row doesn't hold falls through to the environment silently; one it holds
    but can't be used (or a row that isn't an object) is a warning, and the environment applies for it."""
    cur = await conn.execute("SELECT value FROM app_settings WHERE key = %s", (KEY,))
    row = await cur.fetchone()
    value: dict = {}
    if row:
        if isinstance(row[0], dict):
            value = row[0]
        else:
            logger.warning("app_settings row %s isn't an object: ignored", KEY)
    options = from_env(env)
    web, web_source = options.web_search, options.source
    voice, voice_source = options.voice_input, options.voice_source
    if "web_search" in value:
        if value["web_search"] in WEB_MODES:
            web, web_source = value["web_search"], "admin"
        else:
            logger.warning("app_settings row %s holds an unusable web_search value: ignored", KEY)
    if "voice_input" in value:
        if isinstance(value["voice_input"], bool):
            voice, voice_source = value["voice_input"], "admin"
        else:
            logger.warning("app_settings row %s holds an unusable voice_input value: ignored", KEY)
    return ChatOptions(web, web_source, voice, voice_source)


async def _set_key(conn, name: str, value, updated_by: str) -> None:
    """Merge one key into the row (creating it, or replacing a row that isn't an object), in one statement."""
    await conn.execute(
        "INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES (%s, %s, %s, now()) "
        "ON CONFLICT (key) DO UPDATE SET "
        "value = CASE WHEN jsonb_typeof(app_settings.value) = 'object' THEN app_settings.value ELSE '{}'::jsonb END "
        "|| EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = now()",
        (KEY, Jsonb({name: value}), updated_by))


async def _clear_key(conn, name: str, updated_by: str) -> None:
    """Remove one key (a row that isn't an object becomes empty), then delete the row if nothing is left. The
    delete re-checks the row after any concurrent save commits, so a key saved in between is never lost."""
    await conn.execute(
        "UPDATE app_settings SET value = CASE WHEN jsonb_typeof(value) = 'object' THEN value - %s "
        "ELSE '{}'::jsonb END, updated_by = %s, updated_at = now() WHERE key = %s", (name, updated_by, KEY))
    await conn.execute("DELETE FROM app_settings WHERE key = %s AND value = '{}'::jsonb", (KEY,))


async def save_chat_options(conn, web_search: str | None, *, updated_by: str,
                            env: Mapping[str, str] = os.environ) -> ChatOptions:
    """Save the admin's web search mode and return what applies now. None removes that key (the row too, once
    empty), so the environment (or the default) applies again; any other value outside WEB_MODES is a ValueError.
    voice_input is left alone. The audit line names the mode that applied before and the one saved (None for a
    reset): ids and modes only."""
    if web_search is not None and web_search not in WEB_MODES:
        raise ValueError(f"web_search must be one of {', '.join(WEB_MODES)}, or null to reset it.")
    before = await load_chat_options(conn, env)
    if web_search is None:
        await _clear_key(conn, "web_search", updated_by)
    else:
        await _set_key(conn, "web_search", web_search, updated_by)
    audit("chat_options.web_search", by=updated_by, old=before.web_search, new=web_search)
    return await load_chat_options(conn, env)


async def save_voice_input(conn, value: bool | None, *, updated_by: str,
                           env: Mapping[str, str] = os.environ) -> ChatOptions:
    """Save the admin's voice input switch and return what applies now. None removes that key, so
    CHAT_VOICE_INPUT (or off) applies again; web_search is left alone. Anything but a bool or None is a
    ValueError. The audit line carries the value before and after (None for a reset)."""
    if value is not None and not isinstance(value, bool):
        raise ValueError("voice_input must be true or false, or null to reset it.")
    before = await load_chat_options(conn, env)
    if value is None:
        await _clear_key(conn, "voice_input", updated_by)
    else:
        await _set_key(conn, "voice_input", value, updated_by)
    audit("chat_options.voice_input", by=updated_by, old=before.voice_input, new=value)
    return await load_chat_options(conn, env)
