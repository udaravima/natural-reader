"""The profile library: the admin's house rules and named assistant profiles
(docs/superpowers/specs/2026-11-16-profile-library-design.md).

House rules apply to every chat; one profile, chosen per chat, follows them; the app's own rules come last
(server/chat/profile_text.py puts the three together). Both texts are the deployer's trusted instruction, so they
aren't fenced, and only admins change them. Their text is never logged: a log line carries who, which profile and
a length.

While any profile exists exactly one is the default, and the default is always on. Changes take a table lock:
the table is a handful of rows that only admins write, and a turn's reads don't wait for it.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from psycopg import errors
from psycopg.types.json import Jsonb

from .assistant_profile import clean_profile

logger = logging.getLogger(__name__)

HOUSE_RULES_KEY = "assistant_house_rules"
HOUSE_RULES_MAX_CHARS = 2000   # characters; read before every reply, as the profile is
NAME_MAX_CHARS = 60
DESCRIPTION_MAX_CHARS = 200
_COLUMNS = "id::text, name, description, text, enabled, is_default, position"
_LOCK = "LOCK TABLE assistant_profiles IN SHARE ROW EXCLUSIVE MODE"


@dataclass(frozen=True)
class ProfileRow:
    id: str
    name: str
    description: str
    text: str
    enabled: bool
    is_default: bool
    position: int


class LibraryError(ValueError):
    """code: not_found | name_taken | default_required | invalid."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _one_line(value: str) -> str:
    return " ".join(clean_profile(value or "").split())


def _name(value: str) -> str:
    name = _one_line(value)
    if not 1 <= len(name) <= NAME_MAX_CHARS:
        raise LibraryError("invalid", f"A profile's name is 1 to {NAME_MAX_CHARS} characters.")
    return name


def _description(value: str) -> str:
    description = _one_line(value)
    if len(description) > DESCRIPTION_MAX_CHARS:
        raise LibraryError("invalid", f"A description is at most {DESCRIPTION_MAX_CHARS} characters.")
    return description


def _text(value: str) -> str:
    text = clean_profile(value or "").strip()
    if not text:
        raise LibraryError("invalid", "Write the profile first.")
    return text


def _uuid(profile_id: str) -> str | None:
    try:
        return str(uuid.UUID(str(profile_id)))
    except (ValueError, AttributeError, TypeError):
        return None


async def list_profiles(conn, *, enabled_only: bool = False) -> list[ProfileRow]:
    where = "WHERE enabled" if enabled_only else ""
    cur = await conn.execute(f"SELECT {_COLUMNS} FROM assistant_profiles {where} ORDER BY position, created_at, id")
    return [ProfileRow(*r) for r in await cur.fetchall()]


async def get_profile(conn, profile_id: str | None) -> ProfileRow | None:
    pid = _uuid(profile_id) if profile_id else None
    if pid is None:
        return None
    cur = await conn.execute(f"SELECT {_COLUMNS} FROM assistant_profiles WHERE id = %s", (pid,))
    row = await cur.fetchone()
    return ProfileRow(*row) if row else None


async def default_profile(conn) -> ProfileRow | None:
    cur = await conn.execute(f"SELECT {_COLUMNS} FROM assistant_profiles WHERE is_default")
    row = await cur.fetchone()
    return ProfileRow(*row) if row else None


async def chats_using(conn, profile_id: str) -> int:
    pid = _uuid(profile_id)
    if pid is None:
        return 0
    cur = await conn.execute("SELECT count(*) FROM chat_sessions WHERE profile_id = %s", (pid,))
    return (await cur.fetchone())[0]


async def create_profile(conn, *, name: str, description: str, text: str, updated_by: str | None) -> ProfileRow:
    """Add a profile at the end of the list. The first one is the default."""
    name, description, text = _name(name), _description(description), _text(text)
    try:
        async with conn.transaction():
            await conn.execute(_LOCK)
            cur = await conn.execute(
                f"INSERT INTO assistant_profiles (name, description, text, updated_by, is_default, position) "
                f"SELECT %s, %s, %s, %s, NOT EXISTS (SELECT 1 FROM assistant_profiles), "
                f"coalesce((SELECT max(position) + 1 FROM assistant_profiles), 0) RETURNING {_COLUMNS}",
                (name, description, text, updated_by))
            row = ProfileRow(*await cur.fetchone())
    except errors.UniqueViolation:
        raise LibraryError("name_taken", "Another profile has that name.") from None
    logger.info("assistant profile %s added by %s (%d chars)", row.id, updated_by or "-", len(text))
    return row


