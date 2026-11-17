"""Recorded lines that fill the silence while the agent looks something up (spec 2026-11-17-call-bench-round2).

A scenario gives a line or a few per tool (`[agent.fillers]`), a default for any other tool (the agent's `filler`)
and "still checking" lines for a long wait. They are recorded once per voice before any call, so playing one costs
no time on the call. One book per call: a pool's lines take turns, and the line played last is never picked again
straight away, across pools too, unless it is the only line there is."""
from __future__ import annotations

from .scenario import Agent


class FillerBook:
    def __init__(self, agent: Agent) -> None:
        self._pools: dict[str, tuple[str, ...]] = dict(agent.fillers)
        self._still = agent.still_checking or agent.fillers.get("", ())
        self._turn: dict[str, int] = {}
        self._last: str | None = None

    def lines(self) -> list[str]:
        """Every distinct line, in a stable order: what to record for each voice."""
        return list(dict.fromkeys(line for pool in (*self._pools.values(), self._still) for line in pool))

    def _pick(self, key: str, pool: tuple[str, ...]) -> str:
        if not pool:
            return ""
        start = self._turn.get(key, 0)
        for k in range(len(pool)):
            line = pool[(start + k) % len(pool)]
            if line != self._last:
                self._turn[key] = start + k + 1
                break
        else:
            line = pool[start % len(pool)]
            self._turn[key] = start + 1
        self._last = line
        return line

    def for_tool(self, name: str) -> str:
        pool = self._pools.get(name) or self._pools.get("", ())
        return self._pick(name if name in self._pools else "", pool)

    def still(self) -> str:
        return self._pick("\0still", self._still)
