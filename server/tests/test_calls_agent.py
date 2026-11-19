"""The call agent's turn loop (spec 2026-11-17-call-simulation), over a scripted fake model."""
import json

import pytest

from server.calls.agent import CallAgent
from server.calls.scenario import load_scenario
from server.llm.types import Finish, ProviderError, TextDelta, ToolCall, ToolCallReady, Usage

HOTEL = load_scenario("hotel")
STAY = {"check_in": "2026-12-12", "check_out": "2026-12-14", "guests": 2, "room_type": "deluxe"}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeRouter:
    """Plays one list of chunks per model request; a number in the list moves the clock on by that many seconds."""

    def __init__(self, clock, *steps):
        self.clock, self.steps, self.requests = clock, list(steps), []

    def stream_chat(self, model_id, messages, tools, settings):
        self.requests.append({"messages": list(messages), "tools": [t.name for t in tools]})
        chunks = self.steps.pop(0)

        async def play():
            for c in chunks:
                if isinstance(c, Exception):
                    raise c
                if isinstance(c, (int, float)):
                    self.clock.t += c
                    continue
                yield c
        return play()


def agent_with(*steps, max_steps=4):
    clock = Clock()
    router = FakeRouter(clock, *steps)
    tools = HOTEL.toolset()
    return CallAgent(router, "fake:model", HOTEL, tools, clock=clock, max_steps=max_steps), router, tools, clock


async def test_a_turn_that_looks_up_and_then_answers():
    agent, router, tools, _ = agent_with(
        [0.4, ToolCallReady(ToolCall("c1", "check_availability", STAY)), Usage(900, 12), Finish("tool_calls")],
        [0.5, TextDelta("The deluxe room is free. "), 0.3, TextDelta("It is LKR 84,000 for two nights."),
         Usage(1000, 20), Finish("stop")])
    r = await agent.turn("How much is the deluxe room?")
    assert r.text == "The deluxe room is free. It is LKR 84,000 for two nights."
    assert r.pieces == [("The deluxe room is free.", pytest.approx(0.9)),
                        ("It is LKR 84,000 for two nights.", pytest.approx(1.2))]
    assert r.first_word_s == pytest.approx(0.9)
    assert r.steps == 2
    assert r.step_ends == [pytest.approx(0.4), pytest.approx(1.2)]
    assert [run.name for run in r.tool_runs] == ["check_availability"]
    assert r.tokens == (1900, 32)
    assert not r.empty_after_tool and r.error is None
    assert "check_availability" in router.requests[0]["tools"]
    second = router.requests[1]["messages"]
    assert second[-2].role == "assistant" and second[-2].tool_calls[0].name == "check_availability"
    assert second[-1].role == "tool" and second[-1].tool_call_id == "c1"
    assert json.loads(second[-1].content)["rooms"][0]["total"] == 84000


async def test_the_model_is_told_the_greeting_and_the_date_and_the_history_starts_with_the_caller():
    agent, router, _, _ = agent_with([TextDelta("Of course."), Finish("stop")])
    await agent.turn("Hello?")
    system, first = router.requests[0]["messages"]
    assert system.role == "system" and first.role == "user" and first.content == "Hello?"
    assert HOTEL.agent.greeting in system.content
    assert "Tuesday, 1 December 2026" in system.content
    assert "read aloud" in system.content
    assert "Never guess" in system.content


async def test_the_last_step_offers_no_tools_so_the_model_must_answer():
    agent, router, _, _ = agent_with(
        [ToolCallReady(ToolCall("c1", "get_hotel_info", {"topic": "pool"})), Finish("tool_calls")],
        [TextDelta("The pool opens at 7 am."), Finish("stop")], max_steps=2)
    r = await agent.turn("When does the pool open?")
    assert router.requests[1]["tools"] == []
    assert r.text == "The pool opens at 7 am."


