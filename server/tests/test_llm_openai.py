import httpx
import pytest

from server.llm.providers.base import ProviderConfig
from server.llm.providers.openai_compat import OpenAICompatProvider
from server.llm.types import (Attachment, CallSettings, Capabilities, FeatureDropped, Finish,
                              Message, ProviderError, ProviderUnavailable, ReasoningDelta,
                              TextDelta, ToolCall, ToolCallReady, ToolSpec, Usage)
from server.tests.llm_fakes import FakeUpstream, sse

CFG = ProviderConfig(name="router", kind="openai", url="http://llm.test/api/v1", api_key="sk-test")
TOOL = ToolSpec("web_search", "Search the web", {"type": "object", "properties": {"query": {"type": "string"}}})
HI = [Message("user", "hi")]
MODELS = {"data": [{"id": "vendor/model", "context_length": 200000,
                    "supported_parameters": ["tools", "reasoning", "max_tokens"],
                    "architecture": {"input_modalities": ["text", "image"]}}]}


def _up(*chat, models=MODELS):
    return (FakeUpstream()
            .on("GET", "/api/v1/models", lambda: httpx.Response(200, json=models))
            .on("POST", "/api/v1/chat/completions", *chat))


def _delta(**d):
    return {"choices": [{"index": 0, "delta": d, "finish_reason": None}]}


def _finish(reason):
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


USAGE = {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 4}}


async def _collect(agen):
    return [c async for c in agen]


async def test_streams_reasoning_text_usage_and_finish():
    up = _up(lambda: sse(_delta(reasoning_content="r1"), _delta(reasoning="r2"),
                         _delta(content="Hel"), _delta(content="lo"), _finish("stop"), USAGE))
    chunks = await _collect(OpenAICompatProvider(CFG, up.client()).stream_chat(
        "vendor/model", HI, [], CallSettings()))
    assert chunks == [ReasoningDelta("r1"), ReasoningDelta("r2"), TextDelta("Hel"),
                      TextDelta("lo"), Usage(12, 4), Finish("stop")]


async def test_fragmented_parallel_tool_calls_are_assembled_by_index():
    up = _up(lambda: sse(
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_a", "function": {"name": "web_search", "arguments": "{\"qu"}},
            {"index": 1, "id": "call_b", "function": {"name": "web_search", "arguments": ""}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "ery\": \"x\"}"}},
            {"index": 1, "function": {"arguments": "{\"query\": \"y\"}"}}]}}]},
        _finish("tool_calls"), USAGE))
    chunks = await _collect(OpenAICompatProvider(CFG, up.client()).stream_chat(
        "vendor/model", HI, [TOOL], CallSettings()))
    calls = [c.call for c in chunks if isinstance(c, ToolCallReady)]
    assert calls == [ToolCall("call_a", "web_search", {"query": "x"}),
                     ToolCall("call_b", "web_search", {"query": "y"})]
    assert chunks[-1] == Finish("tool_calls")


async def test_invalid_tool_arguments_become_empty_dict():
    up = _up(lambda: sse(
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "web_search", "arguments": "{\"query\": "}}]}}]},
        _finish("tool_calls")))
    chunks = await _collect(OpenAICompatProvider(CFG, up.client()).stream_chat(
        "vendor/model", HI, [TOOL], CallSettings()))
    assert [c.call.arguments for c in chunks if isinstance(c, ToolCallReady)] == [{}]


async def test_request_body_translation_and_auth_header():
    up = _up(lambda: sse(_finish("stop")))
    msgs = [Message("user", "look", attachments=(Attachment("image", "image/png", "AAA"),
                                                 Attachment("audio", "audio/wav", "BBB"))),
            Message("assistant", "", tool_calls=(ToolCall("c1", "web_search", {"query": "x"}),)),
            Message("tool", '{"ok": true}', tool_call_id="c1", name="web_search")]
    await _collect(OpenAICompatProvider(CFG, up.client()).stream_chat(
        "vendor/model", msgs, [TOOL],
        CallSettings(think="on", num_ctx=8192, keep_alive="5m", num_predict=64)))
    req = [r for r in up.requests if r.url.path == "/api/v1/chat/completions"][0]
    assert req.headers["authorization"] == "Bearer sk-test"
    body = up.bodies("/api/v1/chat/completions")[0]
    assert body["messages"] == [
        {"role": "user", "content": [
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
            {"type": "input_audio", "input_audio": {"data": "BBB", "format": "wav"}}]},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "web_search", "arguments": "{\"query\": \"x\"}"}}]},
        {"role": "tool", "content": '{"ok": true}', "tool_call_id": "c1"},
    ]
    assert body["reasoning_effort"] == "medium"          # 'on' -> medium
    assert body["max_tokens"] == 64
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert "num_ctx" not in str(body) and "keep_alive" not in body


async def test_think_off_omits_reasoning_effort():
    up = _up(lambda: sse(_finish("stop")))
    await _collect(OpenAICompatProvider(CFG, up.client()).stream_chat(
        "vendor/model", HI, [], CallSettings(think="off")))
    assert "reasoning_effort" not in up.bodies("/api/v1/chat/completions")[0]


async def test_missing_usage_means_no_usage_chunk():
    up = _up(lambda: sse(_delta(content="x"), _finish("stop")))
    chunks = await _collect(OpenAICompatProvider(CFG, up.client()).stream_chat(
        "vendor/model", HI, [], CallSettings()))
    assert not any(isinstance(c, Usage) for c in chunks)


