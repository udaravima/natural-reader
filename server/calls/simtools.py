"""Pretend tools for a simulated call (spec 2026-11-17-call-simulation).

A scenario's `tools.py` marks plain functions with @tool(description, parameters); each takes (state, arguments) and
returns a dict. A ToolSet holds one call's own copy of the scenario's data, so a booking made in turn 2 is there in
turn 4 and gone in the next call. Like the chat's tools, a tool never raises to the turn: a refusal (ToolError) or a
crash comes back as {"error": ...}, which the agent can say. Arguments are checked against the tool's JSON schema
first: required ones present, whole numbers that a small model sent as "2" read as 2, values outside an enum refused
with the allowed list."""
from __future__ import annotations

import copy
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..llm.types import ToolSpec


class ToolError(Exception):
    """A refusal the agent can pass on: bad dates, a sold-out room. The message becomes the result's `error`."""


@dataclass(frozen=True)
class SimTool:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def tool(description: str, parameters: dict[str, Any]):
    """Marks a scenario function as a tool, named after the function."""
    def mark(fn):
        fn.sim_tool = SimTool(fn.__name__, description, parameters, fn)
        return fn
    return mark


def collect(namespace: Any) -> list[SimTool]:
    """The tools a scenario module defines, in the order it defines them."""
    return [v.sim_tool for v in vars(namespace).values() if isinstance(getattr(v, "sim_tool", None), SimTool)]


@dataclass(frozen=True)
class ToolRun:
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    seconds: float

    @property
    def ok(self) -> bool:
        return "error" not in self.result


def _whole(key: str, value: Any) -> int:
    if isinstance(value, bool):
        raise ToolError(f"{key} must be a whole number, not {value!r}")
    if isinstance(value, int):
        return value
    try:
        number = float(str(value).strip())
    except ValueError:
        raise ToolError(f"{key} must be a whole number, not {value!r}") from None
    if number != int(number):
        raise ToolError(f"{key} must be a whole number, not {value!r}")
    return int(number)


def checked(arguments: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    """The arguments the schema knows, typed; ToolError for a missing, mistyped or out-of-list one. Unknown
    arguments are dropped: small models add extras."""
    props: dict[str, Any] = parameters.get("properties") or {}
    missing = [k for k in parameters.get("required") or () if arguments.get(k) in (None, "")]
    if missing:
        raise ToolError(f"Missing {', '.join(missing)}: ask the caller")
    out: dict[str, Any] = {}
    for key, value in arguments.items():
        if key not in props or value is None:
            continue
        kind = props[key].get("type")
        if kind == "integer":
            value = _whole(key, value)
        elif kind == "number" and not isinstance(value, (int, float)):
            try:
                value = float(str(value).strip())
            except ValueError:
                raise ToolError(f"{key} must be a number, not {value!r}") from None
        elif kind == "string" and not isinstance(value, str):
            value = str(value)
        allowed = props[key].get("enum")
        if allowed and value not in allowed:
            raise ToolError(f"{key} must be one of: {', '.join(map(str, allowed))}")
        out[key] = value
    return out


class ToolSet:
    """One call's tools over its own copy of the data. `log` keeps every run in order."""

    def __init__(self, tools: list[SimTool], data: dict[str, Any], today: str) -> None:
        self._tools = {t.name: t for t in tools}
        self.state: dict[str, Any] = copy.deepcopy(data)
        self.state["today"] = today
        self.log: list[ToolRun] = []

    def specs(self) -> list[ToolSpec]:
        return [ToolSpec(t.name, t.description, t.parameters) for t in self._tools.values()]

    def run(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        started = time.perf_counter()
        given = dict(arguments or {})
        found = self._tools.get(name)
        try:
            if found is None:
                raise ToolError(f"No tool named {name}. The tools are: {', '.join(self._tools)}")
            result = found.fn(self.state, checked(given, found.parameters))
        except ToolError as e:
            result = {"error": str(e)}
        except Exception as e:   # a scenario bug must not end the call: the agent hears that the tool failed
            result = {"error": f"The tool failed ({type(e).__name__}). Apologise and offer to transfer the call."}
        self.log.append(ToolRun(name, given, result, time.perf_counter() - started))
        return result

    def booking(self, reference: str) -> dict[str, Any] | None:
        """A scenario that books things keeps them in state["bookings"], each with a `reference`."""
        for b in self.state.get("bookings") or ():
            if b.get("reference") == reference:
                return b
        return None