async def test_words_before_a_tool_are_held_like_talk_holds_them_and_silence_after_it_is_flagged():
    agent, _, _, _ = agent_with(
        [0.2, TextDelta("Let me check that."), ToolCallReady(ToolCall("c1", "get_hotel_info", {"topic": "pool"})),
         Finish("tool_calls")],
        [1.0, Finish("stop")])
    r = await agent.turn("When does the pool open?")
    assert r.empty_after_tool
    assert r.pieces == [("Let me check that.", pytest.approx(1.2))]
    assert r.held_s == pytest.approx(1.0)


async def test_a_provider_refusal_ends_the_turn_with_its_reason():
    agent, _, _, _ = agent_with([ProviderError(402, "The provider account is out of credit.")])
    r = await agent.turn("Hello?")
    assert "out of credit" in r.error
    assert r.text == "" and r.pieces == []


async def test_tool_results_stay_in_the_history_for_later_turns():
    agent, router, _, _ = agent_with(
        [ToolCallReady(ToolCall("c1", "check_availability", STAY)), Finish("tool_calls")],
        [TextDelta("It is free."), Finish("stop")],
        [TextDelta("LKR 84,000."), Finish("stop")])
    await agent.turn("Is the deluxe room free?")
    await agent.turn("How much again?")
    later = router.requests[2]["messages"]
    assert [m.role for m in later] == ["system", "user", "assistant", "tool", "assistant", "user"]


async def test_each_step_is_logged_with_its_tools_and_whether_it_said_anything():
    from server.calls.agent import Step
    agent, _, _, _ = agent_with(
        [0.4, ToolCallReady(ToolCall("c1", "check_availability", STAY)), Finish("tool_calls")],
        [0.5, TextDelta("It is free."), Finish("stop")])
    r = await agent.turn("Is the deluxe room free?")
    assert r.step_log == [Step(pytest.approx(0.4), ("check_availability",), False, ()),
                          Step(pytest.approx(0.9), (), True, ())]


async def test_a_keypad_step_logs_the_digits_the_caller_keyed():
    from server.calls.agent import Step
    from server.calls.keypad import keypad_tool
    clock = Clock()
    router = FakeRouter(clock, [0.3, ToolCallReady(ToolCall("k", "collect_digits", {"what": "phone"})),
                                Finish("tool_calls")], [0.2, TextDelta("Thank you."), Finish("stop")])
    agent = CallAgent(router, "fake:model", HOTEL, HOTEL.toolset((keypad_tool({"phone": "0771234567"}),)),
                      clock=clock, keypad=True)
    r = await agent.turn("My name is Kasun Perera.")
    assert r.step_log[0] == Step(pytest.approx(0.3), ("collect_digits",), False, (("phone", "0771234567"),))
    assert "collect_digits" in agent.messages[0].content


async def test_without_the_keypad_the_prompt_does_not_mention_it():
    agent, _, _, _ = agent_with([TextDelta("Hello."), Finish("stop")])
    assert "collect_digits" not in agent.messages[0].content


async def test_with_the_facts_in_the_prompt_the_agent_is_told_them_and_answers_without_a_tool():
    clock = Clock()
    router = FakeRouter(clock, [0.4, TextDelta("Yes, breakfast is included."), Finish("stop")])
    tools = HOTEL.toolset(without=(HOTEL.facts.tool,))
    agent = CallAgent(router, "fake:model", HOTEL, tools, clock=clock, facts=HOTEL.facts)
    r = await agent.turn("Is breakfast included in the rate?")
    prompt = agent.messages[0].content
    for answer in HOTEL.facts.answers.values():
        assert answer in prompt
    assert "get_hotel_info" not in router.requests[0]["tools"]
    assert r.steps == 1 and r.text == "Yes, breakfast is included."


async def test_without_the_facts_in_the_prompt_the_agent_is_not_told_them():
    agent, _, _, _ = agent_with([TextDelta("Hello."), Finish("stop")])
    assert HOTEL.facts.answers["breakfast"] not in agent.messages[0].content
