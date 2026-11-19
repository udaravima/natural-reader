"""The call agent's turn loop (spec 2026-11-17-call-simulation).

A short prompt (who the agent is, the scenario's rules, rules written for the ear, today's date, the greeting it
has already said) and the scenario's tools, over the app's model router. One turn streams the model, runs any tool
calls against the call's own data and asks again, up to `max_steps` requests; the last one offers no tools, so the
model has to answer. Tool calls and results stay in the history, as an agent framework keeps them, so a later turn
knows the rate it looked up.

Timings are seconds since the caller's words were handed over. `pieces` are cut with Talk's own rules
(sentences.py), fed exactly as Talk feeds them, with nothing between one step's text and the next: words a model
writes before a tool call ("Let me check that.") can't be cut until more text arrives, so the caller hears them
only once the answer starts. `held_s` measures that wait."""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..llm.router import UnknownModel
from ..llm.types import (CallSettings, Message, ProviderError, ProviderTimeout, ProviderUnavailable, TextDelta,
                         ToolCall, ToolCallReady, Usage)
from .keypad import KEYPAD_RULES
from .scenario import Facts, Scenario
from .sentences import SentenceSplitter
from .simtools import ToolRun, ToolSet

CALL_RULES = """This is a phone call, and everything you write is read aloud by a voice.
- Say one to three short sentences per turn. No lists, headings, markdown, emoji or links.
- Write amounts with the currency, such as "LKR 42,000", and dates the way people say them, such as "Saturday the
  12th of December". Never say a date as 2026-12-12.
- When you need a tool, call it straight away, without announcing it.
- If a detail sounds misheard or unclear, ask the caller to repeat it."""


def system_prompt(scenario: Scenario, keypad: bool = False, facts: Facts | None = None) -> str:
    """`facts`: fixed facts told to the agent up front, so it answers them without a tool (and a model wait)."""
    agent = scenario.agent
    today = date.fromisoformat(scenario.today)
    parts = [
        f"You are {agent.name}, answering the phone for {scenario.name}.",
        agent.backstory,
        "Your rules:\n" + agent.rules,
        CALL_RULES,
        KEYPAD_RULES if keypad else "",
        ("Facts you can give straight away, without a tool. They are as reliable as a tool's answer:\n"
         + "\n".join(f"- {a}" for a in facts.answers.values())) if facts else "",
        f"Today is {today:%A}, {today.day} {today:%B %Y}. Work out full dates from what the caller says, and send "
        "dates to the tools as YYYY-MM-DD.",
        f'The call has started and you have already said: "{agent.greeting}" Don\'t greet the caller again.',
    ]
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


@dataclass(frozen=True)
class Step:
    """One model request in a turn: when its stream ended, the tools it called, whether it said anything, and the
    digits the caller keyed for it (what, digits). A step with tools and no words is when a filler would play."""
    end_s: float
    tools: tuple[str, ...]
    words: bool
    keyed: tuple[tuple[str, str], ...] = ()


@dataclass
class TurnResult:
    text: str = ""
    pieces: list[tuple[str, float]] = field(default_factory=list)   # (what Talk would voice, when it was ready)
    first_word_s: float | None = None
    whole_s: float = 0.0
    steps: int = 0
    step_ends: list[float] = field(default_factory=list)   # when each model request's stream ended
    step_log: list[Step] = field(default_factory=list)
    tool_runs: list[ToolRun] = field(default_factory=list)
    tool_s: float = 0.0
    tokens: tuple[int, int] = (0, 0)    # prompt, completion: summed over the turn's requests
    held_s: float = 0.0                 # words before a tool, waiting for the answer's first words
    empty_after_tool: bool = False      # a tool ran and the turn ended without a word after it
    error: str | None = None


def _reason(e: Exception) -> str:
    if isinstance(e, ProviderError):
        return f"HTTP {e.status}: {e.safe_message}"
    if isinstance(e, ProviderTimeout):
        return "The model stopped sending (timed out)"
    return f"{type(e).__name__}: {e}"


class CallAgent:
    def __init__(self, router: Any, model_id: str, scenario: Scenario, toolset: ToolSet, *,
                 settings: CallSettings | None = None, max_steps: int = 4, first_clause_chars: int = 30,
                 clock: Callable[[], float] = time.perf_counter, keypad: bool = False,
                 facts: Facts | None = None) -> None:
        self.router, self.model_id, self.toolset = router, model_id, toolset
        self.settings = settings or CallSettings()
        self.max_steps = max(1, max_steps)
        self.first_clause_chars = first_clause_chars
        self.clock = clock
        self.messages: list[Message] = [Message("system", system_prompt(scenario, keypad, facts))]

    async def turn(self, caller_text: str) -> TurnResult:
        started = self.clock()
        since = lambda: self.clock() - started   # noqa: E731
        self.messages.append(Message("user", caller_text))
        result = TurnResult()
        splitter = SentenceSplitter(self.first_clause_chars)
        narration_ended: list[float] = []   # when a step that said something before its tool calls ended
        prompt_tokens = completion_tokens = 0
        step_text = ""
        ran_tool = False

        def take(pieces: list[str]) -> None:
            now = since()
            for p in pieces:
                result.pieces.append((p, now))
            if pieces and narration_ended:
                result.held_s += sum(now - t for t in narration_ended)
                narration_ended.clear()

        try:
            for step in range(self.max_steps):
                result.steps += 1
                offered = self.toolset.specs() if step < self.max_steps - 1 else []
                step_text, calls = "", []
                async for chunk in self.router.stream_chat(self.model_id, self.messages, offered, self.settings):
                    if isinstance(chunk, TextDelta) and chunk.text:
                        if result.first_word_s is None and chunk.text.strip():
                            result.first_word_s = since()
                        step_text += chunk.text
                        take(splitter.feed(chunk.text))
                    elif isinstance(chunk, ToolCallReady):
                        calls.append(chunk.call)
                    elif isinstance(chunk, Usage):
                        prompt_tokens += chunk.prompt_tokens
                        completion_tokens += chunk.completion_tokens
                ended = since()
                result.step_ends.append(ended)
                result.text += step_text
                if not calls:
                    self.messages.append(Message("assistant", step_text))
                    result.step_log.append(Step(ended, (), bool(step_text.strip())))
                    break
                if step_text.strip():
                    narration_ended.append(since())
                self.messages.append(Message("assistant", step_text, tool_calls=tuple(calls)))
                ran_tool = True
                runs = self._run_tools(calls, result)
                keyed = tuple((r.result["what"], r.result["digits"]) for r in runs
                              if r.name == "collect_digits" and r.ok and "digits" in r.result)
                result.step_log.append(Step(ended, tuple(c.name for c in calls), bool(step_text.strip()), keyed))
        except (ProviderError, ProviderTimeout, ProviderUnavailable, UnknownModel) as e:
            result.error = _reason(e)
        take(splitter.flush())
        result.whole_s = since()
        result.tokens = (prompt_tokens, completion_tokens)
        result.empty_after_tool = ran_tool and result.error is None and not step_text.strip()
        return result

    def _run_tools(self, calls: list[ToolCall], result: TurnResult) -> list:
        runs = []
        for call in calls:
            started = self.clock()
            outcome = self.toolset.run(call.name, call.arguments)
            result.tool_s += self.clock() - started
            result.tool_runs.append(self.toolset.log[-1])
            runs.append(self.toolset.log[-1])
            self.messages.append(Message("tool", json.dumps(outcome, ensure_ascii=False), tool_call_id=call.id,
                                         name=call.name))
        return runs
