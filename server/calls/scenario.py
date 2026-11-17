"""A scenario pack (spec 2026-11-17-call-simulation): a folder with

- `scenario.toml`: the scenario's id, name and fixed "today", the agent (name, greeting, filler line, backstory,
  rules; optionally a filler line per tool, "still checking" lines and keypad instructions) and the scripted calls,
  each with what the caller says, turn by turn, and what the call must achieve. A call can also say what the caller
  says when numbers are keyed instead of spoken (`caller_keypad`) and the digits they key (`[calls.keys]`);
- `data.json`: the business's data, copied fresh into every call;
- `tools.py`: the tools, plain functions marked with server.calls.simtools.tool.

`load_scenario("hotel")` reads server/calls/scenarios/hotel; a path to any folder works too, so a deployer can keep
their own packs outside the repo. Everything is checked on load, and a ScenarioError names what to fix."""
from __future__ import annotations

import importlib.util
import json
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .simtools import SimTool, ToolSet, collect

SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"


class ScenarioError(Exception):
    """The pack can't be used; the message names the file and what's wrong."""


@dataclass(frozen=True)
class Agent:
    name: str
    greeting: str
    filler: str
    backstory: str
    rules: str
    fillers: dict[str, tuple[str, ...]] = field(default_factory=dict)   # tool -> recorded lines; "" = any tool
    still_checking: tuple[str, ...] = ()                                  # when a lookup takes long
    keypad: dict[str, str] = field(default_factory=dict)                  # what is keyed -> the instruction


@dataclass(frozen=True)
class Expect:
    tools: tuple[str, ...] = ()               # each must run at least once without an error
    never_tools: tuple[str, ...] = ()         # none of these may run
    says: tuple[tuple[str, ...], ...] = ()    # each group: one of its spellings is in the agent's words
    booking: dict[str, Any] | None = None     # a booking made in the call has these fields
    cancelled: tuple[str, ...] = ()           # these references are cancelled at the end
    transferred: bool | None = None           # the call was (True) or wasn't (False) handed to a person


@dataclass(frozen=True)
class CallScript:
    id: str
    title: str
    caller: tuple[str, ...]
    language: str
    expect: Expect
    caller_keypad: tuple[str, ...] | None = None   # what the caller says when numbers are keyed instead
    keys: dict[str, str] = field(default_factory=dict)   # what -> the digits the caller keys

    def lines(self, keypad: bool) -> tuple[str, ...]:
        return self.caller_keypad if keypad and self.caller_keypad else self.caller


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    today: str
    language: str
    agent: Agent
    calls: tuple[CallScript, ...]
    data: dict[str, Any] = field(repr=False)
    tools: tuple[SimTool, ...] = field(repr=False)
    directory: Path = field(repr=False)

    def toolset(self, extra: tuple[SimTool, ...] = ()) -> ToolSet:
        return ToolSet([*self.tools, *extra], self.data, self.today)

    def call(self, call_id: str) -> CallScript:
        for c in self.calls:
            if c.id == call_id:
                return c
        raise ScenarioError(f"{self.id} has no call {call_id!r}: {', '.join(c.id for c in self.calls)}")


def _text(table: dict, key: str, where: str, *, required: bool = True) -> str:
    value = table.get(key, "")
    if not isinstance(value, str) or (required and not value.strip()):
        raise ScenarioError(f"{where}: `{key}` must be text{' and not empty' if required else ''}")
    return value.strip()


def _names(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ScenarioError(f"{where} must be a list of names")
    return tuple(value)


def _says(value: Any, where: str) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list):
        raise ScenarioError(f"{where} must be a list")
    groups = []
    for item in value:
        group = [item] if isinstance(item, str) else item
        if not isinstance(group, list) or not group or not all(isinstance(s, str) and s.strip() for s in group):
            raise ScenarioError(f"{where}: each item is a phrase or a list of phrases")
        groups.append(tuple(group))
    return tuple(groups)


def _expect(table: Any, where: str) -> Expect:
    if table is None:
        return Expect()
    if not isinstance(table, dict):
        raise ScenarioError(f"{where} must be a table")
    booking = table.get("booking")
    if booking is not None and not isinstance(booking, dict):
        raise ScenarioError(f"{where}.booking must be a table of fields")
    transferred = table.get("transferred")
    if transferred is not None and not isinstance(transferred, bool):
        raise ScenarioError(f"{where}.transferred must be true or false")
    return Expect(tools=_names(table.get("tools", []), f"{where}.tools"),
                  never_tools=_names(table.get("never_tools", []), f"{where}.never_tools"),
                  says=_says(table.get("says", []), f"{where}.says"),
                  booking=booking,
                  cancelled=_names(table.get("cancelled", []), f"{where}.cancelled"),
                  transferred=transferred)


def _lines(value: Any, where: str) -> tuple[str, ...]:
    lines = [value] if isinstance(value, str) else value
    if not isinstance(lines, list) or not lines or not all(isinstance(t, str) and t.strip() for t in lines):
        raise ScenarioError(f"{where} must be a line or a list of lines")
    return tuple(t.strip() for t in lines)


def _fillers(agent_raw: dict, default: str, tool_names: set[str], where: str) -> dict[str, tuple[str, ...]]:
    table = agent_raw.get("fillers") or {}
    if not isinstance(table, dict):
        raise ScenarioError(f"{where} [agent.fillers] must be a table of tool = line(s)")
    unknown = sorted(set(table) - tool_names)
    if unknown:
        raise ScenarioError(f"{where} [agent.fillers] names {', '.join(unknown)}, which tools.py doesn't define")
    fillers = {name: _lines(lines, f"{where} [agent.fillers] {name}") for name, lines in table.items()}
    if default:
        fillers[""] = (default,)
    return fillers


