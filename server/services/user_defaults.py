"""Deployment defaults for user preferences (Admin → Defaults).

Preferences live in each browser. A setting the user chose keeps their choice;
one they never chose follows these defaults: the admin's value, else the
built-in one below. Until 2.3.x the browser saved every default on first load
as if it were a choice, so a changed default never reached an existing user.

"Apply to everyone" bumps a setting's epoch. A browser that sees a higher
epoch than it last applied drops the user's own choice for that setting, so
the default reaches everyone (LibreChat's "hard" default) without the server
keeping per-user preferences.

Stored as one app_settings row: {"values": {name: value}, "epochs": {name: n}}.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)

KEY = "user_defaults"

_VOICE = re.compile(r"^[a-z]{2}_[a-z]{2,20}$")
THINK = ("off", "on", "low", "medium", "high")
TTS_MODES = ("streaming", "after-complete")


def _bool(v: Any) -> bool:
    return isinstance(v, bool)


def _speed(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0.5 <= v <= 2.0


# name: (built-in default, valid?, choices shown in the console or None)
SETTINGS: dict[str, tuple[Any, Callable[[Any], bool], tuple | None]] = {
    # Read each reply aloud when it arrives (off: one reply read aloud by
    # surprise is worse than a missing one).
    "chatAutoTts": (False, _bool, None),
    # When auto-read starts: while the reply streams, or once it is complete.
    "chatTtsMode": ("after-complete", lambda v: v in TTS_MODES, TTS_MODES),
    # The model's thinking, for a model the user hasn't set it for.
    "think": ("off", lambda v: v in THINK, THINK),
    "selectedVoice": ("af_heart", lambda v: isinstance(v, str) and bool(_VOICE.match(v)), None),
    "playbackSpeed": (1.0, _speed, None),
}


class DefaultsError(ValueError):
    pass


def built_in() -> dict[str, Any]:
    return {name: spec[0] for name, spec in SETTINGS.items()}


def validate(values: dict[str, Any]) -> dict[str, Any]:
    unknown = [k for k in values if k not in SETTINGS]
    if unknown:
        raise DefaultsError(f"Unknown settings: {', '.join(sorted(unknown))}")
    bad = [k for k, v in values.items() if not SETTINGS[k][1](v)]
    if bad:
        raise DefaultsError(f"Invalid values for: {', '.join(sorted(bad))}")
    return {k: (float(v) if k == "playbackSpeed" else v) for k, v in values.items()}


async def _stored(conn) -> dict[str, Any]:
    cur = await conn.execute("SELECT value FROM app_settings WHERE key = %s", (KEY,))
    row = await cur.fetchone()
    data = (row[0] if row else None) or {}
    values = {k: v for k, v in (data.get("values") or {}).items()
              if k in SETTINGS and SETTINGS[k][1](v)}
    epochs = {k: int(v) for k, v in (data.get("epochs") or {}).items()
              if k in SETTINGS and isinstance(v, int) and v > 0}
    return {"values": values, "epochs": epochs}


async def _write(conn, stored: dict[str, Any], updated_by: str | None) -> None:
    await conn.execute(
        "INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES (%s, %s, %s, now()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, "
        "updated_at = now()", (KEY, Jsonb(stored), updated_by))


async def effective(conn) -> dict[str, Any]:
    """What every browser asks for: the values and the epochs."""
    stored = await _stored(conn)
    return {"values": {**built_in(), **stored["values"]}, "epochs": stored["epochs"]}


async def admin_view(conn) -> dict[str, Any]:
    stored = await _stored(conn)
    return {"values": {**built_in(), **stored["values"]}, "builtIn": built_in(),
            "overridden": sorted(stored["values"]), "epochs": stored["epochs"],
            "choices": {k: list(spec[2]) for k, spec in SETTINGS.items() if spec[2]}}


async def save(conn, values: dict[str, Any], *, updated_by: str | None) -> None:
    """Set the given settings; a value equal to the built-in removes the
    admin's override, so the built-in applies again."""
    values = validate(values)
    stored = await _stored(conn)
    merged = {**stored["values"], **values}
    builtins = built_in()
    stored["values"] = {k: v for k, v in merged.items() if v != builtins[k]}
    await _write(conn, stored, updated_by)
    logger.info("user defaults set by %s: %s", updated_by or "-", sorted(values))


async def apply_to_everyone(conn, name: str, *, updated_by: str | None) -> int:
    if name not in SETTINGS:
        raise KeyError(name)
    stored = await _stored(conn)
    stored["epochs"][name] = stored["epochs"].get(name, 0) + 1
    await _write(conn, stored, updated_by)
    logger.info("user default %s applied to everyone by %s (epoch %d)",
                name, updated_by or "-", stored["epochs"][name])
    return stored["epochs"][name]
