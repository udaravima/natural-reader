import json

import httpx
import pytest

from server.llm.providers.base import ProviderConfig
from server.llm.providers.ollama import OllamaProvider
from server.llm.types import (Attachment, CallSettings, Capabilities, FeatureDropped, Finish,
                              Message, ProviderError, ProviderTimeout, ProviderUnavailable,
                              ReasoningDelta, TextDelta, ToolCall, ToolCallReady, ToolSpec, Usage)
from server.tests.llm_fakes import FakeUpstream, ndjson

CFG = ProviderConfig(name="ollama", kind="ollama", url="http://ollama.test")
SHOW_ALL = {"capabilities": ["completion", "tools", "thinking", "vision"],
            "model_info": {"qwen2.context_length": 32768}}
TOOL = ToolSpec("search_document", "Search the open document",
                {"type": "object", "properties": {"query": {"type": "string"}}})
DONE = {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
        "prompt_eval_count": 10, "eval_count": 5, "total_duration": 9}
HI = [Message("user", "hi")]


def _up(*chat_responses, show=SHOW_ALL):
    return (FakeUpstream()
            .on("POST", "/api/show", lambda: httpx.Response(200, json=show))
            .on("POST", "/api/chat", *chat_responses))


async def _collect(agen):
    return [c async for c in agen]


async def test_streams_reasoning_text_usage_and_finish():
    up = _up(lambda: ndjson({"message": {"thinking": "hmm"}}, {"message": {"content": "Hel"}},
                            {"message": {"content": "lo"}}, DONE))
    chunks = await _collect(OllamaProvider(CFG, up.client()).stream_chat("qwen2", HI, [], CallSettings()))
    assert chunks == [ReasoningDelta("hmm"), TextDelta("Hel"), TextDelta("lo"),
                      Usage(10, 5, detail={"total_duration": 9}), Finish("stop")]


async def test_done_reason_length_is_reported():
    up = _up(lambda: ndjson({"message": {"content": "cut"}}, {**DONE, "done_reason": "length"}))
    chunks = await _collect(OllamaProvider(CFG, up.client()).stream_chat("qwen2", HI, [], CallSettings()))
    assert chunks[-1] == Finish("length")


async def test_missing_counts_mean_no_usage_chunk():
    up = _up(lambda: ndjson({"message": {"content": "x"}}, {"done": True, "done_reason": "stop"}))
    chunks = await _collect(OllamaProvider(CFG, up.client()).stream_chat("qwen2", HI, [], CallSettings()))
    assert not any(isinstance(c, Usage) for c in chunks)


async def test_tool_calls_as_objects_or_json_strings():
    up = _up(lambda: ndjson(
        {"message": {"tool_calls": [{"function": {"name": "search_document", "arguments": {"query": "x"}}}]}},
        {"message": {"tool_calls": [{"function": {"name": "search_document", "arguments": "{\"query\": \"y\"}"}}]}},
        {"message": {"tool_calls": [{"function": {"name": "search_document", "arguments": "{not json"}}]}},
        DONE))
    chunks = await _collect(OllamaProvider(CFG, up.client()).stream_chat("qwen2", HI, [TOOL], CallSettings()))
    calls = [c.call for c in chunks if isinstance(c, ToolCallReady)]
    assert [(c.id, c.name, c.arguments) for c in calls] == [
        ("call_1", "search_document", {"query": "x"}),
        ("call_2", "search_document", {"query": "y"}),
        ("call_3", "search_document", {}),
    ]
    assert chunks[-1] == Finish("tool_calls")


async def test_request_body_translation():
    up = _up(lambda: ndjson(DONE))
    msgs = [Message("user", "look", attachments=(Attachment("image", "image/png", "AAA"),)),
            Message("assistant", "", tool_calls=(ToolCall("call_1", "search_document", {"query": "x"}),)),
            Message("tool", '{"ok": true}', tool_call_id="call_1", name="search_document")]
    await _collect(OllamaProvider(CFG, up.client()).stream_chat(
        "qwen2", msgs, [TOOL], CallSettings(think="low", num_ctx=8192, num_predict=100)))
    body = up.bodies("/api/chat")[0]
    assert body["messages"] == [
        {"role": "user", "content": "look", "images": ["AAA"]},
        {"role": "assistant", "content": "",
         "tool_calls": [{"function": {"name": "search_document", "arguments": {"query": "x"}}}]},
        {"role": "tool", "content": '{"ok": true}', "tool_name": "search_document"},
    ]
    assert body["think"] == "low"
    assert body["options"] == {"num_ctx": 8192, "num_predict": 100}
    assert body["stream"] is True
    assert "keep_alive" not in body
    assert body["tools"] == [{"type": "function", "function": {
        "name": "search_document", "description": "Search the open document",
        "parameters": TOOL.parameters}}]


async def test_rejected_think_level_retries_with_plain_thinking_and_remembers():
    up = _up(lambda: httpx.Response(400, json={"error": "invalid think value"}),
             lambda: ndjson(DONE))
    provider = OllamaProvider(CFG, up.client())
    chunks = await _collect(provider.stream_chat("qwen2", HI, [], CallSettings(think="high")))
    assert FeatureDropped("think_level") in chunks
    await _collect(provider.stream_chat("qwen2", HI, [], CallSettings(think="high")))
    assert [b["think"] for b in up.bodies("/api/chat")] == ["high", True, True]


