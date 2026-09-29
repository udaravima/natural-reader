import asyncio
import dataclasses
import json
import logging

import httpx
import pytest

from server.llm.providers.base import ProviderConfig, safe_error_message
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


@pytest.mark.parametrize("show", [SHOW_ALL, {"model_info": {}}])   # listed tools; an Ollama too old to list
async def test_a_remembered_tools_rejection_makes_capabilities_say_no_tools(show):
    """Task 2 fix round 1: later requests drop tools silently (no FeatureDropped),
    so the caller must learn it from capabilities() — even from a cache filled
    before the rejection was learned — or it offers tools that are never sent."""
    up = _up(lambda: httpx.Response(400, json={"error": "model does not support tools"}),
             lambda: ndjson(DONE), show=show)
    provider = OllamaProvider(CFG, up.client())
    before = await provider.capabilities("qwen2")   # fills the capability cache
    assert before.tools is not False
    await _collect(provider.stream_chat("qwen2", HI, [TOOL], CallSettings()))
    assert await provider.capabilities("qwen2") == dataclasses.replace(before, tools=False)
    assert (await provider.capabilities("other")).tools is not False   # per model
    assert not any(isinstance(c, FeatureDropped)
                   for c in await _collect(provider.stream_chat("qwen2", HI, [TOOL], CallSettings())))


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


def test_safe_error_message_redacts_secrets():
    """Fix round 1, item 6: a provider that echoes the request it received
    (or a misconfigured upstream that leaks its own key) must never let an
    API key, bearer token or URL password reach an SSE event or the log."""
    assert safe_error_message("invalid key sk-abcdefgh12345678") == "invalid key sk-[REDACTED]"
    assert safe_error_message("Authorization: Bearer abc.def-123") == "Authorization: Bearer [REDACTED]"
    assert (safe_error_message("upstream https://user:pass@example.com/v1 failed")
           == "upstream https://***@example.com/v1 failed")
    assert safe_error_message("plain message, nothing secret here") == "plain message, nothing secret here"


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


async def test_list_models_is_cached_not_fetched_per_call():
    up = FakeUpstream().on("GET", "/api/tags", lambda: httpx.Response(
        200, json={"models": [{"name": "gemma3"}]}))
    provider = OllamaProvider(CFG, up.client())
    await provider.list_models()
    await provider.list_models()
    assert len([r for r in up.requests if r.url.path == "/api/tags"]) == 1


def test_config_repr_never_shows_the_key():
    cfg = ProviderConfig(name="x", kind="openai", url="https://x.example.com/v1", api_key="sk-secret")
    assert "sk-secret" not in repr(cfg)


# ---- Final review M3: a 200 that isn't JSON (e.g. a proxy's HTML page) ----

async def test_show_that_is_not_json_means_unknown_capabilities_and_the_chat_still_streams():
    up = (FakeUpstream()
          .on("POST", "/api/show", lambda: httpx.Response(200, content=b"<html>proxy login</html>"))
          .on("POST", "/api/chat", lambda: ndjson({"message": {"content": "Hi"}}, DONE)))
    provider = OllamaProvider(CFG, up.client())
    assert await provider.capabilities("m") == Capabilities()
    chunks = await _collect(provider.stream_chat("m", HI, [], CallSettings()))
    assert TextDelta("Hi") in chunks and chunks[-1] == Finish("stop")


async def test_tags_that_are_not_json_is_a_provider_error():
    up = FakeUpstream().on("GET", "/api/tags", lambda: httpx.Response(200, content=b"<html>oops</html>"))
    with pytest.raises(ProviderError) as ei:
        await OllamaProvider(CFG, up.client()).list_models()
    assert "<html>" not in ei.value.safe_message


# ---- Final review M2 (spec §11): INFERENCE_TIMEOUT_S is an IDLE timeout ----
#
# httpx.MockTransport never enforces timeouts, so this runs a real (tiny) HTTP
# server on an ephemeral 127.0.0.1 port and goes through start_router, i.e.
# the production httpx.Timeout wiring.

async def _slow_ollama(gap_s: float, lines: int):
    async def handle(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        length = next((int(h.split(b":", 1)[1]) for h in head.split(b"\r\n")
                       if h.lower().startswith(b"content-length:")), 0)
        if length:
            await reader.readexactly(length)
        path = head.split(b" ", 2)[1]
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/x-ndjson\r\nConnection: close\r\n\r\n")
        if path == b"/api/show":
            writer.write(json.dumps(SHOW_ALL).encode())
        else:
            for i in range(lines):
                await asyncio.sleep(gap_s)
                writer.write(json.dumps({"message": {"content": f"{i} "}}).encode() + b"\n")
                await writer.drain()
            writer.write(json.dumps(DONE).encode() + b"\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"


@pytest.mark.parametrize("gap_s,lines,cut_off", [(0.15, 8, False), (0.8, 1, True)])
async def test_a_slow_but_streaming_reply_is_never_cut_off_but_silence_is(monkeypatch, gap_s, lines, cut_off):
    """Timeout 0.4 s. 8 chunks 0.15 s apart take 1.2 s in total (3x the
    timeout) and must arrive whole; a single 0.8 s silence must not."""
    from server.llm import router as llm_router
    from server.services import model_router

    server, url = await _slow_ollama(gap_s, lines)
    real = model_router.get_config()
    monkeypatch.setattr(model_router, "get_config", lambda: dataclasses.replace(real, timeout_s=0.4))
    monkeypatch.delenv("INFERENCE_PROVIDERS", raising=False)
    monkeypatch.delenv("INFERENCE_MODELS", raising=False)
    monkeypatch.setenv("OLLAMA_URL", url)
    await llm_router.start_router()
    try:
        agen = llm_router.get_router().stream_chat("ollama:m", HI, [], CallSettings())
        if cut_off:
            with pytest.raises(ProviderTimeout):
                await _collect(agen)
        else:
            text = "".join(c.text for c in await _collect(agen) if isinstance(c, TextDelta))
            assert text == "".join(f"{i} " for i in range(lines))
    finally:
        await llm_router.stop_router()
        server.close()
        await server.wait_closed()


async def test_an_api_show_failure_is_logged_at_debug_only(caplog):
    """Deferred C1 minor: a model the provider can't describe is routine
    (older Ollama, a proxy) — DEBUG, never WARNING, and not cached."""
    caplog.set_level(logging.DEBUG, logger="server.llm.providers.ollama")
    for failure in (httpx.ConnectError("refused"), lambda: httpx.Response(500),
                    lambda: httpx.Response(200, content=b"<html>proxy</html>")):
        caplog.clear()
        up = FakeUpstream().on("POST", "/api/show", failure)
        caps = await OllamaProvider(CFG, up.client()).capabilities("qwen2")
        assert caps == Capabilities()
        records = [r for r in caplog.records if "/api/show" in r.getMessage()]
        assert records and all(r.levelno == logging.DEBUG for r in records)