def _keys(table: Any, keypad: dict[str, str], where: str) -> dict[str, str]:
    if table is None:
        return {}
    if not isinstance(table, dict):
        raise ScenarioError(f"{where} must be a table of what = digits")
    for what, digits in table.items():
        if not isinstance(digits, str) or not digits.isdigit():
            raise ScenarioError(f"{where}.{what} must be digits only, such as \"0771234567\"")
        if what not in keypad:
            raise ScenarioError(f"{where}.{what}: the agent has no [agent.keypad] line asking for {what!r}")
    return dict(table)


def _load_tools(path: Path, scenario_id: str) -> tuple[SimTool, ...]:
    spec = importlib.util.spec_from_file_location(f"_scenario_{scenario_id}_tools", path)
    if spec is None or spec.loader is None:
        raise ScenarioError(f"{path} can't be loaded")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        raise ScenarioError(f"{path} failed to load: {type(e).__name__}: {e}") from None
    tools = tuple(collect(module))
    if not tools:
        raise ScenarioError(f"{path} defines no tools (mark functions with @tool)")
    return tools


def load_scenario(name_or_path: str) -> Scenario:
    folder = Path(name_or_path)
    if not folder.is_dir():
        folder = SCENARIOS_DIR / name_or_path
    toml_path, data_path, tools_path = folder / "scenario.toml", folder / "data.json", folder / "tools.py"
    if not toml_path.is_file():
        known = ", ".join(sorted(p.name for p in SCENARIOS_DIR.iterdir() if (p / "scenario.toml").is_file()))
        raise ScenarioError(f"No scenario.toml in {folder} (packs in the repo: {known})")
    try:
        raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ScenarioError(f"{toml_path}: {e}") from None
    try:
        data = json.loads(data_path.read_text(encoding="utf-8")) if data_path.is_file() else {}
    except ValueError as e:
        raise ScenarioError(f"{data_path}: {e}") from None
    if not tools_path.is_file():
        raise ScenarioError(f"No tools.py in {folder}")

    where = str(toml_path)
    scenario_id = _text(raw, "id", where)
    today = _text(raw, "today", where)
    try:
        date.fromisoformat(today)
    except ValueError:
        raise ScenarioError(f"{where}: `today` must be a date as YYYY-MM-DD") from None
    agent_raw = raw.get("agent")
    if not isinstance(agent_raw, dict):
        raise ScenarioError(f"{where}: an [agent] table is needed")
    tools = _load_tools(tools_path, scenario_id)
    tool_names = {t.name for t in tools}
    filler = _text(agent_raw, "filler", f"{where} [agent]", required=False)
    keypad = agent_raw.get("keypad") or {}
    if not isinstance(keypad, dict) or not all(isinstance(v, str) and v.strip() for v in keypad.values()):
        raise ScenarioError(f"{where} [agent.keypad] must be a table of what = the line asking for it")
    agent = Agent(name=_text(agent_raw, "name", f"{where} [agent]"),
                  greeting=_text(agent_raw, "greeting", f"{where} [agent]"),
                  filler=filler,
                  backstory=_text(agent_raw, "backstory", f"{where} [agent]", required=False),
                  rules=_text(agent_raw, "rules", f"{where} [agent]"),
                  fillers=_fillers(agent_raw, filler, tool_names, where),
                  still_checking=(_lines(agent_raw["still_checking"], f"{where} [agent] still_checking")
                                  if "still_checking" in agent_raw else ()),
                  keypad={k: v.strip() for k, v in keypad.items()})
    language = _text(raw, "language", where, required=False) or "en"

    calls: list[CallScript] = []
    for i, c in enumerate(raw.get("calls") or ()):
        cw = f"{where} call {i + 1}"
        call_id = _text(c, "id", cw)
        caller = c.get("caller")
        if not isinstance(caller, list) or not caller or not all(isinstance(t, str) and t.strip() for t in caller):
            raise ScenarioError(f"{cw} ({call_id}): `caller` must be a list of what the caller says, turn by turn")
        expect = _expect(c.get("expect"), f"{cw} ({call_id}) expect")
        unknown = [t for t in (*expect.tools, *expect.never_tools) if t not in tool_names]
        if unknown:
            raise ScenarioError(f"{cw} ({call_id}) expects {', '.join(unknown)}, which tools.py doesn't define "
                                f"(it has {', '.join(sorted(tool_names))})")
        if any(x.id == call_id for x in calls):
            raise ScenarioError(f"{cw}: the id {call_id!r} is used twice")
        keyed = c.get("caller_keypad")
        if keyed is not None and (not isinstance(keyed, list) or not keyed
                                  or not all(isinstance(t, str) and t.strip() for t in keyed)):
            raise ScenarioError(f"{cw} ({call_id}): `caller_keypad` must be a list of lines, turn by turn")
        calls.append(CallScript(call_id, _text(c, "title", cw, required=False) or call_id,
                                tuple(t.strip() for t in caller),
                                _text(c, "language", cw, required=False) or language, expect,
                                tuple(t.strip() for t in keyed) if keyed else None,
                                _keys(c.get("keys"), agent.keypad, f"{cw} ({call_id}) keys")))
    if not calls:
        raise ScenarioError(f"{where}: no [[calls]]")
    return Scenario(scenario_id, _text(raw, "name", where), today, language, agent, tuple(calls), data, tools,
                    folder)
