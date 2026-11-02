"""The admin console's one row pattern (admin console v2 spec §3): every console setting resolves the same way.

A *section* is one `app_settings` row (key = the section's key, value = `{setting name: value}`), so a section
is a screen's worth of settings that load and save together. Each setting in it resolves, first match wins:
1. the console's value (a key in the row), accepted only if the setting's `parse` takes it;
2. the setting's env var, the deployer's starting value (an invalid one is a warning, once, and is ignored);
3. the built-in default.
Steps 2 and 3 together are the setting's *fallback*: what applies without the console's value. A setting with a
`narrow` function keeps the console's value inside its fallback (an allow-list is intersected with the env's, a
limit is the lower of the two): the console can only narrow what the environment allows.

The row is read on every request (one primary-key read, no cache), so a change applies at once on every worker.
Saving one setting never rewrites the others: it merges with jsonb operators, so a second admin's edit, or a key a
newer version wrote, survives. Each changed setting writes one audit line: who, the value that applied before,
the value saved (None for a reset). The values are the admin's settings, never secrets.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from psycopg.types.json import Jsonb

from ..audit import audit

logger = logging.getLogger(__name__)

_warned: set[tuple[str, str]] = set()   # invalid (env var, value) pairs already reported: once each, not per request


class SettingError(ValueError):
    """A refused change. `name` is the setting that was refused; `str(e)` is a message written for an admin."""

    def __init__(self, name: str, message: str):
        super().__init__(message)
        self.name = name


@dataclass(frozen=True)
class Setting:
    name: str
    env: str | None
    default: Any
    parse: Callable[[Any, Any], Any]       # (console value, fallback) -> the value to store, or ValueError(message)
    parse_env: Callable[[str], Any]        # env text -> value, or ValueError
    narrow: Callable[[Any, Any], Any] | None = None   # (console value, fallback) -> what applies; None: as is


@dataclass(frozen=True)
class Section:
    key: str                               # the app_settings.key
    settings: tuple[Setting, ...]

    def get(self, name: str) -> Setting:
        for setting in self.settings:
            if setting.name == name:
                return setting
        raise KeyError(name)


@dataclass(frozen=True)
class Value:
    value: Any
    source: str            # "admin" | "env" | "default": which of the three above decided it
    fallback: Any          # what applies without the console's value, and the bound a narrowing setting keeps to
    fallback_source: str   # "env" | "default"


def _fallback(setting: Setting, env: Mapping[str, str]) -> tuple[Any, str]:
    raw = (env.get(setting.env) or "").strip() if setting.env else ""
    if raw:
        try:
            return setting.parse_env(raw), "env"
        except ValueError:
            if (setting.env, raw) not in _warned:
                _warned.add((setting.env, raw))
                logger.warning("%s=%r isn't a usable value: ignored, the default %r applies",
                               setting.env, raw, setting.default)
    return setting.default, "default"


async def _stored(conn, section: Section) -> dict[str, Any]:
    cur = await conn.execute("SELECT value FROM app_settings WHERE key = %s", (section.key,))
    row = await cur.fetchone()
    return row[0] if row and isinstance(row[0], dict) else {}


def _resolve(section: Section, stored: Mapping[str, Any], env: Mapping[str, str]) -> dict[str, Value]:
    out: dict[str, Value] = {}
    for setting in section.settings:
        fallback, fallback_source = _fallback(setting, env)
        if setting.name in stored:
            try:
                value = setting.parse(stored[setting.name], fallback)
                if setting.narrow:
                    value = setting.narrow(value, fallback)
                out[setting.name] = Value(value, "admin", fallback, fallback_source)
                continue
            except ValueError:
                logger.warning("app_settings row %s holds an unusable %s value: ignored", section.key, setting.name)
        out[setting.name] = Value(fallback, fallback_source, fallback, fallback_source)
    return out


async def load(conn, section: Section, env: Mapping[str, str] = os.environ) -> dict[str, Value]:
    return _resolve(section, await _stored(conn, section), env)


async def save(conn, section: Section, changes: Mapping[str, Any], *, updated_by: str,
               env: Mapping[str, str] = os.environ) -> dict[str, Value]:
    """Apply `changes` ({name: new value}; None removes the name, so the server setting applies again) and
    return what applies now. Every change is validated before anything is written: one refusal (SettingError,
    naming the setting) writes nothing."""
    sets: dict[str, Any] = {}
    resets: list[str] = []
    for name, new in changes.items():
        try:
            setting = section.get(name)
        except KeyError:
            raise SettingError(name, f"{name} isn't a setting here.") from None
        if new is None:
            resets.append(name)
            continue
        fallback, _ = _fallback(setting, env)
        try:
            sets[name] = setting.parse(new, fallback)
        except ValueError as e:
            raise SettingError(name, str(e)) from None

    stored = await _stored(conn, section)
    before = _resolve(section, stored, env)
    if sets:
        await conn.execute(
            "INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES (%s, %s, %s, now()) "
            "ON CONFLICT (key) DO UPDATE SET value = app_settings.value || EXCLUDED.value, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()", (section.key, Jsonb(sets), updated_by))
    for name in resets:
        await conn.execute(
            "UPDATE app_settings SET value = value - %s, updated_by = %s, updated_at = now() WHERE key = %s",
            (name, updated_by, section.key))
    if resets:
        await conn.execute("DELETE FROM app_settings WHERE key = %s AND value = '{}'::jsonb", (section.key,))

    for name, new in {**sets, **dict.fromkeys(resets)}.items():
        if (name in stored and stored[name] == new) or (name not in stored and new is None):
            continue   # the row already said this: nothing changed, nothing to audit
        audit(f"settings.{section.key}.{name}", by=updated_by, old=before[name].value, new=new)
    return await load(conn, section, env)


def _plain(value: Any) -> Any:
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    return value


def view(section: Section, values: dict[str, Value]) -> dict[str, dict]:
    """The section as JSON for the console: per setting its value and where it came from, the env var's name,
    and the fallback (so the console can offer "use the server setting" and show the bound)."""
    return {s.name: {"value": _plain(values[s.name].value), "source": values[s.name].source, "env": s.env,
                     "fallback": _plain(values[s.name].fallback),
                     "fallback_source": values[s.name].fallback_source}
            for s in section.settings}
