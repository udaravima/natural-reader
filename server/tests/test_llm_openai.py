import httpx
import pytest

from server.llm.providers.base import RAW_DETAIL_CAP, ProviderConfig
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


# Task 10: a provider's HTTP status says what happened, in words a person can
# act on. OpenRouter's :free models answer 429 with
# {"error": {"message": "Provider returned error", "metadata": {"raw": "..."}}}.
RATE_LIMITED = {"error": {"message": "Provider returned error", "code": 429, "metadata": {
    "raw": "google/gemma-4-26b-a4b-it:free is temporarily rate-limited upstream. Please retry shortly.",
    "provider_name": "Example"}}}


async def _stream_error(response):
    up = _up(lambda: response)
    with pytest.raises(ProviderError) as ei:
        await _collect(OpenAICompatProvider(CFG, up.client()).stream_chat(
            "vendor/model", HI, [], CallSettings()))
    return ei.value


async def test_429_says_busy_or_rate_limited_and_quotes_the_provider_detail():
    err = await _stream_error(httpx.Response(429, json=RATE_LIMITED))
    assert err.status == 429
    assert err.safe_message.startswith(
        "This model is busy or rate-limited at the provider. Try again in a moment, or pick another model.")
    assert "temporarily rate-limited upstream" in err.safe_message
    assert "Provider returned error" not in err.safe_message
    assert err.explained


async def test_429_without_metadata_is_just_the_plain_message():
    err = await _stream_error(httpx.Response(429, json={"error": {"message": "Rate limit exceeded"}}))
    assert err.safe_message == (
        "This model is busy or rate-limited at the provider. Try again in a moment, or pick another model.")


async def test_401_says_the_credentials_were_rejected():
    err = await _stream_error(httpx.Response(401, json={"error": {"message": "No auth credentials found"}}))
    assert err.safe_message == "The provider rejected this server's credentials. An admin needs to check the API key."


async def test_403_says_the_request_was_refused_and_keeps_the_provider_reason():
    """OpenRouter answers 403 when a moderated model flags the input — not a
    key problem, so it must not send people to the admin, and the reason is
    the only thing that tells them what to change."""
    body = {"error": {"message": "Input flagged", "code": 403,
                      "metadata": {"reasons": ["violence"], "flagged_input": "..."}}}
    err = await _stream_error(httpx.Response(403, json=body))
    assert err.safe_message == "The provider refused this request. (Provider said: Input flagged)"
    assert "credentials" not in err.safe_message and err.explained


async def test_402_says_out_of_credit_and_is_not_retried_as_a_feature_rejection():
    up = _up(lambda: httpx.Response(402, json={"error": {"message": "Insufficient credits"}}))
    with pytest.raises(ProviderError) as ei:
        await _collect(OpenAICompatProvider(CFG, up.client()).stream_chat(
            "vendor/model", HI, [TOOL], CallSettings(think="high")))
    assert ei.value.safe_message == "The provider account is out of credit."
    assert len(up.bodies("/api/v1/chat/completions")) == 1


async def test_other_statuses_keep_the_provider_text():
    err = await _stream_error(httpx.Response(500, json={"error": {"message": "upstream exploded"}}))
    assert err.safe_message == "upstream exploded"
    assert not err.explained


async def test_a_key_inside_metadata_raw_is_redacted():
    body = {"error": {"message": "Provider returned error", "metadata": {
        "raw": "rate-limited; your key sk-or-v1-0123456789abcdef0123456789abcdef was throttled"}}}
    err = await _stream_error(httpx.Response(429, json=body))
    assert "sk-or-v1-0123456789abcdef" not in err.safe_message
    assert "rate-limited" in err.safe_message


async def test_a_long_metadata_raw_is_trimmed_after_redaction():
    body = {"error": {"message": "x", "metadata": {"raw": "busy " * 200}}}
    err = await _stream_error(httpx.Response(429, json=body))
    detail = err.safe_message.split("(Provider said: ", 1)[1].rstrip(")")
    assert detail.endswith("…") and len(detail) <= RAW_DETAIL_CAP + 1
    # A key straddling the cut is redacted before the cut, never half-shown.
    straddle = "a" * (RAW_DETAIL_CAP - 10) + " sk-or-v1-0123456789abcdef0123456789abcdef tail"
    err = await _stream_error(httpx.Response(429, json={"error": {"metadata": {"raw": straddle}}}))
    assert "0123456789" not in err.safe_message and "sk-or-v1" not in err.safe_message


