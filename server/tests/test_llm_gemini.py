"""The Gemini adapter (kind `gemini`): Google's Interactions API, stateless, streamed.

Wire shapes are the ones in Google's reference (ai.google.dev, read 2026-11-11): SSE events
`step.start` / `step.delta` / `step.stop` / `interaction.completed`, `store: false`, and thought steps
whose signature has to go back with the function call they led to.
"""
import json

import httpx
import pytest

from server.llm.providers.base import ProviderConfig, safe_error_message
from server.llm.providers.gemini import DUMMY_SIGNATURE, GeminiProvider
from server.llm.router import Router, load_provider_configs
from server.llm.types import (Attachment, CallSettings, FeatureDropped, Finish, Message, ProviderError,
                              ProviderUnavailable, ReasoningDelta, TextDelta, ToolCall, ToolCallReady,
                              ToolSpec, Usage)
from server.tests.llm_fakes import FakeUpstream, sse

KEY = "AIzaSyTEST-key_0123456789abcdefghijklmn"
CFG = ProviderConfig(name="google", kind="gemini", url="http://g.test/v1beta", api_key=KEY)
TOOL = ToolSpec("web_search", "Search the web", {"type": "object", "properties": {"query": {"type": "string"}}})
HI = [Message("user", "hi")]
MODELS = {"models": [
    {"name": "models/gemini-x-flash", "inputTokenLimit": 1048576, "thinking": True,
     "supportedGenerationMethods": ["generateContent", "countTokens"]},
    {"name": "models/gemma-x-it", "inputTokenLimit": 131072,
     "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-x-flash-tts", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-embedding-x", "supportedGenerationMethods": ["embedContent"]},
]}
CHAT = "/v1beta/interactions"


def _up(*chat, models=MODELS):
    return (FakeUpstream()
            .on("GET", "/v1beta/models", lambda: httpx.Response(200, json=models))
            .on("POST", CHAT, *chat))


def _start(i, **step):
    return {"event_type": "step.start", "index": i, "step": step}


def _delta(i, **delta):
    return {"event_type": "step.delta", "index": i, "delta": delta}


def _stop(i):
    return {"event_type": "step.stop", "index": i}


def _done(status="completed", **usage):
    usage = usage or {"total_input_tokens": 7, "total_output_tokens": 20, "total_thought_tokens": 22}
    return {"event_type": "interaction.completed", "interaction": {"status": status, "usage": usage}}


def _text(i, *pieces):
    return [_start(i, type="model_output"), *(_delta(i, type="text", text=p) for p in pieces), _stop(i)]


def _thought(i, signature, summary=None):
    events = [_start(i, type="thought")]
    if summary:
        events.append(_delta(i, type="thought_summary", content={"type": "text", "text": summary}))
    return [*events, _delta(i, type="thought_signature", signature=signature), _stop(i)]


def _call(i, call_id, name, *fragments):
    return [_start(i, type="function_call", id=call_id, name=name, arguments={}),
            *(_delta(i, type="arguments_delta", arguments=f) for f in fragments), _stop(i)]


async def _collect(agen):
    return [c async for c in agen]


async def _chat(up, messages=HI, tools=(), settings=CallSettings(), model="gemini-x-flash"):
    return await _collect(GeminiProvider(CFG, up.client()).stream_chat(model, list(messages), list(tools), settings))


# ---------- the request ----------

async def test_nothing_is_stored_at_google_and_the_key_travels_in_a_header():
    up = _up(lambda: sse(*_text(0, "Hello"), _done()))
    await _chat(up)
    request = [r for r in up.requests if r.url.path == CHAT][0]
    body = json.loads(request.content)
    assert body["store"] is False        # the default keeps every chat at Google for up to 55 days
    assert body["stream"] is True
    assert body["model"] == "gemini-x-flash"
    assert request.headers["x-goog-api-key"] == KEY
    assert KEY not in str(request.url) and "authorization" not in request.headers


async def test_the_system_message_is_the_instruction_and_the_rest_are_steps():
    up = _up(lambda: sse(*_text(0, "ok"), _done()))
    await _chat(up, [Message("system", "Be brief."), Message("user", "hi"),
                     Message("assistant", "Hello"), Message("user", "again")])
    body = up.bodies(CHAT)[0]
    assert body["system_instruction"] == "Be brief."
    assert body["input"] == [
        {"type": "user_input", "content": [{"type": "text", "text": "hi"}]},
        {"type": "model_output", "content": [{"type": "text", "text": "Hello"}]},
        {"type": "user_input", "content": [{"type": "text", "text": "again"}]}]


async def test_pictures_and_audio_go_inline_with_their_type():
    up = _up(lambda: sse(*_text(0, "ok"), _done()))
    await _chat(up, [Message("user", "what is this", attachments=(
        Attachment("image", "image/png", "AAAA"), Attachment("audio", "audio/x-wav", "BBBB"),
        Attachment("audio", "audio/mpeg", "CCCC")))])
    assert up.bodies(CHAT)[0]["input"][0]["content"] == [
        {"type": "text", "text": "what is this"},
        {"type": "image", "mime_type": "image/png", "data": "AAAA"},
        {"type": "audio", "mime_type": "audio/wav", "data": "BBBB"},
        {"type": "audio", "mime_type": "audio/mp3", "data": "CCCC"}]


async def test_tools_and_the_reply_limits_are_sent():
    up = _up(lambda: sse(*_text(0, "ok"), _done()))
    await _chat(up, tools=[TOOL], settings=CallSettings(num_predict=256, temperature=0.2, num_ctx=4096))
    body = up.bodies(CHAT)[0]
    assert body["tools"] == [{"type": "function", "name": "web_search", "description": "Search the web",
                              "parameters": TOOL.parameters}]
    assert body["generation_config"]["max_output_tokens"] == 256
    assert body["generation_config"]["temperature"] == 0.2
    assert "num_ctx" not in json.dumps(body)


@pytest.mark.parametrize("think,level,summaries", [
    ("off", "minimal", "none"),    # Gemini has no "off": minimal is "little to no thinking"
    ("on", None, "auto"),          # the model's own default level
    ("low", "low", "auto"), ("medium", "medium", "auto"), ("high", "high", "auto")])
async def test_thinking_levels(think, level, summaries):
    up = _up(lambda: sse(*_text(0, "ok"), _done()))
    await _chat(up, settings=CallSettings(think=think))
    config = up.bodies(CHAT)[0]["generation_config"]
    assert config.get("thinking_level") == level
    assert config["thinking_summaries"] == summaries


async def test_a_model_without_thinking_gets_no_thinking_settings():
    up = _up(lambda: sse(*_text(0, "ok"), _done()),
             models={"models": [{"name": "models/plain", "thinking": False,
                                 "supportedGenerationMethods": ["generateContent"]}]})
    await _chat(up, settings=CallSettings(think="high"), model="plain")
    assert "thinking" not in json.dumps(up.bodies(CHAT)[0])


# ---------- the reply ----------

async def test_streams_reasoning_text_usage_and_finish():
    up = _up(lambda: sse(*_thought(0, "SIG", "Let me think."), *_text(1, "Hel", "lo"), _done()))
    assert await _chat(up, settings=CallSettings(think="on")) == [
        ReasoningDelta("Let me think."), TextDelta("Hel"), TextDelta("lo"),
        Usage(7, 42), Finish("stop")]     # thought tokens are billed as output: 20 + 22


async def test_a_reply_cut_at_the_limit_finishes_as_length():
    up = _up(lambda: sse(*_text(0, "Hel"), _done("incomplete")))
    assert (await _chat(up))[-1] == Finish("length")


async def test_fragmented_parallel_tool_calls_are_assembled():
    up = _up(lambda: sse(*_thought(0, "SIG-A"),
                         *_call(1, "c1", "web_search", "{\"qu", "ery\": \"x\"}"),
                         *_call(2, "c2", "web_search", "{\"query\": \"y\"}"),
                         _done("requires_action")))
    chunks = await _chat(up, tools=[TOOL])
    calls = [c.call for c in chunks if isinstance(c, ToolCallReady)]
    assert calls == [ToolCall("c1", "web_search", {"query": "x"}), ToolCall("c2", "web_search", {"query": "y"})]
    assert chunks[-1] == Finish("tool_calls")


async def test_tool_arguments_that_arrive_whole_are_taken_too():
    up = _up(lambda: sse(_start(0, type="function_call", id="c1", name="web_search", arguments={"query": "x"}),
                         _stop(0), _done("requires_action")))
    calls = [c.call for c in await _chat(up, tools=[TOOL]) if isinstance(c, ToolCallReady)]
    assert calls == [ToolCall("c1", "web_search", {"query": "x"})]


async def test_the_thought_goes_back_with_the_call_it_led_to():
    """Gemini refuses a function call of its own that comes back without the thought before it."""
    up = _up(lambda: sse(*_thought(0, "SIG-A", "I should search."), *_text(1, "Looking."),
                         *_call(2, "c1", "web_search", "{\"query\": \"x\"}"), _done("requires_action")),
             lambda: sse(*_text(0, "Found it."), _done()))
    provider = GeminiProvider(CFG, up.client())
    first = await _collect(provider.stream_chat("gemini-x-flash", HI, [TOOL], CallSettings(think="on")))
    call = next(c.call for c in first if isinstance(c, ToolCallReady))
    history = [*HI, Message("assistant", "Looking.", tool_calls=(call,)),
               Message("tool", "result text", tool_call_id="c1", name="web_search")]
    await _collect(provider.stream_chat("gemini-x-flash", history, [TOOL], CallSettings(think="on")))
    assert up.bodies(CHAT)[1]["input"][1:] == [
        {"type": "thought", "signature": "SIG-A", "summary": [{"type": "text", "text": "I should search."}]},
        {"type": "model_output", "content": [{"type": "text", "text": "Looking."}]},
        {"type": "function_call", "id": "c1", "name": "web_search", "arguments": {"query": "x"}},
        {"type": "function_result", "call_id": "c1", "name": "web_search",
         "result": [{"type": "text", "text": "result text"}]}]


async def test_a_signature_never_shows_in_a_repr_and_calls_still_compare_by_what_they_ask():
    up = _up(lambda: sse(*_thought(0, "SIG-SECRET"), *_call(1, "c1", "web_search", "{}"),
                         _done("requires_action")))
    call = next(c.call for c in await _chat(up, tools=[TOOL]) if isinstance(c, ToolCallReady))
    assert "SIG-SECRET" not in repr(call)
    assert call == ToolCall("c1", "web_search", {})


async def test_a_call_this_app_made_itself_carries_googles_skip_signature():
    """The document prefetch and the web-search consent put a tool call in the history that no model made."""
    up = _up(lambda: sse(*_text(0, "ok"), _done()))
    await _chat(up, [*HI, Message("assistant", "", tool_calls=(ToolCall("app1", "search_documents", {"q": "x"}),)),
                     Message("tool", "passages", tool_call_id="app1", name="search_documents")])
    steps = up.bodies(CHAT)[0]["input"]
    assert steps[1] == {"type": "thought", "signature": DUMMY_SIGNATURE}
    assert steps[2]["type"] == "function_call" and steps[3]["type"] == "function_result"


# ---------- errors ----------

async def test_a_stream_that_stops_before_the_end_is_unavailable():
    up = _up(lambda: sse(*_text(0, "Hel"), done=False))
    with pytest.raises(ProviderUnavailable):
        await _chat(up)


async def test_an_error_event_in_the_stream_is_a_provider_error():
    up = _up(lambda: sse(_start(0, type="model_output"),
                         {"event_type": "error", "error": {"message": "Deadline expired.", "code": "gateway_timeout"}}))
    with pytest.raises(ProviderError) as err:
        await _chat(up)
    assert err.value.status == 504 and "Deadline expired" in err.value.safe_message


async def test_a_failed_interaction_is_a_provider_error():
    up = _up(lambda: sse(_done("failed")))
    with pytest.raises(ProviderError):
        await _chat(up)


async def test_a_bad_key_reads_as_a_key_problem_and_is_not_retried():
    """Google answers a wrong key with 400, which would otherwise look like "this model rejects thinking"."""
    bad = {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                     "message": "API key not valid. Please pass a valid API key."}}
    up = _up(lambda: httpx.Response(400, json=bad))
    with pytest.raises(ProviderError) as err:
        await _chat(up, tools=[TOOL], settings=CallSettings(think="high"))
    assert err.value.status == 401 and err.value.explained
    assert len(up.bodies(CHAT)) == 1