async def test_rejected_tools_retry_without_and_remember():
    up = _up(lambda: httpx.Response(400, json={"error": "model does not support tools"}),
             lambda: ndjson(DONE))
    provider = OllamaProvider(CFG, up.client())
    chunks = await _collect(provider.stream_chat("qwen2", HI, [TOOL], CallSettings()))
    assert FeatureDropped("tools") in chunks
    await _collect(provider.stream_chat("qwen2", HI, [TOOL], CallSettings()))
    assert ["tools" in b for b in up.bodies("/api/chat")] == [True, False, False]


async def test_rejection_is_not_remembered_when_the_retry_also_fails():
    up = _up(lambda: httpx.Response(400, json={"error": "prompt too long"}),
             lambda: httpx.Response(400, json={"error": "prompt too long"}),
             lambda: ndjson(DONE))
    provider = OllamaProvider(CFG, up.client())
    with pytest.raises(ProviderError):
        await _collect(provider.stream_chat("qwen2", HI, [TOOL], CallSettings()))
    await _collect(provider.stream_chat("qwen2", HI, [TOOL], CallSettings()))
    assert ["tools" in b for b in up.bodies("/api/chat")] == [True, False, True]


async def test_compound_rejection_remembers_only_the_feature_that_fixed_it():
    up = _up(lambda: httpx.Response(400, json={"error": "invalid think value"}),
             lambda: httpx.Response(400, json={"error": "model does not support tools"}),
             lambda: ndjson(DONE))
    provider = OllamaProvider(CFG, up.client())
    chunks = await _collect(provider.stream_chat("qwen2", HI, [TOOL], CallSettings(think="high")))
    # Both features are yielded as dropped
    assert FeatureDropped("think_level") in chunks
    assert FeatureDropped("tools") in chunks
    # Second call with same settings: think="high" is sent again (not True), tools omitted
    chunks2 = await _collect(provider.stream_chat("qwen2", HI, [TOOL], CallSettings(think="high")))
    bodies = up.bodies("/api/chat")
    # Four requests total: first call drops both (think, then tools), second call only remembers tools
    # Request 1 (first call): think="high", tools=True → 400
    # Request 2 (first call): think=True, tools=True → 400
    # Request 3 (first call): think=True, tools=False → 200 (success)
    # Request 4 (second call): think="high", tools=False → 200 (tools remembered, not sent)
    assert [(b.get("think"), "tools" in b) for b in bodies] == [("high", True), (True, True), (True, False), ("high", False)]


async def test_model_not_found_is_not_a_feature_rejection():
    up = _up(lambda: httpx.Response(404, json={"error": "model 'x' not found"}))
    with pytest.raises(ProviderError) as ei:
        await _collect(OllamaProvider(CFG, up.client()).stream_chat("x", HI, [TOOL], CallSettings()))
    assert ei.value.status == 404 and "not found" in ei.value.safe_message
    assert len(up.bodies("/api/chat")) == 1


async def test_connect_failure_is_provider_unavailable():
    up = (FakeUpstream().on("POST", "/api/show", httpx.ConnectError("refused"))
          .on("POST", "/api/chat", httpx.ConnectError("refused")))
    with pytest.raises(ProviderUnavailable):
        await _collect(OllamaProvider(CFG, up.client()).stream_chat("qwen2", HI, [], CallSettings()))


async def test_silence_mid_stream_is_provider_timeout():
    async def gen():
        yield json.dumps({"message": {"content": "Hi"}}).encode() + b"\n"
        raise httpx.ReadTimeout("idle")
    up = _up(lambda: httpx.Response(200, content=gen()))
    got = []
    with pytest.raises(ProviderTimeout):
        async for c in OllamaProvider(CFG, up.client()).stream_chat("qwen2", HI, [], CallSettings()):
            got.append(c)
    assert got == [TextDelta("Hi")]


async def test_error_line_mid_stream_is_provider_error():
    up = _up(lambda: ndjson({"message": {"content": "a"}}, {"error": "out of memory"}))
    with pytest.raises(ProviderError) as ei:
        await _collect(OllamaProvider(CFG, up.client()).stream_chat("qwen2", HI, [], CallSettings()))
    assert "out of memory" in ei.value.safe_message


async def test_stream_that_ends_without_done_is_unavailable():
    up = _up(lambda: ndjson({"message": {"content": "a"}}))
    with pytest.raises(ProviderUnavailable):
        await _collect(OllamaProvider(CFG, up.client()).stream_chat("qwen2", HI, [], CallSettings()))


async def test_capabilities_from_show_and_unsupported_features_are_omitted():
    up = _up(lambda: ndjson(DONE),
             show={"capabilities": ["completion"], "model_info": {"llama.context_length": 8192}})
    provider = OllamaProvider(CFG, up.client())
    assert await provider.capabilities("m") == Capabilities(
        tools=False, thinking=False, vision=False, audio=False, context_window=8192)
    await _collect(provider.stream_chat("m", HI, [TOOL], CallSettings(think="on")))
    body = up.bodies("/api/chat")[0]
    assert "think" not in body and "tools" not in body


async def test_older_ollama_without_capability_list_is_unknown():
    up = _up(lambda: ndjson(DONE), show={"model_info": {}})
    assert await OllamaProvider(CFG, up.client()).capabilities("m") == Capabilities()


async def test_list_models_reads_tags():
    up = FakeUpstream().on("GET", "/api/tags", lambda: httpx.Response(
        200, json={"models": [{"name": "gemma3"}, {"name": "qwen2.5:7b"}, {"other": 1}]}))
    assert await OllamaProvider(CFG, up.client()).list_models() == ["gemma3", "qwen2.5:7b"]


def test_config_repr_never_shows_the_key():
    cfg = ProviderConfig(name="x", kind="openai", url="https://x.example.com/v1", api_key="sk-secret")
    assert "sk-secret" not in repr(cfg)
