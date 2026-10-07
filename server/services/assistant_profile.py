"""The deployment's assistant profile (v2.4 Task A2): the assistant's name,
personality, tone and house rules — its "soul". It leads every chat's system
message, and the app's own rules (server/chat/prompt.py) follow it, so where
they conflict the later, more specific rules about documents, tools and data
win.

Where it comes from, first match wins:
1. the admin console (`app_settings` row `assistant_profile`);
2. the file at CHAT_ASSISTANT_PROFILE_FILE (read on every turn: it is small,
   and a deployer's edit applies without a restart);
3. none.

It is the deployer's trusted instruction, so it isn't fenced; only admins
can change it. Its text is never logged, at any level: a log line records
who changed it and its length only.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Literal

from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)

KEY = "assistant_profile"
PROFILE_MAX_CHARS = 8000   # characters; every one is read before every reply
# The app describes the tools offered each turn; a profile that names one
# that isn't offered sends the model calling it in vain.
APP_TOOLS = ("search_documents", "read_document_pages", "web_search")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")   # all but \t and \n
_warned_paths: set[str] = set()


@dataclass(frozen=True)
class Profile:
    text: str
    source: Literal["admin", "file", "none"]


def clean_profile(text: str) -> str:
    """Control characters except newline and tab removed, trailing
    whitespace stripped, capped at PROFILE_MAX_CHARS."""
    return _CONTROL.sub("", text).rstrip()[:PROFILE_MAX_CHARS]


def profile_warnings(text: str) -> list[str]:
    named = [t for t in APP_TOOLS if t in text]
    if not named:
        return []
    return [f"It names {', '.join(named)}. The app describes the tools available on each turn; naming one "
            "that isn't offered makes the model try to call it. Describe the behaviour instead."]


def file_path() -> str | None:
    return (os.environ.get("CHAT_ASSISTANT_PROFILE_FILE") or "").strip() or None


def _from_file() -> str:
    path = file_path()
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read(PROFILE_MAX_CHARS * 4)
    except OSError as e:
        if path not in _warned_paths:   # once per path, not once per turn
            _warned_paths.add(path)
            logger.warning("CHAT_ASSISTANT_PROFILE_FILE %s can't be read (%s): no profile from it",
                           path, type(e).__name__)
        return ""
    _warned_paths.discard(path)
    return clean_profile(text)


async def load_profile(conn) -> Profile:
    cur = await conn.execute("SELECT value FROM app_settings WHERE key = %s", (KEY,))
    row = await cur.fetchone()
    text = clean_profile(str((row[0] or {}).get("text") or "")) if row else ""
    if text:
        return Profile(text, "admin")
    text = _from_file()
    return Profile(text, "file") if text else Profile("", "none")


async def save_profile(conn, text: str, *, updated_by: str | None) -> None:
    """Save the admin's profile; empty text removes it, so the file (or
    nothing) applies again."""
    text = clean_profile(text)
    if not text:
        await conn.execute("DELETE FROM app_settings WHERE key = %s", (KEY,))
    else:
        await conn.execute(
            "INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES (%s, %s, %s, now()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, "
            "updated_at = now()", (KEY, Jsonb({"text": text}), updated_by))
    logger.info("assistant profile %s by %s (%d chars)", "set" if text else "cleared",
                updated_by or "-", len(text))