async def test_a_rejected_thinking_level_is_retried_at_the_models_default():
    reject = {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "thinking_level minimal is not supported"}}
    up = _up(lambda: httpx.Response(400, json=reject), lambda: sse(*_text(0, "ok"), _done()))
    provider = GeminiProvider(CFG, up.client())
    chunks = await _collect(provider.stream_chat("gemini-x-flash", HI, [TOOL], CallSettings(think="off")))
    assert chunks[0] == FeatureDropped("think_level")
    first, second = up.bodies(CHAT)
    assert first["generation_config"]["thinking_level"] == "minimal"
    assert "thinking_level" not in second["generation_config"] and second["tools"]
    # Remembered for that level only: "high" is still asked for, "minimal" isn't sent again.
    await _collect(provider.stream_chat("gemini-x-flash", HI, [], CallSettings(think="off")))
    await _collect(provider.stream_chat("gemini-x-flash", HI, [], CallSettings(think="high")))
    assert "thinking_level" not in up.bodies(CHAT)[2]["generation_config"]
    assert up.bodies(CHAT)[3]["generation_config"]["thinking_level"] == "high"


async def test_rejected_tools_are_dropped_after_the_thinking_level():
    reject = {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "Function calling is not enabled"}}
    up = _up(lambda: httpx.Response(400, json=reject), lambda: sse(*_text(0, "ok"), _done()))
    provider = GeminiProvider(CFG, up.client())
    chunks = await _collect(provider.stream_chat("gemini-x-flash", HI, [TOOL], CallSettings(think="on")))
    assert chunks[0] == FeatureDropped("tools")
    assert "tools" not in up.bodies(CHAT)[1]
    assert (await provider.capabilities("gemini-x-flash")).tools is False


