"""Admin console v2 Task 1: the one row pattern every console setting uses.

A section is one `app_settings` row. Each setting in it resolves console value, then env var, then default; a
narrowing setting keeps the console value inside what the environment (or default) allows. The section here is a
toy, so the store is tested apart from any real setting."""
from __future__ import annotations

import logging

import pytest

from server.auth.users import resolve_or_provision_user
from server.services import console_settings
from server.services.console_settings import (Section, Setting, SettingError, Value, load, save, view)


def _parse_limit(value, fallback):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("limit must be a whole number, 0 or more.")
    return value


def _parse_env_limit(text):
    n = int(text)
    if n < 0:
        raise ValueError("negative")
    return n


def _narrow_limit(value, fallback):
    """The lower of the two, 0 meaning no limit."""
    if value == 0:
        return fallback
    if fallback == 0:
        return value
    return min(value, fallback)


def _parse_zone(value, fallback):
    if not isinstance(value, str) or not value or value == "X":
        raise ValueError("zone must be a known zone name.")
    return value


TOY = Section("toy", (
    Setting("limit", "TOY_LIMIT", 10, _parse_limit, _parse_env_limit, _narrow_limit),
    Setting("zone", "TOY_ZONE", "UTC", _parse_zone, str),
))


@pytest.fixture(autouse=True)
def _fresh_warnings():
    console_settings._warned.clear()      # an invalid env value is warned about once per process


async def _user_id(db_conn, sub="s1", email="a@x.io"):
    u = await resolve_or_provision_user(db_conn, iss="i", sub=sub, email=email)
    return str(u["id"])


async def _row(db_conn):
    cur = await db_conn.execute("SELECT value FROM app_settings WHERE key = 'toy'")
    row = await cur.fetchone()
    return row[0] if row else None


async def test_admin_value_beats_env_beats_default(db_conn):
    by = await _user_id(db_conn)
    assert (await load(db_conn, TOY, {}))["zone"] == Value("UTC", "default", "UTC", "default")
    assert (await load(db_conn, TOY, {"TOY_ZONE": "Asia/Colombo"}))["zone"].source == "env"
    await save(db_conn, TOY, {"zone": "Europe/Berlin"}, updated_by=by)
    got = (await load(db_conn, TOY, {"TOY_ZONE": "Asia/Colombo"}))["zone"]
    assert got == Value("Europe/Berlin", "admin", "Asia/Colombo", "env")


async def test_narrow_keeps_the_console_value_inside_the_env_bound(db_conn):
    by = await _user_id(db_conn)
    await save(db_conn, TOY, {"limit": 100}, updated_by=by)
    assert (await load(db_conn, TOY, {"TOY_LIMIT": "50"}))["limit"].value == 50
    assert (await load(db_conn, TOY, {}))["limit"].value == 10   # the default is the bound too


async def test_invalid_env_value_is_warned_once_and_ignored(db_conn, caplog):
    with caplog.at_level(logging.WARNING):
        first = (await load(db_conn, TOY, {"TOY_LIMIT": "abc"}))["limit"]
        second = (await load(db_conn, TOY, {"TOY_LIMIT": "abc"}))["limit"]
    assert first == second == Value(10, "default", 10, "default")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "TOY_LIMIT" in r.getMessage()]
    assert len(warnings) == 1


async def test_an_unusable_stored_value_is_ignored(db_conn, caplog):
    await db_conn.execute("INSERT INTO app_settings (key, value) VALUES ('toy', '{\"limit\": \"lots\"}')")
    with caplog.at_level(logging.WARNING):
        got = (await load(db_conn, TOY, {"TOY_LIMIT": "7"}))["limit"]
    assert got == Value(7, "env", 7, "env")
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "toy" in warnings[0] and "limit" in warnings[0]


async def test_save_keeps_stored_settings_it_never_loaded(db_conn):   # Review Focus 2
    by = await _user_id(db_conn)
    await db_conn.execute("INSERT INTO app_settings (key, value) VALUES ('toy', '{\"future\": 1, \"zone\": \"UTC\"}')")
    await save(db_conn, TOY, {"limit": 5}, updated_by=by)
    assert await _row(db_conn) == {"future": 1, "zone": "UTC", "limit": 5}


async def test_reset_removes_only_that_setting_and_the_last_reset_deletes_the_row(db_conn):
    by = await _user_id(db_conn)
    await save(db_conn, TOY, {"limit": 5, "zone": "Europe/Berlin"}, updated_by=by)
    got = await save(db_conn, TOY, {"limit": None}, updated_by=by)
    assert await _row(db_conn) == {"zone": "Europe/Berlin"}
    assert got["limit"].source == "default" and got["zone"].source == "admin"
    await save(db_conn, TOY, {"zone": None}, updated_by=by)
    assert await _row(db_conn) is None


async def test_save_refuses_an_unknown_name_or_an_invalid_value_and_writes_nothing(db_conn):
    by = await _user_id(db_conn)
    with pytest.raises(SettingError) as unknown:
        await save(db_conn, TOY, {"limit": 5, "nope": 1}, updated_by=by)
    assert unknown.value.name == "nope"
    assert await _row(db_conn) is None
    with pytest.raises(SettingError) as invalid:
        await save(db_conn, TOY, {"limit": -1, "zone": "X"}, updated_by=by)
    assert invalid.value.name == "limit" and "whole number" in str(invalid.value)
    assert await _row(db_conn) is None


async def test_save_writes_one_audit_line_per_changed_setting(db_conn, caplog):
    by = await _user_id(db_conn)
    with caplog.at_level(logging.INFO, logger="server.audit"):
        await save(db_conn, TOY, {"limit": 5}, updated_by=by)
        await save(db_conn, TOY, {"limit": 5, "zone": "Europe/Berlin"}, updated_by=by)   # limit unchanged
        await save(db_conn, TOY, {"limit": None}, updated_by=by)
        await save(db_conn, TOY, {"limit": None}, updated_by=by)                        # nothing left to reset
    lines = [r.getMessage() for r in caplog.records if r.name == "server.audit"]
    assert lines == [f"settings.toy.limit by={by} old=10 new=5",
                     f"settings.toy.zone by={by} old=UTC new=Europe/Berlin",
                     f"settings.toy.limit by={by} old=5 new=None"]


async def test_view_shape(db_conn):
    values = await load(db_conn, TOY, {})
    assert view(TOY, values)["zone"] == {"value": "UTC", "source": "default", "env": "TOY_ZONE",
                                         "fallback": "UTC", "fallback_source": "default"}


async def test_view_turns_tuples_into_lists():
    section = Section("t", (Setting("tags", None, ("a", "b"), lambda v, f: v, lambda t: t),))
    shown = view(section, {"tags": Value(("a",), "admin", ("a", "b"), "default")})["tags"]
    assert shown["value"] == ["a"] and shown["fallback"] == ["a", "b"] and shown["env"] is None