async def test_a_models_list_401_is_explained_too():
    up = FakeUpstream().on("GET", "/api/v1/models", lambda: httpx.Response(401, json={"error": {"message": "x"}}))
    with pytest.raises(ProviderError) as ei:
        await OpenAICompatProvider(CFG, up.client()).list_models()
    assert ei.value.safe_message.startswith("The provider rejected this server's credentials")


async def test_a_429_mid_stream_error_payload_is_explained_too():
    up = _up(lambda: sse(_delta(content="a"), RATE_LIMITED))
    with pytest.raises(ProviderError) as ei:
        await _collect(OpenAICompatProvider(CFG, up.client()).stream_chat(
            "vendor/model", HI, [], CallSettings()))
    assert ei.value.safe_message.startswith("This model is busy or rate-limited")


async def test_a_403_without_a_message_shows_no_json_and_a_long_reason_is_trimmed():
    err = await _stream_error(httpx.Response(403, json={"error": {"code": 403, "metadata": {"reasons": ["x"]}}}))
    assert err.safe_message == "The provider refused this request."
    err = await _stream_error(httpx.Response(403, json={"error": {"message": "flagged " * 60}}))
    assert err.safe_message.endswith("…)")


# v2.3 Task A review I5: a strict chat template (vLLM's Gemma-2, Mistral
# v0.1/0.2) raises on any system role, and every prompt now has one. The
# adapter folds the system message into the first user message and remembers.
SYS = [Message("system", "RULES"), Message("user", "hi")]
TEMPLATE_ERROR = {"error": {"message": "System role not supported", "type": "BadRequestError"}}


async def test_a_rejected_system_role_is_folded_into_the_first_user_message_and_remembered():
    up = _up(lambda: httpx.Response(400, json=TEMPLATE_ERROR),
             lambda: sse(_delta(content="ok"), _finish("stop")),
             lambda: sse(_delta(content="ok again"), _finish("stop")))
    provider = OpenAICompatProvider(CFG, up.client())
    first = await _collect(provider.stream_chat("vendor/model", SYS, [TOOL], CallSettings()))
    assert FeatureDropped("system") in first
    tried, folded = up.bodies("/api/v1/chat/completions")
    assert tried["messages"][0]["role"] == "system"
    assert [m["role"] for m in folded["messages"]] == ["user"]
    assert folded["messages"][0]["content"] == "RULES\n\nhi"
    assert "tools" in folded                                     # only the system role was dropped
    # Remembered: the next request folds from the start, one request, no notice.
    second = await _collect(provider.stream_chat("vendor/model", SYS, [], CallSettings()))
    assert FeatureDropped("system") not in second
    assert [m["role"] for m in up.bodies("/api/v1/chat/completions")[2]["messages"]] == ["user"]


async def test_an_unrelated_400_does_not_fold_the_system_message():
    up = _up(lambda: httpx.Response(400, json={"error": {"message": "max_tokens too large"}}))
    with pytest.raises(ProviderError):
        await _collect(OpenAICompatProvider(CFG, up.client()).stream_chat(
            "vendor/model", SYS, [], CallSettings()))
    assert all(b["messages"][0]["role"] == "system" for b in up.bodies("/api/v1/chat/completions"))


async def test_a_folded_system_role_is_remembered_even_when_tools_were_dropped_after_it():
    """Re-review minor: settle_dropped remembers only the last feature, but the
    fold is certain (the error names the system role), so it is always kept."""
    up = _up(lambda: httpx.Response(400, json=TEMPLATE_ERROR),
             lambda: httpx.Response(400, json={"error": {"message": "tool use is not supported"}}),
             lambda: sse(_delta(content="ok"), _finish("stop")),
             lambda: sse(_delta(content="ok again"), _finish("stop")))
    provider = OpenAICompatProvider(CFG, up.client())
    first = await _collect(provider.stream_chat("vendor/model", SYS, [TOOL], CallSettings()))
    assert FeatureDropped("system") in first and FeatureDropped("tools") in first
    second = await _collect(provider.stream_chat("vendor/model", SYS, [TOOL], CallSettings()))
    assert not [c for c in second if isinstance(c, FeatureDropped)]
    last = up.bodies("/api/v1/chat/completions")[3]
    assert [m["role"] for m in last["messages"]] == ["user"] and "tools" not in last