def test_a_google_key_is_redacted_from_an_error_a_provider_echoes():
    text = safe_error_message(f"bad request to /v1beta/models?key={KEY} with {KEY}")
    assert KEY not in text and "AIza" in text


# ---------- models ----------

async def test_lists_the_models_that_chat_without_the_models_prefix():
    provider = GeminiProvider(CFG, _up().client())
    assert await provider.list_models() == ["gemini-x-flash", "gemma-x-it"]   # no TTS, no embedder


async def test_an_allow_list_can_name_a_model_the_filter_would_hide():
    cfg = ProviderConfig(name="google", kind="gemini", url="http://g.test/v1beta", api_key=KEY,
                         models=("gemini-x-flash-tts",))
    assert "gemini-x-flash-tts" in await GeminiProvider(cfg, _up().client()).list_models()


async def test_capabilities_come_from_the_model_list():
    provider = GeminiProvider(CFG, _up().client())
    caps = await provider.capabilities("gemini-x-flash")
    assert (caps.thinking, caps.context_window, caps.tools) == (True, 1048576, None)
    assert (await provider.capabilities("gemma-x-it")).thinking is None


async def test_every_page_of_the_model_list_is_read():
    pages = [{"models": MODELS["models"][:1], "nextPageToken": "p2"}, {"models": MODELS["models"][1:2]}]
    up = FakeUpstream().on("GET", "/v1beta/models", *(lambda p=p: httpx.Response(200, json=p) for p in pages))
    assert await GeminiProvider(CFG, up.client()).list_models() == ["gemini-x-flash", "gemma-x-it"]
    assert up.requests[1].url.params["pageToken"] == "p2"


# ---------- the router ----------

def test_kind_gemini_is_a_provider_and_its_url_has_a_default():
    env = {"INFERENCE_PROVIDERS": "google", "INFERENCE_GOOGLE_KIND": "gemini", "INFERENCE_GOOGLE_API_KEY": KEY}
    (cfg,) = load_provider_configs(env)
    assert (cfg.kind, cfg.url) == ("gemini", "https://generativelanguage.googleapis.com/v1beta")
    router = Router([cfg], httpx.AsyncClient())
    assert isinstance(router.providers["google"], GeminiProvider)
    assert router.provider_kind("google:gemini-x-flash") == "gemini"
