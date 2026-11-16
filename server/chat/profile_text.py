"""What leads a chat's system message: the admin's house rules, then the profile this chat uses
(services/profile_library.py), with their placeholders filled in.

Which profile, first match wins (profile library spec, "What a turn sends"):
1. the one the turn asks for, if it exists and is on;
2. the chat's own, if it still exists (on or off);
3. the default;
4. while the library is empty, the file at CHAT_ASSISTANT_PROFILE_FILE; else none.

The Librarian (the built-in profile, character profiles spec) has no text: a turn that uses it gets the app's own
rules with its name in the first line. While it is the only profile, the file's text is its text, as the file was
the profile while the library was empty.

Placeholders are `{{date}}`, `{{app}}` and `{{profile}}`, and only these: each is a value no person chatting can
write, because the text they land in is trusted and unfenced. Anything else in double braces is left as written.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ..services import assistant_profile, profile_library

PLACEHOLDERS = ("date", "app", "profile")
_ANY = re.compile(r"\{\{\s*([^{}\n]{0,40}?)\s*\}\}")


@dataclass(frozen=True)
class Filled:
    text: str
    used: frozenset[str]


@dataclass(frozen=True)
class Resolved:
    id: str | None                 # None: no profile, or the file's
    name: str
    text: str                      # the profile alone
    house_rules: str
    source: Literal["library", "file", "none"]
    builtin: bool = False          # The Librarian with no file text: the app's first line carries its name

    @property
    def combined(self) -> str:
        """House rules, a blank line, the profile: what leads the system message, before filling."""
        return "\n\n".join(t for t in (self.house_rules, self.text) if t)


def fill(text: str, *, date: str, app: str, profile: str) -> Filled:
    """Fill the placeholders in one pass, so a value is never read for placeholders itself."""
    values = {"date": date, "app": app, "profile": profile or "the assistant"}
    used: set[str] = set()

    def swap(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            return m.group(0)
        used.add(key)
        return values[key]
    return Filled(_ANY.sub(swap, text), frozenset(used))


def unknown(text: str) -> list[str]:
    """Double-braced words that aren't placeholders, as written, each once: the editor warns about them."""
    return list(dict.fromkeys(m.group(0) for m in _ANY.finditer(text) if m.group(1) not in PLACEHOLDERS))


async def _chat_profile_id(conn, session_id: str | None) -> str | None:
    if not session_id:
        return None
    cur = await conn.execute("SELECT profile_id::text FROM chat_sessions WHERE id = %s", (session_id,))
    row = await cur.fetchone()
    return row[0] if row else None


async def resolve(conn, *, session_id: str | None, asked_id: str | None) -> Resolved:
    house = await profile_library.house_rules(conn)
    asked = await profile_library.get_profile(conn, asked_id) if asked_id else None
    row = asked if asked is not None and asked.enabled else None
    if row is None:
        row = await profile_library.get_profile(conn, await _chat_profile_id(conn, session_id))
    if row is None:
        row = await profile_library.default_profile(conn)
    if row is not None and row.builtin:
        text = "" if await _has_own_profiles(conn) else assistant_profile._from_file()
        return Resolved(row.id, row.name, text, house, "file" if text else "library", builtin=not text)
    if row is not None:
        return Resolved(row.id, row.name, row.text, house, "library")
    cur = await conn.execute("SELECT EXISTS (SELECT 1 FROM assistant_profiles)")
    text = "" if (await cur.fetchone())[0] else assistant_profile._from_file()
    return Resolved(None, "", text, house, "file" if text else "none")


async def _has_own_profiles(conn) -> bool:
    """A profile an admin wrote (not The Librarian) exists."""
    cur = await conn.execute("SELECT EXISTS (SELECT 1 FROM assistant_profiles WHERE NOT builtin)")
    return (await cur.fetchone())[0]


async def largest(conn) -> str:
    """The longest house rules + profile any chat can be sent: what a context-size check must allow for."""
    house = await profile_library.house_rules(conn)
    own = [r.text for r in await profile_library.list_profiles(conn) if not r.builtin]
    text = max(own, key=len) if own else assistant_profile._from_file()
    return "\n\n".join(t for t in (house, text) if t)
