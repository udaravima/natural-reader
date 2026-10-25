"""The deployment's own settings (admin console v2 spec §4.5), so far its timezone.

The timezone decides where a day starts and ends for the daily token budget, the usage days the console shows,
and the "Today is …" line a chat falls back to when the browser sends no usable zone. It resolves like every
console setting (services/console_settings.py): the console's value, then the TZ environment variable, then UTC.
Zone names are checked against the server's zone database, so a stored name can always be turned into a ZoneInfo.
"""
from __future__ import annotations

import functools
import os
import zoneinfo
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from . import console_settings
from .console_settings import Section, Setting

DEFAULT_ZONE = "UTC"


@functools.cache
def _known() -> frozenset[str]:
    """The names the zone database knows. It can't change while the process runs, so it's listed once."""
    return frozenset(zoneinfo.available_timezones() | {DEFAULT_ZONE})


def zone_name(raw: Any) -> str:
    """A zone name as an admin or TZ wrote it, checked. Spaces and one leading ':' (glibc's TZ=:Area/City) are
    dropped. Anything the database doesn't list is refused, which also keeps paths like ../etc/passwd out of
    ZoneInfo."""
    name = raw.strip() if isinstance(raw, str) else ""
    if name.startswith(":"):
        name = name[1:].strip()
    if name in _known():
        return name
    raise ValueError(f"“{raw}” isn't a timezone this server knows. Use a name such as Europe/Berlin.")


SECTION = Section("deployment", (
    Setting("timezone", env="TZ", default=DEFAULT_ZONE, parse=lambda value, _fallback: zone_name(value),
            parse_env=zone_name),
))


async def deployment_zone_name(conn, env: Mapping[str, str] = os.environ) -> str:
    return (await console_settings.load(conn, SECTION, env))["timezone"].value


async def deployment_zone(conn, env: Mapping[str, str] = os.environ) -> ZoneInfo:
    return ZoneInfo(await deployment_zone_name(conn, env))
