"""Admin console v2, Task 2: the deployment timezone: which names count, and where it comes from."""
from __future__ import annotations

import logging

import pytest

from server.auth.users import SEED_ADMIN_ID
from server.services import console_settings, deployment_settings as ds


@pytest.fixture(autouse=True)
def _fresh_warnings():
    console_settings._warned.clear()   # an invalid env value is warned about once per process


@pytest.mark.parametrize("raw,want", [("Asia/Colombo", "Asia/Colombo"), (" Europe/Berlin ", "Europe/Berlin"),
                                      (":Asia/Colombo", "Asia/Colombo"), ("UTC", "UTC")])
def test_zone_name_accepts_a_known_zone(raw, want):
    assert ds.zone_name(raw) == want


@pytest.mark.parametrize("raw", ["Mars/Base", "../etc/passwd", "", 5])
def test_zone_name_refuses_the_rest(raw):
    with pytest.raises(ValueError) as e:
        ds.zone_name(raw)
    assert str(e.value) == f"“{raw}” isn't a timezone this server knows. Use a name such as Europe/Berlin."


async def test_the_zone_is_utc_with_nothing_set(db_conn):
    assert await ds.deployment_zone_name(db_conn, {}) == "UTC"
    assert str(await ds.deployment_zone(db_conn, {})) == "UTC"


async def test_the_zone_follows_TZ(db_conn):
    assert await ds.deployment_zone_name(db_conn, {"TZ": "Asia/Colombo"}) == "Asia/Colombo"


async def test_the_console_wins_over_TZ(db_conn):
    await console_settings.save(db_conn, ds.SECTION, {"timezone": "Europe/Berlin"},
                                updated_by=SEED_ADMIN_ID, env={})
    assert await ds.deployment_zone_name(db_conn, {"TZ": "Asia/Colombo"}) == "Europe/Berlin"


async def test_a_posix_TZ_string_is_ignored_with_one_warning(db_conn, caplog):
    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            assert await ds.deployment_zone_name(db_conn, {"TZ": "EST5EDT,M3.2.0"}) == "UTC"
    assert len([r for r in caplog.records if "TZ" in r.getMessage()]) == 1