async def update_profile(conn, profile_id: str, *, name: str | None = None, description: str | None = None,
                         text: str | None = None, enabled: bool | None = None, is_default: bool | None = None,
                         updated_by: str | None) -> ProfileRow:
    """Change what is given. `is_default=True` moves the default here; the default can't be turned off, and its
    mark can't be taken away without giving it to another."""
    sets: dict[str, object] = {}
    if name is not None:
        sets["name"] = _name(name)
    if description is not None:
        sets["description"] = _description(description)
    if text is not None:
        sets["text"] = _text(text)
    try:
        async with conn.transaction():
            await conn.execute(_LOCK)
            now = await get_profile(conn, profile_id)
            if now is None:
                raise LibraryError("not_found", "That profile no longer exists.")
            will_be_on = now.enabled if enabled is None else enabled
            if now.is_default and (enabled is False or is_default is False):
                raise LibraryError("default_required", "Make another profile the default first.")
            if is_default and not will_be_on:
                raise LibraryError("invalid", "Turn the profile on before making it the default.")
            if enabled is not None:
                sets["enabled"] = enabled
            if is_default and not now.is_default:
                await conn.execute("UPDATE assistant_profiles SET is_default = FALSE WHERE is_default")
                sets["is_default"] = True
            if sets:
                cols = ", ".join(f"{c} = %s" for c in sets)
                await conn.execute(
                    f"UPDATE assistant_profiles SET {cols}, updated_by = %s, updated_at = now() WHERE id = %s",
                    (*sets.values(), updated_by, now.id))
            row = await get_profile(conn, now.id)
    except errors.UniqueViolation:
        raise LibraryError("name_taken", "Another profile has that name.") from None
    logger.info("assistant profile %s changed by %s (%s; %d chars)", row.id, updated_by or "-",
                ", ".join(sorted(sets)) or "nothing", len(row.text))
    return row


async def delete_profile(conn, profile_id: str) -> int:
    """Delete a profile; its chats keep its name and use the default from their next turn. Returns how many chats
    used it. The default can be deleted only when it is the last profile."""
    async with conn.transaction():
        await conn.execute(_LOCK)
        now = await get_profile(conn, profile_id)
        if now is None:
            raise LibraryError("not_found", "That profile no longer exists.")
        if now.is_default:
            cur = await conn.execute("SELECT count(*) FROM assistant_profiles")
            if (await cur.fetchone())[0] > 1:
                raise LibraryError("default_required", "Make another profile the default first.")
        used = await chats_using(conn, now.id)
        await conn.execute("DELETE FROM assistant_profiles WHERE id = %s", (now.id,))
    logger.info("assistant profile %s deleted (%d chats used it)", now.id, used)
    return used


async def reorder(conn, ids: list[str]) -> None:
    """Put the named profiles first, in this order; any left out keep their order after them."""
    wanted = [pid for pid in (_uuid(i) for i in ids) if pid]
    async with conn.transaction():
        await conn.execute(_LOCK)
        rest = [p.id for p in await list_profiles(conn) if p.id not in wanted]
        for position, pid in enumerate([*dict.fromkeys(wanted), *rest]):
            await conn.execute("UPDATE assistant_profiles SET position = %s WHERE id = %s", (position, pid))


async def house_rules(conn) -> str:
    cur = await conn.execute("SELECT value FROM app_settings WHERE key = %s", (HOUSE_RULES_KEY,))
    row = await cur.fetchone()
    value = row[0] if row and isinstance(row[0], dict) else {}   # a row edited by hand into another shape: none
    return clean_profile(str(value.get("text") or ""))[:HOUSE_RULES_MAX_CHARS]


async def save_house_rules(conn, text: str, *, updated_by: str | None) -> None:
    """Save the house rules; empty text removes them."""
    text = clean_profile(text or "").strip()
    if len(text) > HOUSE_RULES_MAX_CHARS:
        raise LibraryError("invalid", f"House rules are at most {HOUSE_RULES_MAX_CHARS:,} characters.")
    if not text:
        await conn.execute("DELETE FROM app_settings WHERE key = %s", (HOUSE_RULES_KEY,))
    else:
        await conn.execute(
            "INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES (%s, %s, %s, now()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, "
            "updated_at = now()", (HOUSE_RULES_KEY, Jsonb({"text": text}), updated_by))
    logger.info("house rules %s by %s (%d chars)", "set" if text else "cleared", updated_by or "-", len(text))
