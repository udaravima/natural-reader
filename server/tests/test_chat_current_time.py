"""current_time: the time now where the person is (the owner's ask, 2026-11-04). The rules carry today's date only,
to the day (context.today_line); a question that needs the time of day calls this tool."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from server.chat import tools as chat_tools
from server.chat.tools import ToolContext, available_tools, run_tool
from server.chat.tools import current_time as ct_tool
from server.llm.types import ToolCall

NOW = datetime(2026, 11, 4, 16, 10, 30, tzinfo=timezone.utc)   # 21:40 in Colombo


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    monkeypatch.setattr(ct_tool, "_now", lambda: NOW)


async def _call(ctx):
    return await run_tool(ToolCall("c1", "current_time", {}), ctx, available_tools(ctx))


async def test_the_time_is_the_persons_in_their_browsers_timezone():
    run = await _call(ToolContext("u", None, timezone="Asia/Colombo", default_timezone="Europe/London"))
    assert run.result == {"time": "21:40", "date": "Wednesday, 4 November 2026", "timezone": "Asia/Colombo",
                          "utc_offset": "+05:30"}
    assert run.summary["result_summary"]["summary_text"] == "21:40, Wednesday 4 November 2026 (Asia/Colombo)"


@pytest.mark.parametrize("browser, deployment, label, time", [
    (None, "Europe/London", "Europe/London", "16:10"),        # no browser zone: the deployment's
    ("Not/AZone", "Asia/Colombo", "Asia/Colombo", "21:40"),   # a zone that doesn't exist is skipped
    (None, "Nope/Nope", "UTC", "16:10"),                      # nothing usable: UTC
])
async def test_without_the_browsers_timezone_the_deployments_then_utc(browser, deployment, label, time):
    run = await _call(ToolContext("u", None, timezone=browser, default_timezone=deployment))
    assert (run.result["timezone"], run.result["time"]) == (label, time)


def test_it_is_always_offered_sends_nothing_out_and_takes_no_arguments():
    tool = ct_tool.TOOL
    assert tool in available_tools(ToolContext("u", None))
    assert (tool.source, tool.reads_documents, tool.unmetered) == ("app", False, True)
    assert tool.spec.parameters == {"type": "object", "properties": {}, "required": []}
    assert "time" in tool.spec.description and "date" in tool.spec.description


def test_a_tool_context_without_a_timezone_falls_back_to_utc():
    ctx = ToolContext("u", None)
    assert (ctx.timezone, ctx.default_timezone) == (None, "UTC")


def test_it_is_registered_last():
    assert [t.name for t in chat_tools.REGISTRY][-1] == "current_time"