async def test_rejected_reasoning_retries_without_and_remembers():
    up = _up(lambda: httpx.Response(400, json={"error": {"message": "reasoning_effort not supported"}}),
             lambda: sse(_finish("stop")))
    provider = OpenAICompatProvider(CFG, up.client())
    chunks = await _collect(provider.stream_chat("vendor/model", HI, [], CallSettings(think="high")))
    assert FeatureDropped("thinking") in chunks
    await _collect(provider.stream_chat("vendor/model", HI, [], CallSettings(think="high")))
    assert ["reasoning_effort" in b for b in up.bodies("/api/v1/chat/completions")] == [True, False, False]


async def test_error_payload_mid_stream_is_provider_error():
    up = _up(lambda: sse(_delta(content="a"), {"error": {"message": "upstream overloaded"}}))
    with pytest.raises(ProviderError) as ei:
        await _collect(OpenAICompatProvider(CFG, up.client()).stream_chat(
            "vendor/model", HI, [], CallSettings()))
    assert "overloaded" in ei.value.safe_message


async def test_stream_cut_without_done_or_finish_is_unavailable():
    up = _up(lambda: sse(_delta(content="a"), done=False))
    with pytest.raises(ProviderUnavailable):
        await _collect(OpenAICompatProvider(CFG, up.client()).stream_chat(
            "vendor/model", HI, [], CallSettings()))


async def test_capabilities_openrouter_style_and_vllm_style():
    provider = OpenAICompatProvider(CFG, _up(lambda: sse(_finish("stop"))).client())
    assert await provider.capabilities("vendor/model") == Capabilities(
        tools=True, thinking=True, vision=True, audio=False, context_window=200000)
    vllm = _up(lambda: sse(_finish("stop")),
               models={"data": [{"id": "Qwen/Qwen2.5-7B", "max_model_len": 32768}]})
    assert await OpenAICompatProvider(CFG, vllm.client()).capabilities("Qwen/Qwen2.5-7B") == \
        Capabilities(context_window=32768)


async def test_a_remembered_tools_rejection_makes_capabilities_say_no_tools():
    """Task 2 fix round 1: vLLM lists no supported_parameters (tools=None), and
    after a remembered rejection requests drop tools silently — capabilities()
    must then say tools=False, though the /models list is already cached."""
    up = _up(lambda: httpx.Response(400, json={"error": {"message": "tools not supported"}}),
             lambda: sse(_finish("stop")),
             models={"data": [{"id": "Qwen/Qwen2.5-7B", "max_model_len": 32768}, {"id": "other"}]})
    provider = OpenAICompatProvider(CFG, up.client())
    assert await provider.capabilities("Qwen/Qwen2.5-7B") == Capabilities(context_window=32768)
    chunks = await _collect(provider.stream_chat("Qwen/Qwen2.5-7B", HI, [TOOL], CallSettings()))
    assert FeatureDropped("tools") in chunks
    assert await provider.capabilities("Qwen/Qwen2.5-7B") == Capabilities(tools=False, context_window=32768)
    assert (await provider.capabilities("other")).tools is None   # per model
    assert sum(r.url.path == "/api/v1/models" for r in up.requests) == 1   # served from the cache


async def test_list_models():
    provider = OpenAICompatProvider(CFG, _up(lambda: sse(_finish("stop"))).client())
    assert await provider.list_models() == ["vendor/model"]


async def test_compound_rejection_remembers_only_the_feature_that_fixed_it():
    up = _up(lambda: httpx.Response(400, json={"error": {"message": "reasoning_effort not supported"}}),
             lambda: httpx.Response(400, json={"error": {"message": "tools not supported"}}),
             lambda: sse(_finish("stop")))
    provider = OpenAICompatProvider(CFG, up.client())
    chunks = await _collect(provider.stream_chat("vendor/model", HI, [TOOL], CallSettings(think="high")))
    # Both features are yielded as dropped
    assert FeatureDropped("thinking") in chunks
    assert FeatureDropped("tools") in chunks
    # Second call with same settings: reasoning_effort is sent again (not omitted), tools omitted
    chunks2 = await _collect(provider.stream_chat("vendor/model", HI, [TOOL], CallSettings(think="high")))
    bodies = up.bodies("/api/v1/chat/completions")
    # Four requests total: first call drops both (thinking, then tools), second call only remembers tools
    # Request 1 (first call): reasoning_effort=medium, tools=True → 400
    # Request 2 (first call): reasoning_effort=None, tools=True → 400
    # Request 3 (first call): reasoning_effort=None, tools=False → 200 (success)
    # Request 4 (second call): reasoning_effort=medium, tools=False → 200 (tools remembered, not sent)
    assert [("reasoning_effort" in b, "tools" in b) for b in bodies] == [
        (True, True), (False, True), (False, False), (True, False)]


async def test_models_that_are_not_json_is_a_provider_error_and_the_chat_still_streams():
    """Final review M3: a proxy's HTML 200 on /models must be a ProviderError
    (listing reports the provider failed), never a ValueError escaping into
    stream_chat, which the orchestrator would call an internal_error."""
    up = (FakeUpstream()
          .on("GET", "/api/v1/models", lambda: httpx.Response(200, content=b"<html>login</html>"))
          .on("POST", "/api/v1/chat/completions", lambda: sse(_delta(content="Hi"), _finish("stop"))))
    provider = OpenAICompatProvider(CFG, up.client())
    with pytest.raises(ProviderError) as ei:
        await provider.list_models()
    assert "<html>" not in ei.value.safe_message
    chunks = await _collect(provider.stream_chat("vendor/model", HI, [], CallSettings()))
    assert TextDelta("Hi") in chunks and chunks[-1] == Finish("stop")
