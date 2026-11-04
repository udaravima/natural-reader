"""current_time: the time now where the person is, in their browser's timezone, else the deployment's, else UTC.

The rules carry today's date only, to the day (chat/context.py today_line): a clock in every message was read as part
of the question ("say that more simply" taken as being about the time, eval 2026-10-05), and a line that changes once
a day keeps the prompt cache for the day. A question that needs the time of day, or a time difference, asks for it
here instead. Nothing leaves the server, and the answer is a few dozen characters.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ...llm.types import ToolSpec

_SPEC = ToolSpec(
    name="current_time",
    # Names no other tool (test_no_tool_description_names_another_tool).
    description=(
        "The current time and date where the user is, with their timezone. Use it when the answer depends on the "
        "time of day or on how long until or since a time (\"what time is it\", \"is it too late to call\", \"how "
        "long until 5 pm\"). Today's date is already in your instructions: you don't need this for the date alone."),
    parameters={"type": "object", "properties": {}, "required": []},
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _zone(name: str | None) -> ZoneInfo | None:
    """As chat/context.py's (which imports this package, so it can't be imported here)."""
    try:
        return ZoneInfo(name) if name else None
    except Exception:  # noqa: BLE001 — ZoneInfo raises several types for bad names
        return None


def _where(ctx) -> tuple[Any, str]:
    for name in (getattr(ctx, "timezone", None), getattr(ctx, "default_timezone", None)):
        if zone := _zone(name):
            return zone, name
    return timezone.utc, "UTC"


def _offset(moment: datetime) -> str:
    raw = moment.strftime("%z")   # +0530
    return f"{raw[:3]}:{raw[3:]}" if len(raw) == 5 else "+00:00"


class _CurrentTime:
    name = "current_time"
    spec = _SPEC
    source = "app"            # from the server's own clock: neither a document nor the web
    reads_documents = False
    unmetered = True          # tiny, and asking twice in one answer is fine: the time moves (TurnTools)

    def available(self, ctx) -> bool:
        return True

    async def execute(self, args: dict[str, Any], ctx) -> dict[str, Any]:
        zone, label = _where(ctx)
        local = _now().astimezone(zone)
        return {"time": f"{local:%H:%M}", "date": f"{local:%A}, {local.day} {local:%B %Y}", "timezone": label,
                "utc_offset": _offset(local)}

    def summarize(self, args: dict[str, Any], result: dict[str, Any], ctx) -> dict[str, Any]:
        text = f"{result['time']}, {result['date'].replace(',', '')} ({result['timezone']})"
        return {"ok": True, "chunk_count": None, "query": None, "summary_text": text}


TOOL = _CurrentTime()
