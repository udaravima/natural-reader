# Chat Orchestrator Foundation (C1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the chat turn from the browser to the server. A provider layer (native Ollama + OpenAI-compatible) sits under a server-side orchestrator that does stage-0 work first, streams typed SSE events, and saves the conversation as it streams. Local mode is removed.

**Architecture:** `server/llm/` is the provider layer. It has one internal OpenAI-shaped format, one adapter per provider API, and a router that owns provider config and `provider:model` ids. `server/chat/` is the orchestrator. `context.py` does stage 0 (prefetch, time, prompt order, history budget). `tools/` holds the server-side tools, `store.py` holds all persistence on short connections, and `orchestrator.py` runs the step loop and yields events. `server/routers/chat_turns.py` handles HTTP and SSE framing. In the SPA, `src/lib/chatStream.js` reads the SSE, the pure reducer `src/lib/chatEvents.js` turns events into message state, and `useChatEngine.js` shrinks to session state plus the TTS queue.

**Tech Stack:** FastAPI + psycopg 3 (async pool) + pgvector + httpx on the backend. Tests use pytest + pytest-asyncio with `httpx.MockTransport` against the `natural_reader_test` Postgres DB. The frontend is React 19 + Vite, tested with vitest + @testing-library/react (jsdom).

**Spec:** `docs/superpowers/specs/2026-09-27-chat-orchestrator-design.md` (committed `9c5b38c`). Executors read the spec's section for their task before starting.

## Global Constraints

- **Open-source rule:** nothing deployment-specific is hardcoded. Every knob is an env var documented in `.env.example` with a safe default, and docs use example.com.
- **Git:** work on `development`; never push. **Every commit needs the user's explicit per-action permission.** Each task's "Commit" step means: stage the files, show `git diff --cached --stat`, ask the user, and commit only after they say yes. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Secrets:** API keys never appear in logs, SSE events, admin responses or `repr()`.
- **Logging:** no message text, prompts, attachment bytes or API keys at INFO or above (spec §10).
- **Refusals** use `server/http_errors.refusal(status, error, message, **extra)`. The SPA maps them in `src/lib/apiErrors.js`.
- **Event names and fields** are exactly spec §6, plus Rulings R1–R3 below. Every event carries `type` and `seq` (1, 2, 3… per turn). The stream ends with `data: [DONE]`.
- **Config defaults, verbatim from spec §9:** `CHAT_MAX_TOOL_ROUNDS=1`, `CHAT_PREFETCH_MIN_SCORE=0.75` (`1` disables prefetch), `CHAT_PREFETCH_K=4`, `CHAT_REPLY_RESERVE_TOKENS=2048`, `CHAT_ATTACHMENT_TOKEN_ESTIMATE=1500`, `CHAT_MAX_REQUEST_MB=25`. `INFERENCE_TIMEOUT_S=300` is an **idle** timeout in seconds (httpx `read`), not a reply-length cap.
- **Internal constants (spec §5.5, §7.3):** heartbeat every 15 s; a claim is stale after 60 s; partial content is flushed at most every 2 s.
- **Test commands:**
  - Backend: `.venv/bin/python -m pytest server/tests -q`. Postgres must be up: `env -u XDG_DATA_HOME podman-compose up -d postgres`, or `docker compose up -d postgres`.
  - Frontend: `npx vitest run`. Lint: `npm run lint`.
- **Journey rule:** a task that changes something a user sees names the control that performs each step. "Done" for C1 means Task 16's running-app walk passed.

## Rulings (spec gaps this plan decides; each is cheap to reverse)

| # | Ruling | Why | Cost if wrong |
|---|---|---|---|
| R1 | Add a `data-notice` event `{code, message}` (Vercel's `data-*` family) when an adapter drops a feature the provider rejected | Today those rejections are toasts ("This model rejected tools…"); the spec's event table has no way to say it | One extra event type to remove |
| R2 | `finish` also carries `stats`, the object saved on the message | The SPA renders stats and the truncation notice without re-fetching | One field |
| R3 | `tool-output-available.output` is the compact `result_summary`, not the full tool result | Full passages would bloat the stream; the UI shows only summaries today | The UI can't show raw results live |
| R4 | The turn body takes an optional `session: {pins}`, used **only** when the turn creates the session | Pins added before the first message used to ride the first `PUT`, which is being removed | One optional field |
| R5 | `/v1/inference/models` entries add `kind` (`ollama`\|`openai`) | Settings show only the knobs a provider supports (spec §8) | One field |
| R6 | `PUT /v1/chat/sessions/{id}` is replaced by a create-only `POST /v1/chat/sessions/import`, used only to continue a legacy browser-only (IndexedDB) chat | Today, editing a legacy chat forks it to Postgres via `PUT`. With history on the server, the old messages must reach it once | One endpoint |
| R7 | Audio attachments **stay disabled in the UI**, as today (`src/components/ChatView.jsx:111`: Ollama's image projector can't decode audio in `images`). Adapters still map audio | Enabling it needs a provider verified to accept audio; the spec's journey 5 assumed audio works today, and it doesn't | Journey 5's audio step is a follow-up; Task 16 says so |
| R8 | `chat_sessions.active_turn_id` = the streaming assistant message's id | One id identifies both the claim and the message recovery marks `aborted` | None |
| R9 | No base system prompt, because today has none. Volatile context (time, passages) is **one** `system` message placed right before the new user message | Parity; keeps the reusable prefix intact | A system prompt can be added later at the head |
| R10 | For `kind=openai`, `INFERENCE_<NAME>_URL` is the API base **including** the version path (`https://openrouter.example.com/api/v1`, `http://localhost:11434/v1`); the adapter appends `/chat/completions` and `/models` | That's how OpenRouter/vLLM/LiteLLM document their base URLs | Docs wording |
| R11 | Attachments on the wire use `mime` (spec §7.1); stored metadata keeps today's `mimeType` key, plus `ordinal` | Existing chips (`stripAttachmentData`) read `mimeType` | None |
| R12 | An adapter remembers a rejected feature only if the retry **without** it succeeds | An unrelated 400 (e.g. prompt too long) must never disable tools for the process lifetime | None |
| R13 | `describeRefusal(res, {notFound})` gains an optional 404 wording override. Chat passes "This chat doesn't exist or you don't have access." | `noticeFor(404)` is document-specific today | One option |
| R14 | Window for trimming = `settings.num_ctx` if set, else the model's reported context length | With `num_ctx` set, that is Ollama's real window | Trimming may be loose when `num_ctx` is unset |

## Review Focus

Most likely to bite a person first. Each line has a test in the owning task.

1. **A tool call with arguments that aren't valid JSON** (OpenAI streams arguments as fragments). The tool gets `{}` and returns an error to the model; the turn continues and ends in an answer. Tests: Task 2 `test_invalid_tool_arguments_become_empty_dict`, Task 8 `test_tool_error_is_reported_and_turn_continues`.
2. **The tab closes while a tool is running** (not only mid-stream). The message is saved `aborted` with its partial text and the session claim is released, so the next message isn't refused with 409. Test: Task 8 `test_cancel_during_tool_saves_aborted_and_releases_claim`.
3. **A garbage timezone from the browser** (`"Mars/Olympus"`, `""`, `"../etc"`). The time line falls back to UTC; no 500. Tests: Task 7 `test_time_line_falls_back_to_utc_on_bad_timezone`, Task 9 `test_bad_timezone_still_streams`.
4. **A second tab sends while the first is still streaming.** The second gets 409 `turn_in_progress`; the first turn's claim and message are untouched. Test: Task 9 `test_second_tab_gets_409_and_first_turn_is_untouched`.
5. **The chat is deleted while its reply streams.** The turn ends without an exception and nothing is resurrected. Tests: Task 5 `test_writes_after_session_delete_are_noops`, Task 8 `test_session_deleted_mid_turn_finishes_quietly`.

## File map

| File | Responsibility | Task |
|---|---|---|
| `server/llm/__init__.py`, `server/llm/types.py` | Internal format, stream chunks, errors | 1 |
| `server/llm/providers/__init__.py`, `providers/base.py` | Provider config, HTTP error mapping, feature memory, TTL cache, argument parsing | 1 |
| `server/llm/providers/ollama.py` | Native Ollama adapter | 1 |
| `server/llm/providers/openai_compat.py` | OpenAI-compatible adapter | 2 |
| `server/llm/router.py` | Provider config parsing, `provider:model` ids, allow-lists, lifecycle | 3 |
| `server/routers/inference.py` (modify) | `/models` from the router (Task 4); `/chat` deleted (Task 14) | 4, 14 |
| `server/routers/admin.py` (modify) | Provider list in the inference config view | 4 |
| `server/services/web_search.py` (modify) | Summaries via `router.complete` | 4 |
| `server/services/model_router.py` (modify) | Narrowed to non-chat config (spec §4.6) | 4, 14 |
| `server/sql/013_chat_orchestrator.sql` | Message status, claim columns, `chat_attachments` | 5 |
| `server/chat/__init__.py`, `server/chat/store.py` | All chat reads/writes, claims, recovery | 5 |
| `server/services/doc_search.py` | Shared document search + readable-doc lookup | 6 |
| `server/routers/docs.py` (modify) | `/search` uses `doc_search` | 6 |
| `server/chat/tools/__init__.py`, `tools/search_document.py`, `tools/web_search.py` | Server-side tools | 6 |
| `server/chat/config.py`, `server/chat/context.py` | Chat env config; stage 0 | 7 |
| `server/chat/orchestrator.py` | The turn loop | 8 |
| `server/routers/chat_turns.py`, `server/app.py` (modify) | `POST …/turns`, SSE framing, wiring, startup recovery | 9 |
| `server/routers/chat_sessions.py` (modify) | Message `status` in GET, lazy recovery, `POST /import`; `PUT` deleted in Task 14 | 10, 14 |
| `server/tests/llm_fakes.py`, `server/tests/chat_harness.py` | Test plumbing | 1, 5 |
| `server/tests/fixtures/chat_events/*.jsonl` | Contract fixtures shared with the SPA | 8 |
| `src/lib/chatStream.js`, `src/lib/chatEvents.js` | SSE reader; pure event reducer | 11 |
| `src/hooks/useChatEngine.js`, `src/lib/sessionStore.js`, `src/hooks/inference.js` (modify) | Engine on turns; no `PUT`; wire settings | 12 |
| `src/lib/modelIds.js`, `ChatSidebar.jsx`, `SettingsPage.jsx`, `ChatView.jsx`, `App.jsx`, `apiErrors.js`, `chatTransport.js` (modify) | Model picker, knobs, statuses, Local mode removal | 13 |
| deleted: `src/lib/chatTools/`, `src/hooks/chatHistory.js` (+tests), `src/components/chat/InferenceSourceSelect.jsx` (+test) | Browser loop and Local mode | 12, 13 |
| `.env.example`, `docs/*.md`, `CHANGELOG.md`, `README.md` | Docs | 15 |

---

### Task 1: Internal format and the native Ollama adapter

**Files:**
- Create: `server/llm/__init__.py`, `server/llm/types.py`, `server/llm/providers/__init__.py`, `server/llm/providers/base.py`, `server/llm/providers/ollama.py`
- Create: `server/tests/llm_fakes.py`
- Test: `server/tests/test_llm_ollama.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `server.llm.types`: `Attachment(kind, mime, base64, name="")`, `ToolCall(id, name, arguments: dict)`, `Message(role, content="", attachments=(), tool_calls=(), tool_call_id=None, name=None)`, `ToolSpec(name, description, parameters)`, `CallSettings(think="off", num_ctx=None, keep_alive=None, num_predict=None, temperature=None)`, `Capabilities(tools, thinking, vision, audio, context_window)` with `.to_json()` (every flag is `bool | None`; `None` = unknown), chunks `TextDelta(text)`, `ReasoningDelta(text)`, `ToolCallReady(call)`, `Usage(prompt_tokens, completion_tokens, estimated=False, detail={})`, `Finish(reason)`, `FeatureDropped(feature)`, `Chunk`, errors `ProviderUnavailable`, `ProviderTimeout`, `ProviderError(status, safe_message)`.
  - `server.llm.providers.base`: `ProviderConfig(name, kind, url, api_key="", models=None)`, `auth_headers(cfg)`, `safe_error_message(raw)`, `parse_arguments(raw) -> dict`, `FeatureMemory`, `TTLCache`, `open_stream(client, request)`, `iter_lines(resp)`, `is_feature_rejection(err)`.
  - `server.llm.providers.ollama.OllamaProvider(config, client)` with `list_models()`, `capabilities(model)`, `stream_chat(model, messages, tools, settings)` (async iterator of `Chunk`).

- [ ] **Step 1: Write the test fakes** — `server/tests/llm_fakes.py`:

```python
"""Programmable provider HTTP for adapter tests (httpx.MockTransport).

A route's responses are consumed in order; the last one repeats. Give
factories (lambdas), not Response objects: a streamed body can be read once.
An Exception instead of a factory is raised by the transport.
"""
from __future__ import annotations

import json

import httpx


def streamed(body: bytes, status: int = 200,
             content_type: str = "application/x-ndjson") -> httpx.Response:
    # An async-iterator body keeps the response unconsumed, like a real
    # streamed reply (Response(content=bytes) is eagerly read).
    async def gen():
        yield body
    return httpx.Response(status, content=gen(), headers={"content-type": content_type})


def ndjson(*payloads) -> httpx.Response:
    return streamed(b"".join(json.dumps(p).encode() + b"\n" for p in payloads))


def sse(*payloads, done: bool = True) -> httpx.Response:
    parts = [b"data: " + json.dumps(p).encode() + b"\n\n" for p in payloads]
    if done:
        parts.append(b"data: [DONE]\n\n")
    return streamed(b"".join(parts), content_type="text/event-stream")


class FakeUpstream:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], list] = {}

    def on(self, method: str, path: str, *responses) -> "FakeUpstream":
        self.routes.setdefault((method, path), []).extend(responses)
        return self

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.routes.get((request.method, request.url.path))
        if not queue:
            return httpx.Response(404, json={"error": f"no route {request.url.path}"})
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item()

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def bodies(self, path: str) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.url.path == path]
```

- [ ] **Step 2: Write the failing adapter tests** — `server/tests/test_llm_ollama.py`:

```python
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
```

- [ ] **Step 3: Run to confirm failure**

Run: `.venv/bin/python -m pytest server/tests/test_llm_ollama.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'server.llm'`.

- [ ] **Step 4: Write `server/llm/__init__.py` and `server/llm/providers/__init__.py`**

```python
"""Provider layer (C1 spec §4): one internal format, one adapter per provider API."""
```

```python
"""One module per provider API. See base.py for what they share."""
```

- [ ] **Step 5: Write `server/llm/types.py`**

```python
"""The internal format every server-side model call speaks (C1 spec §4.1).

Messages follow OpenAI's chat shape (what LiteLLM normalizes to), so the
OpenAI-compatible adapter is close to a pass-through and the Ollama adapter is
the one that translates. Nothing here knows about HTTP or any provider.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Union

Role = Literal["system", "user", "assistant", "tool"]
ThinkLevel = Literal["off", "on", "low", "medium", "high"]
FinishReason = Literal["stop", "length", "tool_calls", "error"]


@dataclass(frozen=True)
class Attachment:
    kind: Literal["image", "audio"]
    mime: str
    base64: str
    name: str = ""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str = ""
    attachments: tuple[Attachment, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None   # on role="tool": which call this answers
    name: str | None = None           # on role="tool": the tool's name (Ollama wants it)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class CallSettings:
    think: ThinkLevel = "off"
    num_ctx: int | None = None
    keep_alive: str | int | None = None
    num_predict: int | None = None
    temperature: float | None = None


@dataclass(frozen=True)
class Capabilities:
    """What a model can do. None = the provider didn't say; callers refuse
    only on an explicit False."""
    tools: bool | None = None
    thinking: bool | None = None
    vision: bool | None = None
    audio: bool | None = None
    context_window: int | None = None   # tokens

    def to_json(self) -> dict[str, Any]:
        return {"tools": self.tools, "thinking": self.thinking, "vision": self.vision,
                "audio": self.audio, "contextWindow": self.context_window}


# ---- stream chunks an adapter yields ----

@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ReasoningDelta:
    text: str


@dataclass(frozen=True)
class ToolCallReady:
    """A COMPLETE tool call: adapters assemble streamed argument fragments."""
    call: ToolCall


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    estimated: bool = False
    detail: dict[str, Any] = field(default_factory=dict)   # e.g. Ollama's *_duration, in ns


@dataclass(frozen=True)
class Finish:
    reason: FinishReason


@dataclass(frozen=True)
class FeatureDropped:
    """The provider rejected a feature; the adapter retried without it."""
    feature: Literal["tools", "thinking", "think_level"]


Chunk = Union[TextDelta, ReasoningDelta, ToolCallReady, Usage, Finish, FeatureDropped]


# ---- errors ----

class ProviderUnavailable(Exception):
    """Could not connect to the provider, or the stream broke off."""


class ProviderTimeout(Exception):
    """The provider sent nothing for INFERENCE_TIMEOUT_S seconds (an idle timeout)."""


class ProviderError(Exception):
    """The provider answered with an error. `safe_message` is the provider's
    own text, trimmed: never request headers, never keys."""

    def __init__(self, status: int, safe_message: str) -> None:
        super().__init__(f"HTTP {status}: {safe_message}")
        self.status = status
        self.safe_message = safe_message
```

- [ ] **Step 6: Write `server/llm/providers/base.py`**

```python
"""What every adapter shares: the provider config record, HTTP error mapping,
tool-argument parsing, and the per-model memory of rejected features (spec §4.2)."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol

import httpx

from ..types import (Capabilities, CallSettings, Chunk, Message, ProviderError,
                     ProviderTimeout, ProviderUnavailable, ToolSpec)

logger = logging.getLogger(__name__)

CAPABILITY_TTL_S = 300.0   # re-probe a model's capabilities after 5 minutes


@dataclass(frozen=True)
class ProviderConfig:
    name: str                               # lower-case; the model-id prefix
    kind: str                               # "ollama" | "openai"
    url: str                                # base URL, no trailing slash
    api_key: str = ""
    models: tuple[str, ...] | None = None   # allow-list; None = everything it lists

    def __repr__(self) -> str:   # never print the key
        return (f"ProviderConfig(name={self.name!r}, kind={self.kind!r}, "
                f"url={self.url!r}, models={self.models!r})")


class Provider(Protocol):
    config: ProviderConfig

    async def list_models(self) -> list[str]: ...

    async def capabilities(self, model: str) -> Capabilities: ...

    def stream_chat(self, model: str, messages: list[Message], tools: list[ToolSpec],
                    settings: CallSettings) -> AsyncIterator[Chunk]: ...


def auth_headers(cfg: ProviderConfig) -> dict[str, str]:
    return {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}


def safe_error_message(raw: bytes | str) -> str:
    """The provider's error text, trimmed to 300 chars."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    try:
        body = json.loads(text)
        err = body.get("error", body) if isinstance(body, dict) else body
        if isinstance(err, dict):
            err = err.get("message") or json.dumps(err)
        text = str(err)
    except ValueError:
        pass
    return text.strip()[:300]


def parse_arguments(raw: Any) -> dict[str, Any]:
    """Tool-call arguments as a dict. Invalid JSON becomes {}: the tool then
    reports what's missing to the model, which can recover (Review Focus 1)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            logger.warning("Tool call arguments were not valid JSON (%d chars)", len(raw))
    return {}


class FeatureMemory:
    """Features a provider rejected for a model, for the process lifetime.
    Recorded only after the retry WITHOUT the feature succeeded (ruling R12)."""

    def __init__(self) -> None:
        self._rejected: dict[str, set[str]] = {}

    def rejected(self, model: str, feature: str) -> bool:
        return feature in self._rejected.get(model, set())

    def remember(self, model: str, feature: str) -> None:
        self._rejected.setdefault(model, set()).add(feature)


class TTLCache:
    def __init__(self, ttl_s: float = CAPABILITY_TTL_S) -> None:
        self._ttl = ttl_s
        self._items: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any:
        hit = self._items.get(key)
        if hit is not None and time.monotonic() - hit[0] < self._ttl:
            return hit[1]
        return None

    def put(self, key: str, value: Any) -> None:
        self._items[key] = (time.monotonic(), value)


async def open_stream(client: httpx.AsyncClient, request: httpx.Request) -> httpx.Response:
    """Send `request` streaming. Returns a 2xx response the caller must close;
    a non-2xx is read, closed and raised as ProviderError."""
    try:
        resp = await client.send(request, stream=True)
    except httpx.ReadTimeout as e:
        raise ProviderTimeout() from e
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
        raise ProviderUnavailable(str(e) or type(e).__name__) from e
    except httpx.HTTPError as e:
        raise ProviderUnavailable(type(e).__name__) from e
    if resp.status_code >= 400:
        raw = await resp.aread()
        await resp.aclose()
        message = safe_error_message(raw)
        logger.warning("Provider answered HTTP %s: %s", resp.status_code, message)
        raise ProviderError(resp.status_code, message)
    return resp


async def iter_lines(resp: httpx.Response) -> AsyncIterator[str]:
    """Lines of a streamed body. A read that waits longer than the client's
    read timeout (INFERENCE_TIMEOUT_S, idle) becomes ProviderTimeout."""
    try:
        async for line in resp.aiter_lines():
            yield line
    except httpx.ReadTimeout as e:
        raise ProviderTimeout() from e
    except httpx.HTTPError as e:
        raise ProviderUnavailable(type(e).__name__) from e


def is_feature_rejection(err: ProviderError) -> bool:
    """A 4xx that may mean "this model can't take that feature". Auth, missing
    model and rate limits are never feature rejections."""
    return 400 <= err.status < 500 and err.status not in (401, 403, 404, 408, 429)
```

- [ ] **Step 7: Write `server/llm/providers/ollama.py`**

```python
"""Native Ollama adapter (spec §4.2): POST /api/chat (never /api/generate,
which skips the model's chat template), GET /api/tags, POST /api/show."""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import httpx

from ..types import (Capabilities, CallSettings, Chunk, FeatureDropped, Finish, Message,
                     ProviderError, ProviderUnavailable, ReasoningDelta, TextDelta, ToolCall,
                     ToolCallReady, ToolSpec, Usage)
from .base import (FeatureMemory, ProviderConfig, TTLCache, auth_headers, is_feature_rejection,
                   iter_lines, open_stream, parse_arguments, safe_error_message)

logger = logging.getLogger(__name__)

# 'off'/'on' send the boolean (byte-identical to the browser's old requests);
# the levels send their string.
_THINK_WIRE: dict[str, Any] = {"off": False, "on": True}
_DURATIONS = ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration")


def _wire_message(m: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.attachments:
        # Ollama's message has ONE binary field. Audio rides in `images` too,
        # today's convention (spec §4.2); see ruling R7.
        out["images"] = [a.base64 for a in m.attachments]
    if m.tool_calls:
        out["tool_calls"] = [{"function": {"name": c.name, "arguments": c.arguments}}
                             for c in m.tool_calls]
    if m.role == "tool" and m.name:
        out["tool_name"] = m.name
    return out


class OllamaProvider:
    def __init__(self, config: ProviderConfig, client: httpx.AsyncClient) -> None:
        self.config = config
        self._client = client
        self._features = FeatureMemory()
        self._caps = TTLCache()

    async def list_models(self) -> list[str]:
        try:
            resp = await self._client.get(f"{self.config.url}/api/tags",
                                          headers=auth_headers(self.config))
        except httpx.HTTPError as e:
            raise ProviderUnavailable(type(e).__name__) from e
        if resp.status_code != 200:
            raise ProviderError(resp.status_code, safe_error_message(resp.content))
        return [m["name"] for m in resp.json().get("models", [])
                if isinstance(m, dict) and m.get("name")]

    async def capabilities(self, model: str) -> Capabilities:
        cached = self._caps.get(model)
        if cached is not None:
            return cached
        try:
            resp = await self._client.post(f"{self.config.url}/api/show", json={"model": model},
                                           headers=auth_headers(self.config))
        except httpx.HTTPError:
            return Capabilities()   # unknown, and not cached: the next call probes again
        if resp.status_code != 200:
            return Capabilities()
        body = resp.json()
        window = next((v for k, v in (body.get("model_info") or {}).items()
                       if k.endswith(".context_length") and isinstance(v, int)), None)
        listed = body.get("capabilities")
        if isinstance(listed, list):
            caps = Capabilities(tools="tools" in listed, thinking="thinking" in listed,
                                vision="vision" in listed, audio="audio" in listed,
                                context_window=window)
        else:   # an Ollama too old to list capabilities
            caps = Capabilities(context_window=window)
        self._caps.put(model, caps)
        return caps

    async def stream_chat(self, model: str, messages: list[Message], tools: list[ToolSpec],
                          settings: CallSettings) -> AsyncIterator[Chunk]:
        caps = await self.capabilities(model)
        think = self._think_value(model, settings, caps)
        use_tools = bool(tools) and caps.tools is not False and not self._features.rejected(model, "tools")
        dropped: list[str] = []
        while True:
            body = self._body(model, messages, tools if use_tools else [], settings, think)
            request = self._client.build_request("POST", f"{self.config.url}/api/chat", json=body,
                                                 headers=auth_headers(self.config))
            try:
                resp = await open_stream(self._client, request)
                break
            except ProviderError as err:
                if not is_feature_rejection(err):
                    raise
                # Today's order (the browser's retry chain): a think LEVEL first,
                # keeping tools, then tools.
                if isinstance(think, str):
                    think, feature = True, "think_level"
                elif use_tools:
                    use_tools, feature = False, "tools"
                else:
                    raise
                dropped.append(feature)
        for feature in dropped:   # the retry succeeded: now it's safe to remember
            self._features.remember(model, feature)
            logger.debug("Ollama model %s rejected %s; retried without it", model, feature)
            yield FeatureDropped(feature)
        async for chunk in self._read(resp):
            yield chunk

    def _think_value(self, model: str, settings: CallSettings, caps: Capabilities) -> Any:
        if caps.thinking is False:
            return None   # omit the field: a model without thinking may reject it
        value = _THINK_WIRE.get(settings.think, settings.think)
        if isinstance(value, str) and self._features.rejected(model, "think_level"):
            return True
        return value

    @staticmethod
    def _body(model: str, messages: list[Message], tools: list[ToolSpec],
              settings: CallSettings, think: Any) -> dict[str, Any]:
        body: dict[str, Any] = {"model": model, "stream": True,
                                "messages": [_wire_message(m) for m in messages]}
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools]
        if think is not None:
            body["think"] = think
        if settings.keep_alive is not None:
            body["keep_alive"] = settings.keep_alive
        options = {k: v for k, v in (("num_ctx", settings.num_ctx),
                                     ("num_predict", settings.num_predict),
                                     ("temperature", settings.temperature)) if v is not None}
        if options:
            body["options"] = options
        return body

    @staticmethod
    async def _read(resp: httpx.Response) -> AsyncIterator[Chunk]:
        n_calls = 0
        try:
            async for line in iter_lines(resp):
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except ValueError:
                    continue
                if payload.get("error"):
                    message = safe_error_message(json.dumps(payload))
                    logger.warning("Ollama stream error: %s", message)
                    raise ProviderError(500, message)
                msg = payload.get("message") or {}
                if msg.get("thinking"):
                    yield ReasoningDelta(msg["thinking"])
                if msg.get("content"):
                    yield TextDelta(msg["content"])
                for tc in msg.get("tool_calls") or []:
                    fn = (tc or {}).get("function") or {}
                    n_calls += 1
                    yield ToolCallReady(ToolCall(id=tc.get("id") or f"call_{n_calls}",
                                                 name=str(fn.get("name") or ""),
                                                 arguments=parse_arguments(fn.get("arguments"))))
                if payload.get("done"):
                    if "eval_count" in payload:
                        yield Usage(int(payload.get("prompt_eval_count") or 0),
                                    int(payload["eval_count"] or 0),
                                    detail={k: payload[k] for k in _DURATIONS if payload.get(k) is not None})
                    if n_calls:
                        yield Finish("tool_calls")
                    else:
                        yield Finish("length" if payload.get("done_reason") == "length" else "stop")
                    return
            raise ProviderUnavailable("stream ended before the reply finished")
        finally:
            await resp.aclose()
```

- [ ] **Step 8: Run the tests**

Run: `.venv/bin/python -m pytest server/tests/test_llm_ollama.py -q`
Expected: all pass (17 tests).

- [ ] **Step 9: Commit** (ask the user first)

```bash
git add server/llm server/tests/llm_fakes.py server/tests/test_llm_ollama.py
git commit -m "feat(llm): internal chat format and native Ollama adapter (C1)"
```

---

### Task 2: OpenAI-compatible adapter

**Files:**
- Create: `server/llm/providers/openai_compat.py`
- Test: `server/tests/test_llm_openai.py`

**Interfaces:**
- Consumes (Task 1): `server.llm.types.*`; `base.ProviderConfig`, `auth_headers`, `safe_error_message`, `parse_arguments`, `FeatureMemory`, `TTLCache`, `open_stream`, `iter_lines`, `is_feature_rejection`.
- Produces: `server.llm.providers.openai_compat.OpenAICompatProvider(config, client)` with the same three methods as `OllamaProvider`. `config.url` is the API base including the version path (ruling R10).

- [ ] **Step 1: Write the failing tests** — `server/tests/test_llm_openai.py`:

```python
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


async def test_list_models():
    provider = OpenAICompatProvider(CFG, _up(lambda: sse(_finish("stop"))).client())
    assert await provider.list_models() == ["vendor/model"]
```

- [ ] **Step 2: Run to confirm failure**

Run: `.venv/bin/python -m pytest server/tests/test_llm_openai.py -q`
Expected: `ModuleNotFoundError: No module named 'server.llm.providers.openai_compat'`.

- [ ] **Step 3: Write `server/llm/providers/openai_compat.py`**

```python
"""OpenAI-compatible adapter (spec §4.2): vLLM, OpenRouter, LiteLLM, and
Ollama's own /v1. `config.url` is the API base INCLUDING the version path
(ruling R10), e.g. https://openrouter.example.com/api/v1."""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import httpx

from ..types import (Capabilities, CallSettings, Chunk, FeatureDropped, Finish, Message,
                     ProviderError, ProviderUnavailable, ReasoningDelta, TextDelta, ToolCall,
                     ToolCallReady, ToolSpec, Usage)
from .base import (FeatureMemory, ProviderConfig, TTLCache, auth_headers, is_feature_rejection,
                   iter_lines, open_stream, parse_arguments, safe_error_message)

logger = logging.getLogger(__name__)

_EFFORT = {"on": "medium", "low": "low", "medium": "medium", "high": "high"}   # 'off' -> omitted
_AUDIO_FORMAT = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
                 "audio/mpeg": "mp3", "audio/mp3": "mp3"}


def _wire_message(m: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": m.role}
    if m.attachments and m.role == "user":
        parts: list[dict[str, Any]] = [{"type": "text", "text": m.content}] if m.content else []
        for a in m.attachments:
            if a.kind == "image":
                parts.append({"type": "image_url",
                              "image_url": {"url": f"data:{a.mime};base64,{a.base64}"}})
            elif a.mime in _AUDIO_FORMAT:
                parts.append({"type": "input_audio",
                              "input_audio": {"data": a.base64, "format": _AUDIO_FORMAT[a.mime]}})
            else:
                logger.warning("Audio type %s has no OpenAI input_audio format; not sent", a.mime)
        out["content"] = parts
    else:
        out["content"] = m.content
    if m.tool_calls:
        out["tool_calls"] = [{"id": c.id, "type": "function",
                              "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                             for c in m.tool_calls]
    if m.role == "tool":
        out["tool_call_id"] = m.tool_call_id
    return out


class OpenAICompatProvider:
    def __init__(self, config: ProviderConfig, client: httpx.AsyncClient) -> None:
        self.config = config
        self._client = client
        self._features = FeatureMemory()
        self._models = TTLCache()

    async def _entries(self) -> dict[str, dict]:
        cached = self._models.get("models")
        if cached is not None:
            return cached
        try:
            resp = await self._client.get(f"{self.config.url}/models", headers=auth_headers(self.config))
        except httpx.HTTPError as e:
            raise ProviderUnavailable(type(e).__name__) from e
        if resp.status_code != 200:
            raise ProviderError(resp.status_code, safe_error_message(resp.content))
        entries = {e["id"]: e for e in resp.json().get("data", [])
                   if isinstance(e, dict) and e.get("id")}
        self._models.put("models", entries)
        return entries

    async def list_models(self) -> list[str]:
        return list((await self._entries()).keys())

    async def capabilities(self, model: str) -> Capabilities:
        try:
            entry = (await self._entries()).get(model) or {}
        except (ProviderUnavailable, ProviderError):
            return Capabilities()
        params = entry.get("supported_parameters")
        modalities = (entry.get("architecture") or {}).get("input_modalities")
        window = entry.get("context_length") or entry.get("max_model_len")
        has_params, has_modalities = isinstance(params, list), isinstance(modalities, list)
        return Capabilities(
            tools=("tools" in params) if has_params else None,
            thinking=("reasoning" in params or "reasoning_effort" in params) if has_params else None,
            vision=("image" in modalities) if has_modalities else None,
            audio=("audio" in modalities) if has_modalities else None,
            context_window=window if isinstance(window, int) else None)

    async def stream_chat(self, model: str, messages: list[Message], tools: list[ToolSpec],
                          settings: CallSettings) -> AsyncIterator[Chunk]:
        caps = await self.capabilities(model)
        effort = None
        if caps.thinking is not False and not self._features.rejected(model, "thinking"):
            effort = _EFFORT.get(settings.think)
        use_tools = bool(tools) and caps.tools is not False and not self._features.rejected(model, "tools")
        dropped: list[str] = []
        while True:
            body = self._body(model, messages, tools if use_tools else [], settings, effort)
            request = self._client.build_request("POST", f"{self.config.url}/chat/completions",
                                                 json=body, headers=auth_headers(self.config))
            try:
                resp = await open_stream(self._client, request)
                break
            except ProviderError as err:
                if not is_feature_rejection(err):
                    raise
                if effort is not None:
                    effort, feature = None, "thinking"
                elif use_tools:
                    use_tools, feature = False, "tools"
                else:
                    raise
                dropped.append(feature)
        for feature in dropped:
            self._features.remember(model, feature)
            logger.debug("Model %s on %s rejected %s; retried without it",
                         model, self.config.name, feature)
            yield FeatureDropped(feature)
        async for chunk in self._read(resp):
            yield chunk

    @staticmethod
    def _body(model: str, messages: list[Message], tools: list[ToolSpec],
              settings: CallSettings, effort: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {"model": model, "stream": True,
                                "stream_options": {"include_usage": True},
                                "messages": [_wire_message(m) for m in messages]}
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools]
        if effort:
            body["reasoning_effort"] = effort
        if settings.num_predict is not None:
            body["max_tokens"] = settings.num_predict
        if settings.temperature is not None:
            body["temperature"] = settings.temperature
        # num_ctx and keep_alive have no OpenAI equivalent: ignored (spec §4.2).
        return body

    @staticmethod
    async def _read(resp: httpx.Response) -> AsyncIterator[Chunk]:
        calls: dict[int, dict[str, str]] = {}
        finish: str | None = None
        usage: Usage | None = None
        saw_done = False
        try:
            async for line in iter_lines(resp):
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    saw_done = True
                    break
                try:
                    payload = json.loads(data)
                except ValueError:
                    continue
                if payload.get("error"):
                    message = safe_error_message(json.dumps(payload))
                    logger.warning("Provider stream error: %s", message)
                    raise ProviderError(500, message)
                if payload.get("usage"):
                    u = payload["usage"]
                    usage = Usage(int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0))
                for choice in payload.get("choices") or []:
                    delta = choice.get("delta") or {}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        yield ReasoningDelta(reasoning)
                    if delta.get("content"):
                        yield TextDelta(delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        slot = calls.setdefault(int(tc.get("index", 0)), {"id": "", "name": "", "args": ""})
                        fn = tc.get("function") or {}
                        slot["id"] = slot["id"] or tc.get("id") or ""
                        slot["name"] = slot["name"] or fn.get("name") or ""
                        slot["args"] += fn.get("arguments") or ""
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
        finally:
            await resp.aclose()
        if not saw_done and finish is None:
            raise ProviderUnavailable("stream ended before the reply finished")
        for n, (_, slot) in enumerate(sorted(calls.items()), start=1):
            yield ToolCallReady(ToolCall(id=slot["id"] or f"call_{n}", name=slot["name"],
                                         arguments=parse_arguments(slot["args"])))
        if usage is not None:
            yield usage
        yield Finish("tool_calls" if calls else ("length" if finish == "length" else "stop"))
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest server/tests/test_llm_openai.py server/tests/test_llm_ollama.py -q`
Expected: all pass.

- [ ] **Step 5: Commit** (ask the user first)

```bash
git add server/llm/providers/openai_compat.py server/tests/test_llm_openai.py
git commit -m "feat(llm): OpenAI-compatible adapter (vLLM, OpenRouter, LiteLLM) (C1)"
```

---

### Task 3: Router and provider configuration

**Files:**
- Create: `server/llm/router.py`
- Test: `server/tests/test_llm_router.py`

**Interfaces:**
- Consumes: Tasks 1–2 adapters; `server.services.model_router.load_inference_config(env)` (existing; returns `ollama_url`, `allowed_models`, `timeout_s`).
- Produces:
  - `load_provider_configs(env) -> list[ProviderConfig]`
  - `ModelInfo(id, provider, kind, name, capabilities)` with `.to_json()` → `{id, provider, kind, name, capabilities}`
  - `UnknownModel(Exception)`
  - `Router(configs, client)` with:
    - `.providers: dict[str, Provider]`
    - `.has_providers() -> bool`
    - `.resolve(model_id) -> (Provider, name)`
    - `.canonical_id(model_id) -> str`
    - `.is_allowed(model_id) -> bool`
    - `async .capabilities(model_id) -> Capabilities`
    - `async .list_models() -> (list[ModelInfo], list[str] failed_provider_names)`
    - `.stream_chat(model_id, messages, tools, settings)` → async iterator of `Chunk`
    - `async .complete(model_id, messages, settings=CallSettings()) -> str`
    - `async .aclose()`
  - Module lifecycle: `async start_router(transport=None)`, `async stop_router()`, `get_router() -> Router` (raises `RuntimeError` before start).

- [ ] **Step 1: Write the failing tests** — `server/tests/test_llm_router.py`:

```python
import logging

import httpx
import pytest

from server.llm import router as llm_router
from server.llm.router import Router, UnknownModel, load_provider_configs
from server.llm.types import CallSettings, Message
from server.tests.llm_fakes import FakeUpstream, ndjson, sse

DONE = {"message": {"content": ""}, "done": True, "done_reason": "stop", "eval_count": 1, "prompt_eval_count": 1}


def test_no_config_is_one_ollama_provider_from_today_s_env():
    cfgs = load_provider_configs({"OLLAMA_URL": "http://gpu.example.com:11434/", "INFERENCE_MODELS": "a, b"})
    assert [(c.name, c.kind, c.url, c.models) for c in cfgs] == [
        ("ollama", "ollama", "http://gpu.example.com:11434", ("a", "b"))]


def test_two_providers_in_order_with_keys_and_allow_lists():
    env = {"INFERENCE_PROVIDERS": "local, open-router",
           "INFERENCE_LOCAL_KIND": "ollama", "INFERENCE_LOCAL_URL": "http://ollama.example.com:11434",
           "INFERENCE_OPEN_ROUTER_KIND": "openai",
           "INFERENCE_OPEN_ROUTER_URL": "https://openrouter.example.com/api/v1/",
           "INFERENCE_OPEN_ROUTER_API_KEY": "sk-1", "INFERENCE_OPEN_ROUTER_MODELS": "x/y"}
    cfgs = load_provider_configs(env)
    assert [(c.name, c.kind, c.url, c.api_key, c.models) for c in cfgs] == [
        ("local", "ollama", "http://ollama.example.com:11434", "", None),
        ("open-router", "openai", "https://openrouter.example.com/api/v1", "sk-1", ("x/y",))]


def test_invalid_providers_are_skipped_with_a_warning(caplog):
    env = {"INFERENCE_PROVIDERS": "good,badkind,nourl,Bad Name,good",
           "INFERENCE_GOOD_KIND": "ollama", "INFERENCE_GOOD_URL": "http://a.example.com",
           "INFERENCE_BADKIND_KIND": "anthropic", "INFERENCE_BADKIND_URL": "http://b.example.com",
           "INFERENCE_NOURL_KIND": "openai"}
    with caplog.at_level(logging.WARNING):
        cfgs = load_provider_configs(env)
    assert [c.name for c in cfgs] == ["good"]
    assert "badkind" in caplog.text and "nourl" in caplog.text


def _router():
    configs = load_provider_configs({
        "INFERENCE_PROVIDERS": "local,cloud",
        "INFERENCE_LOCAL_KIND": "ollama", "INFERENCE_LOCAL_URL": "http://ollama.test",
        "INFERENCE_CLOUD_KIND": "openai", "INFERENCE_CLOUD_URL": "http://cloud.test/v1",
        "INFERENCE_CLOUD_MODELS": "vendor/a"})
    up = (FakeUpstream()
          .on("GET", "/api/tags", lambda: httpx.Response(200, json={"models": [{"name": "qwen2.5:7b"}]}))
          .on("POST", "/api/show", lambda: httpx.Response(200, json={"capabilities": ["tools"]}))
          .on("POST", "/api/chat", lambda: ndjson({"message": {"content": "Hi "}},
                                                  {"message": {"content": "there"}}, DONE))
          .on("GET", "/v1/models", lambda: httpx.Response(200, json={"data": [{"id": "vendor/a"}, {"id": "vendor/b"}]})))
    return Router(configs, up.client()), up


def test_resolve_splits_at_the_first_colon():
    r, _ = _router()
    p, name = r.resolve("local:qwen2.5:7b")
    assert (p.config.name, name) == ("local", "qwen2.5:7b")
    p, name = r.resolve("cloud:vendor/a")
    assert (p.config.name, name) == ("cloud", "vendor/a")


def test_bare_pre_c1_names_go_to_the_first_ollama_provider():
    r, _ = _router()
    p, name = r.resolve("qwen2.5:7b")
    assert (p.config.name, name) == ("local", "qwen2.5:7b")
    assert r.canonical_id("qwen2.5:7b") == "local:qwen2.5:7b"


def test_no_ollama_provider_means_bare_names_are_unknown():
    cfgs = load_provider_configs({"INFERENCE_PROVIDERS": "cloud", "INFERENCE_CLOUD_KIND": "openai",
                                  "INFERENCE_CLOUD_URL": "http://cloud.test/v1"})
    with pytest.raises(UnknownModel):
        Router(cfgs, httpx.AsyncClient()).resolve("qwen2.5")


def test_allow_lists():
    r, _ = _router()
    assert r.is_allowed("local:anything")          # no allow-list: everything (dev, today's rule)
    assert r.is_allowed("cloud:vendor/a")
    assert not r.is_allowed("cloud:vendor/b")


async def test_list_models_filters_and_tags_ids_and_capabilities():
    r, _ = _router()
    models, failed = await r.list_models()
    assert failed == []
    assert [m.to_json()["id"] for m in models] == ["local:qwen2.5:7b", "cloud:vendor/a"]
    assert models[0].to_json()["kind"] == "ollama"
    assert models[0].capabilities.tools is True


async def test_one_failing_provider_is_omitted_and_reported():
    configs = load_provider_configs({
        "INFERENCE_PROVIDERS": "local,cloud",
        "INFERENCE_LOCAL_KIND": "ollama", "INFERENCE_LOCAL_URL": "http://ollama.test",
        "INFERENCE_CLOUD_KIND": "openai", "INFERENCE_CLOUD_URL": "http://cloud.test/v1"})
    up = (FakeUpstream()
          .on("GET", "/api/tags", lambda: httpx.Response(200, json={"models": [{"name": "m"}]}))
          .on("POST", "/api/show", lambda: httpx.Response(200, json={}))
          .on("GET", "/v1/models", httpx.ConnectError("down")))
    models, failed = await Router(configs, up.client()).list_models()
    assert [m.id for m in models] == ["local:m"] and failed == ["cloud"]


async def test_complete_concatenates_text():
    r, up = _router()
    assert await r.complete("local:qwen2.5:7b", [Message("user", "hi")]) == "Hi there"
    assert "think" not in up.bodies("/api/chat")[0]   # /api/show says no thinking: field omitted


async def test_lifecycle_uses_env_and_transport(monkeypatch):
    monkeypatch.delenv("INFERENCE_PROVIDERS", raising=False)
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.test")
    up = FakeUpstream().on("GET", "/api/tags", lambda: httpx.Response(200, json={"models": []}))
    await llm_router.start_router(transport=httpx.MockTransport(up._handle))
    try:
        assert list(llm_router.get_router().providers) == ["ollama"]
    finally:
        await llm_router.stop_router()
    with pytest.raises(RuntimeError):
        llm_router.get_router()
```

- [ ] **Step 2: Run to confirm failure**

Run: `.venv/bin/python -m pytest server/tests/test_llm_router.py -q`
Expected: `ModuleNotFoundError: No module named 'server.llm.router'`.

- [ ] **Step 3: Write `server/llm/router.py`**

```python
"""Which providers exist, and which one serves a model id (spec §4.3, §4.4, §4.6).

Model ids carry the provider name: `local:qwen2.5:7b`. The router splits at the
FIRST colon, because model names contain colons themselves. A name whose prefix
is not a provider (a pre-C1 session's plain `qwen2.5:7b`) goes to the first
provider of kind `ollama`. Trap: a provider named like a model family (`llama3`)
would capture `llama3:8b`; pick provider names that aren't model names.

With INFERENCE_PROVIDERS unset, there is exactly one provider, `ollama`, built
from today's OLLAMA_URL + INFERENCE_MODELS through model_router, so those
variables keep a single parser.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import AsyncIterator, Mapping

import httpx

from ..services import model_router
from .providers.base import Provider, ProviderConfig
from .providers.ollama import OllamaProvider
from .providers.openai_compat import OpenAICompatProvider
from .types import Capabilities, CallSettings, Chunk, Message, TextDelta, ToolSpec

logger = logging.getLogger(__name__)

KINDS = {"ollama": OllamaProvider, "openai": OpenAICompatProvider}
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def load_provider_configs(env: Mapping[str, str]) -> list[ProviderConfig]:
    raw = (env.get("INFERENCE_PROVIDERS") or "").strip()
    if not raw:
        cfg = model_router.load_inference_config(env)
        return [ProviderConfig(name="ollama", kind="ollama", url=cfg.ollama_url,
                               models=cfg.allowed_models)]
    out: list[ProviderConfig] = []
    for name in (n.strip().lower() for n in raw.split(",")):
        if not name:
            continue
        if not _NAME_RE.match(name) or any(c.name == name for c in out):
            logger.warning("Skipping inference provider %r: invalid or duplicate name", name)
            continue
        key = name.upper().replace("-", "_")
        kind = (env.get(f"INFERENCE_{key}_KIND") or "").strip().lower()
        url = (env.get(f"INFERENCE_{key}_URL") or "").strip().rstrip("/")
        if kind not in KINDS or not url:
            logger.warning("Skipping inference provider %r: INFERENCE_%s_KIND must be ollama or "
                           "openai, and INFERENCE_%s_URL is required", name, key, key)
            continue
        models_raw = (env.get(f"INFERENCE_{key}_MODELS") or "").strip()
        models = tuple(m.strip() for m in models_raw.split(",") if m.strip()) if models_raw else None
        out.append(ProviderConfig(name=name, kind=kind, url=url, models=models,
                                  api_key=(env.get(f"INFERENCE_{key}_API_KEY") or "").strip()))
    return out


@dataclass(frozen=True)
class ModelInfo:
    id: str
    provider: str
    kind: str
    name: str
    capabilities: Capabilities

    def to_json(self) -> dict:
        return {"id": self.id, "provider": self.provider, "kind": self.kind,
                "name": self.name, "capabilities": self.capabilities.to_json()}


class UnknownModel(Exception):
    pass


class Router:
    def __init__(self, configs: list[ProviderConfig], client: httpx.AsyncClient) -> None:
        self._client = client
        self.providers: dict[str, Provider] = {c.name: KINDS[c.kind](c, client) for c in configs}

    def has_providers(self) -> bool:
        return bool(self.providers)

    def resolve(self, model_id: str) -> tuple[Provider, str]:
        prefix, sep, rest = model_id.partition(":")
        if sep and rest and prefix in self.providers:
            return self.providers[prefix], rest
        for provider in self.providers.values():
            if provider.config.kind == "ollama":
                return provider, model_id
        raise UnknownModel(model_id)

    def canonical_id(self, model_id: str) -> str:
        provider, name = self.resolve(model_id)
        return f"{provider.config.name}:{name}"

    def is_allowed(self, model_id: str) -> bool:
        try:
            provider, name = self.resolve(model_id)
        except UnknownModel:
            return False
        return provider.config.models is None or name in provider.config.models

    async def capabilities(self, model_id: str) -> Capabilities:
        provider, name = self.resolve(model_id)
        return await provider.capabilities(name)

    async def list_models(self) -> tuple[list[ModelInfo], list[str]]:
        async def one(p: Provider) -> list[ModelInfo] | None:
            try:
                names = await p.list_models()
            except Exception as e:  # noqa: BLE001 — one bad provider must not hide the others
                logger.warning("Provider %s could not list models: %s", p.config.name, type(e).__name__)
                return None
            if p.config.models is not None:
                names = [n for n in names if n in p.config.models]
            caps = await asyncio.gather(*(p.capabilities(n) for n in names))
            return [ModelInfo(f"{p.config.name}:{n}", p.config.name, p.config.kind, n, c)
                    for n, c in zip(names, caps)]

        providers = list(self.providers.values())
        results = await asyncio.gather(*(one(p) for p in providers))
        models: list[ModelInfo] = []
        failed: list[str] = []
        for p, r in zip(providers, results):
            if r is None:
                failed.append(p.config.name)
            else:
                models.extend(r)
        return models, failed

    def stream_chat(self, model_id: str, messages: list[Message], tools: list[ToolSpec],
                    settings: CallSettings) -> AsyncIterator[Chunk]:
        provider, name = self.resolve(model_id)
        return provider.stream_chat(name, messages, tools, settings)

    async def complete(self, model_id: str, messages: list[Message],
                       settings: CallSettings = CallSettings()) -> str:
        parts: list[str] = []
        async for chunk in self.stream_chat(model_id, messages, [], settings):
            if isinstance(chunk, TextDelta):
                parts.append(chunk.text)
        return "".join(parts)

    async def aclose(self) -> None:
        await self._client.aclose()


_router: Router | None = None


async def start_router(transport: httpx.AsyncBaseTransport | None = None) -> None:
    """App startup. `transport` lets tests inject an httpx.MockTransport."""
    global _router
    if _router is not None:
        return
    timeout_s = model_router.get_config().timeout_s
    # read= is httpx's IDLE timeout (time between chunks): a slow but
    # streaming reply is never cut off; a silent provider is (spec §4.4).
    client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(
        connect=5.0, read=timeout_s, write=30.0, pool=5.0))
    _router = Router(load_provider_configs(os.environ), client)
    names = ", ".join(f"{p.config.name} ({p.config.kind})" for p in _router.providers.values())
    logger.info("Inference providers: %s", names or "none")


async def stop_router() -> None:
    global _router
    if _router is not None:
        await _router.aclose()
        _router = None


def get_router() -> Router:
    if _router is None:
        raise RuntimeError("Inference router not started — call start_router() first")
    return _router
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest server/tests/test_llm_router.py -q`
Expected: all pass.

- [ ] **Step 5: Commit** (ask the user first)

```bash
git add server/llm/router.py server/tests/test_llm_router.py
git commit -m "feat(llm): provider router, provider:model ids and env config (C1)"
```

---

### Task 4: Callers use the router — `/v1/inference/models`, admin view, web-search summaries, app wiring

**Files:**
- Modify: `server/routers/inference.py` (the `list_models` handler only; `/chat` stays until Task 14)
- Modify: `server/routers/admin.py:262-278` (`inference_config`)
- Modify: `server/services/web_search.py:166-181` (`summarize_one`)
- Modify: `server/app.py` (start/stop the router)
- Modify tests: `server/tests/test_inference_router.py` (models tests), `server/tests/test_web_search.py` (`test_summarize_one_calls_ollama_generate`), `server/tests/test_admin_lifecycle.py` (inference config tests)

**Interfaces:**
- Consumes (Task 3): `get_router()`, `Router.list_models()`, `Router.has_providers()`, `Router.complete()`, `start_router`, `stop_router`; `server.llm.types.Message`, `CallSettings`.
- Produces:
  - `GET /v1/inference/models` → `{"models": [{id, provider, kind, name, capabilities}], "budget"?: {...}}`.
    - No providers → 503 `no_providers`.
    - Every provider failed → 502 `providers_unreachable`.
  - `GET /v1/admin/inference/config` gains `"providers": [{name, kind, url_host, models}]`, where `models` is a count, or `null` if unreachable.

- [ ] **Step 1: Update the models tests to the new shape** — in `server/tests/test_inference_router.py`:

  (a) Make the `app` fixture start the LLM router on the same mock transport. Replace the fixture with:

```python
@pytest.fixture
async def app(db_conn, upstream, monkeypatch):
    from server.llm import router as llm_router
    application = FastAPI()
    application.include_router(inf.router)

    async def _conn():
        yield db_conn

    application.dependency_overrides[deps.get_conn] = _conn
    monkeypatch.delenv("INFERENCE_PROVIDERS", raising=False)
    await inf.start_client(transport=upstream["transport"])
    await llm_router.start_router(transport=upstream["transport"])
    yield application
    await llm_router.stop_router()
    await inf.stop_client()
```

  (b) Replace `test_models_lists_from_upstream`, `test_models_filtered_by_allowlist` and `test_models_upstream_down_is_502` with:

```python
def _tags_and_show(names):
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": n} for n in names] + [{"other": 1}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "tools"],
                                             "model_info": {"x.context_length": 4096}})
        return httpx.Response(404)
    return handler


async def test_models_lists_ids_with_provider_and_capabilities(client, upstream):
    upstream["handler"] = _tags_and_show(["gemma3", "qwen2.5"])
    r = await client.get("/v1/inference/models")
    assert r.status_code == 200
    models = r.json()["models"]
    assert [m["id"] for m in models] == ["ollama:gemma3", "ollama:qwen2.5"]
    assert models[0] == {"id": "ollama:gemma3", "provider": "ollama", "kind": "ollama", "name": "gemma3",
                         "capabilities": {"tools": True, "thinking": False, "vision": False,
                                          "audio": False, "contextWindow": 4096}}


async def test_models_filtered_by_allowlist(client, upstream, monkeypatch):
    from server.llm import router as llm_router
    monkeypatch.setenv("INFERENCE_MODELS", "gemma3")
    await llm_router.stop_router()
    await llm_router.start_router(transport=upstream["transport"])
    upstream["handler"] = _tags_and_show(["gemma3", "qwen2.5"])
    r = await client.get("/v1/inference/models")
    assert [m["id"] for m in r.json()["models"]] == ["ollama:gemma3"]


async def test_models_every_provider_down_is_502(client, upstream):
    def boom(request):
        raise httpx.ConnectError("nope")
    upstream["handler"] = boom
    r = await client.get("/v1/inference/models")
    assert r.status_code == 502
    assert r.json()["detail"]["error"] == "providers_unreachable"
```

  Leave `test_models_includes_budget` and `test_models_budget_absent_when_unlimited` as they are. The default handler returns `{"models": []}`, which is a successful empty list.

- [ ] **Step 2: Update the summary test** — in `server/tests/test_web_search.py`, replace `test_summarize_one_calls_ollama_generate` with:

```python
async def test_summarize_one_goes_through_the_router(monkeypatch):
    from server.llm.types import CallSettings

    calls = {}

    class _FakeRouter:
        async def complete(self, model_id, messages, settings=CallSettings()):
            calls["model"] = model_id
            calls["prompt"] = messages[0].content
            return "  A short summary.  "

    monkeypatch.setenv("SUMMARIZE_MODEL", "llama3.2:3b")
    monkeypatch.setattr(ws, "get_router", lambda: _FakeRouter())
    out = await ws.summarize_one("what is x", "long page text about x")
    assert out == "A short summary."
    assert calls["model"] == "llama3.2:3b"            # bare name: the router sends it to the first ollama provider
    assert "what is x" in calls["prompt"] and "long page text about x" in calls["prompt"]
```

  (`ws` is the module alias that file already uses for `server.services.web_search`. If the file imports it differently, keep its existing alias.)

- [ ] **Step 3: Admin view tests** — in `server/tests/test_admin_lifecycle.py`, the config tests now need a router. Add this autouse fixture near the top, after the imports; it serves every test in the file, and the admin routes that don't use the router ignore it:

```python
@pytest.fixture(autouse=True)
def _no_providers(monkeypatch):
    class _EmptyRouter:
        providers = {}

        async def list_models(self):
            return [], []

    monkeypatch.setattr(admin_router, "get_router", lambda: _EmptyRouter())
```

  Then append, under the `GET /v1/admin/inference/config` heading:

```python
async def test_config_lists_providers_without_keys(db_conn, monkeypatch):
    from server.llm.providers.base import ProviderConfig
    from server.llm.router import ModelInfo
    from server.llm.types import Capabilities

    class _P:
        def __init__(self, name, kind, url):
            self.config = ProviderConfig(name=name, kind=kind, url=url, api_key="sk-secret")

    class _R:
        providers = {"local": _P("local", "ollama", "http://gpu.example.com:11434"),
                     "cloud": _P("cloud", "openai", "https://openrouter.example.com/api/v1")}

        async def list_models(self):
            return [ModelInfo("local:m", "local", "ollama", "m", Capabilities())], ["cloud"]

    monkeypatch.setattr(admin_router, "get_router", lambda: _R())
    admin, p = await _admin(db_conn)
    async with _client(_app(db_conn, p)) as client:
        body = (await client.get("/v1/admin/inference/config")).json()
    assert body["providers"] == [
        {"name": "local", "kind": "ollama", "url_host": "gpu.example.com", "models": 1},
        {"name": "cloud", "kind": "openai", "url_host": "openrouter.example.com", "models": None},
    ]
    assert "sk-secret" not in str(body)
```

- [ ] **Step 4: Run to confirm failures**

Run: `.venv/bin/python -m pytest server/tests/test_inference_router.py server/tests/test_web_search.py server/tests/test_admin_lifecycle.py -q`
Expected: the new and updated tests fail (old response shape, no `get_router` in `web_search` or `admin`).

- [ ] **Step 5: Rewrite the `list_models` handler** — `server/routers/inference.py`. Add the imports `from ..http_errors import refusal` and `from ..llm.router import get_router`, then replace the whole `list_models` function with:

```python
@router.get("/models")
async def list_models(
    principal: deps.Principal = Depends(deps.require_capability("chat")),
    conn=Depends(deps.get_conn),
):
    """Every configured provider's models, tagged `provider:name`, with
    capabilities (spec §7.5). One failing provider is omitted; all failing is 502."""
    llm = get_router()
    if not llm.has_providers():
        raise refusal(503, "no_providers", "No model provider is configured.")
    models, failed = await llm.list_models()
    if failed and not models and len(failed) == len(llm.providers):
        raise refusal(502, "providers_unreachable", "Can't reach any model provider.")
    out: dict = {"models": [m.to_json() for m in models]}
    cfg = model_router.get_config()
    try:
        out["budget"] = await inference_budget.budget_state(
            conn, principal.user_id, cfg.daily_token_budget)
    except Exception:
        # Fail open — the model list must survive a Postgres outage.
        logger.warning("Budget lookup failed (fail-open)", exc_info=True)
    return out
```

- [ ] **Step 6: Admin view** — `server/routers/admin.py`. Add `from urllib.parse import urlsplit` and `from ..llm.router import get_router`, then replace `inference_config` with:

```python
@router.get("/inference/config")
async def inference_config(
    _: deps.Principal = Depends(deps.require_admin),
):
    """Read-only view of the effective inference config — why a model 422s,
    without shell access. Never includes API keys (spec §4.5)."""
    cfg = model_router.get_config()
    llm = get_router()
    models, failed = await llm.list_models()
    counts: dict[str, int] = {}
    for m in models:
        counts[m.provider] = counts.get(m.provider, 0) + 1
    return {
        "ollama_url": cfg.ollama_url,   # where embeddings run (spec §4.6)
        "timeout_s": cfg.timeout_s,
        "allowed_models": (
            list(cfg.allowed_models) if cfg.allowed_models is not None else None
        ),
        "summarize_model": cfg.summarize_model,
        "embed_model": cfg.embed_model,
        "daily_token_budget": cfg.daily_token_budget,
        "providers": [
            {"name": name, "kind": p.config.kind, "url_host": urlsplit(p.config.url).hostname,
             "models": None if name in failed else counts.get(name, 0)}
            for name, p in llm.providers.items()
        ],
    }
```

- [ ] **Step 7: Summaries through the router** — `server/services/web_search.py`. Add these imports at the top with the others:

```python
from ..llm.router import get_router
from ..llm.types import CallSettings, Message
```

  Replace `summarize_one` with:

```python
async def summarize_one(query: str, text: str) -> str:
    """Summarize one page's text with the summary model, through the provider
    router (spec §4.5) — any configured provider can serve it."""
    cfg = model_router.get_config()
    prompt = _SUMMARY_PROMPT.format(query=query, text=text)
    out = await asyncio.wait_for(
        get_router().complete(cfg.summarize_model, [Message("user", prompt)], CallSettings()),
        timeout=SUMMARY_TIMEOUT_S)
    return out.strip()
```

  (`_get_client()` is still used by `fetch_and_extract` and `searxng_search`. Keep it.)

- [ ] **Step 8: Wire the router into the app** — `server/app.py`. Add:

```python
from .llm.router import start_router as start_llm_router, stop_router as stop_llm_router
```

  In `_startup`, after `await start_inference()`, add `await start_llm_router()`. In `_shutdown`, add `await stop_llm_router()` as the first line.

- [ ] **Step 9: Run the affected tests, then the whole backend suite**

Run: `.venv/bin/python -m pytest server/tests -q && .venv/bin/python -c "import ast; ast.parse(open('server/app.py').read())"`
Expected: all pass. The suite deliberately never imports `server.app`, because importing it loads the 325 MB Kokoro model, so `app.py` wiring is only syntax-checked here. Task 16's running-app walk is where it gets exercised.

- [ ] **Step 10: Commit** (ask the user first)

```bash
git add server/routers/inference.py server/routers/admin.py server/services/web_search.py server/app.py server/tests/test_inference_router.py server/tests/test_web_search.py server/tests/test_admin_lifecycle.py
git commit -m "feat(llm): model list, admin view and web-search summaries go through the provider router (C1)"
```

---

### Task 5: Migration `013` and the chat store

**Files:**
- Create: `server/sql/013_chat_orchestrator.sql`
- Create: `server/chat/__init__.py`, `server/chat/store.py`
- Create: `server/tests/chat_harness.py`
- Test: `server/tests/test_migration_013.py`, `server/tests/test_chat_store.py`

**Interfaces:**
- Consumes: `server.db.get_pool`; `server.llm.types.Attachment` (Task 1).
- Produces (`server.chat.store`):
  - Constants: `HEARTBEAT_S = 15.0`, `STALE_AFTER_S = 60`, `FLUSH_EVERY_S = 2.0`.
  - Exceptions: `NotOwner`, `TurnInProgress`.
  - Records:
    - `NewAttachment(kind, mime, name, data: bytes, client_id=None)` with `.size`
    - `TurnClaim(session_id, user_message_id, assistant_message_id, created_session)` with `.turn_id`
    - `StoredMessage(id, role, content, attachments: list[dict])`
    - `SessionInfo(owner_id, pins)`
  - Helpers: `now_ms()`, `new_message_id(prefix)`, `title_from_prompt(text)`.
  - Async functions:
    - `session_info(session_id) -> SessionInfo | None`
    - `begin_turn(*, session_id, user_id, model_id, text, attachments, new_session_pins) -> TurnClaim`
    - `heartbeat(session_id, turn_id) -> bool`
    - `save_progress(message_id, *, content, thinking, tool_calls, doc_context)`
    - `finish_turn(claim, *, status, finish_reason, content, thinking, tool_calls, doc_context, stats)`
    - `add_event(session_id, kind, message)`
    - `load_turn_context(session_id, *, exclude) -> (pins, list[StoredMessage])`
    - `load_attachment_bytes(wanted: list[tuple[str, int]]) -> dict[tuple[str, int], Attachment]`
    - `recover_stale(session_id=None) -> int`
  - `server.tests.chat_harness`: `PoolShim(conn)`, `shim_pool(monkeypatch, conn, *modules)`, `member(conn, sub, caps=("chat",)) -> Principal`.

- [ ] **Step 1: Write the migration test** — `server/tests/test_migration_013.py`:

```python
"""Migration 013 (C1): chat message status, the one-turn-per-session claim,
and stored attachment bytes. Additive only: existing messages become
'complete' and nothing is rewritten."""
import secrets

import pytest
from psycopg import AsyncConnection
from psycopg.errors import CheckViolation

from server.tests.dbutil import ADMIN_URL, SQL_DIR, apply_migrations, url_for

pytestmark = pytest.mark.asyncio


async def _one(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def test_013_is_additive_and_cascades():
    name = f"natural_reader_mig_{secrets.token_hex(4)}"
    admin = await AsyncConnection.connect(ADMIN_URL, autocommit=True)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        try:
            url = url_for(name)
            await apply_migrations(url, max_version=12)
            conn = await AsyncConnection.connect(url, autocommit=True)
            try:
                uid = (await _one(conn,
                    "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
                    "VALUES ('i','s','a@x.io','member','active') RETURNING id"))[0]
                await conn.execute("INSERT INTO chat_sessions (id, user_id) VALUES ('s-old', %s)", (uid,))
                await conn.execute(
                    "INSERT INTO chat_messages (id, session_id, role, content) "
                    "VALUES ('m-old', 's-old', 'assistant', 'kept')")

                async with conn.transaction():  # one transaction, like server/db.py
                    await conn.execute((SQL_DIR / "013_chat_orchestrator.sql").read_text())

                assert await _one(conn, "SELECT status, finish_reason, model, content "
                                        "FROM chat_messages WHERE id='m-old'") == ("complete", None, None, "kept")
                assert await _one(conn, "SELECT active_turn_id, active_turn_heartbeat_at "
                                        "FROM chat_sessions WHERE id='s-old'") == (None, None)
                with pytest.raises(CheckViolation):
                    await conn.execute("UPDATE chat_messages SET status='bogus' WHERE id='m-old'")
                await conn.execute(
                    "INSERT INTO chat_attachments (message_id, ordinal, kind, mime, name, size, data) "
                    "VALUES ('m-old', 0, 'image', 'image/png', 'a.png', 3, '\\x010203')")
                await conn.execute("DELETE FROM chat_sessions WHERE id='s-old'")
                assert (await _one(conn, "SELECT count(*) FROM chat_attachments"))[0] == 0
                assert (await _one(conn, "SELECT 1 FROM schema_migrations WHERE version = 13")) == (1,)
            finally:
                await conn.close()
        finally:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()
```

- [ ] **Step 2: Run to confirm failure**

Run: `.venv/bin/python -m pytest server/tests/test_migration_013.py -q`
Expected: FAIL, because `013_chat_orchestrator.sql` doesn't exist.

- [ ] **Step 3: Write `server/sql/013_chat_orchestrator.sql`**

```sql
-- C1: the server runs the chat turn (spec §5.5, §7.3, §7.4). Additive only.
--
-- chat_messages.status: 'streaming' while a turn writes it; 'complete',
-- 'aborted' (stop button, closed tab, or a worker that died mid-turn) or
-- 'error' when it ends. Every row that exists now is a finished message.
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'complete';
ALTER TABLE chat_messages DROP CONSTRAINT IF EXISTS chat_messages_status_check;
ALTER TABLE chat_messages ADD CONSTRAINT chat_messages_status_check
    CHECK (status IN ('streaming', 'complete', 'aborted', 'error'));
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS finish_reason TEXT;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS model TEXT;

-- One turn per session. active_turn_id is the streaming assistant message's
-- id; the running turn refreshes the heartbeat every 15 s. A claim whose
-- heartbeat is older than 60 s belongs to a dead worker and may be taken over.
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS active_turn_id TEXT;
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS active_turn_heartbeat_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS chat_sessions_active_turn_idx
    ON chat_sessions (active_turn_heartbeat_at) WHERE active_turn_id IS NOT NULL;

-- Attachment bytes, so a follow-up turn (and a reload) can send an earlier
-- image to the model again. Metadata stays in chat_messages.attachments.
-- Deleting the chat deletes them.
CREATE TABLE IF NOT EXISTS chat_attachments (
    id          BIGSERIAL PRIMARY KEY,
    message_id  TEXT NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    ordinal     INT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('image', 'audio')),
    mime        TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    size        INT NOT NULL,
    data        BYTEA NOT NULL,
    UNIQUE (message_id, ordinal)
);

INSERT INTO schema_migrations(version) VALUES (13) ON CONFLICT DO NOTHING;
```

- [ ] **Step 4: Run the migration test**

Run: `.venv/bin/python -m pytest server/tests/test_migration_013.py -q`
Expected: PASS.

- [ ] **Step 5: Write the test harness** — `server/tests/chat_harness.py`:

```python
"""Shared plumbing for C1 chat tests.

The chat code opens short pooled connections itself (spec §5.5). Tests hand
every such module ONE transactional test connection through a pool shim, so a
test sees its own writes and everything rolls back afterwards.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from server.auth import deps
from server.auth.users import resolve_or_provision_user, set_status


class PoolShim:
    def __init__(self, conn) -> None:
        self._conn = conn

    @asynccontextmanager
    async def connection(self):
        yield self._conn


def shim_pool(monkeypatch, conn, *modules) -> PoolShim:
    shim = PoolShim(conn)
    for mod in modules:
        monkeypatch.setattr(mod, "get_pool", lambda: shim)
        if hasattr(mod, "is_ready"):
            monkeypatch.setattr(mod, "is_ready", lambda: True)
    return shim


async def member(conn, sub: str, caps=("chat",)) -> deps.Principal:
    u = await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io")
    await set_status(conn, u["id"], "active")
    return deps.Principal(user_id=str(u["id"]), email=u["email"], role="member",
                          capabilities=frozenset(caps))
```

- [ ] **Step 6: Write the failing store tests** — `server/tests/test_chat_store.py`:

```python
import base64

import pytest

from server.chat import store
from server.llm.types import Attachment
from server.tests.chat_harness import member, shim_pool

IMG_A = store.NewAttachment(kind="image", mime="image/png", name="a.png", data=b"A", client_id="att-a")
IMG_B = store.NewAttachment(kind="image", mime="image/png", name="b.png", data=b"B", client_id="att-b")


@pytest.fixture
def conn(db_conn, monkeypatch):
    shim_pool(monkeypatch, db_conn, store)
    return db_conn


async def _one(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def _begin(user, sid="s-1", text="Hello there, world", atts=(), pins=None):
    return await store.begin_turn(session_id=sid, user_id=user.user_id, model_id="ollama:m",
                                  text=text, attachments=list(atts), new_session_pins=pins)


async def _finish(claim, content="", status="complete", reason="stop"):
    await store.finish_turn(claim, status=status, finish_reason=reason, content=content,
                            thinking="", tool_calls=[], doc_context=None, stats={})


def test_title_from_prompt():
    assert store.title_from_prompt("  a \n b ") == "a b"
    assert store.title_from_prompt("") == "New chat"
    assert store.title_from_prompt("x" * 80) == "x" * 59 + "…"


async def test_begin_turn_creates_session_messages_bytes_and_claim(conn):
    alice = await member(conn, "alice")
    claim = await _begin(alice, atts=[IMG_A], pins=[{"id": "pin-1", "text": "x"}])
    assert claim.created_session and claim.turn_id == claim.assistant_message_id
    assert await _one(conn, "SELECT title, model, pins, active_turn_id FROM chat_sessions WHERE id='s-1'") == (
        "Hello there, world", "ollama:m", [{"id": "pin-1", "text": "x"}], claim.turn_id)
    assert (await _one(conn, "SELECT attachments FROM chat_messages WHERE id=%s", (claim.user_message_id,)))[0] == [
        {"id": "att-a", "kind": "image", "name": "a.png", "mimeType": "image/png", "size": 1, "ordinal": 0}]
    assert (await _one(conn, "SELECT data FROM chat_attachments WHERE message_id=%s",
                       (claim.user_message_id,)))[0] == b"A"
    assert (await _one(conn, "SELECT status, role FROM chat_messages WHERE id=%s",
                       (claim.assistant_message_id,))) == ("streaming", "assistant")


async def test_existing_session_keeps_its_pins_and_title(conn):
    alice = await member(conn, "alice")
    await _finish(await _begin(alice, pins=[{"id": "p1"}]))
    await _begin(alice, text="another question", pins=[{"id": "p2"}])
    assert await _one(conn, "SELECT title, pins FROM chat_sessions WHERE id='s-1'") == (
        "Hello there, world", [{"id": "p1"}])


async def test_a_second_turn_is_refused_while_one_is_active(conn):
    alice = await member(conn, "alice")
    first = await _begin(alice)
    with pytest.raises(store.TurnInProgress):
        await _begin(alice, text="again")
    assert (await _one(conn, "SELECT active_turn_id FROM chat_sessions WHERE id='s-1'"))[0] == first.turn_id
    assert (await _one(conn, "SELECT count(*) FROM chat_messages WHERE session_id='s-1'"))[0] == 2


async def test_someone_elses_session_is_not_owner(conn):
    alice, bob = await member(conn, "alice"), await member(conn, "bob")
    await _begin(alice)
    with pytest.raises(store.NotOwner):
        await _begin(bob)
    assert (await _one(conn, "SELECT count(*) FROM chat_messages WHERE session_id='s-1'"))[0] == 2


async def test_a_stale_claim_is_taken_over_and_its_message_aborted(conn):
    alice = await member(conn, "alice")
    old = await _begin(alice)
    await conn.execute("UPDATE chat_sessions SET active_turn_heartbeat_at = now() - interval '5 minutes'")
    new = await _begin(alice, text="again")
    assert await _one(conn, "SELECT status, finish_reason FROM chat_messages WHERE id=%s",
                      (old.turn_id,)) == ("aborted", "aborted")
    assert (await _one(conn, "SELECT active_turn_id FROM chat_sessions WHERE id='s-1'"))[0] == new.turn_id


async def test_startup_recovery_only_touches_stale_claims(conn):
    alice = await member(conn, "alice")
    fresh, stale = await _begin(alice, sid="s-fresh"), await _begin(alice, sid="s-stale")
    await conn.execute("UPDATE chat_sessions SET active_turn_heartbeat_at = now() - interval '2 minutes' "
                       "WHERE id='s-stale'")
    assert await store.recover_stale() == 1
    assert (await _one(conn, "SELECT status FROM chat_messages WHERE id=%s", (fresh.turn_id,)))[0] == "streaming"
    assert (await _one(conn, "SELECT active_turn_id FROM chat_sessions WHERE id='s-fresh'"))[0] == fresh.turn_id
    assert (await _one(conn, "SELECT status FROM chat_messages WHERE id=%s", (stale.turn_id,)))[0] == "aborted"
    assert (await _one(conn, "SELECT active_turn_id FROM chat_sessions WHERE id='s-stale'"))[0] is None


async def test_heartbeat_refreshes_only_its_own_claim(conn):
    alice = await member(conn, "alice")
    claim = await _begin(alice)
    assert await store.heartbeat("s-1", claim.turn_id) is True
    assert await store.heartbeat("s-1", "a-someone-else") is False


async def test_finish_turn_saves_and_releases_its_claim(conn):
    alice = await member(conn, "alice")
    claim = await _begin(alice)
    await store.finish_turn(claim, status="complete", finish_reason="stop", content="Hi", thinking="",
                            tool_calls=[{"name": "x"}], doc_context={"notes": []}, stats={"model": "ollama:m"})
    assert await _one(conn, "SELECT status, finish_reason, content, thinking, tool_calls, doc_context, stats "
                            "FROM chat_messages WHERE id=%s", (claim.turn_id,)) == (
        "complete", "stop", "Hi", None, [{"name": "x"}], {"notes": []}, {"model": "ollama:m"})
    assert (await _one(conn, "SELECT active_turn_id FROM chat_sessions WHERE id='s-1'"))[0] is None


async def test_finishing_a_taken_over_turn_leaves_the_new_claim_alone(conn):
    alice = await member(conn, "alice")
    old = await _begin(alice)
    await conn.execute("UPDATE chat_sessions SET active_turn_heartbeat_at = now() - interval '5 minutes'")
    new = await _begin(alice, text="again")
    await _finish(old, content="late")
    assert (await _one(conn, "SELECT active_turn_id FROM chat_sessions WHERE id='s-1'"))[0] == new.turn_id


async def test_save_progress_writes_partial_content(conn):
    alice = await member(conn, "alice")
    claim = await _begin(alice)
    await store.save_progress(claim.turn_id, content="par", thinking="t", tool_calls=None,
                              doc_context={"notes": [{"kind": "trimmed"}]})
    assert await _one(conn, "SELECT content, thinking, doc_context, status FROM chat_messages WHERE id=%s",
                      (claim.turn_id,)) == ("par", "t", {"notes": [{"kind": "trimmed"}]}, "streaming")


async def test_history_excludes_current_turn_streaming_and_empty_replies(conn):
    alice = await member(conn, "alice")
    await conn.execute("INSERT INTO chat_sessions (id, user_id) VALUES ('s-1', %s)", (alice.user_id,))
    await conn.execute("INSERT INTO chat_messages (id, session_id, role, content, attachments, timestamp) "
                       "VALUES ('u-legacy', 's-1', 'user', 'legacy', '[{\"id\":\"x\",\"kind\":\"image\"}]', 1)")
    t1 = await _begin(alice, text="first", atts=[IMG_A])
    await _finish(t1, content="answer one")
    t2 = await _begin(alice, text="second")
    await _finish(t2, content="", status="error", reason="error")
    t3 = await _begin(alice, text="third")
    pins, history = await store.load_turn_context("s-1", exclude=(t3.user_message_id, t3.assistant_message_id))
    assert pins == []
    assert [(m.role, m.content) for m in history] == [
        ("user", "legacy"), ("user", "first"), ("assistant", "answer one"), ("user", "second")]
    assert history[0].attachments == []                  # pre-C1 metadata: bytes were never stored
    assert history[1].attachments[0]["ordinal"] == 0


async def test_attachment_bytes_load_only_what_is_asked(conn):
    alice = await member(conn, "alice")
    claim = await _begin(alice, atts=[IMG_A, IMG_B])
    got = await store.load_attachment_bytes([(claim.user_message_id, 1)])
    assert got == {(claim.user_message_id, 1): Attachment("image", "image/png",
                                                          base64.b64encode(b"B").decode(), "b.png")}
    assert await store.load_attachment_bytes([]) == {}


async def test_add_event_and_session_info(conn):
    alice = await member(conn, "alice")
    await _begin(alice, pins=[{"id": "p"}])
    await store.add_event("s-1", "sent", "prompt: hi")
    assert await _one(conn, "SELECT kind, message FROM chat_events WHERE session_id='s-1'") == ("sent", "prompt: hi")
    info = await store.session_info("s-1")
    assert (info.owner_id, info.pins) == (alice.user_id, [{"id": "p"}])
    assert await store.session_info("s-none") is None


async def test_writes_after_session_delete_are_noops(conn):
    alice = await member(conn, "alice")
    claim = await _begin(alice, atts=[IMG_A])
    await conn.execute("DELETE FROM chat_sessions WHERE id='s-1'")
    assert (await _one(conn, "SELECT count(*) FROM chat_attachments"))[0] == 0
    await store.save_progress(claim.turn_id, content="x", thinking="", tool_calls=None, doc_context=None)
    assert await store.heartbeat("s-1", claim.turn_id) is False
    await store.add_event("s-1", "sent", "x")
    await _finish(claim, content="x")
    assert (await _one(conn, "SELECT count(*) FROM chat_messages WHERE session_id='s-1'"))[0] == 0
    assert (await _one(conn, "SELECT count(*) FROM chat_events WHERE session_id='s-1'"))[0] == 0
```

- [ ] **Step 7: Run to confirm failure**

Run: `.venv/bin/python -m pytest server/tests/test_chat_store.py -q`
Expected: `ModuleNotFoundError: No module named 'server.chat'`.

- [ ] **Step 8: Write `server/chat/__init__.py`**

```python
"""The server-side chat orchestrator (C1 spec §5): stage 0, tools, persistence, the turn loop."""
```

- [ ] **Step 9: Write `server/chat/store.py`**

```python
"""Every chat read and write a turn makes (spec §5.5, §7.3).

Each function opens its own SHORT pooled connection: a reply can stream for
minutes, and holding a pooled connection that long would drain the pool.
Writes aimed at a session that was deleted mid-turn simply match no rows.
"""
from __future__ import annotations

import base64
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Jsonb

from ..db import get_pool
from ..llm.types import Attachment

logger = logging.getLogger(__name__)

HEARTBEAT_S = 15.0     # a running turn refreshes its claim this often (seconds)
STALE_AFTER_S = 60     # a claim older than this belongs to a dead worker (seconds)
FLUSH_EVERY_S = 2.0    # partial content is saved at most this often (seconds)


class NotOwner(Exception):
    """The session exists and belongs to someone else."""


class TurnInProgress(Exception):
    """Another turn holds this session's claim and its heartbeat is fresh."""


@dataclass(frozen=True)
class NewAttachment:
    kind: str
    mime: str
    name: str
    data: bytes
    client_id: str | None = None

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class TurnClaim:
    session_id: str
    user_message_id: str
    assistant_message_id: str
    created_session: bool

    @property
    def turn_id(self) -> str:
        # ruling R8: the claim IS the streaming assistant message's id
        return self.assistant_message_id


@dataclass(frozen=True)
class StoredMessage:
    id: str
    role: str
    content: str
    attachments: list[dict[str, Any]]   # metadata with "ordinal" (C1 rows only)


@dataclass(frozen=True)
class SessionInfo:
    owner_id: str
    pins: list[dict[str, Any]]


def now_ms() -> int:
    return int(time.time() * 1000)


def new_message_id(prefix: str) -> str:
    return f"{prefix}-{now_ms()}-{secrets.token_hex(4)}"


def title_from_prompt(text: str) -> str:
    """The SPA's old titleFromPrompt: one line, at most 60 characters."""
    one = " ".join((text or "").split())
    if not one:
        return "New chat"
    return one if len(one) <= 60 else one[:59] + "…"


# Clears claims whose heartbeat stopped and marks their message aborted.
# %(sid)s NULL = every session (the startup pass); otherwise just that one.
_RECOVER_SQL = """
WITH stale AS (
    SELECT id, active_turn_id FROM chat_sessions
    WHERE active_turn_id IS NOT NULL
      AND active_turn_heartbeat_at < now() - make_interval(secs => %(stale_s)s::double precision)
      AND (%(sid)s::text IS NULL OR id = %(sid)s::text)
    FOR UPDATE
), cleared AS (
    UPDATE chat_sessions s SET active_turn_id = NULL, active_turn_heartbeat_at = NULL
    FROM stale WHERE s.id = stale.id
    RETURNING stale.active_turn_id AS turn_id
)
UPDATE chat_messages m SET status = 'aborted', finish_reason = 'aborted'
FROM cleared WHERE m.id = cleared.turn_id AND m.status = 'streaming'
RETURNING m.id
"""


async def _recover(conn, session_id: str | None) -> int:
    cur = await conn.execute(_RECOVER_SQL, {"stale_s": STALE_AFTER_S, "sid": session_id})
    return len(await cur.fetchall())


async def recover_stale(session_id: str | None = None) -> int:
    """Abort turns whose worker stopped heartbeating. Safe with WORKERS > 1:
    a live turn's heartbeat is never older than HEARTBEAT_S (spec §5.5)."""
    async with get_pool().connection() as conn:
        n = await _recover(conn, session_id)
    if n:
        logger.warning("Recovered %d stale chat turn(s)", n)
    return n


async def session_info(session_id: str) -> SessionInfo | None:
    async with get_pool().connection() as conn:
        cur = await conn.execute("SELECT user_id, pins FROM chat_sessions WHERE id = %s", (session_id,))
        row = await cur.fetchone()
    return SessionInfo(str(row[0]), row[1] or []) if row else None


async def begin_turn(*, session_id: str, user_id: str, model_id: str, text: str,
                     attachments: list[NewAttachment],
                     new_session_pins: list[dict] | None) -> TurnClaim:
    """Create the session if new, claim it, and write the user message (with
    attachment bytes) and the streaming assistant message — one transaction,
    committed before the first byte streams (spec §7.3)."""
    user_mid, asst_mid = new_message_id("u"), new_message_id("a")
    async with get_pool().connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                "INSERT INTO chat_sessions (id, title, model, pins, user_id) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO NOTHING RETURNING id",
                (session_id, title_from_prompt(text), model_id, Jsonb(new_session_pins or []), user_id))
            created = await cur.fetchone() is not None
            cur = await conn.execute("SELECT user_id FROM chat_sessions WHERE id = %s FOR UPDATE", (session_id,))
            row = await cur.fetchone()
            if row is None or str(row[0]) != str(user_id):
                raise NotOwner(session_id)
            await _recover(conn, session_id)
            cur = await conn.execute(
                "UPDATE chat_sessions SET active_turn_id = %s, active_turn_heartbeat_at = now(), "
                "model = %s, updated_at = now() WHERE id = %s AND active_turn_id IS NULL RETURNING id",
                (asst_mid, model_id, session_id))
            if await cur.fetchone() is None:
                raise TurnInProgress(session_id)
            # Monotonic timestamps: the UI sorts by them, and two fast turns can
            # land in the same millisecond.
            cur = await conn.execute(
                "SELECT COALESCE(MAX(timestamp), 0) FROM chat_messages WHERE session_id = %s", (session_id,))
            ts = max(now_ms(), (await cur.fetchone())[0] + 1)
            meta = [{"id": a.client_id or f"att-{i}", "kind": a.kind, "name": a.name,
                     "mimeType": a.mime, "size": a.size, "ordinal": i}   # ruling R11
                    for i, a in enumerate(attachments)]
            await conn.execute(
                "INSERT INTO chat_messages (id, session_id, role, content, attachments, timestamp, status, model) "
                "VALUES (%s, %s, 'user', %s, %s, %s, 'complete', %s)",
                (user_mid, session_id, text, Jsonb(meta), ts, model_id))
            for i, a in enumerate(attachments):
                await conn.execute(
                    "INSERT INTO chat_attachments (message_id, ordinal, kind, mime, name, size, data) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (user_mid, i, a.kind, a.mime, a.name, a.size, a.data))
            await conn.execute(
                "INSERT INTO chat_messages (id, session_id, role, content, timestamp, status, model) "
                "VALUES (%s, %s, 'assistant', '', %s, 'streaming', %s)",
                (asst_mid, session_id, ts + 1, model_id))
    return TurnClaim(session_id, user_mid, asst_mid, created)


async def heartbeat(session_id: str, turn_id: str) -> bool:
    """False when this turn no longer holds the claim (taken over, or the chat was deleted)."""
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "UPDATE chat_sessions SET active_turn_heartbeat_at = now() "
            "WHERE id = %s AND active_turn_id = %s RETURNING id", (session_id, turn_id))
        return await cur.fetchone() is not None


def _json_or_null(value: Any) -> Jsonb | None:
    return Jsonb(value) if value else None


async def save_progress(message_id: str, *, content: str, thinking: str,
                        tool_calls: list | None, doc_context: dict | None) -> None:
    async with get_pool().connection() as conn:
        await conn.execute(
            "UPDATE chat_messages SET content = %s, thinking = %s, tool_calls = %s, doc_context = %s "
            "WHERE id = %s",
            (content, thinking or None, _json_or_null(tool_calls), _json_or_null(doc_context), message_id))


async def finish_turn(claim: TurnClaim, *, status: str, finish_reason: str | None, content: str,
                      thinking: str, tool_calls: list | None, doc_context: dict | None,
                      stats: dict | None) -> None:
    async with get_pool().connection() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE chat_messages SET status = %s, finish_reason = %s, content = %s, thinking = %s, "
                "tool_calls = %s, doc_context = %s, stats = %s WHERE id = %s",
                (status, finish_reason, content, thinking or None, _json_or_null(tool_calls),
                 _json_or_null(doc_context), _json_or_null(stats), claim.assistant_message_id))
            await conn.execute(
                "UPDATE chat_sessions SET active_turn_id = NULL, active_turn_heartbeat_at = NULL, "
                "updated_at = now() WHERE id = %s AND active_turn_id = %s",
                (claim.session_id, claim.turn_id))


async def add_event(session_id: str, kind: str, message: str) -> None:
    """One line of the chat's event log (the sidebar's Log). Written by the
    server now that the browser no longer saves whole sessions."""
    async with get_pool().connection() as conn:
        await conn.execute(
            "INSERT INTO chat_events (session_id, kind, message, ts) "
            "SELECT %s, %s, %s, %s WHERE EXISTS (SELECT 1 FROM chat_sessions WHERE id = %s)",
            (session_id, kind, message, now_ms(), session_id))


async def load_turn_context(session_id: str, *, exclude: tuple[str, ...]
                            ) -> tuple[list[dict], list[StoredMessage]]:
    """The session's pins and its finished history, oldest first, without the
    current turn's own two messages. Attachment METADATA only; bytes are
    fetched later for the attachments that survive trimming (spec §5.2)."""
    async with get_pool().connection() as conn:
        cur = await conn.execute("SELECT pins FROM chat_sessions WHERE id = %s", (session_id,))
        row = await cur.fetchone()
        pins = (row[0] if row else None) or []
        cur = await conn.execute(
            "SELECT id, role, content, attachments FROM chat_messages "
            "WHERE session_id = %s AND role IN ('user', 'assistant') AND status <> 'streaming' "
            "AND NOT (id = ANY(%s)) "
            "ORDER BY timestamp, CASE role WHEN 'user' THEN 1 ELSE 3 END, created_at, id",
            (session_id, list(exclude)))
        rows = await cur.fetchall()
    history: list[StoredMessage] = []
    for mid, role, content, atts in rows:
        if role == "assistant" and not (content or "").strip():
            continue   # a failed or empty reply adds nothing the model can use
        meta = [a for a in (atts or []) if isinstance(a, dict) and "ordinal" in a]
        history.append(StoredMessage(mid, role, content or "", meta))
    return pins, history


async def load_attachment_bytes(wanted: list[tuple[str, int]]) -> dict[tuple[str, int], Attachment]:
    if not wanted:
        return {}
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "SELECT a.message_id, a.ordinal, a.kind, a.mime, a.name, a.data FROM chat_attachments a "
            "JOIN unnest(%s::text[], %s::int[]) AS w(mid, ord) "
            "ON a.message_id = w.mid AND a.ordinal = w.ord",
            ([w[0] for w in wanted], [w[1] for w in wanted]))
        rows = await cur.fetchall()
    return {(mid, ordinal): Attachment(kind, mime, base64.b64encode(bytes(data)).decode("ascii"), name)
            for mid, ordinal, kind, mime, name, data in rows}
```

- [ ] **Step 10: Run the tests**

Run: `.venv/bin/python -m pytest server/tests/test_chat_store.py server/tests/test_migration_013.py -q`
Expected: all pass. The session fixture also applies 013 to the test DB.

- [ ] **Step 11: Commit** (ask the user first)

```bash
git add server/sql/013_chat_orchestrator.sql server/chat/__init__.py server/chat/store.py server/tests/chat_harness.py server/tests/test_chat_store.py server/tests/test_migration_013.py
git commit -m "feat(chat): migration 013 and the chat store — claims, heartbeat recovery, stored attachments (C1)"
```

---

### Task 6: Shared document search and the server-side tools

**Files:**
- Create: `server/services/doc_search.py`
- Modify: `server/routers/docs.py:652-700` (`search_document` handler uses `doc_search.search_chunks`)
- Create: `server/chat/tools/__init__.py`, `server/chat/tools/search_document.py`, `server/chat/tools/web_search.py`
- Test: `server/tests/test_chat_tools.py`

**Interfaces:**
- Consumes: `server.auth.authz.readable_docs_where`, `readable_docs_params`; `server.services.embeddings.embed_one`; `server.services.web_search.web_search`, `SEARXNG_URL`, `RESULT_COUNT`; `server.llm.types.ToolSpec`, `ToolCall` (Task 1).
- Produces:
  - `server.services.doc_search`:
    - `ReadableDoc(doc_id, name, state)`
    - `async readable_doc(conn, doc_id, user_id) -> ReadableDoc | None`
    - `async search_chunks(conn, doc_id, qvec, k) -> list[dict]` (`{id, page, chunk_type, text, score}`; text capped at 4000)
  - `server.chat.tools`:
    - `ToolContext(user_id, doc: ReadableDoc | None)`
    - `Tool` protocol (`name`, `spec`, `available(ctx)`, `async execute(args, ctx) -> dict`, `summarize(args, result) -> dict`)
    - `REGISTRY`, `available_tools(ctx) -> list[Tool]`
    - `ToolRun(call_id, name, arguments, result, summary)` with `.ok`
    - `async run_tool(call: ToolCall, ctx, offered: list[Tool]) -> ToolRun`. `summary` is today's saved shape `{name, arguments, result_summary}`; the tool message content for the model is `json.dumps(result, indent=2)`.

- [ ] **Step 1: Write the failing tests** — `server/tests/test_chat_tools.py`:

```python
import pytest

from server.chat import tools as chat_tools
from server.chat.tools import ToolContext, available_tools, run_tool
from server.chat.tools import search_document as sd_tool
from server.chat.tools import web_search as ws_tool
from server.llm.types import ToolCall
from server.services import doc_search
from server.services.embeddings import EMBEDDING_DIM
from server.tests import seed
from server.tests.chat_harness import member, shim_pool

DOC = "d" * 64


def _vec(*head):
    return "[" + ",".join(str(x) for x in list(head) + [0] * (EMBEDDING_DIM - len(head))) + "]"


async def _chunk(conn, ord_, page, text, vec):
    await conn.execute(
        "INSERT INTO doc_chunks (doc_id, ord, page, chunk_type, text, text_hash, embedding, embedding_model) "
        "VALUES (%s, %s, %s, 'page', %s, %s, %s::vector, 'm')", (DOC, ord_, page, text, f"h{ord_}", vec))


@pytest.fixture
async def indexed(db_conn, monkeypatch):
    shim_pool(monkeypatch, db_conn, sd_tool)

    async def fake_embed(text):
        return [1.0] + [0.0] * (EMBEDDING_DIM - 1)

    monkeypatch.setattr(sd_tool, "embed_one", fake_embed)
    alice = await member(db_conn, "alice")
    await seed.seed_doc(db_conn, DOC, alice.user_id, file_name="Thesis.pdf", state="indexed")
    await _chunk(db_conn, 0, 3, "exact match " + "x" * 2000, _vec(1))
    await _chunk(db_conn, 1, 7, "half match", _vec(1, 1))
    await _chunk(db_conn, 2, 9, "no match", _vec(0, 1))
    return db_conn, alice


async def test_readable_doc_uses_the_readers_name_and_hides_others_docs(indexed):
    conn, alice = indexed
    await conn.execute("UPDATE library_entries SET file_name = 'My thesis.pdf' WHERE user_id = %s", (alice.user_id,))
    doc = await doc_search.readable_doc(conn, DOC, alice.user_id)
    assert (doc.doc_id, doc.name, doc.state) == (DOC, "My thesis.pdf", "indexed")
    bob = await member(conn, "bob")
    assert await doc_search.readable_doc(conn, DOC, bob.user_id) is None


async def test_search_document_is_offered_only_for_an_indexed_readable_doc(indexed):
    conn, alice = indexed
    doc = await doc_search.readable_doc(conn, DOC, alice.user_id)
    assert [t.name for t in available_tools(ToolContext(alice.user_id, doc))] == ["search_document", "web_search"]
    assert [t.name for t in available_tools(ToolContext(alice.user_id, None))] == ["web_search"]
    extracting = doc_search.ReadableDoc(DOC, "x", "extracting")
    assert "search_document" not in [t.name for t in available_tools(ToolContext(alice.user_id, extracting))]


async def test_search_document_returns_ranked_capped_passages(indexed):
    conn, alice = indexed
    ctx = ToolContext(alice.user_id, await doc_search.readable_doc(conn, DOC, alice.user_id))
    run = await run_tool(ToolCall("c1", "search_document", {"query": "match", "k": 2}), ctx, available_tools(ctx))
    assert run.ok
    assert run.result["query"] == "match" and run.result["chunk_count"] == 2
    first, second = run.result["results"]
    assert (first["index"], first["page"], first["score"]) == (1, 3, 1.0)
    assert first["text"].endswith(" [truncated]") and len(first["text"]) == 1500 + len(" [truncated]")
    assert (second["page"], second["score"]) == (7, 0.7071)
    assert run.summary == {"name": "search_document", "arguments": {"query": "match", "k": 2},
                           "result_summary": {"ok": True, "chunk_count": 2, "query": "match", "summary_text": None}}


async def test_search_document_clamps_k_and_needs_a_query(indexed):
    conn, alice = indexed
    ctx = ToolContext(alice.user_id, await doc_search.readable_doc(conn, DOC, alice.user_id))
    run = await run_tool(ToolCall("c1", "search_document", {"query": "m", "k": 99}), ctx, available_tools(ctx))
    assert run.result["chunk_count"] == 3                     # clamped to 10; only 3 chunks exist
    empty = await run_tool(ToolCall("c2", "search_document", {}), ctx, available_tools(ctx))
    assert not empty.ok and empty.result == {"error": "query is required and must be non-empty."}
    assert empty.summary["result_summary"] == {"error": "query is required and must be non-empty."}


async def test_a_tool_not_offered_is_unknown(indexed):
    _, alice = indexed
    ctx = ToolContext(alice.user_id, None)
    run = await run_tool(ToolCall("c1", "search_document", {"query": "x"}), ctx, available_tools(ctx))
    assert run.result == {"error": "Unknown tool: search_document"}


async def test_a_crashing_tool_becomes_an_error_result(monkeypatch):
    async def boom(query, count):
        raise RuntimeError("searxng down")
    monkeypatch.setattr(ws_tool, "web_search", boom)
    ctx = ToolContext("u", None)
    run = await run_tool(ToolCall("c1", "web_search", {"query": "x"}), ctx, available_tools(ctx))
    assert run.result == {"error": "web_search failed: RuntimeError"}


async def test_web_search_validates_count_and_summarizes(monkeypatch):
    async def fake(query, count):
        return {"query": query, "results": [{"title": "t", "url": "https://example.com", "summary": "s"}] * count}
    monkeypatch.setattr(ws_tool, "web_search", fake)
    ctx = ToolContext("u", None)
    bad = await run_tool(ToolCall("c1", "web_search", {"query": "x", "count": 50}), ctx, available_tools(ctx))
    assert bad.result == {"error": "count must be between 1 and 10."}
    ok = await run_tool(ToolCall("c2", "web_search", {"query": "news", "count": 2}), ctx, available_tools(ctx))
    assert ok.summary["result_summary"] == {"ok": True, "chunk_count": None, "query": "news",
                                            "summary_text": 'Web search for "news" returned 2 result(s).'}


def test_registry_order_is_stable():
    assert [t.name for t in chat_tools.REGISTRY] == ["search_document", "web_search"]
```

- [ ] **Step 2: Run to confirm failure**

Run: `.venv/bin/python -m pytest server/tests/test_chat_tools.py -q`
Expected: `ModuleNotFoundError: No module named 'server.chat.tools'`.

- [ ] **Step 3: Write `server/services/doc_search.py`**

```python
"""Semantic search over ONE document's chunks, shared by POST /v1/docs/{id}/search,
the chat's search_document tool and stage-0 prefetch (spec §5.2, §5.3), so the
three can never disagree about what "relevant" means."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..auth.authz import readable_docs_params, readable_docs_where

TEXT_CAP = 4000   # characters per returned chunk


@dataclass(frozen=True)
class ReadableDoc:
    doc_id: str
    name: str      # the reader's own name for it (their library entry), else the file name
    state: str


async def readable_doc(conn, doc_id: str, user_id: str) -> ReadableDoc | None:
    """The document if `user_id` may read it (the one SQL definition in
    authz.readable_docs_where), else None. Never trusts the id alone."""
    cur = await conn.execute(
        "SELECT d.doc_id, COALESCE((SELECT e.file_name FROM library_entries e "
        "WHERE e.doc_id = d.doc_id AND e.user_id = %s), d.file_name), d.state "
        f"FROM documents d WHERE d.doc_id = %s AND {readable_docs_where('d')}",
        [user_id, doc_id, *readable_docs_params(user_id)])
    row = await cur.fetchone()
    return ReadableDoc(row[0], row[1] or "document", row[2]) if row else None


async def search_chunks(conn, doc_id: str, qvec: list[float], k: int) -> list[dict[str, Any]]:
    """Top-k chunks by cosine similarity (higher `score` = closer). Trap: the
    score is NOT a calibrated probability; a "good" value depends on the model."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, page, chunk_type, text,
                   1 - (embedding <=> %s::vector) AS score
            FROM doc_chunks
            WHERE doc_id = %s AND embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (qvec, doc_id, qvec, k),
        )
        rows = await cur.fetchall()
        cols = [d.name for d in cur.description]
    results = [dict(zip(cols, r)) for r in rows]
    for r in results:
        r["score"] = float(r["score"])
        if r.get("text") and len(r["text"]) > TEXT_CAP:
            r["text"] = r["text"][:TEXT_CAP] + " [truncated]"
    return results
```

- [ ] **Step 4: Point the docs route at it** — in `server/routers/docs.py`, add `from ..services.doc_search import search_chunks` to the imports. In `search_document`, replace everything from `async with pool.connection() as conn, conn.cursor() as cur:` (the second one, the chunk query) to the end of the function with:

```python
    async with pool.connection() as conn:
        results = await search_chunks(conn, doc_id, qvec, req.k)
    return {"doc_id": doc_id, "results": results}
```

- [ ] **Step 5: Write `server/chat/tools/__init__.py`**

```python
"""Server-side chat tools (spec §5.3). One file per tool; adding a tool = one
file plus one line in REGISTRY. A tool never raises to the turn: failures come
back as {"error": ...} so the model can recover (the OpenAI Agents SDK rule)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from ...llm.types import ToolCall, ToolSpec
from ...services.doc_search import ReadableDoc

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolContext:
    user_id: str
    doc: ReadableDoc | None   # the open document, already checked readable


class Tool(Protocol):
    name: str
    spec: ToolSpec

    def available(self, ctx: ToolContext) -> bool: ...

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]: ...

    def summarize(self, args: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ToolRun:
    call_id: str
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]    # what the model reads
    summary: dict[str, Any]   # what is saved on the message: {name, arguments, result_summary}

    @property
    def ok(self) -> bool:
        return "error" not in self.result


from . import search_document, web_search  # noqa: E402 — after the shared types, by convention

REGISTRY: list[Tool] = [search_document.TOOL, web_search.TOOL]


def available_tools(ctx: ToolContext) -> list[Tool]:
    return [t for t in REGISTRY if t.available(ctx)]


async def run_tool(call: ToolCall, ctx: ToolContext, offered: list[Tool]) -> ToolRun:
    tool = next((t for t in offered if t.name == call.name), None)
    if tool is None:
        result: dict[str, Any] = {"error": f"Unknown tool: {call.name}"}
    else:
        try:
            result = await tool.execute(call.arguments, ctx)
        except Exception as e:  # noqa: BLE001 — a tool failure is the model's to handle
            logger.warning("Tool %s failed: %r", call.name, e)
            result = {"error": f"{call.name} failed: {type(e).__name__}"}
    summary = ({"error": result["error"]} if "error" in result or tool is None
               else tool.summarize(call.arguments, result))
    return ToolRun(call.id, call.name, call.arguments, result,
                   {"name": call.name, "arguments": call.arguments, "result_summary": summary})
```

- [ ] **Step 6: Write `server/chat/tools/search_document.py`**

```python
"""search_document: semantic search over the open, indexed document (spec §5.3).
Moved from src/lib/chatTools/searchDocument.js; same description and result shape."""
from __future__ import annotations

from typing import Any

from ...db import get_pool
from ...llm.types import ToolSpec
from ...services.doc_search import search_chunks
from ...services.embeddings import embed_one

PER_CHUNK_TEXT_CAP = 1500   # characters per passage the model reads

_SPEC = ToolSpec(
    name="search_document",
    description=(
        "Search the document the user is currently reading for passages relevant to a question. "
        "Use this when the user's question is about the document's contents and you don't already "
        "have the relevant excerpt in your context. Returns up to k chunks ranked by semantic "
        "similarity, each tagged with its page number so you can cite it."),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": (
                "Natural-language search query. Be specific — concepts and phrasings from "
                "the user's question work better than single keywords.")},
            "k": {"type": "integer", "minimum": 1, "maximum": 10,
                  "description": "How many top chunks to return (1-10). Default 5."},
        },
        "required": ["query"],
    },
)


def _cap(text: str) -> str:
    return text if len(text) <= PER_CHUNK_TEXT_CAP else text[:PER_CHUNK_TEXT_CAP] + " [truncated]"


class _SearchDocument:
    name = "search_document"
    spec = _SPEC

    def available(self, ctx) -> bool:
        return ctx.doc is not None and ctx.doc.state == "indexed"

    async def execute(self, args: dict[str, Any], ctx) -> dict[str, Any]:
        if ctx.doc is None:
            return {"error": "No document is currently loaded."}
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "query is required and must be non-empty."}
        k = args.get("k")
        k = max(1, min(10, int(k))) if isinstance(k, (int, float)) and not isinstance(k, bool) else 5
        qvec = await embed_one(query)
        async with get_pool().connection() as conn:
            rows = await search_chunks(conn, ctx.doc.doc_id, qvec, k)
        return {"query": query, "chunk_count": len(rows), "results": [
            {"index": i, "page": r["page"], "score": round(r["score"], 4), "text": _cap(r["text"] or "")}
            for i, r in enumerate(rows, start=1)]}

    def summarize(self, args: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "chunk_count": result["chunk_count"], "query": result["query"],
                "summary_text": None}


TOOL = _SearchDocument()
```

- [ ] **Step 7: Write `server/chat/tools/web_search.py`**

```python
"""web_search: SearXNG + page summaries (spec §5.3). Moved from
src/lib/chatTools/webSearch.js; the service's SSRF guards are unchanged."""
from __future__ import annotations

from typing import Any

from ...llm.types import ToolSpec
from ...services import web_search as web_search_service
from ...services.web_search import RESULT_COUNT, web_search

_SPEC = ToolSpec(
    name="web_search",
    description=(
        "Searches the live internet for real-time information, recent news, current prices, and "
        "up-to-date facts. Use this tool whenever a user asks about events after your knowledge "
        "cutoff, requests current data/statistics, or asks a factual question requiring live "
        "information or verification."),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "The optimized search query string used to look up information on the web."},
            "count": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Number of results."},
        },
        "required": ["query"],
    },
)


class _WebSearch:
    name = "web_search"
    spec = _SPEC

    def available(self, ctx) -> bool:
        return bool(web_search_service.SEARXNG_URL)

    async def execute(self, args: dict[str, Any], ctx) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "query is required and must be non-empty."}
        count = args.get("count", RESULT_COUNT)
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 10:
            return {"error": "count must be between 1 and 10."}
        return await web_search(query, count)

    def summarize(self, args: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        n = len(result.get("results") or [])
        return {"ok": True, "chunk_count": None, "query": result.get("query"),
                "summary_text": f'Web search for "{result.get("query")}" returned {n} result(s).'}


TOOL = _WebSearch()
```

  (The test monkeypatches `ws_tool.web_search`; the module-level name is what `execute` calls.)

- [ ] **Step 8: Run the tests, plus the existing doc search tests**

Run: `.venv/bin/python -m pytest server/tests/test_chat_tools.py server/tests/test_docs_authz.py server/tests/test_library_access_matrix.py -q`
Expected: all pass.

- [ ] **Step 9: Commit** (ask the user first)

```bash
git add server/services/doc_search.py server/routers/docs.py server/chat/tools server/tests/test_chat_tools.py
git commit -m "feat(chat): shared document search and server-side search_document/web_search tools (C1)"
```

---

### Task 7: Stage 0 — chat config and context building

**Files:**
- Create: `server/chat/config.py`, `server/chat/context.py`
- Test: `server/tests/test_chat_context.py`

**Interfaces:**
- Consumes:
  - Task 5: `store.StoredMessage`, `store.load_attachment_bytes`.
  - Task 6: `doc_search.search_chunks`, `doc_search.ReadableDoc`.
  - Existing: `embeddings.embed_one`.
  - Task 1: `llm.types.Message`, `Attachment`.
- Produces:
  - `server.chat.config`:
    - `ChatConfig(max_tool_rounds=1, prefetch_min_score=0.75, prefetch_k=4, reply_reserve_tokens=2048, attachment_token_estimate=1500, max_request_mb=25)`
    - `load_chat_config(env)`, `get_chat_config()`
  - `server.chat.context`:
    - `pin_messages(pins) -> list[Message]`
    - `time_line(now, tz_name) -> str`
    - `PREFETCH_PREAMBLE`
    - `Prefetch(passages, top_score, note)`
    - `async prefetch(doc, question, cfg) -> Prefetch`
    - `TurnInput(user_id, text, attachments, doc, timezone, pins, history, window, now)`
    - `BuiltContext(messages, notes, prefetch_hit)`
    - `async build_context(turn, cfg) -> BuiltContext`
  - Note shapes (the `data-context` items):
    - `{"kind": "prefetch", "docId", "docName", "count", "topScore", "pages"}`
    - `{"kind": "trimmed", "messages", "attachments"}`

- [ ] **Step 1: Write the failing tests** — `server/tests/test_chat_context.py`:

```python
import logging
from datetime import datetime, timezone

import pytest

from server.chat import context as ctx_mod
from server.chat.config import ChatConfig, load_chat_config
from server.chat.context import (PREFETCH_PREAMBLE, TurnInput, build_context, pin_messages,
                                 prefetch, time_line)
from server.chat.store import StoredMessage
from server.llm.types import Attachment, Message
from server.services.doc_search import ReadableDoc

NOW = datetime(2026, 9, 26, 19, 24, tzinfo=timezone.utc)
DOC = ReadableDoc("d" * 64, "Thesis.pdf", "indexed")


class _NullPool:
    class _Ctx:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    def connection(self):
        return self._Ctx()


@pytest.fixture
def search(monkeypatch):
    """Scripted embedding + search; records calls."""
    state = {"rows": [], "embeds": 0, "raise": None}

    async def fake_embed(text):
        state["embeds"] += 1
        if state["raise"]:
            raise state["raise"]
        return [1.0]

    async def fake_search(conn, doc_id, qvec, k):
        state["k"] = k
        return state["rows"]

    monkeypatch.setattr(ctx_mod, "embed_one", fake_embed)
    monkeypatch.setattr(ctx_mod, "search_chunks", fake_search)
    monkeypatch.setattr(ctx_mod, "get_pool", lambda: _NullPool())
    return state


def _turn(**over):
    base = dict(user_id="u", text="What does chapter 2 say?", attachments=(), doc=None,
                timezone="Asia/Colombo", pins=[], history=[], window=None, now=NOW)
    base.update(over)
    return TurnInput(**base)


def test_config_defaults_and_bad_values(caplog):
    assert load_chat_config({}) == ChatConfig()
    with caplog.at_level(logging.WARNING):
        cfg = load_chat_config({"CHAT_PREFETCH_MIN_SCORE": "2", "CHAT_PREFETCH_K": "x",
                                "CHAT_MAX_TOOL_ROUNDS": "3"})
    assert (cfg.prefetch_min_score, cfg.prefetch_k, cfg.max_tool_rounds) == (0.75, 4, 3)
    assert "CHAT_PREFETCH_MIN_SCORE" in caplog.text and "CHAT_PREFETCH_K" in caplog.text


def test_time_line_uses_the_browser_timezone():
    assert time_line(NOW, "Asia/Colombo") == "Current time: 2026-09-27 00:54 (Asia/Colombo)"


@pytest.mark.parametrize("bad", ["Mars/Olympus", "", None, "../etc", "America"])
def test_time_line_falls_back_to_utc_on_bad_timezone(bad):
    assert time_line(NOW, bad) == "Current time: 2026-09-26 19:24 (UTC)"


def test_pin_messages_keep_today_s_wording():
    [m] = pin_messages([{"fileName": "T.pdf", "kind": "page", "page": 4, "text": "the excerpt"}])
    assert m.role == "system"
    assert m.content == ('The user is reading "T.pdf".\nRelevant excerpt (page, page 4):\n\n'
                         '"""\nthe excerpt\n"""\n\nUse this excerpt as primary context for the user\'s '
                         "question. If it does not contain the answer, say so or use the document "
                         "search tool if available.")


async def test_prefetch_keeps_only_passages_above_the_threshold(search):
    search["rows"] = [{"page": 3, "score": 0.91, "text": "a"}, {"page": 5, "score": 0.80, "text": "b"},
                      {"page": 8, "score": 0.40, "text": "c"}]
    got = await prefetch(DOC, "q", ChatConfig())
    assert [p["page"] for p in got.passages] == [3, 5] and search["k"] == 4
    assert got.note == {"kind": "prefetch", "docId": DOC.doc_id, "docName": "Thesis.pdf",
                        "count": 2, "topScore": 0.91, "pages": [3, 5]}


async def test_prefetch_miss_disabled_unreadable_and_failure_cost_nothing_visible(search):
    search["rows"] = [{"page": 1, "score": 0.5, "text": "weak"}]
    miss = await prefetch(DOC, "q", ChatConfig())
    assert miss.passages == [] and miss.note is None and miss.top_score == 0.5
    assert (await prefetch(DOC, "q", ChatConfig(prefetch_min_score=1.0))).passages == []
    assert (await prefetch(None, "q", ChatConfig())).passages == []
    assert (await prefetch(ReadableDoc("x", "n", "extracting"), "q", ChatConfig())).passages == []
    assert search["embeds"] == 1            # disabled / no doc / not indexed never embed
    search["raise"] = RuntimeError("embedding service down")
    assert (await prefetch(DOC, "q", ChatConfig())).passages == []


async def test_message_order_pins_history_volatile_then_the_question(search, monkeypatch):
    search["rows"] = [{"page": 3, "score": 0.9, "text": "passage text"}]
    history = [StoredMessage("u1", "user", "earlier q", []), StoredMessage("a1", "assistant", "earlier a", [])]
    built = await build_context(_turn(doc=DOC, pins=[{"text": "pinned"}], history=history), ChatConfig())
    roles = [(m.role, m.content[:12]) for m in built.messages]
    assert roles[0][0] == "system" and "pinned" in built.messages[0].content
    assert [m.content for m in built.messages[1:3]] == ["earlier q", "earlier a"]
    volatile = built.messages[3]
    assert volatile.role == "system"
    assert volatile.content.startswith(PREFETCH_PREAMBLE.format(name="Thesis.pdf"))
    assert "[1] (page 3)\npassage text" in volatile.content
    assert volatile.content.endswith("Current time: 2026-09-27 00:54 (Asia/Colombo)")
    assert built.messages[4] == Message("user", "What does chapter 2 say?")
    assert built.prefetch_hit and built.notes[0]["kind"] == "prefetch"


async def test_no_window_means_no_trimming(search):
    history = [StoredMessage(f"m{i}", "user", "x" * 1000, []) for i in range(50)]
    built = await build_context(_turn(history=history, window=None), ChatConfig())
    assert len(built.messages) == 52 and built.notes == []


async def test_old_attachments_go_before_old_turns_and_only_kept_bytes_load(search, monkeypatch):
    asked = {}

    async def fake_bytes(wanted):
        asked["wanted"] = wanted
        return {w: Attachment("image", "image/png", "QQ==", "img") for w in wanted}

    monkeypatch.setattr(ctx_mod.store, "load_attachment_bytes", fake_bytes)
    img = lambda n: {"id": n, "kind": "image", "name": f"{n}.png", "mimeType": "image/png", "size": 9, "ordinal": 0}
    history = [StoredMessage("u1", "user", "look at this", [img("old")]),
               StoredMessage("a1", "assistant", "a cat", []),
               StoredMessage("u2", "user", "and this", [img("new")]),
               StoredMessage("a2", "assistant", "a dog", [])]
    # window: fixed text is ~40 tokens; each attachment is estimated at 1500.
    # 2048 reserve + ~40 + one attachment (1500) fits in 3700; two don't.
    built = await build_context(_turn(history=history, window=3700), ChatConfig())
    assert asked["wanted"] == [("u2", 0)]
    first = built.messages[0]
    assert first.attachments == () and first.content == "look at this\n\n[image old.png from earlier; no longer attached]"
    assert len(built.messages[2].attachments) == 1
    assert built.notes == [{"kind": "trimmed", "messages": 0, "attachments": 1}]


async def test_then_the_oldest_turns_are_dropped(search):
    history = [StoredMessage(f"m{i}", "user" if i % 2 == 0 else "assistant", "x" * 400, []) for i in range(10)]
    # reserve 2048 + question ~6 + time line ~12 + 10 x 100 tokens; window 2600 keeps 5
    built = await build_context(_turn(history=history, window=2600), ChatConfig())
    kept = [m for m in built.messages if m.content == "x" * 400]
    assert len(kept) == 5
    assert built.notes == [{"kind": "trimmed", "messages": 5, "attachments": 0}]


async def test_the_current_message_keeps_its_attachments_even_when_over_budget(search):
    att = Attachment("image", "image/png", "QQ==", "now.png")
    built = await build_context(_turn(attachments=(att,), window=100), ChatConfig())
    assert built.messages[-1].attachments == (att,)
```

- [ ] **Step 2: Run to confirm failure**

Run: `.venv/bin/python -m pytest server/tests/test_chat_context.py -q`
Expected: `ModuleNotFoundError: No module named 'server.chat.config'`.

- [ ] **Step 3: Write `server/chat/config.py`**

```python
"""Chat orchestrator knobs (spec §9). Every value is an env var with a safe
default; a bad value logs a WARNING and falls back to the default."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable, Mapping

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatConfig:
    max_tool_rounds: int = 1               # tool rounds before the final tools-off step
    prefetch_min_score: float = 0.75       # cosine similarity; >= 1 disables prefetch (unmeasured default)
    prefetch_k: int = 4                    # passages at most
    reply_reserve_tokens: int = 2048       # kept free for the reply when trimming history
    attachment_token_estimate: int = 1500  # tokens counted per image/audio when trimming (a guess)
    max_request_mb: int = 25               # turn request body cap, in MB


def load_chat_config(env: Mapping[str, str]) -> ChatConfig:
    d = ChatConfig()

    def num(key: str, default, cast: Callable, lo, hi):
        raw = (env.get(key) or "").strip()
        if not raw:
            return default
        try:
            value = cast(raw)
        except ValueError:
            logger.warning("%s=%r is not a number; using %s", key, raw, default)
            return default
        if not lo <= value <= hi:
            logger.warning("%s=%r is outside %s..%s; using %s", key, raw, lo, hi, default)
            return default
        return value

    return ChatConfig(
        max_tool_rounds=num("CHAT_MAX_TOOL_ROUNDS", d.max_tool_rounds, int, 0, 20),
        prefetch_min_score=num("CHAT_PREFETCH_MIN_SCORE", d.prefetch_min_score, float, 0.0, 1.0),
        prefetch_k=num("CHAT_PREFETCH_K", d.prefetch_k, int, 1, 20),
        reply_reserve_tokens=num("CHAT_REPLY_RESERVE_TOKENS", d.reply_reserve_tokens, int, 0, 1_000_000),
        attachment_token_estimate=num("CHAT_ATTACHMENT_TOKEN_ESTIMATE", d.attachment_token_estimate,
                                      int, 0, 100_000),
        max_request_mb=num("CHAT_MAX_REQUEST_MB", d.max_request_mb, int, 1, 1024),
    )


def get_chat_config() -> ChatConfig:
    return load_chat_config(os.environ)
```

- [ ] **Step 4: Write `server/chat/context.py`**

```python
"""Stage 0 (spec §5.2): build the model input with the cheap, bounded work
first, so a document question can be answered in ONE model call.

Prompt order is fixed for prefix-cache reuse: pins -> history -> volatile
context (passages, time) -> the new message. Counterintuitive: the time line
belongs at the END; at the top it would change the prefix every turn and
defeat the provider's reuse for the whole conversation. There is no base
system prompt (ruling R9); a future one goes at the head.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ..db import get_pool
from ..llm.types import Attachment, Message
from ..services.doc_search import ReadableDoc, search_chunks
from ..services.embeddings import embed_one
from . import store
from .config import ChatConfig
from .store import StoredMessage

logger = logging.getLogger(__name__)

PASSAGE_TEXT_CAP = 1500   # characters per prefetched passage
PREFETCH_PREAMBLE = (
    'Passages retrieved from "{name}" for this question. Answer from them when they are enough; '
    "call search_document only if they don't contain what you need.")
_MARKER = "[{kind} {name} from earlier; no longer attached]"


def pin_messages(pins: list[dict[str, Any]]) -> list[Message]:
    """One system message per pin, worded exactly as the SPA's old buildPinPreamble."""
    out = []
    for p in pins:
        page = f", page {p['page']}" if p.get("page") is not None else ""
        out.append(Message("system",
            f'The user is reading "{p.get("fileName") or "a document"}".\n'
            f'Relevant excerpt ({p.get("kind") or "page"}{page}):\n\n'
            f'"""\n{p.get("text") or ""}\n"""\n\n'
            "Use this excerpt as primary context for the user's question. If it does "
            "not contain the answer, say so or use the document search tool if available."))
    return out


def time_line(now: datetime, tz_name: str | None) -> str:
    """The current time in the browser's timezone; anything unusable is UTC."""
    tz, label = timezone.utc, "UTC"
    if tz_name:
        try:
            tz, label = ZoneInfo(tz_name), tz_name
        except Exception:  # noqa: BLE001 — ZoneInfo raises several types for bad names
            tz, label = timezone.utc, "UTC"
    return f"Current time: {now.astimezone(tz):%Y-%m-%d %H:%M} ({label})"


@dataclass
class Prefetch:
    passages: list[dict[str, Any]] = field(default_factory=list)
    top_score: float | None = None
    note: dict[str, Any] | None = None


def _cap(text: str) -> str:
    return text if len(text) <= PASSAGE_TEXT_CAP else text[:PASSAGE_TEXT_CAP] + " [truncated]"


async def prefetch(doc: ReadableDoc | None, question: str, cfg: ChatConfig) -> Prefetch:
    """Search the open document before the model runs. Cost when it doesn't
    help: one embedding call. Every decision is logged at DEBUG so a deployer
    can tune CHAT_PREFETCH_MIN_SCORE on their own documents."""
    if doc is None or doc.state != "indexed" or cfg.prefetch_min_score >= 1.0 or not question.strip():
        return Prefetch()
    try:
        qvec = await embed_one(question)
        async with get_pool().connection() as conn:
            rows = await search_chunks(conn, doc.doc_id, qvec, cfg.prefetch_k)
    except Exception as e:  # noqa: BLE001 — prefetch is an optimization; the tool is still offered
        logger.warning("Prefetch failed for doc %s: %r", doc.doc_id, e)
        return Prefetch()
    top = max((float(r["score"]) for r in rows), default=None)
    kept = [r for r in rows if float(r["score"]) >= cfg.prefetch_min_score]
    logger.debug("prefetch doc=%s top=%s kept=%d/%d threshold=%s",
                 doc.doc_id, top, len(kept), len(rows), cfg.prefetch_min_score)
    if not kept:
        return Prefetch(top_score=top)
    note = {"kind": "prefetch", "docId": doc.doc_id, "docName": doc.name, "count": len(kept),
            "topScore": round(top, 4),
            "pages": sorted({r["page"] for r in kept if r.get("page") is not None})}
    return Prefetch([{"page": r.get("page"), "score": float(r["score"]), "text": _cap(r["text"] or "")}
                     for r in kept], top, note)


def _passages_block(name: str, passages: list[dict[str, Any]]) -> str:
    lines = [PREFETCH_PREAMBLE.format(name=name), ""]
    for i, p in enumerate(passages, start=1):
        page = f" (page {p['page']})" if p["page"] is not None else ""
        lines += [f"[{i}]{page}", p["text"], ""]
    return "\n".join(lines).strip()


def _marker(meta: dict[str, Any]) -> str:
    return _MARKER.format(kind=meta.get("kind", "file"), name=meta.get("name") or "attachment")


@dataclass
class _Item:
    stored: StoredMessage
    keep: list[dict[str, Any]]
    dropped: list[dict[str, Any]] = field(default_factory=list)


def _fit_history(history: list[StoredMessage], fixed_chars: int, fixed_attachments: int,
                 window: int | None, cfg: ChatConfig) -> tuple[list[_Item], int, int]:
    """Two passes (spec §5.2 step 4): drop old attachments first (the costliest
    part of a long chat), then the oldest turns. Pins, the volatile block and
    the new message are never trimmed."""
    items = [_Item(m, list(m.attachments)) for m in history]
    if window is None:
        return items, 0, 0
    budget = window - cfg.reply_reserve_tokens

    def tokens() -> int:
        chars = fixed_chars + sum(len(i.stored.content) + sum(len(_marker(a)) + 2 for a in i.dropped)
                                  for i in items)
        attachments = fixed_attachments + sum(len(i.keep) for i in items)
        return math.ceil(chars / 4) + attachments * cfg.attachment_token_estimate

    dropped_attachments = 0
    for item in items:
        while item.keep and tokens() > budget:
            item.dropped.append(item.keep.pop(0))
            dropped_attachments += 1
    dropped_messages = 0
    while items and tokens() > budget:
        items.pop(0)
        dropped_messages += 1
    return items, dropped_messages, dropped_attachments


@dataclass(frozen=True)
class TurnInput:
    user_id: str
    text: str
    attachments: tuple[Attachment, ...]
    doc: ReadableDoc | None
    timezone: str | None
    pins: list[dict[str, Any]]
    history: list[StoredMessage]
    window: int | None   # settings.num_ctx, else the model's context length (ruling R14); None = don't trim
    now: datetime


@dataclass
class BuiltContext:
    messages: list[Message]
    notes: list[dict[str, Any]]
    prefetch_hit: bool


async def build_context(turn: TurnInput, cfg: ChatConfig) -> BuiltContext:
    pre = await prefetch(turn.doc, turn.text, cfg)
    parts = [_passages_block(pre.note["docName"], pre.passages)] if pre.passages else []
    parts.append(time_line(turn.now, turn.timezone))
    volatile = Message("system", "\n\n".join(parts))
    pins = pin_messages(turn.pins)
    current = Message("user", turn.text, attachments=turn.attachments)
    fixed_chars = sum(len(m.content) for m in (*pins, volatile, current))
    items, n_messages, n_attachments = _fit_history(turn.history, fixed_chars, len(turn.attachments),
                                                    turn.window, cfg)
    wanted = [(i.stored.id, a["ordinal"]) for i in items for a in i.keep]
    blobs = await store.load_attachment_bytes(wanted) if wanted else {}
    history_messages = []
    for i in items:
        content = i.stored.content
        if i.dropped:
            content = (content + "\n\n" + "\n".join(_marker(a) for a in i.dropped)).strip()
        kept = tuple(blobs[(i.stored.id, a["ordinal"])] for a in i.keep
                     if (i.stored.id, a["ordinal"]) in blobs)
        history_messages.append(Message(i.stored.role, content, attachments=kept))
    notes = [pre.note] if pre.note else []
    if n_messages or n_attachments:
        notes.append({"kind": "trimmed", "messages": n_messages, "attachments": n_attachments})
        logger.debug("history trimmed: %d messages, %d attachments (window %s)",
                     n_messages, n_attachments, turn.window)
    return BuiltContext([*pins, *history_messages, volatile, current], notes, bool(pre.passages))
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest server/tests/test_chat_context.py -q`
Expected: all pass. If a trimming test's window arithmetic is off by one message, don't adjust the assertion. Instead print `tokens()` for the fixture and set the window so the stated intent holds ("one attachment fits, two don't"; "five of ten turns fit"), then note the value in the test comment.

- [ ] **Step 6: Commit** (ask the user first)

```bash
git add server/chat/config.py server/chat/context.py server/tests/test_chat_context.py
git commit -m "feat(chat): stage 0 — prefetch, time line, cache-friendly order, history budget (C1)"
```

---

### Task 8: The orchestrator

**Files:**
- Create: `server/chat/orchestrator.py`
- Modify: `server/tests/chat_harness.py` (append the fake router and fixture helpers)
- Create: `server/tests/fixtures/chat_events/{plain,tool_round,error,notice,context}.jsonl` (generated by the tests, then committed)
- Test: `server/tests/test_chat_orchestrator.py`

**Interfaces:**
- Consumes:
  - Task 1: `llm.types.*`
  - Task 3: `llm.router.UnknownModel`, plus a router object with `capabilities(model_id)` and `stream_chat(model_id, messages, tools, settings)`
  - Task 5: `store.*`
  - Task 6: `doc_search.readable_doc`, `tools.ToolContext`, `available_tools`, `run_tool`
  - Task 7: `config.ChatConfig`, `context.TurnInput`, `build_context`
  - Existing: `inference_budget.over_budget`, `record_usage`
- Produces:
  - `server.chat.orchestrator.TurnRequest(user_id, session_id, model_id, text, attachments, settings, doc_id, timezone)`
  - `async run_turn(req, claim, *, router, cfg, deployment_budget) -> AsyncIterator[dict]`. It yields spec §6 event dicts with `type` and `seq`; `finish` carries `usage`, `finishReason` and `stats` (R2). It never raises for provider or tool failures. On disconnect or cancellation it saves `aborted` and re-raises.
  - `server.tests.chat_harness`: `reply()`, `FakeRouter`, `normalize(events)`, `assert_fixture(name, events)`, `FIXTURE_DIR`.

- [ ] **Step 1: Append the fake router and fixture helpers to `server/tests/chat_harness.py`**

```python
# ---- Task 8: a scripted router and the SPA contract fixtures ----

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from server.llm.types import Capabilities, Finish, TextDelta, Usage


def reply(text: str = "Hello there.", prompt: int = 10, completion: int = 3) -> list:
    return [TextDelta(text), Usage(prompt, completion), Finish("stop")]


@dataclass
class FakeRouter:
    """Stands in for server.llm.router.Router. Each stream_chat call plays the
    next scripted step: a list of chunks, or (chunks, exception) to raise after
    them. With no steps left it plays reply()."""
    steps: list = field(default_factory=list)
    caps: Capabilities = field(default_factory=lambda: Capabilities(
        tools=True, thinking=True, vision=True, audio=None, context_window=None))
    allowed: bool = True
    providers: bool = True
    calls: list = field(default_factory=list)

    def has_providers(self) -> bool:
        return self.providers

    def is_allowed(self, model_id: str) -> bool:
        return self.allowed

    def canonical_id(self, model_id: str) -> str:
        return model_id if model_id.startswith("ollama:") else f"ollama:{model_id}"

    async def capabilities(self, model_id: str) -> Capabilities:
        return self.caps

    def stream_chat(self, model_id, messages, tools, settings):
        self.calls.append({"model": model_id, "messages": list(messages),
                           "tools": [t.name for t in tools], "settings": settings})
        step = self.steps.pop(0) if self.steps else reply()
        return _play(step)


async def _play(step):
    chunks, exc = step if isinstance(step, tuple) else (step, None)
    for c in chunks:
        yield c
    if exc is not None:
        raise exc


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "chat_events"
_ID_FIELDS = {"runId": "<message-id>", "messageId": "<message-id>",
              "userMessageId": "<user-message-id>", "sessionId": "<session-id>"}


def normalize(events: list[dict]) -> list[dict]:
    """Replace per-run values (ids, wall-clock time) so fixtures are stable."""
    out = []
    for e in events:
        e = {k: (_ID_FIELDS[k] if k in _ID_FIELDS else v) for k, v in e.items()}
        if e.get("type") == "finish" and isinstance(e.get("stats"), dict):
            e["stats"] = {**e["stats"], "totalNs": 0}
        out.append(e)
    return out


def assert_fixture(name: str, events: list[dict]) -> None:
    """The event sequences the backend asserts are the SPA reducer's test input
    (spec §11). A format change fails here first; regenerate with
    UPDATE_CHAT_FIXTURES=1, then make the SPA tests pass against the new files."""
    path = FIXTURE_DIR / f"{name}.jsonl"
    got = normalize(events)
    if os.environ.get("UPDATE_CHAT_FIXTURES") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(e, sort_keys=True, ensure_ascii=False) + "\n" for e in got))
        return
    assert path.exists(), f"{path} missing: run once with UPDATE_CHAT_FIXTURES=1"
    want = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert got == want, f"event format for {name!r} changed: regenerate with UPDATE_CHAT_FIXTURES=1 and update src/lib/chatEvents.js"
```

- [ ] **Step 2: Write the failing orchestrator tests** — `server/tests/test_chat_orchestrator.py`:

```python
import asyncio
import json

import pytest

from server.chat import context as ctx_mod
from server.chat import orchestrator, store
from server.chat.config import ChatConfig
from server.chat.context import Prefetch
from server.chat.orchestrator import TurnRequest, run_turn
from server.chat.tools import search_document as sd_tool
from server.chat.tools import web_search as ws_tool
from server.llm.types import (CallSettings, Capabilities, FeatureDropped, Finish, ProviderError,
                              ProviderTimeout, ProviderUnavailable, ReasoningDelta, TextDelta,
                              ToolCall, ToolCallReady, Usage)
from server.tests.chat_harness import FakeRouter, assert_fixture, member, reply, shim_pool


@pytest.fixture
def conn(db_conn, monkeypatch):
    shim_pool(monkeypatch, db_conn, store, orchestrator, ctx_mod, sd_tool)
    return db_conn


async def fake_web_search(query, count):
    return {"query": query, "results": [{"title": "t", "url": "https://example.com", "summary": "s"}]}


async def _start(user, text="Hi", sid="s-1"):
    claim = await store.begin_turn(session_id=sid, user_id=user.user_id, model_id="ollama:m", text=text,
                                   attachments=[], new_session_pins=None)
    req = TurnRequest(user_id=user.user_id, session_id=sid, model_id="ollama:m", text=text, attachments=(),
                      settings=CallSettings(), doc_id=None, timezone="UTC")
    return claim, req


async def _run(conn, router, user=None, cfg=ChatConfig(), budget=None, text="Hi"):
    user = user or await member(conn, "alice")
    claim, req = await _start(user, text=text)
    events = [e async for e in run_turn(req, claim, router=router, cfg=cfg, deployment_budget=budget)]
    return events, claim


async def _one(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def _msg(conn, mid):
    return await _one(conn, "SELECT status, finish_reason, content, thinking, tool_calls, doc_context, stats "
                            "FROM chat_messages WHERE id=%s", (mid,))


async def _claim(conn):
    row = await _one(conn, "SELECT active_turn_id FROM chat_sessions WHERE id='s-1'")
    return row[0] if row else None


async def _log(conn):
    cur = await conn.execute("SELECT kind FROM chat_events WHERE session_id='s-1' ORDER BY id")
    return [r[0] for r in await cur.fetchall()]


def types(events):
    return [e["type"] for e in events]


async def test_plain_reply_streams_saves_and_releases(conn):
    router = FakeRouter(steps=[[TextDelta("Hello "), TextDelta("there."), Usage(10, 3), Finish("stop")]])
    alice = await member(conn, "alice")
    events, claim = await _run(conn, router, user=alice)
    assert types(events) == ["start", "start-step", "text-start", "text-delta", "text-delta", "text-end",
                             "finish-step", "finish"]
    assert [e["seq"] for e in events] == list(range(1, 9))
    assert events[0]["messageId"] == claim.turn_id and events[0]["userMessageId"] == claim.user_message_id
    assert events[-1]["finishReason"] == "stop"
    assert events[-1]["usage"] == {"promptTokens": 10, "completionTokens": 3, "estimated": False}
    status, reason, content, _, _, _, stats = await _msg(conn, claim.turn_id)
    assert (status, reason, content) == ("complete", "stop", "Hello there.")
    assert (stats["model"], stats["evalCount"], stats["doneReason"]) == ("ollama:m", 3, "stop")
    assert await _claim(conn) is None
    assert await _one(conn, "SELECT prompt_tokens, eval_tokens FROM inference_usage WHERE user_id=%s",
                      (alice.user_id,)) == (10, 3)
    sent = router.calls[0]
    assert sent["tools"] == ["web_search"]                  # no open document: no search_document
    assert sent["messages"][-1].content == "Hi" and sent["messages"][-2].role == "system"
    assert await _log(conn) == ["sent", "received"]
    assert_fixture("plain", events)


async def test_reasoning_closes_before_text(conn):
    router = FakeRouter(steps=[[ReasoningDelta("think"), TextDelta("Answer"), Usage(1, 1), Finish("stop")]])
    events, claim = await _run(conn, router)
    assert types(events)[2:8] == ["reasoning-start", "reasoning-delta", "reasoning-end",
                                  "text-start", "text-delta", "text-end"]
    assert (await _msg(conn, claim.turn_id))[3] == "think"


async def test_one_tool_round_then_answer(conn, monkeypatch):
    monkeypatch.setattr(ws_tool, "web_search", fake_web_search)
    router = FakeRouter(steps=[
        [ToolCallReady(ToolCall("c1", "web_search", {"query": "news"})), Usage(5, 2), Finish("tool_calls")],
        [TextDelta("Here is the news."), Usage(20, 4), Finish("stop")]])
    events, claim = await _run(conn, router)
    assert types(events) == ["start", "start-step", "finish-step", "tool-input-available",
                             "tool-output-available", "start-step", "text-start", "text-delta", "text-end",
                             "finish-step", "finish"]
    out = next(e for e in events if e["type"] == "tool-output-available")
    assert out["output"] == {"ok": True, "chunk_count": None, "query": "news",
                             "summary_text": 'Web search for "news" returned 1 result(s).'}
    second = router.calls[1]["messages"]
    assert second[-2].role == "assistant" and second[-2].tool_calls[0].id == "c1"
    assert second[-1].role == "tool" and second[-1].tool_call_id == "c1"
    assert json.loads(second[-1].content)["query"] == "news"
    assert router.calls[1]["tools"] == []                   # CHAT_MAX_TOOL_ROUNDS=1: final step has tools off
    status, reason, content, _, tool_calls, _, _ = await _msg(conn, claim.turn_id)
    assert (status, reason, content) == ("complete", "stop", "Here is the news.")
    assert tool_calls == [{"name": "web_search", "arguments": {"query": "news"}, "result_summary": out["output"]}]
    assert events[-1]["usage"] == {"promptTokens": 25, "completionTokens": 6, "estimated": False}
    assert "tool-call" in await _log(conn)
    assert_fixture("tool_round", events)


async def test_silent_final_step_after_the_cap_is_max_steps(conn, monkeypatch):
    monkeypatch.setattr(ws_tool, "web_search", fake_web_search)
    router = FakeRouter(steps=[
        [ToolCallReady(ToolCall("c1", "web_search", {"query": "a"})), Usage(1, 1), Finish("tool_calls")],
        [Usage(1, 0), Finish("stop")]])
    events, _ = await _run(conn, router)
    assert events[-1]["type"] == "finish" and events[-1]["finishReason"] == "max-steps"


async def test_two_rounds_when_the_cap_allows_it(conn, monkeypatch):
    monkeypatch.setattr(ws_tool, "web_search", fake_web_search)
    call = lambda i: [ToolCallReady(ToolCall(f"c{i}", "web_search", {"query": "a"})), Usage(1, 1), Finish("tool_calls")]
    router = FakeRouter(steps=[call(1), call(2), reply("done")])
    events, _ = await _run(conn, router, cfg=ChatConfig(max_tool_rounds=2))
    assert [c["tools"] for c in router.calls] == [["web_search"], ["web_search"], []]
    assert events[-1]["finishReason"] == "stop"


async def test_tool_error_is_reported_and_turn_continues(conn):
    router = FakeRouter(steps=[
        [ToolCallReady(ToolCall("c1", "made_up", {})), ToolCallReady(ToolCall("c2", "web_search", {})),
         Usage(1, 1), Finish("tool_calls")],
        reply("Sorry, I couldn't search.")])
    events, claim = await _run(conn, router)
    errors = [e["errorText"] for e in events if e["type"] == "tool-output-error"]
    assert errors == ["Unknown tool: made_up", "query is required and must be non-empty."]
    assert events[-1]["type"] == "finish"
    assert (await _msg(conn, claim.turn_id))[2] == "Sorry, I couldn't search."


@pytest.mark.parametrize("exc,code", [
    (ProviderError(500, "out of memory"), "provider_error"),
    (ProviderUnavailable("refused"), "provider_unavailable"),
    (ProviderTimeout(), "provider_timeout"),
])
async def test_provider_failure_saves_the_partial_reply(conn, exc, code):
    router = FakeRouter(steps=[([TextDelta("Partial")], exc)])
    events, claim = await _run(conn, router)
    assert events[-1]["type"] == "error" and events[-1]["code"] == code
    assert (await _msg(conn, claim.turn_id))[:3] == ("error", "error", "Partial")
    assert await _claim(conn) is None
    assert "error" in await _log(conn)
    if code == "provider_error":
        assert "out of memory" in events[-1]["message"]
        assert_fixture("error", events)


async def test_budget_runs_out_between_steps(conn, monkeypatch):
    monkeypatch.setattr(ws_tool, "web_search", fake_web_search)
    alice = await member(conn, "alice")
    await conn.execute("UPDATE users SET inference_daily_token_budget = 10 WHERE id = %s", (alice.user_id,))
    router = FakeRouter(steps=[
        [ToolCallReady(ToolCall("c1", "web_search", {"query": "a"})), Usage(8, 4), Finish("tool_calls")],
        reply()])
    events, claim = await _run(conn, router, user=alice)
    assert (events[-1]["type"], events[-1]["code"]) == ("error", "budget_exhausted")
    assert len(router.calls) == 1
    assert (await _msg(conn, claim.turn_id))[0] == "error"
    assert "budget" in await _log(conn)


async def test_disconnect_mid_step_saves_aborted(conn):
    router = FakeRouter(steps=[[TextDelta("Part"), TextDelta("ial"), Usage(1, 1), Finish("stop")]])
    alice = await member(conn, "alice")
    claim, req = await _start(alice)
    agen = run_turn(req, claim, router=router, cfg=ChatConfig(), deployment_budget=None)
    async for e in agen:
        if e["type"] == "text-delta":
            break
    await agen.aclose()
    assert (await _msg(conn, claim.turn_id))[:3] == ("aborted", "aborted", "Part")
    assert await _claim(conn) is None
    assert "aborted" in await _log(conn)


async def test_cancel_during_tool_saves_aborted_and_releases_claim(conn, monkeypatch):
    started = asyncio.Event()

    async def slow_web_search(query, count):
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(ws_tool, "web_search", slow_web_search)
    router = FakeRouter(steps=[[TextDelta("Let me look. "), ToolCallReady(ToolCall("c1", "web_search", {"query": "x"})),
                                Usage(1, 1), Finish("tool_calls")]])
    alice = await member(conn, "alice")
    claim, req = await _start(alice)

    async def consume():
        async for _ in run_turn(req, claim, router=router, cfg=ChatConfig(), deployment_budget=None):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await _msg(conn, claim.turn_id))[:3] == ("aborted", "aborted", "Let me look. ")
    assert await _claim(conn) is None


async def test_model_without_tools_is_offered_none(conn):
    router = FakeRouter(caps=Capabilities(tools=False))
    await _run(conn, router)
    assert router.calls[0]["tools"] == []


async def test_missing_usage_is_estimated_and_recorded(conn):
    router = FakeRouter(steps=[[TextDelta("x" * 40), Finish("stop")]])
    events, claim = await _run(conn, router)
    assert events[-1]["usage"]["estimated"] is True and events[-1]["usage"]["completionTokens"] == 10
    assert (await _msg(conn, claim.turn_id))[6]["usageEstimated"] is True


async def test_dropped_feature_becomes_a_notice_and_a_log_line(conn):
    router = FakeRouter(steps=[[FeatureDropped("tools"), *reply()]])
    events, _ = await _run(conn, router)
    notice = next(e for e in events if e["type"] == "data-notice")
    assert notice["code"] == "tools_unsupported"
    assert "tool-fallback" in await _log(conn)
    assert_fixture("notice", events)


async def test_prefetch_note_comes_before_the_first_step_and_is_saved(conn, monkeypatch):
    note = {"kind": "prefetch", "docId": "d" * 64, "docName": "Thesis.pdf", "count": 1, "topScore": 0.9, "pages": [3]}

    async def fake_prefetch(doc, question, cfg):
        return Prefetch([{"page": 3, "score": 0.9, "text": "passage"}], 0.9, note)

    monkeypatch.setattr(ctx_mod, "prefetch", fake_prefetch)
    events, claim = await _run(conn, FakeRouter())
    assert types(events)[:3] == ["start", "data-context", "start-step"]
    assert events[1]["items"] == [note]
    assert (await _msg(conn, claim.turn_id))[5] == {"notes": [note]}
    assert_fixture("context", events)


async def test_second_turn_sends_the_first_as_history(conn):
    router = FakeRouter()
    alice = await member(conn, "alice")
    claim, req = await _start(alice, text="first")
    [_ async for _ in run_turn(req, claim, router=router, cfg=ChatConfig(), deployment_budget=None)]
    claim, req = await _start(alice, text="second")
    [_ async for _ in run_turn(req, claim, router=router, cfg=ChatConfig(), deployment_budget=None)]
    sent = [(m.role, m.content) for m in router.calls[1]["messages"] if m.role != "system"]
    assert sent == [("user", "first"), ("assistant", "Hello there."), ("user", "second")]


async def test_session_deleted_mid_turn_finishes_quietly(conn):
    router = FakeRouter(steps=[[TextDelta("a"), TextDelta("b"), Usage(1, 1), Finish("stop")]])
    alice = await member(conn, "alice")
    claim, req = await _start(alice)
    events = []
    async for e in run_turn(req, claim, router=router, cfg=ChatConfig(), deployment_budget=None):
        events.append(e)
        if e["type"] == "text-start":
            await conn.execute("DELETE FROM chat_sessions WHERE id='s-1'")
    assert events[-1]["type"] == "finish"
    assert (await _one(conn, "SELECT count(*) FROM chat_messages WHERE session_id='s-1'"))[0] == 0
```

- [ ] **Step 3: Run to confirm failure**

Run: `.venv/bin/python -m pytest server/tests/test_chat_orchestrator.py -q`
Expected: `ModuleNotFoundError: No module named 'server.chat.orchestrator'`.

- [ ] **Step 4: Write `server/chat/orchestrator.py`**

```python
"""One chat turn, server-side (spec §5.1).

run_turn is an async generator of event dicts (spec §6); routers/chat_turns.py
frames them as SSE. Per step: stream one model call; if it asked for tools, run
them and go again; after CHAT_MAX_TOOL_ROUNDS rounds, one last step with tools
OFF, so the turn always ends in an answer (the OpenAI Agents SDK max_turns
idea). Provider and tool failures become an `error` event or a tool error the
model sees; they never escape. A disconnect (the Stop button, a closed tab)
arrives as cancellation: the partial reply is saved `aborted` and the claim is
released, even though the task is being torn down (spec §5.4).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from ..db import get_pool
from ..llm.router import UnknownModel
from ..llm.types import (Attachment, CallSettings, Capabilities, FeatureDropped, Finish, Message,
                         ProviderError, ProviderTimeout, ProviderUnavailable, ReasoningDelta,
                         TextDelta, ToolCallReady, Usage)
from ..services import inference_budget
from ..services.doc_search import ReadableDoc, readable_doc
from . import store
from .config import ChatConfig
from .context import TurnInput, build_context
from .store import TurnClaim, title_from_prompt
from .tools import ToolContext, available_tools, run_tool

logger = logging.getLogger(__name__)

ERROR_TEXT = {
    "provider_unavailable": "Can't reach the model provider.",
    "provider_timeout": "The model provider stopped responding.",
    "budget_exhausted": "Daily inference budget exhausted.",
    "model_not_allowed": "That model isn't available on this server.",
    "internal_error": "Something went wrong on the server.",
}
# Ruling R1: what today's toasts said when a model rejected a feature.
_FEATURE_NOTICE = {
    "tools": ("tools_unsupported", "This model rejected tools — answered without them."),
    "thinking": ("thinking_unsupported", "This model rejected thinking — answered without it."),
    "think_level": ("think_level_unsupported", "This model rejected the thinking level — used plain thinking."),
}
_FEATURE_LOG = {"tools": "tool-fallback", "thinking": "think-fallback", "think_level": "think-fallback"}
_BACKGROUND: set[asyncio.Task] = set()   # end-of-turn saves that outlive a cancelled request


@dataclass(frozen=True)
class TurnRequest:
    user_id: str
    session_id: str
    model_id: str                         # canonical provider:name
    text: str
    attachments: tuple[Attachment, ...]
    settings: CallSettings
    doc_id: str | None
    timezone: str | None


@dataclass
class _State:
    content: str = ""
    thinking: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    doc_context: dict | None = None
    steps: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated: bool = False
    last_usage: Usage | None = None
    last_finish: str = "stop"
    finish_reason: str = "stop"

    def add(self, usage: Usage, finish: str) -> None:
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        self.estimated = self.estimated or usage.estimated
        self.last_usage, self.last_finish = usage, finish

    def usage_json(self) -> dict[str, Any]:
        return {"promptTokens": self.prompt_tokens, "completionTokens": self.completion_tokens,
                "estimated": self.estimated}

    def stats(self, model_id: str, started: float) -> dict[str, Any]:
        """The message's stats disclosure. Per-request figures are the LAST
        step's, as today (the truncation notice and tokens/s read them)."""
        u = self.last_usage
        d = u.detail if u else {}
        return {"model": model_id, "doneReason": self.last_finish if self.steps else None,
                "promptEvalCount": u.prompt_tokens if u else None,
                "evalCount": u.completion_tokens if u else None,
                "totalNs": int((time.monotonic() - started) * 1e9),
                "loadNs": d.get("load_duration"), "promptEvalNs": d.get("prompt_eval_duration"),
                "evalNs": d.get("eval_duration"), "steps": self.steps, "usageEstimated": self.estimated}


def _usage_json(u: Usage) -> dict[str, Any]:
    return {"promptTokens": u.prompt_tokens, "completionTokens": u.completion_tokens, "estimated": u.estimated}


async def run_turn(req: TurnRequest, claim: TurnClaim, *, router: Any, cfg: ChatConfig,
                   deployment_budget: int | None) -> AsyncIterator[dict[str, Any]]:
    seq = 0

    def ev(kind: str, **fields: Any) -> dict[str, Any]:
        nonlocal seq
        seq += 1
        return {"type": kind, "seq": seq, **fields}

    state = _State()
    started = time.monotonic()
    outcome = "error"   # complete | aborted | error
    beat = asyncio.create_task(_heartbeat(claim))
    logger.info("chat turn start user=%s session=%s model=%s", req.user_id, req.session_id, req.model_id)
    try:
        yield ev("start", runId=claim.turn_id, sessionId=claim.session_id,
                 userMessageId=claim.user_message_id, messageId=claim.assistant_message_id)
        await _log_sent(claim, req)
        caps = await _capabilities(router, req.model_id)
        doc = await _open_doc(req)
        pins, history = await store.load_turn_context(
            claim.session_id, exclude=(claim.user_message_id, claim.assistant_message_id))
        built = await build_context(TurnInput(
            user_id=req.user_id, text=req.text, attachments=req.attachments, doc=doc,
            timezone=req.timezone, pins=pins, history=history,
            window=req.settings.num_ctx or caps.context_window, now=datetime.now(timezone.utc)), cfg)
        if built.notes:
            state.doc_context = {"notes": built.notes}
            yield ev("data-context", items=built.notes)
        tool_ctx = ToolContext(req.user_id, doc)
        offered = [] if caps.tools is False else available_tools(tool_ctx)
        messages = list(built.messages)
        rounds = 0
        while True:
            if await _over_budget(req.user_id, deployment_budget):
                await store.add_event(claim.session_id, "budget", "daily token budget exhausted")
                yield ev("error", code="budget_exhausted", message=ERROR_TEXT["budget_exhausted"])
                return
            state.steps += 1
            step = state.steps
            tools_now = offered if rounds < cfg.max_tool_rounds else []
            capped = bool(offered) and not tools_now
            yield ev("start-step", step=step)
            text = reasoning = ""
            calls: list = []
            usage: Usage | None = None
            finish = "stop"
            open_text = open_reasoning = False
            last_flush = time.monotonic()
            async with aclosing(router.stream_chat(req.model_id, messages, [t.spec for t in tools_now],
                                                   req.settings)) as stream:
                async for chunk in stream:
                    if isinstance(chunk, ReasoningDelta):
                        if not open_reasoning:
                            open_reasoning = True
                            yield ev("reasoning-start", id=f"r{step}")
                        reasoning += chunk.text
                        state.thinking += chunk.text
                        yield ev("reasoning-delta", id=f"r{step}", delta=chunk.text)
                    elif isinstance(chunk, TextDelta):
                        if open_reasoning:
                            open_reasoning = False
                            yield ev("reasoning-end", id=f"r{step}")
                        if not open_text:
                            open_text = True
                            yield ev("text-start", id=f"t{step}")
                        text += chunk.text
                        state.content += chunk.text
                        yield ev("text-delta", id=f"t{step}", delta=chunk.text)
                    elif isinstance(chunk, ToolCallReady):
                        if tools_now:   # a tools-off step cannot call tools
                            calls.append(chunk.call)
                    elif isinstance(chunk, Usage):
                        usage = chunk
                    elif isinstance(chunk, Finish):
                        finish = chunk.reason
                    elif isinstance(chunk, FeatureDropped):
                        code, notice = _FEATURE_NOTICE[chunk.feature]
                        await store.add_event(claim.session_id, _FEATURE_LOG[chunk.feature], notice)
                        yield ev("data-notice", code=code, message=notice)
                    if time.monotonic() - last_flush >= store.FLUSH_EVERY_S:
                        await _save(claim, state)   # a reload mid-reply shows the text so far
                        last_flush = time.monotonic()
            if open_reasoning:
                yield ev("reasoning-end", id=f"r{step}")
            if open_text:
                yield ev("text-end", id=f"t{step}")
            usage = await _record_usage(req.user_id, usage, messages, text + reasoning)
            state.add(usage, finish)
            yield ev("finish-step", step=step, usage=_usage_json(usage), finishReason=finish)
            await _save(claim, state)
            if not calls:
                state.finish_reason = ("max-steps" if capped and not text.strip()
                                       else "length" if finish == "length" else "stop")
                break
            rounds += 1
            messages.append(Message("assistant", text, tool_calls=tuple(calls)))
            await store.add_event(claim.session_id, "tool-call",
                                  f"{len(calls)} tool call{'s' if len(calls) > 1 else ''}")
            for call in calls:
                yield ev("tool-input-available", toolCallId=call.id, toolName=call.name, input=call.arguments)
                run = await run_tool(call, tool_ctx, tools_now)
                state.tool_calls.append(run.summary)
                if run.ok:
                    yield ev("tool-output-available", toolCallId=call.id, output=run.summary["result_summary"])
                else:
                    yield ev("tool-output-error", toolCallId=call.id, errorText=run.result["error"])
                messages.append(Message("tool", json.dumps(run.result, indent=2, ensure_ascii=False),
                                        tool_call_id=call.id, name=call.name))
            await _save(claim, state)
        if state.last_finish == "length":
            await store.add_event(claim.session_id, "truncated", "reply hit the length limit")
        await store.add_event(claim.session_id, "received", f"assistant reply ({len(state.content)} chars)")
        outcome = "complete"
        yield ev("finish", usage=state.usage_json(), finishReason=state.finish_reason,
                 stats=state.stats(req.model_id, started))
    except (asyncio.CancelledError, GeneratorExit):
        if outcome != "complete":   # a disconnect while sending `finish` doesn't undo a finished reply
            outcome = "aborted"
        raise
    except (ProviderUnavailable, ProviderTimeout, ProviderError, UnknownModel) as e:
        code, message = _provider_error(e)
        await store.add_event(claim.session_id, "error", message)
        yield ev("error", code=code, message=message)
    except Exception:
        logger.exception("chat turn failed user=%s session=%s", req.user_id, req.session_id)
        await store.add_event(claim.session_id, "error", ERROR_TEXT["internal_error"])
        yield ev("error", code="internal_error", message=ERROR_TEXT["internal_error"])
    finally:
        beat.cancel()
        await _finalize(claim, state, outcome, req, started)


def _provider_error(e: Exception) -> tuple[str, str]:
    if isinstance(e, ProviderTimeout):
        return "provider_timeout", ERROR_TEXT["provider_timeout"]
    if isinstance(e, ProviderUnavailable):
        return "provider_unavailable", ERROR_TEXT["provider_unavailable"]
    if isinstance(e, ProviderError):
        return "provider_error", f"The model provider returned an error: {e.safe_message}"
    return "model_not_allowed", ERROR_TEXT["model_not_allowed"]


async def _finalize(claim: TurnClaim, state: _State, outcome: str, req: TurnRequest, started: float) -> None:
    """Write the end of the turn, even from a cancelled task: the write runs in
    its own task under asyncio.shield, so a second cancellation (anyio re-raises
    on every await in a cancelled scope) can't stop it releasing the claim."""
    finish_reason = state.finish_reason if outcome == "complete" else outcome

    async def write() -> None:
        if outcome == "aborted":
            await store.add_event(claim.session_id, "aborted", "user stopped the stream")
        await store.finish_turn(claim, status=outcome, finish_reason=finish_reason, content=state.content,
                                thinking=state.thinking, tool_calls=state.tool_calls or None,
                                doc_context=state.doc_context, stats=state.stats(req.model_id, started))

    task = asyncio.ensure_future(write())
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        pass   # the save continues in the background
    except Exception:
        logger.warning("Saving the end of turn %s failed", claim.turn_id, exc_info=True)
    logger.info("chat turn end user=%s session=%s model=%s status=%s reason=%s steps=%d tokens=%d+%d "
                "duration_ms=%d", req.user_id, req.session_id, req.model_id, outcome, finish_reason,
                state.steps, state.prompt_tokens, state.completion_tokens,
                int((time.monotonic() - started) * 1000))


async def _heartbeat(claim: TurnClaim) -> None:
    """Refresh the claim on a timer, not per token: a long prompt can take
    minutes before the first token arrives (spec §5.5)."""
    while True:
        await asyncio.sleep(store.HEARTBEAT_S)
        try:
            if not await store.heartbeat(claim.session_id, claim.turn_id):
                logger.warning("Turn %s no longer holds its session's claim", claim.turn_id)
                return
        except Exception:
            logger.warning("Heartbeat for turn %s failed", claim.turn_id, exc_info=True)


async def _save(claim: TurnClaim, state: _State) -> None:
    await store.save_progress(claim.assistant_message_id, content=state.content, thinking=state.thinking,
                              tool_calls=state.tool_calls or None, doc_context=state.doc_context)


async def _log_sent(claim: TurnClaim, req: TurnRequest) -> None:
    await store.add_event(claim.session_id, "sent",
                          f"prompt: {title_from_prompt(req.text or '(attachment only)')}")
    if req.attachments:
        images = sum(1 for a in req.attachments if a.kind == "image")
        audio = len(req.attachments) - images
        parts = ([f"{images} image{'s' if images > 1 else ''}"] if images else []) + \
                ([f"{audio} audio"] if audio else [])
        await store.add_event(claim.session_id, "attached", ", ".join(parts))


async def _capabilities(router: Any, model_id: str) -> Capabilities:
    try:
        return await router.capabilities(model_id)
    except Exception:  # noqa: BLE001 — unknown capabilities are safe: nothing is refused
        logger.warning("Capability lookup failed for %s", model_id, exc_info=True)
        return Capabilities()


async def _open_doc(req: TurnRequest) -> ReadableDoc | None:
    if not req.doc_id:
        return None
    try:
        async with get_pool().connection() as conn:
            return await readable_doc(conn, req.doc_id, req.user_id)
    except Exception:  # noqa: BLE001 — no document context is a safe fallback
        logger.warning("Open-document lookup failed for %s", req.doc_id, exc_info=True)
        return None


async def _over_budget(user_id: str, deployment_budget: int | None) -> bool:
    try:
        async with get_pool().connection() as conn:
            return await inference_budget.over_budget(conn, user_id, deployment_budget)
    except Exception:
        logger.warning("Budget check failed (fail-open)", exc_info=True)
        return False


async def _record_usage(user_id: str, usage: Usage | None, messages: list[Message], output: str) -> Usage:
    """Record the step's usage now. A provider that sends no counts is charged
    an estimate (chars/4), so it can't be used for free (spec §5.6)."""
    if usage is None:
        prompt = math.ceil(sum(len(m.content) for m in messages) / 4)
        usage = Usage(prompt, math.ceil(len(output) / 4), estimated=True)
        logger.warning("Provider returned no usage; recorded an estimate (%d+%d tokens)",
                       usage.prompt_tokens, usage.completion_tokens)
    try:
        async with get_pool().connection() as conn:
            await inference_budget.record_usage(conn, user_id, usage.prompt_tokens, usage.completion_tokens)
    except Exception:
        logger.warning("Usage accounting failed (fail-open)", exc_info=True)
    return usage
```

- [ ] **Step 5: Generate the contract fixtures once, then run normally**

Run: `UPDATE_CHAT_FIXTURES=1 .venv/bin/python -m pytest server/tests/test_chat_orchestrator.py -q && .venv/bin/python -m pytest server/tests/test_chat_orchestrator.py -q`
Expected: both runs pass, and `server/tests/fixtures/chat_events/` holds `plain`, `tool_round`, `error`, `notice` and `context` `.jsonl`. Open `tool_round.jsonl` and check by eye that it reads as spec §6 describes (`start` … `finish`, `seq` 1..11).

- [ ] **Step 6: Run the whole backend suite**

Run: `.venv/bin/python -m pytest server/tests -q`
Expected: all pass.

- [ ] **Step 7: Commit** (ask the user first)

```bash
git add server/chat/orchestrator.py server/tests/chat_harness.py server/tests/test_chat_orchestrator.py server/tests/fixtures/chat_events
git commit -m "feat(chat): the orchestrator — step loop, tools-off cap, typed events, aborted saves (C1)"
```

---

### Task 9: `POST /v1/chat/sessions/{id}/turns`, SSE framing and app wiring

**Files:**
- Create: `server/routers/chat_turns.py`
- Modify: `server/app.py` (include the router; recover stale turns at startup)
- Test: `server/tests/test_chat_turns.py`

**Interfaces:**
- Consumes:
  - Task 3: `get_router()`
  - Task 5: `store.session_info`, `begin_turn`, `recover_stale`, `NewAttachment`, `NotOwner`, `TurnInProgress`
  - Task 7: `get_chat_config()`
  - Task 8: `TurnRequest`, `run_turn`
  - Existing: `inference_budget`, `model_router.get_config().daily_token_budget`
- Produces: `POST /v1/chat/sessions/{session_id}/turns` per spec §7.1–7.2, plus `session: {pins}` (R4). The response is `text/event-stream` with `Cache-Control: no-cache` and `X-Accel-Buffering: no`; each frame is `data: {json}\n\n`, ending with `data: [DONE]\n\n`. Refusals are listed in Step 3.

- [ ] **Step 1: Write the failing tests** — `server/tests/test_chat_turns.py`:

```python
import base64
import json

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.chat import context as ctx_mod
from server.chat import orchestrator, store
from server.chat.tools import search_document as sd_tool
from server.llm.types import Capabilities
from server.routers import chat_turns
from server.services import inference_budget
from server.tests.chat_harness import FakeRouter, member, shim_pool

BODY = {"message": {"content": "Hi"}, "model": "ollama:m", "context": {"timezone": "UTC"}}
PNG = base64.b64encode(b"\x89PNG fake").decode()


@pytest.fixture
def app(db_conn, monkeypatch):
    shim_pool(monkeypatch, db_conn, store, orchestrator, ctx_mod, sd_tool, chat_turns)
    fake = FakeRouter()
    monkeypatch.setattr(chat_turns, "get_router", lambda: fake)
    for var in ("INFERENCE_DAILY_TOKEN_BUDGET", "CHAT_MAX_REQUEST_MB"):
        monkeypatch.delenv(var, raising=False)
    application = FastAPI()
    application.include_router(chat_turns.router)
    current: dict = {}
    application.dependency_overrides[deps.get_current_user] = lambda: current["p"]
    return application, fake, current


async def _post(app, principal, sid="s-1", body=BODY, **kw):
    application, _, current = app
    current["p"] = principal
    async with httpx.AsyncClient(transport=ASGITransport(app=application), base_url="http://t") as c:
        if "content" in kw:
            return await c.post(f"/v1/chat/sessions/{sid}/turns", headers={"content-type": "application/json"}, **kw)
        return await c.post(f"/v1/chat/sessions/{sid}/turns", json=body)


def _frames(text):
    out = []
    for frame in text.split("\n\n"):
        if frame.startswith("data: "):
            data = frame[6:]
            out.append(data if data == "[DONE]" else json.loads(data))
    return out


async def _one(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def test_a_turn_streams_sse_and_creates_the_session(db_conn, app):
    alice = await member(db_conn, "alice")
    body = {**BODY, "session": {"pins": [{"id": "p1", "text": "pinned"}]}}
    r = await _post(app, alice, body=body)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache" and r.headers["x-accel-buffering"] == "no"
    frames = _frames(r.text)
    assert frames[0]["type"] == "start" and frames[-2]["type"] == "finish" and frames[-1] == "[DONE]"
    assert await _one(db_conn, "SELECT title, pins, active_turn_id FROM chat_sessions WHERE id='s-1'") == (
        "Hi", [{"id": "p1", "text": "pinned"}], None)


async def test_someone_elses_session_is_404(db_conn, app):
    alice, bob = await member(db_conn, "alice"), await member(db_conn, "bob")
    await db_conn.execute("INSERT INTO chat_sessions (id, user_id) VALUES ('s-1', %s)", (alice.user_id,))
    r = await _post(app, bob)
    assert r.status_code == 404 and r.json()["detail"]["error"] == "not_found"


async def test_second_tab_gets_409_and_first_turn_is_untouched(db_conn, app):
    alice = await member(db_conn, "alice")
    first = await store.begin_turn(session_id="s-1", user_id=alice.user_id, model_id="ollama:m", text="first",
                                   attachments=[], new_session_pins=None)
    r = await _post(app, alice)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "turn_in_progress"
    assert (await _one(db_conn, "SELECT active_turn_id FROM chat_sessions WHERE id='s-1'"))[0] == first.turn_id
    assert (await _one(db_conn, "SELECT status FROM chat_messages WHERE id=%s", (first.turn_id,)))[0] == "streaming"


async def test_body_over_the_cap_is_413(db_conn, app, monkeypatch):
    monkeypatch.setenv("CHAT_MAX_REQUEST_MB", "1")
    alice = await member(db_conn, "alice")
    big = base64.b64encode(b"x" * 1_200_000).decode()
    body = {**BODY, "message": {"content": "Hi", "attachments": [
        {"kind": "image", "mime": "image/png", "name": "a.png", "base64": big}]}}
    r = await _post(app, alice, body=body)
    assert r.status_code == 413
    assert r.json()["detail"] == {"error": "too_large", "message": "Attachments too large (limit 1 MB).", "limit_mb": 1}


async def test_model_not_allowed_is_422(db_conn, app):
    app[1].allowed = False
    r = await _post(app, await member(db_conn, "alice"))
    assert r.status_code == 422 and r.json()["detail"]["error"] == "model_not_allowed"


async def test_attachment_the_model_cant_read_is_422_and_nothing_is_written(db_conn, app):
    app[1].caps = Capabilities(tools=True, vision=False)
    body = {**BODY, "message": {"content": "look", "attachments": [
        {"kind": "image", "mime": "image/png", "name": "a.png", "base64": PNG}]}}
    r = await _post(app, await member(db_conn, "alice"), body=body)
    assert r.status_code == 422
    assert r.json()["detail"] == {"error": "attachment_unsupported", "message": "This model can't read images.",
                                  "kind": "image"}
    assert await _one(db_conn, "SELECT 1 FROM chat_sessions WHERE id='s-1'") is None


@pytest.mark.parametrize("att", [
    {"kind": "image", "mime": "image/png", "name": "a.png", "base64": "not base64!!"},
    {"kind": "image", "mime": "audio/wav", "name": "a.wav", "base64": PNG},
])
async def test_unreadable_attachment_is_422(db_conn, app, att):
    body = {**BODY, "message": {"content": "look", "attachments": [att]}}
    r = await _post(app, await member(db_conn, "alice"), body=body)
    assert r.status_code == 422 and r.json()["detail"]["error"] == "invalid_attachment"


async def test_empty_message_without_pins_is_422(db_conn, app):
    r = await _post(app, await member(db_conn, "alice"), body={**BODY, "message": {"content": "  "}})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "empty_message"


async def test_pins_only_message_is_allowed(db_conn, app):
    body = {**BODY, "message": {"content": ""}, "session": {"pins": [{"id": "p1", "text": "x"}]}}
    r = await _post(app, await member(db_conn, "alice"), body=body)
    assert r.status_code == 200


async def test_budget_already_spent_is_429_with_reset_time(db_conn, app):
    alice = await member(db_conn, "alice")
    await db_conn.execute("UPDATE users SET inference_daily_token_budget = 10 WHERE id = %s", (alice.user_id,))
    await inference_budget.record_usage(db_conn, alice.user_id, 10, 0)
    r = await _post(app, alice)
    detail = r.json()["detail"]
    assert r.status_code == 429 and detail["error"] == "budget_exhausted"
    assert detail["remaining_tokens"] == 0 and detail["reset_at"].endswith("Z")


async def test_no_providers_is_503(db_conn, app):
    app[1].providers = False
    r = await _post(app, await member(db_conn, "alice"))
    assert r.status_code == 503 and r.json()["detail"]["error"] == "no_providers"


async def test_missing_chat_capability_is_403(db_conn, app):
    r = await _post(app, await member(db_conn, "alice", caps=("reader",)))
    assert r.status_code == 403


async def test_unknown_fields_are_422(db_conn, app):
    r = await _post(app, await member(db_conn, "alice"), body={**BODY, "history": []})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "invalid_request"


async def test_bad_timezone_still_streams(db_conn, app):
    r = await _post(app, await member(db_conn, "alice"), body={**BODY, "context": {"timezone": "Mars/Olympus"}})
    assert r.status_code == 200
    volatile = app[1].calls[0]["messages"][-2].content
    assert volatile.endswith("(UTC)")


async def test_bare_model_names_are_canonicalized(db_conn, app):
    await _post(app, await member(db_conn, "alice"), body={**BODY, "model": "qwen2.5:7b"})
    assert app[1].calls[0]["model"] == "ollama:qwen2.5:7b"
    assert (await _one(db_conn, "SELECT model FROM chat_sessions WHERE id='s-1'"))[0] == "ollama:qwen2.5:7b"
```

- [ ] **Step 2: Run to confirm failure**

Run: `.venv/bin/python -m pytest server/tests/test_chat_turns.py -q`
Expected: `ImportError: cannot import name 'chat_turns'`.

- [ ] **Step 3: Write `server/routers/chat_turns.py`**

```python
"""POST /v1/chat/sessions/{id}/turns — one chat turn, streamed as SSE (spec §6, §7).

Everything that can refuse does so BEFORE the first byte, as an ordinary HTTP
refusal (§7.2): 503 no_providers, 413 too_large, 422 invalid_request /
invalid_attachment / empty_message / model_not_allowed / attachment_unsupported,
404 not_found, 429 budget_exhausted, 409 turn_in_progress. After the first
byte, failures arrive as an `error` event.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
from contextlib import aclosing
from typing import Annotated, Any, AsyncIterator, Literal

from fastapi import APIRouter, Depends, Path as PathParam, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..auth.deps import Principal, require_capability
from ..chat import store
from ..chat.config import get_chat_config
from ..chat.orchestrator import TurnRequest, run_turn
from ..db import get_pool, is_ready
from ..http_errors import refusal
from ..llm.router import get_router
from ..llm.types import Attachment, CallSettings, Capabilities
from ..services import inference_budget, model_router

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/chat/sessions", tags=["chat-turns"])

SessionId = Annotated[str, PathParam(pattern=r"^[A-Za-z0-9._-]{1,128}$")]
NOT_FOUND = "This chat doesn't exist or you don't have access."


class AttachmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(default=None, max_length=100)
    kind: Literal["image", "audio"]
    mime: str = Field(min_length=3, max_length=100)
    name: str = Field(default="", max_length=255)
    size: int = Field(default=0, ge=0)
    base64: str


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(default="", max_length=100_000)
    attachments: list[AttachmentIn] = Field(default_factory=list, max_length=10)


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    think: Literal["off", "on", "low", "medium", "high"] = "off"
    num_ctx: int | None = Field(default=None, ge=256, le=2_097_152)
    keep_alive: str | int | None = None
    num_predict: int | None = Field(default=None, ge=1, le=1_000_000)


class ContextIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    timezone: str | None = Field(default=None, max_length=64)


class NewSessionIn(BaseModel):
    """Ruling R4: used only when this turn creates the session."""
    model_config = ConfigDict(extra="forbid")
    pins: list[dict[str, Any]] = Field(default_factory=list, max_length=6)


class TurnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: MessageIn
    model: str = Field(min_length=1, max_length=300)
    settings: SettingsIn = Field(default_factory=SettingsIn)
    context: ContextIn = Field(default_factory=ContextIn)
    session: NewSessionIn | None = None


async def _read_capped(request: Request, limit_mb: int) -> bytes:
    """The body, refused as soon as it passes the cap — never buffered whole first."""
    limit = limit_mb * 1024 * 1024
    too_large = refusal(413, "too_large", f"Attachments too large (limit {limit_mb} MB).", limit_mb=limit_mb)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise too_large
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise too_large
    return bytes(buf)


def _decode(attachments: list[AttachmentIn]) -> list[store.NewAttachment]:
    out = []
    for a in attachments:
        label = a.name or "An attachment"
        if not a.mime.startswith(f"{a.kind}/"):
            raise refusal(422, "invalid_attachment", f"{label} is not a valid {a.kind} file.")
        try:
            data = base64.b64decode(a.base64, validate=True)
        except (binascii.Error, ValueError):
            data = b""
        if not data:
            raise refusal(422, "invalid_attachment", f"{label} could not be read.")
        out.append(store.NewAttachment(kind=a.kind, mime=a.mime, name=a.name, data=data, client_id=a.id))
    return out


async def _budget_state_if_over(user_id: str, budget: int | None) -> dict | None:
    try:
        async with get_pool().connection() as conn:
            if await inference_budget.over_budget(conn, user_id, budget):
                return await inference_budget.budget_state(conn, user_id, budget)
    except Exception:
        # Fail open — chat must survive a Postgres hiccup here (today's gateway rule).
        logger.warning("Budget pre-check failed (fail-open)", exc_info=True)
    return None


async def _capabilities(llm: Any, model_id: str) -> Capabilities:
    try:
        return await llm.capabilities(model_id)
    except Exception:  # noqa: BLE001 — unknown means nothing is refused
        return Capabilities()


async def _sse(events: AsyncIterator[dict]) -> AsyncIterator[bytes]:
    # aclosing: a client that hangs up closes run_turn too, which saves `aborted`.
    async with aclosing(events) as stream:
        async for ev in stream:
            yield f"data: {json.dumps(ev, ensure_ascii=False, separators=(',', ':'))}\n\n".encode()
    yield b"data: [DONE]\n\n"


@router.post("/{session_id}/turns")
async def post_turn(session_id: SessionId, request: Request,
                    principal: Principal = Depends(require_capability("chat"))):
    if not is_ready():
        raise refusal(503, "db_unavailable", "Chat is offline: the database is unavailable.")
    llm = get_router()
    if not llm.has_providers():
        raise refusal(503, "no_providers", "No model provider is configured.")
    cfg = get_chat_config()
    raw = await _read_capped(request, cfg.max_request_mb)
    try:
        body = TurnIn.model_validate_json(raw)
    except ValidationError as e:
        raise refusal(422, "invalid_request", "The request is malformed.",
                      errors=e.errors(include_url=False, include_input=False, include_context=False))

    info = await store.session_info(session_id)
    if info is not None and info.owner_id != principal.user_id:
        raise refusal(404, "not_found", NOT_FOUND)
    attachments = _decode(body.message.attachments)
    pins = info.pins if info is not None else (body.session.pins if body.session else [])
    if not body.message.content.strip() and not attachments and not pins:
        raise refusal(422, "empty_message", "Type a message or attach something first.")
    if not llm.is_allowed(body.model):
        raise refusal(422, "model_not_allowed", "That model isn't available on this server.")
    model_id = llm.canonical_id(body.model)
    caps = await _capabilities(llm, model_id)
    for kind, flag, word in (("image", caps.vision, "images"), ("audio", caps.audio, "audio")):
        if flag is False and any(a.kind == kind for a in attachments):
            raise refusal(422, "attachment_unsupported", f"This model can't read {word}.", kind=kind)
    budget = model_router.get_config().daily_token_budget
    state = await _budget_state_if_over(principal.user_id, budget)
    if state is not None:
        raise refusal(429, "budget_exhausted", "Daily inference budget exhausted.", **state)
    try:
        claim = await store.begin_turn(
            session_id=session_id, user_id=principal.user_id, model_id=model_id,
            text=body.message.content, attachments=attachments,
            new_session_pins=body.session.pins if body.session else None)
    except store.NotOwner:
        raise refusal(404, "not_found", NOT_FOUND)
    except store.TurnInProgress:
        raise refusal(409, "turn_in_progress", "A reply is still being written in this chat.")

    req = TurnRequest(
        user_id=principal.user_id, session_id=session_id, model_id=model_id, text=body.message.content,
        attachments=tuple(Attachment(a.kind, a.mime, base64.b64encode(a.data).decode("ascii"), a.name)
                          for a in attachments),
        settings=CallSettings(think=body.settings.think, num_ctx=body.settings.num_ctx,
                              keep_alive=body.settings.keep_alive, num_predict=body.settings.num_predict),
        doc_id=body.context.doc_id, timezone=body.context.timezone)
    events = run_turn(req, claim, router=llm, cfg=cfg, deployment_budget=budget)
    return StreamingResponse(_sse(events), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

- [ ] **Step 4: Wire it into the app** — `server/app.py`:
  - Add the imports `from .routers.chat_turns import router as chat_turns_router` and `from .chat import store as chat_store`.
  - Add `app.include_router(chat_turns_router)` next to `app.include_router(chat_sessions_router)`.
  - In `_startup`, right after the `if not ok:` block, add:

```python
        else:
            # A crashed worker leaves its turn 'streaming'. Only claims whose
            # heartbeat stopped are touched, so other live workers are safe.
            try:
                await chat_store.recover_stale()
            except Exception:
                logger.warning("Stale chat-turn recovery failed", exc_info=True)
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest server/tests/test_chat_turns.py -q && .venv/bin/python -m pytest server/tests -q && .venv/bin/python -c "import ast; ast.parse(open('server/app.py').read())"`
Expected: all pass.

- [ ] **Step 6: Commit** (ask the user first)

```bash
git add server/routers/chat_turns.py server/app.py server/tests/test_chat_turns.py
git commit -m "feat(chat): POST /v1/chat/sessions/{id}/turns streams the turn as SSE (C1)"
```

---

### Task 10: Session API — message status, lazy recovery, legacy import

**Files:**
- Modify: `server/routers/chat_sessions.py` (`get_session`; new `import_session`)
- Test: `server/tests/test_chat_sessions_api.py`

**Interfaces:**
- Consumes (Task 5): `store.recover_stale`, `store.new_message_id`.
- Produces:
  - `GET /v1/chat/sessions/{id}`: each message gains `status` (`streaming|complete|aborted|error`), plus `finishReason` and `model` when set. A stale `streaming` message is marked `aborted` before the read.
  - `POST /v1/chat/sessions/import` (R6) takes `{title, model, createdAt, messages, events, pins}` and returns `{"ok": true, "id": "<new s-… id>"}`. It creates a new session owned by the caller with new message ids; attachment metadata only.

- [ ] **Step 1: Write the failing tests** — `server/tests/test_chat_sessions_api.py`:

```python
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.chat import store
from server.routers import chat_sessions as chat_router
from server.tests.chat_harness import member, shim_pool


@pytest.fixture
def app(db_conn, monkeypatch):
    shim_pool(monkeypatch, db_conn, store, chat_router)
    application = FastAPI()
    application.include_router(chat_router.router)
    current: dict = {}
    application.dependency_overrides[deps.get_current_user] = lambda: current["p"]
    return application, current


def _client(app, principal):
    application, current = app
    current["p"] = principal
    return httpx.AsyncClient(transport=ASGITransport(app=application), base_url="http://t")


async def test_get_reports_status_and_recovers_a_dead_turn(db_conn, app):
    alice = await member(db_conn, "alice")
    claim = await store.begin_turn(session_id="s-1", user_id=alice.user_id, model_id="ollama:m", text="hi",
                                   attachments=[], new_session_pins=None)
    async with _client(app, alice) as c:
        live = (await c.get("/v1/chat/sessions/s-1")).json()["messages"]
    assert [m["status"] for m in live] == ["complete", "streaming"]
    assert live[1]["model"] == "ollama:m"
    await db_conn.execute("UPDATE chat_sessions SET active_turn_heartbeat_at = now() - interval '5 minutes'")
    async with _client(app, alice) as c:
        after = (await c.get("/v1/chat/sessions/s-1")).json()["messages"]
    assert (after[1]["status"], after[1]["finishReason"]) == ("aborted", "aborted")
    assert after[1]["id"] == claim.turn_id


async def test_import_copies_a_legacy_chat_under_a_new_id(db_conn, app):
    alice = await member(db_conn, "alice")
    legacy = {"title": "Old chat", "model": "qwen2.5", "createdAt": 1700000000000,
              "messages": [{"id": "u-1", "role": "user", "content": "q", "timestamp": 1,
                            "attachments": [{"id": "a", "kind": "image", "name": "x.png", "mimeType": "image/png",
                                             "size": 3, "base64": "AAA", "dataUrl": "data:image/png;base64,AAA"}]},
                           {"id": "a-2", "role": "assistant", "content": "ans", "timestamp": 2}],
              "events": [{"ts": 1, "kind": "sent", "message": "prompt: q"}],
              "pins": [{"id": "p1", "text": "x"}]}
    async with _client(app, alice) as c:
        r = await c.post("/v1/chat/sessions/import", json=legacy)
        assert r.status_code == 200
        new_id = r.json()["id"]
        assert new_id.startswith("s-")
        got = (await c.get(f"/v1/chat/sessions/{new_id}")).json()
    assert (got["title"], got["model"], got["pins"]) == ("Old chat", "qwen2.5", [{"id": "p1", "text": "x"}])
    assert [(m["role"], m["content"], m["status"]) for m in got["messages"]] == [
        ("user", "q", "complete"), ("assistant", "ans", "complete")]
    assert got["messages"][0]["id"] != "u-1"                              # ids are global: always new
    assert got["messages"][0]["attachments"] == [
        {"id": "a", "kind": "image", "name": "x.png", "mimeType": "image/png", "size": 3}]   # bytes never stored
    assert [e["kind"] for e in got["events"]] == ["sent"]
    cur = await db_conn.execute("SELECT user_id FROM chat_sessions WHERE id = %s", (new_id,))
    assert str((await cur.fetchone())[0]) == alice.user_id
```

- [ ] **Step 2: Run to confirm failure**

Run: `.venv/bin/python -m pytest server/tests/test_chat_sessions_api.py -q`
Expected: FAIL (`status` KeyError; `/import` answers 405 or 404).

- [ ] **Step 3: Change `get_session`** — in `server/routers/chat_sessions.py`, add `from ..chat import store as chat_store`. In `get_session`, first add this line right after `_ensure_ready()`:

```python
    try:
        # A dead worker's turn reads as 'aborted', not forever 'streaming'.
        await chat_store.recover_stale(session_id)
    except Exception:   # an optimization: the read must still work
        logger.warning("Stale-turn check failed for %s", session_id, exc_info=True)
```

  Also, in `server/tests/test_chat_sessions_authz.py`, the `chat_app` fixture must shim the store too. Add `from server.chat import store as chat_store` and, next to the existing `monkeypatch.setattr(chat_router, "get_pool", ...)`, add `monkeypatch.setattr(chat_store, "get_pool", lambda: _PoolShim())`.

  Change the message SELECT column list to:

```python
            SELECT id, role, content, thinking, attachments, doc_context, stats, tool_calls, timestamp,
                   status, finish_reason, model
```

  Then replace the message-building loop with:

```python
    messages = []
    for (mid, role, content, thinking, attachments, doc_context, stats, tool_calls, ts,
         status, finish_reason, model) in message_rows:
        msg = {
            "id": mid,
            "role": role,
            "content": content,
            "attachments": attachments or [],
            "timestamp": int(ts) if ts else 0,
            "status": status,
        }
        if thinking:
            msg["thinking"] = thinking
        if doc_context:
            msg["docContext"] = doc_context
        if stats:
            msg["stats"] = stats
        if tool_calls:
            msg["toolCalls"] = tool_calls
        if finish_reason:
            msg["finishReason"] = finish_reason
        if model:
            msg["model"] = model
        messages.append(msg)
```

- [ ] **Step 4: Add the import endpoint** — in `server/routers/chat_sessions.py`, add this model after `SessionIn`:

```python
class ImportIn(BaseModel):
    """A legacy browser-only (IndexedDB) chat being continued (ruling R6)."""
    title: str = Field(default="New chat", max_length=200)
    model: str | None = Field(default=None, max_length=300)
    createdAt: int | None = None
    messages: list[MessageIn] = Field(default_factory=list, max_length=2000)
    events: list[EventIn] = Field(default_factory=list, max_length=5000)
    pins: list[dict[str, Any]] = Field(default_factory=list, max_length=6)
```

  and this route, placed **before** the `@router.get("/{session_id}")` route so `/import` is never read as a session id:

```python
_META_KEYS = ("id", "kind", "name", "mimeType", "size")


@router.post("/import")
async def import_session(
    payload: ImportIn,
    principal: Principal = Depends(require_capability("chat")),
) -> dict[str, Any]:
    """Copy a legacy browser-only chat to the server under a NEW id, so it can
    be continued (the old PUT's fork-on-edit). Attachment bytes are never
    stored for imported messages; message ids are always new because they are
    global primary keys."""
    _ensure_ready()
    session_id = f"s-{chat_store.now_ms()}-{secrets.token_hex(3)}"
    pool = get_pool()
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO chat_sessions (id, title, model, created_at, updated_at, pins, user_id) "
                "VALUES (%s, %s, %s, COALESCE(to_timestamp(%s::double precision / 1000.0), now()), now(), %s, %s)",
                (session_id, payload.title, payload.model, payload.createdAt, Jsonb(payload.pins),
                 principal.user_id))
            for m in payload.messages:
                meta = [{k: a[k] for k in _META_KEYS if k in a}
                        for a in m.attachments if isinstance(a, dict)]
                await conn.execute(
                    "INSERT INTO chat_messages (id, session_id, role, content, thinking, attachments, "
                    "doc_context, stats, tool_calls, timestamp) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (chat_store.new_message_id(m.role[:1] or "m"), session_id, m.role, m.content, m.thinking,
                     Jsonb(meta), Jsonb(m.docContext) if m.docContext is not None else None,
                     Jsonb(m.stats) if m.stats is not None else None,
                     Jsonb(m.toolCalls) if m.toolCalls is not None else None, m.timestamp))
            for ev in payload.events:
                await conn.execute(
                    "INSERT INTO chat_events (session_id, kind, message, ts) VALUES (%s, %s, %s, %s)",
                    (session_id, ev.kind, ev.message, ev.ts))
    return {"ok": True, "id": session_id}
```

  Add `import secrets` to the imports.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest server/tests/test_chat_sessions_api.py server/tests/test_chat_sessions_authz.py -q`
Expected: all pass.

- [ ] **Step 6: Commit** (ask the user first)

```bash
git add server/routers/chat_sessions.py server/tests/test_chat_sessions_api.py server/tests/test_chat_sessions_authz.py
git commit -m "feat(chat): message status in GET, lazy stale-turn recovery, legacy chat import (C1)"
```

---

### Task 11: SPA — the SSE reader and the pure event reducer

**Files:**
- Create: `src/lib/chatStream.js`, `src/lib/chatEvents.js`, `src/lib/chatFixtures.testutil.js`
- Test: `src/lib/chatStream.test.js`, `src/lib/chatEvents.test.js`

**Interfaces:**
- Consumes: `server/tests/fixtures/chat_events/*.jsonl` (Task 8); `apiFetch(host, port, path, opts)` (existing).
- Produces:
  - `turnPath(sessionId)`
  - `postTurn({apiHost, apiPort, sessionId, body, signal}) -> Promise<Response>`
  - `readEvents(body)`: an async generator of event objects that stops at `[DONE]`
  - `applyEvent(message, event) -> message`, pure. It sets these fields on the assistant message:
    - `id` and `status` (`streaming|complete|aborted|error`)
    - `content` and `thinking`
    - `toolCalls` (`[{id, name, arguments, result_summary}]`) and `toolStatus`
    - `docContext: {notes}` and `notices: string[]`
    - `finishReason`, `stats`, `error: {code, message}`

- [ ] **Step 1: Write the fixture helper** — `src/lib/chatFixtures.testutil.js` (not a test file; only tests import it):

```js
// Test-only: the backend's contract fixtures (server/tests/fixtures/chat_events,
// spec §11) and a fake streamed body. Imported by *.test.js files only.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const DIR = new URL('../../server/tests/fixtures/chat_events/', import.meta.url);

export const loadFixture = (name) =>
    readFileSync(fileURLToPath(new URL(`${name}.jsonl`, DIR)), 'utf8')
        .trim().split('\n').map((line) => JSON.parse(line));

// A ReadableStream-like body that hands out `text` in tiny chunks, so frames
// (and multi-byte characters) split across reads the way real networks split them.
export const bodyOf = (text, chunkSize = 7) => {
    const bytes = new TextEncoder().encode(text);
    let i = 0;
    return {
        getReader: () => ({
            read: async () => {
                if (i >= bytes.length) return { done: true, value: undefined };
                const value = bytes.slice(i, i + chunkSize);
                i += chunkSize;
                return { done: false, value };
            },
            releaseLock() {},
        }),
    };
};

export const sseText = (events, extra = '') =>
    events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('') + 'data: [DONE]\n\n' + extra;
```

- [ ] **Step 2: Write the failing tests**

`src/lib/chatStream.test.js`:

```js
import { describe, it, expect } from 'vitest';
import { readEvents, turnPath } from './chatStream';
import { bodyOf, loadFixture, sseText } from './chatFixtures.testutil';

const collect = async (body) => {
    const out = [];
    for await (const ev of readEvents(body)) out.push(ev);
    return out;
};

describe('chatStream', () => {
    it('encodes the session id into the turns path', () => {
        expect(turnPath('s-1/x')).toBe('/v1/chat/sessions/s-1%2Fx/turns');
    });

    it('reads every fixture event in order even when frames split across reads', async () => {
        for (const name of ['plain', 'tool_round', 'error', 'notice', 'context']) {
            const events = loadFixture(name);
            expect(await collect(bodyOf(sseText(events)))).toEqual(events);
        }
    });

    it('stops at [DONE] and ignores anything after it', async () => {
        const got = await collect(bodyOf(sseText([{ type: 'start' }], 'data: {"type":"late"}\n\n')));
        expect(got).toEqual([{ type: 'start' }]);
    });

    it('skips a malformed frame and tolerates CRLF line endings', async () => {
        const text = 'data: {nope\r\n\r\ndata: {"type":"text-delta","delta":"a"}\r\n\r\ndata: [DONE]\r\n\r\n';
        expect(await collect(bodyOf(text, 5))).toEqual([{ type: 'text-delta', delta: 'a' }]);
    });
});
```

`src/lib/chatEvents.test.js`:

```js
import { describe, it, expect } from 'vitest';
import { applyEvent } from './chatEvents';
import { readEvents } from './chatStream';
import { bodyOf, loadFixture, sseText } from './chatFixtures.testutil';

const fresh = () => ({ id: 'a-local', role: 'assistant', content: '', thinking: '', status: 'streaming' });
const reduce = (events) => events.reduce(applyEvent, fresh());

describe('applyEvent over the backend contract fixtures', () => {
    it('plain: text and a completed status with stats', () => {
        const m = reduce(loadFixture('plain'));
        expect(m.content).toBe('Hello there.');
        expect(m.status).toBe('complete');
        expect(m.finishReason).toBe('stop');
        expect(m.stats.model).toBe('ollama:m');
        expect(m.id).toBe('<message-id>');
    });

    it('tool_round: a settled tool call and the answer', () => {
        const m = reduce(loadFixture('tool_round'));
        expect(m.toolCalls).toEqual([{
            id: 'c1', name: 'web_search', arguments: { query: 'news' },
            result_summary: { ok: true, chunk_count: null, query: 'news', summary_text: 'Web search for "news" returned 1 result(s).' },
        }]);
        expect(m.toolStatus).toBeUndefined();
        expect(m.content).toBe('Here is the news.');
    });

    it('shows "executing tool…" between the tool input and the next step', () => {
        const events = loadFixture('tool_round');
        const upto = events.findIndex((e) => e.type === 'tool-input-available') + 1;
        expect(reduce(events.slice(0, upto)).toolStatus).toBe('executing tool…');
    });

    it('error: keeps the partial reply and records the error', () => {
        const m = reduce(loadFixture('error'));
        expect(m.status).toBe('error');
        expect(m.error.code).toBe('provider_error');
        expect(m.content).toBe('Partial');
    });

    it('notice and context: surfaced on the message', () => {
        expect(reduce(loadFixture('notice')).notices).toEqual(['This model rejected tools — answered without them.']);
        expect(reduce(loadFixture('context')).docContext.notes[0]).toMatchObject({ kind: 'prefetch', docName: 'Thesis.pdf' });
    });

    it('ignores unknown event types (forward compatible)', () => {
        expect(applyEvent(fresh(), { type: 'data-something-new', x: 1 })).toEqual(fresh());
    });

    it('is the same whether events come from the file or through the SSE reader', async () => {
        const events = loadFixture('tool_round');
        let m = fresh();
        for await (const ev of readEvents(bodyOf(sseText(events)))) m = applyEvent(m, ev);
        expect(m).toEqual(reduce(events));
    });
});
```

- [ ] **Step 3: Run to confirm failure**

Run: `npx vitest run src/lib/chatStream.test.js src/lib/chatEvents.test.js`
Expected: FAIL (cannot resolve `./chatStream` / `./chatEvents`).

- [ ] **Step 4: Write `src/lib/chatStream.js`**

```js
/**
 * The chat turn transport (C1 spec §6, §7.1): POST the new message and read
 * the server-sent events back. Each event is one `data: {json}` line plus a
 * blank line; `data: [DONE]` ends the stream.
 */
import { apiFetch } from '../utils/apiFetch';

export const turnPath = (sessionId) => `/v1/chat/sessions/${encodeURIComponent(sessionId)}/turns`;

export function postTurn({ apiHost, apiPort, sessionId, body, signal }) {
    return apiFetch(apiHost, apiPort, turnPath(sessionId), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify(body),
        signal,
    });
}

const dataOf = (frame) => frame.split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).replace(/^ /, ''))
    .join('\n');

// Yields parsed events; returns at [DONE]. An aborted fetch rejects the read
// with an AbortError, which propagates to the caller.
export async function* readEvents(body) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    try {
        for (;;) {
            const { value, done } = await reader.read();
            if (done) return;
            buf += decoder.decode(value, { stream: true });
            buf = buf.replace(/\r\n/g, '\n');
            let cut;
            while ((cut = buf.indexOf('\n\n')) !== -1) {
                const data = dataOf(buf.slice(0, cut));
                buf = buf.slice(cut + 2);
                if (!data) continue;
                if (data === '[DONE]') return;
                try {
                    yield JSON.parse(data);
                } catch {
                    // a malformed frame is skipped, not fatal
                }
            }
        }
    } finally {
        try { reader.releaseLock(); } catch { /* a read still pending after an abort */ }
    }
}
```

- [ ] **Step 5: Write `src/lib/chatEvents.js`**

```js
/**
 * Pure reducer: one server chat event -> the assistant message it updates
 * (spec §6, §8). No React, no I/O: the same fixtures the backend asserts are
 * replayed through it in tests, so a format change on either side fails.
 * Unknown event types return the message unchanged (forward compatible).
 */
const settle = (msg, toolCallId, summary) => ({
    ...msg,
    toolCalls: (msg.toolCalls || []).map((tc) => (tc.id === toolCallId ? { ...tc, result_summary: summary } : tc)),
});

export function applyEvent(msg, ev) {
    switch (ev?.type) {
        case 'start':
            return { ...msg, id: ev.messageId, status: 'streaming' };
        case 'data-context':
            return { ...msg, docContext: { notes: ev.items || [] } };
        case 'data-notice':
            return { ...msg, notices: [...(msg.notices || []), ev.message] };
        case 'start-step':
            return { ...msg, toolStatus: undefined };
        case 'reasoning-delta':
            return { ...msg, thinking: (msg.thinking || '') + (ev.delta || '') };
        case 'text-delta':
            return { ...msg, content: (msg.content || '') + (ev.delta || '') };
        case 'tool-input-available':
            return {
                ...msg,
                toolStatus: 'executing tool…',
                toolCalls: [...(msg.toolCalls || []),
                    { id: ev.toolCallId, name: ev.toolName, arguments: ev.input || {}, result_summary: null }],
            };
        case 'tool-output-available':
            return settle(msg, ev.toolCallId, ev.output || {});
        case 'tool-output-error':
            return settle(msg, ev.toolCallId, { error: ev.errorText || 'Tool failed' });
        case 'finish':
            return {
                ...msg,
                status: ev.finishReason === 'aborted' ? 'aborted' : 'complete',
                finishReason: ev.finishReason,
                stats: ev.stats || msg.stats,
                toolStatus: undefined,
            };
        case 'error':
            return { ...msg, status: 'error', error: { code: ev.code, message: ev.message }, toolStatus: undefined };
        default:
            return msg;
    }
}
```

- [ ] **Step 6: Run the tests**

Run: `npx vitest run src/lib/chatStream.test.js src/lib/chatEvents.test.js`
Expected: all pass.

- [ ] **Step 7: Commit** (ask the user first)

```bash
git add src/lib/chatStream.js src/lib/chatEvents.js src/lib/chatFixtures.testutil.js src/lib/chatStream.test.js src/lib/chatEvents.test.js
git commit -m "feat(chat-ui): SSE turn reader and pure event reducer, tested on the backend's fixtures (C1)"
```

---

### Task 12: SPA — the chat engine runs on turns

**Files:**
- Modify: `src/hooks/useChatEngine.js` (sendMessage, refreshModels, props; the browser loop goes)
- Modify: `src/lib/sessionStore.js` (`saveSession` removed, `importLegacy` added)
- Modify: `src/hooks/inference.js` (`toWireSettings` replaces `buildRequestFields`)
- Modify: `src/hooks/pins.js` (gains `buildPinPreamble`, used only by the context meter)
- Modify: `src/lib/apiErrors.js` (404 override, chat codes)
- Modify: `src/lib/chatTransport.js` (server-only helpers)
- Create: `src/lib/modelIds.js`
- Modify: `src/components/ChatView.jsx` (import path; restore the draft on refusal)
- Modify: `src/components/ChatSidebar.jsx` (grouped picker)
- Modify: `src/components/settings/SettingsPage.jsx` (per-model select uses ids)
- Modify: `src/App.jsx` (engine props, model-id migration)
- Delete: `src/lib/chatTools/` (all files), `src/hooks/chatHistory.js`, `src/hooks/chatHistory.test.js`, `src/hooks/inference.requestBody.test.js`
- Tests: `src/hooks/useChatEngine.test.js` (rewritten), `src/lib/sessionStore.test.js`, `src/hooks/inference.test.js`, `src/hooks/pins.test.js`, `src/lib/apiErrors.test.js`, `src/lib/chatTransport.test.js` (rewritten), `src/lib/modelIds.test.js`, `src/components/ChatSidebar.settings-removed.test.jsx`, `src/components/settings/SettingsPage.test.jsx`

**Interfaces:**
- Consumes (Task 11): `postTurn`, `readEvents`, `applyEvent`. Backend (Tasks 9, 10, 4): `POST …/turns`, `POST /v1/chat/sessions/import`, `GET /v1/inference/models` (model objects with `kind`).
- Produces:
  - `useChatEngine` returns the same shape as today, except `availableModels` is `[{id, provider, kind, name, capabilities}]`. `sendMessage` resolves `{sent: true}` or `{sent: false, refused?: true, text, attachments}`.
  - `toWireSettings(settings) -> {think, num_ctx, keep_alive, num_predict}`
  - `modelIds.js`: `migrateModelId(saved, models)`, `groupByProvider(models)`, `capabilityBadges(model)`, `modelLabel(model)`, `supportedKnobs(model)`
  - `describeRefusal(res, {notFound})`

- [ ] **Step 1: Write the pure helpers first (TDD)** — `src/lib/modelIds.test.js`:

```js
import { describe, it, expect } from 'vitest';
import { capabilityBadges, groupByProvider, migrateModelId, modelLabel, supportedKnobs } from './modelIds';

const m = (provider, kind, name, capabilities = {}) => ({ id: `${provider}:${name}`, provider, kind, name, capabilities });
const MODELS = [m('local', 'ollama', 'qwen2.5:7b', { tools: true, thinking: false }),
                m('local', 'ollama', 'gemma3', { vision: true }),
                m('cloud', 'openai', 'vendor/x', { tools: true, thinking: true })];

describe('modelIds', () => {
    it('keeps a current id and maps a pre-C1 bare name to its ollama id', () => {
        expect(migrateModelId('cloud:vendor/x', MODELS)).toBe('cloud:vendor/x');
        expect(migrateModelId('qwen2.5:7b', MODELS)).toBe('local:qwen2.5:7b');
        expect(migrateModelId('gone', MODELS)).toBe('gone');
        expect(migrateModelId('qwen2.5:7b', [])).toBe('qwen2.5:7b');
    });
    it('groups by provider in server order', () => {
        expect(groupByProvider(MODELS).map(([p, ms]) => [p, ms.length])).toEqual([['local', 2], ['cloud', 1]]);
    });
    it('labels with capability badges', () => {
        expect(capabilityBadges(MODELS[2])).toEqual(['tools', 'thinking']);
        expect(modelLabel(MODELS[1])).toBe('gemma3 · vision');
        expect(modelLabel(m('local', 'ollama', 'plain'))).toBe('plain');
    });
    it('offers only the knobs the provider supports', () => {
        expect(supportedKnobs(MODELS[0])).toEqual({ numCtx: true, keepAlive: true, think: false, numPredict: true });
        expect(supportedKnobs(MODELS[2])).toEqual({ numCtx: false, keepAlive: false, think: true, numPredict: true });
        expect(supportedKnobs(undefined)).toEqual({ numCtx: true, keepAlive: true, think: true, numPredict: true });
    });
});
```

  Then `src/lib/modelIds.js`:

```js
// Model ids are `provider:name` (C1 spec §4.3). Pure helpers for the model
// picker, and for carrying a pre-C1 saved selection ("qwen2.5:7b") to its id.

export function migrateModelId(saved, models) {
    if (!saved || !Array.isArray(models) || models.length === 0) return saved;
    if (models.some((m) => m.id === saved)) return saved;
    const match = models.find((m) => m.kind === 'ollama' && m.name === saved)
        || models.find((m) => m.name === saved);
    return match ? match.id : saved;
}

export function groupByProvider(models) {
    const groups = new Map();
    for (const m of models || []) {
        if (!groups.has(m.provider)) groups.set(m.provider, []);
        groups.get(m.provider).push(m);
    }
    return [...groups.entries()];
}

export function capabilityBadges(model) {
    const c = model?.capabilities || {};
    return [c.tools && 'tools', c.thinking && 'thinking', c.vision && 'vision', c.audio && 'audio'].filter(Boolean);
}

export function modelLabel(model) {
    const badges = capabilityBadges(model);
    return badges.length ? `${model.name} · ${badges.join(' · ')}` : model.name;
}

// Which per-model settings a provider honours (spec §4.2): context size and
// keep-alive are Ollama-only; thinking hides only when the model says it can't.
// Unknown model (list not loaded yet) = show everything, today's behaviour.
export function supportedKnobs(model) {
    const ollama = !model || model.kind === 'ollama';
    return { numCtx: ollama, keepAlive: ollama, think: model?.capabilities?.thinking !== false, numPredict: true };
}
```

- [ ] **Step 2: `toWireSettings`** — in `src/hooks/inference.js`, delete `THINK_WIRE`, `isSet` and `buildRequestFields`, and add:

```js
// The turn body's `settings` (C1 spec §7.1). An unset value is null and the
// server leaves it out of the provider request, so a model's own tuned
// defaults (its Modelfile values) stay in force.
export const toWireSettings = (settings) => {
    const s = { ...INFERENCE_DEFAULTS, ...(settings || {}) };
    return { think: s.think, num_ctx: s.numCtx, keep_alive: s.keepAlive, num_predict: s.numPredict };
};
```

  In `src/hooks/inference.test.js`, change the import (`buildRequestFields` → `toWireSettings`) and replace the whole `describe('buildRequestFields', …)` block with:

```js
describe('toWireSettings', () => {
    it('sends every field, unset ones as null', () => {
        expect(toWireSettings(INFERENCE_DEFAULTS)).toEqual({ think: 'off', num_ctx: null, keep_alive: null, num_predict: null });
        expect(toWireSettings(undefined)).toEqual({ think: 'off', num_ctx: null, keep_alive: null, num_predict: null });
    });
    it('maps the per-model settings to wire names', () => {
        expect(toWireSettings({ think: 'high', numCtx: 16384, keepAlive: -1, numPredict: 512 }))
            .toEqual({ think: 'high', num_ctx: 16384, keep_alive: -1, num_predict: 512 });
    });
});
```

  Delete `src/hooks/inference.requestBody.test.js`; it tested the removed request builder.

- [ ] **Step 3: Move `buildPinPreamble` into `pins.js`** — append to `src/hooks/pins.js`:

```js
// Pin wording as the model sees it. The SERVER builds the real prompt
// (server/chat/context.py pin_messages, same text); this copy exists only so
// the composer's context meter can count what pins cost.
export const buildPinPreamble = (pins = []) =>
    pins.map((p) => ({
        role: 'system',
        content:
            `The user is reading "${p.fileName || 'a document'}".\n` +
            `Relevant excerpt (${p.kind || 'page'}` +
            `${p.page != null ? `, page ${p.page}` : ''}):\n\n` +
            `"""\n${p.text}\n"""\n\n` +
            `Use this excerpt as primary context for the user's question. If it does ` +
            `not contain the answer, say so or use the document search tool if available.`,
    }));
```

  Move the `describe('buildPinPreamble', …)` block from `src/hooks/chatHistory.test.js` into `src/hooks/pins.test.js`, minus its last test (`composes with buildChatHistory`), and import `buildPinPreamble` from `./pins`. In `src/components/ChatView.jsx`, change `import { buildPinPreamble } from "../hooks/chatHistory";` to `import { buildPinPreamble } from "../hooks/pins";`. Delete `src/hooks/chatHistory.js` and `src/hooks/chatHistory.test.js`, and `git rm -r src/lib/chatTools`.

- [ ] **Step 4: Refusal notices** — in `src/lib/apiErrors.js`, replace `noticeFor` and `describeRefusal` with:

```js
export function noticeFor(status, detail, { notFound } = {}) {
    const d = detail && typeof detail === 'object' ? detail : null;
    if (status === 409 && d?.error === 'content_shared') {
        return CONTENT_SHARED[d.reason] || CONTENT_SHARED.other_holders;
    }
    if (status === 413 && d?.error === 'too_large') return `Attachments too large (limit ${d.limit_mb ?? '?'} MB).`;
    if (status === 413) return `File too large (limit ${d?.limit_mb ?? '?'} MB).`;
    if (status === 415) return 'Unsupported file type.';
    if (status === 422 && d?.error === 'empty_file') return 'The file is empty.';
    if (status === 404) return notFound || "This document doesn't exist or you don't have access.";
    // Chat refusals (C1 spec §7.2) carry a message written for people; a 503
    // "no provider" is a setup problem, not a crash, so it isn't "server error".
    if (d?.error === 'no_providers') return d.message || 'No model provider is configured.';
    if (status >= 500) return 'Something went wrong on the server. Try again.';
    if (d?.message) return d.message;
    if (typeof detail === 'string' && detail) return detail;
    return `Request failed (HTTP ${status}).`;
}

export async function describeRefusal(res, options = {}) {
    let detail = null;
    try {
        detail = (await res.json())?.detail ?? null;
    } catch {
        // non-JSON error body (proxy page, empty) — fall back to the status
    }
    return noticeFor(res.status, detail, options);
}
```

  Append to `src/lib/apiErrors.test.js`:

```js
describe('chat refusals', () => {
    it('uses the chat wording for a 404 when asked', () => {
        expect(noticeFor(404, { error: 'not_found' }, { notFound: 'No such chat.' })).toBe('No such chat.');
    });
    it('explains a too-large turn, a busy chat and a missing provider', () => {
        expect(noticeFor(413, { error: 'too_large', limit_mb: 25 })).toBe('Attachments too large (limit 25 MB).');
        expect(noticeFor(409, { error: 'turn_in_progress', message: 'A reply is still being written in this chat.' }))
            .toBe('A reply is still being written in this chat.');
        expect(noticeFor(503, { error: 'no_providers', message: 'No model provider is configured.' }))
            .toBe('No model provider is configured.');
    });
});
```

- [ ] **Step 5: Slim the transport** — replace `src/lib/chatTransport.js` with:

```js
// Server-side chat helpers (C1: the browser no longer talks to Ollama, and
// Local mode is gone). The turn itself is src/lib/chatStream.js.
export const MODELS_PATH = '/v1/inference/models';

// 429 from the server means the daily token budget is gone.
export async function budgetDetail(res) {
  if (res.status !== 429) return null;
  try {
    const body = await res.json();
    const detail = body?.detail;
    return detail && typeof detail === 'object' ? detail : null;
  } catch {
    return null;
  }
}

export function formatResetAt(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
```

  In `src/lib/chatTransport.test.js`, delete the first `describe('chatTransport', …)` block (lines 1–42, the `chatFetch` tests) and its import. Keep the `budgetDetail`/`formatResetAt` blocks with their import. Add:

```js
import { MODELS_PATH } from './chatTransport';
describe('MODELS_PATH', () => {
  it('is the server model list', () => expect(MODELS_PATH).toBe('/v1/inference/models'));
});
```

- [ ] **Step 6: Session store** — in `src/lib/sessionStore.js`:
  - Add `import { stripAttachmentData } from '../utils/attachment';`.
  - Delete `const newId = …` and the whole `saveSession` method, with its doc comment.
  - Change the header comment's second paragraph to: "Reads merge both stores. The server writes messages itself as each turn streams (C1). A legacy IDB session is read-only until continued; then `importLegacy` copies it to Postgres once, under a new id."
  - Add this method after `getSession`:

```js
        /**
         * Copy a legacy browser-only chat to the server so it can be continued
         * (C1 ruling R6). Attachment bytes stay behind: only metadata goes up.
         * Resolves the new server session id, or null on failure.
         */
        importLegacy: async (id) => {
            const record = await idb.getSession(id);
            if (!record) return null;
            const payload = {
                title: record.title || 'New chat',
                model: record.model || null,
                createdAt: record.createdAt || null,
                messages: (record.messages || []).map((m) => ({
                    ...m,
                    attachments: (m.attachments || []).map(stripAttachmentData),
                })),
                events: record.events || [],
                pins: record.pins || [],
            };
            try {
                const out = await fetchJson('/v1/chat/sessions/import', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                notifyOnline();
                return out?.id || null;
            } catch (e) {
                notifyOffline(e);
                return null;
            }
        },
```

  Append to `src/lib/sessionStore.test.js`:

```js
vi.mock('../db', () => ({
    getRecentSessions: async () => [],
    deleteSession: async () => true,
    saveSession: async () => true,
    getSession: async () => ({
        id: 's-old', title: 'Old', model: 'qwen2.5', createdAt: 1,
        messages: [{ id: 'u-1', role: 'user', content: 'q', timestamp: 1,
            attachments: [{ id: 'a', kind: 'image', name: 'x.png', mimeType: 'image/png', size: 3, base64: 'AAA', dataUrl: 'data:' }] }],
        events: [], pins: [],
    }),
}));

describe('importLegacy', () => {
    let fetchMock;
    beforeEach(() => {
        fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, text: async () => '{"ok":true,"id":"s-new"}' });
        vi.stubGlobal('fetch', fetchMock);
    });
    afterEach(() => vi.unstubAllGlobals());

    it('POSTs the legacy chat without attachment bytes and resolves the new id', async () => {
        const store = makeSessionStore({ apiHost: '', apiPort: '', onBackendOffline: () => {} });
        expect(await store.importLegacy('s-old')).toBe('s-new');
        const [url, init] = fetchMock.mock.calls[0];
        expect(url).toContain('/v1/chat/sessions/import');
        expect(init.method).toBe('POST');
        const sent = JSON.parse(init.body);
        expect(sent.title).toBe('Old');
        expect(sent.messages[0].attachments).toEqual([{ id: 'a', kind: 'image', name: 'x.png', mimeType: 'image/png', size: 3 }]);
    });
});
```

- [ ] **Step 7: Rewrite the engine** — `src/hooks/useChatEngine.js`. Keep these parts of the current file **exactly as they are** (copy them across unchanged):
  - the TTS block, from `// Read latest values via refs so the playback loop…` down to the end of `drainQueueAndRevoke`;
  - `setActive`, `persistPins`, `addPin`, `removePin`, `clearPins`, `refreshSessions`;
  - `deleteSession`, `renameSession`, and the mount effect that calls `refreshSessions()`;
  - `flushBufferedSentences`, `stopSpeaking`, `speakMessage`, `stopStream`, `clearHistory`, and the `activeSession` line and the returned object.

  Replace everything else as follows.

  (a) Imports and constants at the top:

```js
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { markdownToSpeech } from '../utils/markdownToSpeech';
import { apiFetch } from '../utils/apiFetch';
import { makeSessionStore } from '../lib/sessionStore';
import { MODELS_PATH, budgetDetail, formatResetAt } from '../lib/chatTransport';
import { postTurn, readEvents } from '../lib/chatStream';
import { applyEvent } from '../lib/chatEvents';
import { describeRefusal } from '../lib/apiErrors';
import { addPin as addPinReducer, removePin as removePinReducer, MAX_PINS } from './pins';
import { INFERENCE_DEFAULTS, toWireSettings, truncationMessage } from './inference';

const SENTENCE_TERMINATOR = /(?<=[.!?])\s+/;
const MIN_TTS_LENGTH = 5;
const POLL_MS = 3000;   // how often a reloaded "still generating…" chat re-reads the server
const CHAT_NOT_FOUND = "This chat doesn't exist or you don't have access.";

const newSessionId = () => `s-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
const browserTimezone = () => {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || null; } catch { return null; }
};
```

  (b) The doc comment and signature:

```js
/**
 * Chat state for the SPA. The SERVER runs each turn (C1 spec §5): this hook
 * posts the new message, reads the typed event stream (lib/chatStream) and
 * renders it through the pure reducer (lib/chatEvents). What stays here: the
 * session list and switching, pins, and the read-aloud queue.
 *
 * TTS queue design (mirrors useTtsEngine's reader pattern):
 *   - Each enqueued sentence's synthesis starts lazily, at most N..N+2 in flight.
 *   - A single playback loop awaits items in order, so there's no audible gap.
 *   - Single chat audio element is reused; volume/speed read at play time.
 */
export function useChatEngine({
    selectedModel,
    chatTtsMode,        // 'streaming' | 'after-complete'
    chatAutoTts,        // bool — disables TTS entirely
    inference = INFERENCE_DEFAULTS,  // this model's settings → the turn's `settings`
    isLocalhost,        // bool — true means use Kokoro, false means Web Speech fallback
    selectedVoice,
    playbackSpeed,
    requestTimeout,
    apiHost,            // FastAPI host — chat turns, sessions and models
    apiPort,
    currentDocId,       // sha256 of the open doc (null if none) — the server decides what to do with it
    synthesizeText,     // from useTtsEngine — returns Promise<blobUrl|null>
    playChatUrl,        // from useTtsEngine — plays a pre-fetched blob URL
    playChatSpeech,     // from useTtsEngine — Web Speech API fallback
    stopChatPlayback,   // from useTtsEngine — silences chat audio + speech synthesis
    showToast,
}) {
```

  (c) State and refs. Same as today, minus `toolFallbackToastedRef`, `thinkLevelFallbackToastedRef`, `createdAtRef` and the whole `chatHosts`/`callChat` block. Keep `backendOfflineToastedRef` and the `sessionStore` `useMemo` as they are.

  (d) `logEvent` and `saveActiveSession` are deleted; the server writes the log now. Add in their place:

```js
    // The server writes the chat's log (sent, tool calls, errors…). Re-read it
    // after a turn so the sidebar's Log is current.
    const refreshEvents = useCallback(async (id) => {
        const record = await sessionStore.getSession(id);
        if (record && activeSessionIdRef.current === id) {
            eventsRef.current = record.events || [];
            setEvents(eventsRef.current);
        }
    }, [sessionStore]);
```

  (e) In `switchToSession` and `newSession`, delete the `createdAtRef.current = …` lines. Nothing else changes in them.

  (f) `refreshModels`:

```js
    // Every configured provider's models: [{id, provider, kind, name, capabilities}].
    const refreshModels = useCallback(async () => {
        try {
            const controller = new AbortController();
            const t = setTimeout(() => controller.abort(), 8000);
            const res = await apiFetch(apiHost, apiPort, MODELS_PATH, { signal: controller.signal });
            clearTimeout(t);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            setAvailableModels(Array.isArray(data?.models) ? data.models.filter((m) => m && m.id) : []);
            setReachable(true);
            setInferenceBudget(data?.budget ?? null);
        } catch (e) {
            console.warn('Model list unavailable:', e.message);
            setAvailableModels([]);
            setReachable(false);
        }
    }, [apiHost, apiPort]);
```

  Keep the debounced effect that calls it: `useEffect(() => { const h = setTimeout(refreshModels, 400); return () => clearTimeout(h); }, [refreshModels]);`.

  (g) `sendMessage`:

```js
    const sendMessage = useCallback(async (userText, attachments = []) => {
        const trimmed = (userText || '').trim();
        const cleanAttachments = (attachments || []).filter((a) => a && a.kind);
        if (!trimmed && cleanAttachments.length === 0 && pinsRef.current.length === 0) return { sent: false };
        if (isStreaming) return { sent: false };
        const giveBack = { sent: false, refused: true, text: userText, attachments };
        if (!selectedModel) {
            showToast?.('Pick a model first.', 4000);
            return giveBack;
        }

        // Lock TTS mode + inference settings for THIS message.
        const modeForThisMsg = chatTtsMode;
        const inferenceForThisMsg = inference;

        // Which server session gets this turn?
        let sessionId = activeSessionIdRef.current;
        let isNew = false;
        if (sessionId && sessionStore.isLocalId(sessionId)) {
            // A pre-Postgres chat kept only in this browser: copy it to the
            // server once, then continue it there (today's fork-on-edit).
            const forkedId = await sessionStore.importLegacy(sessionId);
            if (!forkedId) {
                showToast?.("Couldn't copy this older chat to the server.", 5000);
                return giveBack;
            }
            sessionId = forkedId;
            setActive(forkedId);
        }
        if (!sessionId) {
            sessionId = newSessionId();
            isNew = true;
            setActive(sessionId);
            eventsRef.current = [];
            setEvents([]);
        }

        const now = Date.now();
        let userId = `u-local-${now}`;
        let assistantId = `a-local-${now}`;
        setMessages((prev) => [...prev,
            { role: 'user', content: trimmed, attachments: cleanAttachments, id: userId, timestamp: now },
            { role: 'assistant', content: '', thinking: '', id: assistantId, timestamp: now + 1, status: 'streaming' }]);
        sentenceBufferRef.current = '';
        setIsStreaming(true);
        if (chatAutoTts) setSpeaking(assistantId);
        const controller = new AbortController();
        abortRef.current = controller;
        const updateAssistant = (fn) => setMessages((prev) => prev.map((m) => (m.id === assistantId ? fn(m) : m)));

        const body = {
            message: {
                content: trimmed,
                attachments: cleanAttachments.filter((a) => a.base64).map((a) => ({
                    id: a.id, kind: a.kind, mime: a.mimeType, name: a.name || '', size: a.size || 0, base64: a.base64,
                })),
            },
            model: selectedModel,
            settings: toWireSettings(inferenceForThisMsg),
            context: { doc_id: currentDocId || null, timezone: browserTimezone() },
            ...(isNew ? { session: { pins: pinsRef.current } } : {}),
        };

        let refused = false;
        let completed = false;
        let replyText = '';
        try {
            const res = await postTurn({ apiHost, apiPort, sessionId, body, signal: controller.signal });
            if (!res.ok) {
                refused = true;
                const budget = await budgetDetail(res.clone());
                if (budget) {
                    setInferenceBudget(budget);
                    showToast?.(`Daily inference budget exhausted — resets at ${formatResetAt(budget.reset_at)}`, 6000);
                } else {
                    showToast?.(await describeRefusal(res, { notFound: CHAT_NOT_FOUND }), 5000);
                }
            } else {
                for await (const ev of readEvents(res.body)) {
                    if (ev.type === 'start') {
                        // Adopt the server's ids so reload, read-aloud and pins line up.
                        const [oldUser, oldAssistant] = [userId, assistantId];
                        setMessages((prev) => prev.map((m) => (
                            m.id === oldUser ? { ...m, id: ev.userMessageId }
                                : m.id === oldAssistant ? { ...m, id: ev.messageId } : m)));
                        if (speakingMessageIdRef.current === oldAssistant) setSpeaking(ev.messageId);
                        userId = ev.userMessageId;
                        assistantId = ev.messageId;
                        continue;
                    }
                    updateAssistant((m) => applyEvent(m, ev));
                    if (ev.type === 'text-delta') {
                        replyText += ev.delta || '';
                        if (modeForThisMsg === 'streaming') {
                            sentenceBufferRef.current += ev.delta || '';
                            flushBufferedSentences();
                        }
                    } else if (ev.type === 'data-notice') {
                        showToast?.(ev.message, 4000);
                    } else if (ev.type === 'finish') {
                        completed = true;
                        const truncated = truncationMessage(ev.stats, inferenceForThisMsg);
                        if (truncated) showToast?.(truncated, 7000);
                    } else if (ev.type === 'error') {
                        if (ev.code === 'budget_exhausted') {
                            showToast?.('Daily inference budget exhausted.', 6000);
                            refreshModels();   // re-reads the budget readout
                        } else {
                            showToast?.(`Chat failed: ${ev.message}`, 5000);
                        }
                    }
                }
                setReachable(true);
            }
            if (completed) {
                if (modeForThisMsg === 'streaming') {
                    const tail = sentenceBufferRef.current.trim();
                    if (tail) enqueueTts(tail);
                    sentenceBufferRef.current = '';
                } else if (chatAutoTts) {
                    replyText.replace(/\s+/g, ' ').split(SENTENCE_TERMINATOR)
                        .filter((s) => s.trim().length >= MIN_TTS_LENGTH)
                        .forEach(enqueueTts);
                }
                if (chatAutoTts) {
                    const spokenId = assistantId;
                    chatPlaybackPromiseRef.current.then(() => {
                        if (speakingMessageIdRef.current === spokenId) setSpeaking(null);
                    });
                }
            }
        } catch (e) {
            if (e.name === 'AbortError') {
                updateAssistant((m) => ({ ...m, status: 'aborted', finishReason: 'aborted', toolStatus: undefined }));
            } else {
                console.error('Chat error:', e);
                setReachable(false);
                showToast?.(`Chat failed: ${e.message}`, 5000);
                updateAssistant((m) => ({ ...m, status: 'error', toolStatus: undefined }));
            }
        } finally {
            abortRef.current = null;
            setIsStreaming(false);
            if (refused) {
                const localIds = new Set([userId, assistantId]);
                setMessages((prev) => prev.filter((m) => !localIds.has(m.id)));
                if (isNew) setActive(null);
                setSpeaking(null);
            } else {
                refreshSessions();
                refreshEvents(sessionId);
            }
        }
        return refused ? giveBack : { sent: true };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isStreaming, selectedModel, chatTtsMode, chatAutoTts, inference, apiHost, apiPort, currentDocId,
        sessionStore, showToast, flushBufferedSentences, enqueueTts, refreshSessions, refreshEvents, refreshModels]);
```

  (h) The "still generating…" poll, placed after `sendMessage`:

```js
    // A reloaded chat whose reply the server is still writing: re-read it until
    // it settles. Server-side stale recovery (spec §5.5) guarantees it does,
    // even if the worker writing it died.
    const hasRemoteStreaming = !isStreaming && messages.some((m) => m.status === 'streaming');
    useEffect(() => {
        if (!hasRemoteStreaming || !activeSessionId) return undefined;
        const id = activeSessionId;
        const handle = setInterval(async () => {
            const record = await sessionStore.getSession(id);
            if (!record || activeSessionIdRef.current !== id) return;
            setMessages(record.messages || []);
            eventsRef.current = record.events || [];
            setEvents(eventsRef.current);
        }, POLL_MS);
        return () => clearInterval(handle);
    }, [hasRemoteStreaming, activeSessionId, sessionStore]);
```

- [ ] **Step 8: Rewrite the engine tests** — replace `src/hooks/useChatEngine.test.js` with:

```js
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useChatEngine } from './useChatEngine';
import { INFERENCE_DEFAULTS } from './inference';
import { postTurn } from '../lib/chatStream';
import { apiFetch } from '../utils/apiFetch';
import { bodyOf, loadFixture, sseText } from '../lib/chatFixtures.testutil';

vi.mock('../lib/chatStream', async (importOriginal) => ({ ...(await importOriginal()), postTurn: vi.fn() }));
vi.mock('../utils/apiFetch', () => ({ apiFetch: vi.fn() }));

// vi.hoisted: vi.mock factories run before this file's own top-level code.
const store = vi.hoisted(() => ({
    getRecentSessions: vi.fn(async () => []),
    getSession: vi.fn(async () => null),
    deleteSession: vi.fn(async () => true),
    renameSession: vi.fn(async () => true),
    updateSessionPins: vi.fn(async () => true),
    importLegacy: vi.fn(async () => 's-imported'),
    isLocalId: vi.fn(() => false),
}));
vi.mock('../lib/sessionStore', () => ({ makeSessionStore: () => store }));

const MODELS = [{ id: 'ollama:m', provider: 'ollama', kind: 'ollama', name: 'm', capabilities: {} }];
const ok = (events) => ({ ok: true, status: 200, body: bodyOf(sseText(events)), clone() { return this; } });
const refusal = (status, detail) => ({ ok: false, status, json: async () => ({ detail }), clone() { return this; } });

const baseProps = (over = {}) => ({
    selectedModel: 'ollama:m', chatTtsMode: 'after-complete', chatAutoTts: false,
    inference: { ...INFERENCE_DEFAULTS, think: 'low' }, isLocalhost: false, selectedVoice: 'af_heart',
    playbackSpeed: 1, requestTimeout: 15, apiHost: '', apiPort: '8000', currentDocId: 'a'.repeat(64),
    synthesizeText: vi.fn(), playChatUrl: vi.fn(), playChatSpeech: vi.fn(), stopChatPlayback: vi.fn(),
    showToast: vi.fn(), ...over,
});

beforeEach(() => {
    vi.clearAllMocks();
    apiFetch.mockResolvedValue({ ok: true, status: 200, json: async () => ({ models: MODELS, budget: null }) });
});
afterEach(() => vi.useRealTimers());

describe('useChatEngine — turns', () => {
    it('posts one turn, adopts the server ids and renders the streamed reply', async () => {
        postTurn.mockResolvedValue(ok(loadFixture('plain')));
        const props = baseProps();
        const { result } = renderHook(() => useChatEngine(props));
        let out;
        await act(async () => { out = await result.current.sendMessage('Hi'); });
        expect(out).toEqual({ sent: true });
        const [{ sessionId, body }] = postTurn.mock.calls[0];
        expect(sessionId).toMatch(/^s-/);
        expect(body).toMatchObject({
            message: { content: 'Hi', attachments: [] }, model: 'ollama:m',
            settings: { think: 'low', num_ctx: null, keep_alive: null, num_predict: null },
            context: { doc_id: 'a'.repeat(64) }, session: { pins: [] },
        });
        const [user, assistant] = result.current.messages;
        expect(user.id).toBe('<user-message-id>');
        expect(assistant).toMatchObject({ id: '<message-id>', content: 'Hello there.', status: 'complete' });
        expect(store.getSession).toHaveBeenCalled();          // the server-written log is re-read
    });

    it('sends no `session` block when the chat already exists', async () => {
        postTurn.mockResolvedValue(ok(loadFixture('plain')));
        const { result } = renderHook(() => useChatEngine(baseProps()));
        await act(async () => { await result.current.sendMessage('one'); });
        postTurn.mockResolvedValue(ok(loadFixture('plain')));
        await act(async () => { await result.current.sendMessage('two'); });
        expect(postTurn.mock.calls[1][0].body.session).toBeUndefined();
    });

    it('a refusal removes the optimistic messages and gives the text back', async () => {
        postTurn.mockResolvedValue(refusal(409, { error: 'turn_in_progress', message: 'A reply is still being written in this chat.' }));
        const props = baseProps();
        const { result } = renderHook(() => useChatEngine(props));
        let out;
        await act(async () => { out = await result.current.sendMessage('Hi'); });
        expect(out).toMatchObject({ sent: false, refused: true, text: 'Hi' });
        expect(result.current.messages).toEqual([]);
        expect(props.showToast).toHaveBeenCalledWith('A reply is still being written in this chat.', 5000);
    });

    it('a 429 shows the budget notice and never streams', async () => {
        postTurn.mockResolvedValue(refusal(429, { error: 'budget_exhausted', remaining_tokens: 0, reset_at: '2026-09-28T00:00:00Z' }));
        const props = baseProps();
        const { result } = renderHook(() => useChatEngine(props));
        await act(async () => { await result.current.sendMessage('Hi'); });
        expect(result.current.inferenceBudget).toMatchObject({ remaining_tokens: 0 });
        expect(props.showToast.mock.calls[0][0]).toMatch(/budget exhausted/);
    });

    it('Stop marks the reply aborted and keeps the partial text', async () => {
        const events = loadFixture('plain').filter((e) => !['text-end', 'finish-step', 'finish'].includes(e.type));
        const abortable = { ok: true, status: 200, clone() { return this; }, body: {
            getReader: () => {
                const inner = bodyOf(events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')).getReader();
                return { read: async () => { const r = await inner.read(); if (r.done) { const err = new Error('aborted'); err.name = 'AbortError'; throw err; } return r; }, releaseLock() {} };
            } } };
        postTurn.mockResolvedValue(abortable);
        const { result } = renderHook(() => useChatEngine(baseProps()));
        await act(async () => { await result.current.sendMessage('Hi'); });
        expect(result.current.messages[1]).toMatchObject({ status: 'aborted', content: 'Hello there.' });
    });

    it('continuing a legacy browser-only chat imports it first', async () => {
        store.isLocalId.mockImplementation((id) => id === 's-legacy');
        store.getSession.mockResolvedValueOnce({ id: 's-legacy', messages: [], events: [], pins: [] });
        postTurn.mockResolvedValue(ok(loadFixture('plain')));
        const { result } = renderHook(() => useChatEngine(baseProps()));
        await act(async () => { await result.current.switchToSession('s-legacy'); });
        await act(async () => { await result.current.sendMessage('continue'); });
        expect(store.importLegacy).toHaveBeenCalledWith('s-legacy');
        expect(postTurn.mock.calls[0][0].sessionId).toBe('s-imported');
        store.isLocalId.mockImplementation(() => false);
    });
});

describe('useChatEngine — models and reload', () => {
    it('keeps the model objects and the budget from /v1/inference/models', async () => {
        const { result } = renderHook(() => useChatEngine(baseProps()));
        await waitFor(() => expect(result.current.availableModels).toEqual(MODELS));
        expect(result.current.reachable).toBe(true);
    });

    it('polls a reloaded chat whose reply is still streaming until it settles', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        store.getSession
            .mockResolvedValueOnce({ id: 's-1', messages: [{ id: 'a1', role: 'assistant', content: 'Par', status: 'streaming' }], events: [], pins: [] })
            .mockResolvedValue({ id: 's-1', messages: [{ id: 'a1', role: 'assistant', content: 'Partial done', status: 'complete' }], events: [], pins: [] });
        const { result } = renderHook(() => useChatEngine(baseProps()));
        await act(async () => { await result.current.switchToSession('s-1'); });
        expect(result.current.messages[0].status).toBe('streaming');
        await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
        expect(result.current.messages[0]).toMatchObject({ status: 'complete', content: 'Partial done' });
    });
});
```

- [ ] **Step 9: ChatView gives a refused message back** — in `src/components/ChatView.jsx`, replace `handleSend` with:

```jsx
  const handleSend = () => {
    if (!canSend) return;
    const text = draft;
    const atts = pendingAttachments;
    setDraft("");
    setPendingAttachments([]);
    // A refused turn (409, 413, …) never reached the chat: put the text and
    // attachments back, unless the user already started typing something new.
    Promise.resolve(sendMessage(text, atts)).then((r) => {
      if (!r?.refused) return;
      setDraft((d) => d || text);
      setPendingAttachments((a) => (a.length ? a : atts));
    });
  };
```

- [ ] **Step 10: Picker, settings select and App wiring for model objects**
  - `src/components/ChatSidebar.jsx`: add `import { groupByProvider, modelLabel } from '../lib/modelIds';` and replace the `{availableModels.map(name => …)}` block inside the `<select>` with:

```jsx
                                    {groupByProvider(availableModels).map(([provider, models]) => (
                                        <optgroup key={provider} label={provider}>
                                            {models.map((m) => (
                                                <option key={m.id} value={m.id}>{modelLabel(m)}</option>
                                            ))}
                                        </optgroup>
                                    ))}
```

  - `src/components/settings/SettingsPage.jsx`: add `import { groupByProvider, modelLabel } from '../../lib/modelIds';`. Change the `configModel` initial value to `ch.selectedModel || ch.availableModels[0]?.id || ''`, and the per-model `<select>`'s options to:

```jsx
                            {ch.availableModels.length === 0
                                ? <option value="">No models available</option>
                                : groupByProvider(ch.availableModels).map(([provider, models]) => (
                                    <optgroup key={provider} label={provider}>
                                        {models.map((m) => <option key={m.id} value={m.id}>{modelLabel(m)}</option>)}
                                    </optgroup>
                                ))}
```

  - `src/App.jsx`: add `import { migrateModelId } from './lib/modelIds';`. In the `useChatEngine({ … })` call, remove `ollamaHost, ollamaPort, inferenceSource`, `onInferencePersist: setInference` and `currentDocIndexState: …`. Keep `currentDocId`. Then, right after the block that destructures `availableModels` from `chatEngine`, add:

```jsx
  // Model ids became `provider:name` in C1. Carry a saved pre-C1 selection
  // ("qwen2.5:7b") and its per-model settings over to the matching id once.
  useEffect(() => {
    if (!availableModels.length) return;
    const next = migrateModelId(selectedModel, availableModels);
    if (next === selectedModel) return;
    setInferenceByModel((prev) => (prev[selectedModel] && !prev[next]
      ? { ...prev, [next]: prev[selectedModel] } : prev));
    setSelectedModel(next);
  }, [availableModels, selectedModel, setSelectedModel, setInferenceByModel]);
```

  - Tests: in `src/components/ChatSidebar.settings-removed.test.jsx`, set `availableModels: [{ id: 'ollama:qwen3.5:latest', provider: 'ollama', kind: 'ollama', name: 'qwen3.5:latest', capabilities: {} }]` and `selectedModel: 'ollama:qwen3.5:latest'`. In `src/components/settings/SettingsPage.test.jsx`'s context-window test, use `availableModels: [{ id: 'ollama:m1', provider: 'ollama', kind: 'ollama', name: 'm1', capabilities: {} }, { id: 'ollama:m2', provider: 'ollama', kind: 'ollama', name: 'm2', capabilities: {} }]`, `selectedModel: 'ollama:m1'`, `inferenceByModel: { 'ollama:m1': {}, 'ollama:m2': {} }`, and expect the value `'ollama:m1'`.

- [ ] **Step 11: Run the frontend suite and lint**

Run: `npx vitest run && npm run lint`
Expected: all pass, lint clean. If a test still imports `chatTools`, `chatHistory`, `buildRequestFields` or `chatFetch`, it tests removed code: delete that test case, and say which ones in the task report.

- [ ] **Step 12: Commit** (ask the user first)

```bash
git add -A src/hooks src/lib src/components/ChatView.jsx src/components/ChatSidebar.jsx src/components/ChatSidebar.settings-removed.test.jsx src/components/settings src/App.jsx
git commit -m "feat(chat-ui): the SPA sends turns to the server and renders its events; browser loop removed (C1)"
```

---

### Task 13: SPA — Local mode removed; statuses, context notes, provider-aware settings

**Files:**
- Delete: `src/components/chat/InferenceSourceSelect.jsx`, `src/components/chat/InferenceSourceSelect.test.jsx`
- Modify: `src/App.jsx`, `src/constants.js`, `src/components/settings/SettingsPage.jsx`, `src/components/ChatSidebar.jsx`, `src/components/ChatView.jsx`
- Test: `src/components/ChatView.status.test.jsx` (new), `src/components/settings/SettingsPage.test.jsx`

**Interfaces:**
- Consumes (Task 12): `supportedKnobs(model)`; message fields `status`, `docContext.notes`, `error`.
- Produces: no new interfaces. User-visible changes:
  - The Settings page has no "Inference source" or "Ollama host" fields.
  - Per-model knobs show only what the provider supports.
  - Assistant bubbles show "Still generating…" / "Stopped" / the error text, and a muted context line ("Used 4 passages from *Thesis.pdf*", "Trimmed 6 older messages to fit").

- [ ] **Step 1: Write the failing UI tests** — `src/components/ChatView.status.test.jsx`:

```jsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import ChatView from './ChatView';

const theme = { bg: '', bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '', text: '', textSecondary: '', textMuted: '', hover: '' };
const baseProps = (over = {}) => ({
    theme, darkMode: false, effectiveIsMobile: false, messages: [], isStreaming: false,
    selectedModel: 'ollama:m', reachable: true, sendMessage: vi.fn(), stopStream: vi.fn(),
    speakingMessageId: null, speakMessage: vi.fn(), stopSpeaking: vi.fn(), downloadingMessageId: null,
    downloadMessageAudio: vi.fn(), showToast: vi.fn(), pins: [], onRemovePin: vi.fn(), numCtx: null, ...over,
});
const q = { id: 'u1', role: 'user', content: 'q' };

describe('ChatView — reply status and context notes', () => {
    it('shows "Still generating…" for a reply the server is still writing after a reload', () => {
        render(<ChatView {...baseProps({ messages: [q, { id: 'a1', role: 'assistant', content: 'Par', status: 'streaming' }] })} />);
        expect(screen.getByText(/still generating/i)).toBeInTheDocument();
    });

    it('marks stopped and failed replies', () => {
        render(<ChatView {...baseProps({ messages: [q,
            { id: 'a1', role: 'assistant', content: 'half', status: 'aborted' },
            { id: 'u2', role: 'user', content: 'again' },
            { id: 'a2', role: 'assistant', content: 'x', status: 'error', error: { message: "Can't reach the model provider." } }] })} />);
        expect(screen.getByText('Stopped')).toBeInTheDocument();
        expect(screen.getByText("Can't reach the model provider.")).toBeInTheDocument();
    });

    it('explains a turn that ran out of tool rounds (finishReason max-steps)', () => {
        render(<ChatView {...baseProps({ messages: [q, { id: 'a1', role: 'assistant', content: '', status: 'complete', finishReason: 'max-steps' }] })} />);
        expect(screen.getByText(/used its tool rounds without writing an answer/i)).toBeInTheDocument();
    });

    it('shows the stage-0 notes on the reply', () => {
        const notes = [{ kind: 'prefetch', docName: 'Thesis.pdf', count: 4 }, { kind: 'trimmed', messages: 6, attachments: 1 }];
        render(<ChatView {...baseProps({ messages: [q, { id: 'a1', role: 'assistant', content: 'ok', status: 'complete', docContext: { notes } }] })} />);
        expect(screen.getByText('Used 4 passages from Thesis.pdf · Trimmed 6 older messages and 1 older attachment to fit')).toBeInTheDocument();
    });

    it('no longer mentions Ollama host/port when the server is unreachable', () => {
        render(<ChatView {...baseProps({ reachable: false })} />);
        expect(screen.queryByText(/host & port/i)).toBeNull();
        expect(screen.getByText(/can't reach the model server/i)).toBeInTheDocument();
    });
});
```

  In `src/components/settings/SettingsPage.test.jsx`:
  - Remove `inferenceSource`, `setInferenceSource`, `ollamaHost`, `setOllamaHost`, `ollamaPort` and `setOllamaPort` from `bags()`.
  - Append:

```jsx
describe('SettingsPage — provider-aware inference settings', () => {
  const models = [
    { id: 'local:m1', provider: 'local', kind: 'ollama', name: 'm1', capabilities: { thinking: false } },
    { id: 'cloud:x', provider: 'cloud', kind: 'openai', name: 'x', capabilities: { thinking: true } },
  ];
  it('has no inference-source or Ollama host fields (Local mode is gone)', () => {
    render(<SettingsPage {...bags()} />);
    expect(screen.queryByText(/inference source/i)).toBeNull();
    expect(screen.queryByLabelText(/ollama host/i)).toBeNull();
  });
  it('shows context size and keep-alive only for Ollama models, thinking only when supported', () => {
    const b = bags();
    b.chatSettings = { ...b.chatSettings, availableModels: models, selectedModel: 'cloud:x' };
    render(<SettingsPage {...b} />);
    expect(screen.queryByLabelText(/context window/i)).toBeNull();
    expect(screen.queryByLabelText(/keep model warm/i)).toBeNull();
    expect(screen.getByLabelText(/thinking/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/configuring model/i), { target: { value: 'local:m1' } });
    expect(screen.getByLabelText(/context window/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/thinking/i)).toBeNull();
  });
});
```

  (`InferenceRow` labels its control with its `label` prop; if `getByLabelText` doesn't find it, check `src/components/chat/InferenceRow.jsx` and query the same way the existing context-window test does.)

- [ ] **Step 2: Run to confirm failure**

Run: `npx vitest run src/components/ChatView.status.test.jsx src/components/settings/SettingsPage.test.jsx`
Expected: FAIL (no status text; the source select is still rendered).

- [ ] **Step 3: Remove Local mode**
  - `git rm src/components/chat/InferenceSourceSelect.jsx src/components/chat/InferenceSourceSelect.test.jsx`
  - `src/constants.js`: delete the `OLLAMA_DEFAULTS` export and its comment.
  - `src/App.jsx`:
    - Delete the `OLLAMA_DEFAULTS` import and the `ollamaHost`, `ollamaPort` and `inferenceSource` `usePersistedState` lines, with their comment.
    - Remove `inferenceSource={inferenceSource}` from `<ChatSidebar>`.
    - In `chatSettings`, drop `inferenceSource` and `setInferenceSource`.
    - In `connectionSettings`, drop `ollamaHost`, `setOllamaHost`, `ollamaPort`, `setOllamaPort` and `inferenceSource`.
    - An existing browser's saved `inferenceSource: 'local'` in localStorage is now simply never read (spec §8).
  - `src/components/ChatSidebar.jsx`: delete the `inferenceSource = 'server',` prop, and change `{inferenceSource === 'server' && inferenceBudget?.remaining_tokens != null && (` to `{inferenceBudget?.remaining_tokens != null && (`.
  - `src/components/settings/SettingsPage.jsx`:
    - Delete the `InferenceSourceSelect` import and its `<Field label="Inference source">`, and the whole `{c.inferenceSource === 'local' && (…)}` Ollama host block.
    - Import `supportedKnobs` from `../../lib/modelIds`.
    - After the `inf` line, add `const knobs = supportedKnobs(ch.availableModels.find((m) => m.id === configModel));`.
    - Wrap the four `InferenceRow`s: `{knobs.numCtx && <InferenceRow … label="Context window" …/>}`, `{knobs.keepAlive && …"Keep model warm"…}`, `{knobs.think && …"Thinking"…}`, and leave "Max reply tokens" unconditional.
    - Change the help line to: `Saved per model. Only the settings this model's provider supports are shown; changing an Ollama model's context window reloads it.`

- [ ] **Step 4: Statuses and notes in the bubble** — `src/components/ChatView.jsx`. Add these two components near `DocContextChip`:

```jsx
// Stage-0 notes the server attached to a reply (spec §5.2, §8).
function ContextNotes({ notes, theme }) {
  if (!Array.isArray(notes) || notes.length === 0) return null;
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const lines = notes
    .map((n) => {
      if (n.kind === "prefetch") return `Used ${plural(n.count, "passage")} from ${n.docName}`;
      if (n.kind === "trimmed") {
        const parts = [];
        if (n.messages) parts.push(`${plural(n.messages, "older message")}`);
        if (n.attachments) parts.push(`${plural(n.attachments, "older attachment")}`);
        return parts.length ? `Trimmed ${parts.join(" and ")} to fit` : null;
      }
      return null;
    })
    .filter(Boolean);
  if (lines.length === 0) return null;
  return <p className={`text-[10px] italic ${theme.textMuted}`}>{lines.join(" · ")}</p>;
}

// Where a reply stands when it isn't simply complete (spec §7.3, §8).
function ReplyStatus({ message, isStreamingNow, theme }) {
  if (message.status === "streaming" && !isStreamingNow) {
    return (
      <p className={`flex items-center gap-1.5 text-[10px] font-bold ${theme.textMuted}`}>
        <Loader2 size={10} className="animate-spin" /> Still generating…
      </p>
    );
  }
  if (message.status === "aborted") return <p className={`text-[10px] italic ${theme.textMuted}`}>Stopped</p>;
  if (message.status === "error") {
    return <p className="text-[10px] font-bold text-red-400">{message.error?.message || "The reply failed."}</p>;
  }
  if (message.finishReason === "max-steps") {
    return (
      <p className={`text-[10px] italic ${theme.textMuted}`}>
        The model used its tool rounds without writing an answer. Try asking again more specifically.
      </p>
    );
  }
  return null;
}
```

  In the message bubble component:
  - Right after the `{docContext && (<DocContextChip … />)}` block, add `{!isUser && <ContextNotes notes={message.docContext?.notes} theme={theme} />}`.
  - Right before `{toolCalls && toolCalls.length > 0 && (`, add `{!isUser && <ReplyStatus message={message} isStreamingNow={isStreamingNow} theme={theme} />}`.

  The existing `const docContext = isUser ? message.docContext : null;` stays: the chip is for user excerpts; assistant `docContext` now carries `notes`.

  Replace these strings:
  - `"Ollama unreachable — check host/port in sidebar."` becomes `"Can't reach the model server."`
  - `Talk to Ollama and have the responses read aloud through Kokoro.` becomes `Talk to a model and have its replies read aloud through Kokoro.`
  - `<>Ollama not reachable. Set host & port in the sidebar.</>` becomes `<>Can't reach the model server. Check that the backend is running, or ask your admin.</>`
  - `Chat with a local model` becomes `Chat with a model`

- [ ] **Step 5: Run the frontend suite and lint**

Run: `npx vitest run && npm run lint`
Expected: all pass, lint clean.

- [ ] **Step 6: Commit** (ask the user first)

```bash
git add -A src/App.jsx src/constants.js src/components
git commit -m "feat(chat-ui): remove Local mode; reply status, stage-0 notes, provider-aware settings (C1)"
```

---

### Task 14: Remove the old paths on the server

**Files:**
- Modify: `server/routers/inference.py` (delete `/chat`, its models, `_UsageTap` and the module httpx client)
- Modify: `server/app.py` (drop `start_inference`/`stop_inference`)
- Modify: `server/routers/chat_sessions.py` (delete `PUT` and `SessionIn`; update the module docstring)
- Modify: `server/services/model_router.py` (delete `is_model_allowed`)
- Modify tests:
  - `server/tests/test_inference_router.py`: delete the `/chat` tests and the `stream_response`/`NDJSON`/`VALID_BODY` helpers they alone use. Keep the `/models` tests; the `app` fixture no longer calls `inf.start_client`.
  - `server/tests/test_chat_sessions_authz.py`: delete `test_upsert_stamps_owner`. Ownership stamping is now covered by `test_a_turn_streams_sse_and_creates_the_session` and `test_import_copies_a_legacy_chat_under_a_new_id`.
  - `server/tests/test_model_router.py`: delete any `is_model_allowed` test.

**Interfaces:**
- Consumes: Tasks 12–13 (the SPA no longer calls these).
- Produces: `POST /v1/inference/chat` → 404/405, and `PUT /v1/chat/sessions/{id}` → 405. `GET /v1/inference/models` is unchanged from Task 4.

- [ ] **Step 1: Prove nothing in the repo still calls them**

Run: `grep -rn "inference/chat\|method: 'PUT'\|saveSession\|is_model_allowed\|start_client as start_inference" src extension server --include=*.js --include=*.jsx --include=*.py | grep -v "/tests/"`
Expected: matches only in the files this task edits. Anything else means an earlier task missed it: fix it there first and note it in the report.

- [ ] **Step 2: Add the regression tests** — append to `server/tests/test_chat_sessions_authz.py`:

```python
async def test_whole_record_put_is_gone(db_conn, chat_app):
    owner = await _member(db_conn, "owner")
    chat_app.dependency_overrides[deps.get_current_user] = lambda: owner
    async with _client(chat_app) as client:
        r = await client.put("/v1/chat/sessions/s-new", json={"id": "s-new"})
    assert r.status_code == 405
```

  and to `server/tests/test_inference_router.py`:

```python
async def test_raw_chat_passthrough_is_gone(client):
    r = await client.post("/v1/inference/chat", json={"model": "m", "messages": []})
    assert r.status_code in (404, 405)
```

- [ ] **Step 3: Run to confirm failure**

Run: `.venv/bin/python -m pytest server/tests/test_chat_sessions_authz.py server/tests/test_inference_router.py -q`
Expected: the two new tests fail (the endpoints still exist).

- [ ] **Step 4: Delete the code**
  - `server/routers/inference.py`:
    - Keep: the imports that `list_models` needs, `router`, and `list_models`.
    - Delete: `_client`, `start_client`, `stop_client`, `_get_client`, `ChatOptions`, `ToolCallFunction`, `ToolCall`, `ChatMessage`, `ChatRequest`, `_UsageTap` and `chat`.
    - Replace the module docstring with: `"""/v1/inference/models — every configured provider's models, tagged provider:name, with capabilities and the caller's budget (C1 spec §7.5). Chat turns are POST /v1/chat/sessions/{id}/turns."""`
  - `server/app.py`: delete the `start_inference`/`stop_inference` import and their two calls.
  - `server/routers/chat_sessions.py`:
    - Delete `upsert_session` and `SessionIn`.
    - Keep `MessageIn` and `EventIn`, which `ImportIn` uses.
    - Replace the module docstring with: `"""Chat session reads and small edits. The server writes messages itself as each turn streams (routers/chat_turns.py, C1); this router lists, reads (with message status), renames, pins, deletes, and imports a legacy browser-only chat."""`
    - In `patch_session`'s docstring, change "timestamps and message data must go through PUT" to "messages are written by turns".
  - `server/services/model_router.py`: delete `is_model_allowed`. In the module docstring, add after the first sentence: "Since C1 it owns only what isn't chat: embeddings, the budget, the timeout and the summary model; provider lists and allow-lists live in server/llm/router.py (spec §4.6)."

- [ ] **Step 5: Run the whole backend suite**

Run: `.venv/bin/python -m pytest server/tests -q && .venv/bin/python -c "import ast; ast.parse(open('server/app.py').read())"`
Expected: all pass.

- [ ] **Step 6: Commit** (ask the user first)

```bash
git add -A server
git commit -m "refactor(chat)!: remove /v1/inference/chat and whole-record PUT; model_router narrowed (C1)"
```

---

### Task 15: Docs, `.env.example` and CHANGELOG

**Files:**
- Modify: `.env.example`, `CHANGELOG.md`, `README.md`, `docs/DEPLOYMENT.md`, `docs/ARCHITECTURE.md`, `docs/CHAT_WITH_PDF.md`, `docs/USER_GUIDE.md`

**Interfaces:** none (documentation). Every statement describes what a user can do **today** from the UI (the journey rule).

- [ ] **Step 1: `.env.example`** — after the existing `INFERENCE_TIMEOUT_S` block, add:

```bash
# ---- Model providers (C1) ------------------------------------------------
# Unset = one provider named "ollama" at OLLAMA_URL, limited by
# INFERENCE_MODELS (exactly the pre-C1 behaviour). Model ids in the chat are
# "<provider>:<model>", e.g. ollama:qwen2.5:7b.
#
# To add providers, list their names in display order and describe each one.
# KIND is ollama (native API) or openai (any OpenAI-compatible server: vLLM,
# OpenRouter, LiteLLM, Ollama's own /v1). For kind=openai, URL is the API base
# INCLUDING the version path. API keys stay on the server: they are never sent
# to the browser, logged, or shown in the admin console.
# INFERENCE_PROVIDERS=local,openrouter
# INFERENCE_LOCAL_KIND=ollama
# INFERENCE_LOCAL_URL=http://localhost:11434
# INFERENCE_OPENROUTER_KIND=openai
# INFERENCE_OPENROUTER_URL=https://openrouter.example.com/api/v1
# INFERENCE_OPENROUTER_API_KEY=sk-your-key
# INFERENCE_OPENROUTER_MODELS=vendor/model-a,vendor/model-b   # allow-list; unset = all it lists
#
# Trap: OLLAMA_URL is still where document EMBEDDINGS run, even when
# INFERENCE_PROVIDERS is set. Remove it and indexing calls localhost:11434.
#
# INFERENCE_TIMEOUT_S above is an IDLE timeout (seconds of silence from a
# provider, first token included), not a cap on how long a reply may take.

# ---- Chat orchestrator (C1) ----------------------------------------------
# Tool rounds per turn before one final answer with tools off.
# CHAT_MAX_TOOL_ROUNDS=1
# Stage 0: search the open document BEFORE the model runs; passages scoring at
# least this (cosine similarity) go into the prompt, saving a tool round. The
# default is a cautious guess, not a measurement; tune it from the DEBUG logs.
# 1 disables prefetch.
# CHAT_PREFETCH_MIN_SCORE=0.75
# CHAT_PREFETCH_K=4
# Tokens kept free for the reply when old history is trimmed to fit.
# CHAT_REPLY_RESERVE_TOKENS=2048
# Tokens counted per image/audio attachment when trimming (a deliberate overestimate).
# CHAT_ATTACHMENT_TOKEN_ESTIMATE=1500
# Largest chat request (message + attachments), in MB. Attachment bytes are
# stored with the chat so follow-up questions can see them; deleting the chat deletes them.
# CHAT_MAX_REQUEST_MB=25
```

- [ ] **Step 2: `CHANGELOG.md`** — under `## [Unreleased]`:
  - `### Added`:
    - **Server-side chat.** Each turn runs on the server and streams typed events. The chat is saved as it streams, so a reload mid-reply shows "Still generating…" and then the full reply. Any image you attach stays visible to the model for follow-up questions, including after a reload.
    - **Model providers.** Native Ollama plus any OpenAI-compatible server (vLLM, OpenRouter, LiteLLM), configured in `.env` (`INFERENCE_PROVIDERS`). The model picker groups models by provider and shows what each model can do.
    - **Stage 0.** The open document is searched before the model runs, and the reply shows "Used N passages from …"; the time is in the prompt.
  - `### Changed`:
    - **Breaking: `POST /v1/inference/chat` is removed.** Chat turns are `POST /v1/chat/sessions/{id}/turns` (server-sent events).
    - **Breaking: `PUT /v1/chat/sessions/{id}` is removed.** The server writes messages itself; `POST /v1/chat/sessions/import` copies a legacy browser-only chat.
    - **`GET /v1/inference/models`** returns objects `{id, provider, kind, name, capabilities}` instead of name strings.
    - **Local mode (browser → Ollama directly) is removed.** Chat always goes through the server, which by default uses the same `OLLAMA_URL` as before.
    - **Pins now sit near the top of the prompt** instead of just before your message, so providers can reuse cached work across turns.
    - **The `current_time_date` tool is gone.** The current time is in every prompt.
  - `### Upgrade notes`:
    - Migration `013` is additive: it runs on the next backend start. Back up first as usual.
    - Deploy the SPA and backend together.
    - A saved selection like `qwen2.5:7b` becomes `ollama:qwen2.5:7b` automatically.
    - Proxies that buffer `text/event-stream` make replies appear all at once (see DEPLOYMENT.md).
    - Audio attachments are still disabled in the UI (unchanged).

- [ ] **Step 3: `docs/DEPLOYMENT.md`** — add a "Model providers and chat streaming" section. It summarizes the `.env` block above with one worked example (local Ollama + one OpenRouter-style provider), states the `OLLAMA_URL`-still-embeds trap, and adds this paragraph:

  > Replies stream as `text/event-stream`. nginx honours the `X-Accel-Buffering: no` header the server sends, and the sample config's `proxy_buffering off` covers it. Some CDNs and tunnels (Cloudflare among them) may still buffer event streams. The symptom is a reply that appears all at once after a long pause. Turn off buffering for `/v1/chat/` in that proxy.

- [ ] **Step 4: `docs/ARCHITECTURE.md`, `docs/CHAT_WITH_PDF.md`, `docs/USER_GUIDE.md`, `README.md`** — change every place that says the browser runs the chat, calls Ollama or offers Local mode:
  - The chat loop, tools and history now live in `server/chat/`, and providers in `server/llm/`.
  - Add a short "What happens when you send a message" list mirroring spec §5.1.
  - The user guide tells a user where the new controls are: the grouped model picker; Settings → Chat & Inference showing only supported knobs; "Still generating…"; "Stopped".
  - The user guide is explicit that choosing providers is an admin `.env` task: there is no provider screen yet (spec §12).

  Run `grep -rn "Local mode\|inferenceSource\|/v1/inference/chat\|chatTools\|browser.*Ollama" README.md docs/*.md` and fix every hit that describes current behaviour. Specs and plans under `docs/superpowers/` are history: leave them alone.

- [ ] **Step 5: Check**

Run: `grep -rn "oraian\|sk-[A-Za-z0-9]\{20,\}" .env.example docs/DEPLOYMENT.md CHANGELOG.md; npx vitest run && .venv/bin/python -m pytest server/tests -q`
Expected: no deployment hostnames and no real-looking keys (only `sk-your-key`); all tests still pass.

- [ ] **Step 6: Commit** (ask the user first)

```bash
git add .env.example CHANGELOG.md README.md docs/DEPLOYMENT.md docs/ARCHITECTURE.md docs/CHAT_WITH_PDF.md docs/USER_GUIDE.md
git commit -m "docs: C1 — model providers, server-side chat, removed endpoints, streaming through proxies"
```

---

### Task 16: Running-app walk and the stage-0 measurement (required before "done")

**Files:**
- Create: `docs/superpowers/plans/2026-09-27-chat-orchestrator-walk.md` (the evidence)

**Interfaces:** none. This task is done by the controller, not a subagent, in the running app, with a browser (Playwright MCP or chrome-devtools MCP).

- [ ] **Step 1: Start the app** — `./startup.sh up-with-dev-auth` (podman: from a terminal without the snap `XDG_DATA_HOME`, per the startup memory). Confirm the backend logs `Inference providers: ollama (ollama)` and `Applying migration 013_chat_orchestrator.sql` on first start. Log in as the dev `admin-user`.

- [ ] **Step 2: Walk journeys 1–10 (spec §2) against local Ollama.** For each step, write the control used and what was seen into the walk file:

| # | Journey | Control(s) | Pass when |
|---|---|---|---|
| 1 | Send; stream; thinking | composer + Enter; a thinking model with Settings → Thinking = On | text streams; "Show thinking" disclosure fills |
| 2 | Read aloud streaming and after-complete | Settings → Read-aloud mode; Auto read-aloud | speech starts mid-reply / after it |
| 3 | Stop mid-stream | the Stop button | partial text stays, "Stopped" shown; a new message works immediately (no 409) |
| 4 | Pin an excerpt; ask about it | reader: select text → Pin; then chat | the answer uses the pin; compare with a long chat for the pins-moved risk (§5.2) |
| 5 | Attach an image; follow-up; reload; follow-up | paperclip / drop; F5 | the model describes it, answers the follow-up, and after reload still sees it. Audio: the toast "not supported yet" (ruling R7, unchanged) |
| 5b | Image to a non-vision model | pick a text-only model | notice "This model can't read images.", no model call |
| 6 | Ask about the open, indexed document | open a PDF in the reader, then chat | "Used N passages from …" line; or the `search_document` tool panel when nothing is strong enough |
| 7 | Web question; date question | chat | web_search panel; the date answered with no tool call |
| 8 | Budget exhausted | Admin → user → daily budget 1; send | the budget notice; no retries in the network panel |
| 9 | Models from two providers; per-model knobs | set `INFERENCE_PROVIDERS=local,ollamav1` with `INFERENCE_OLLAMAV1_KIND=openai`, `INFERENCE_OLLAMAV1_URL=http://localhost:11434/v1`; restart | the picker shows two groups; an `ollamav1:` model hides Context window / Keep warm |
| 10 | Rename, reload, delete, export; reload mid-reply | sidebar controls; F5 while streaming | "Still generating…" then the full reply |

  Then repeat journeys 1, 3, 5 and 6 with an `ollamav1:` model (the OpenAI-compatible adapter). Any failure is a bug to fix before calling C1 done. Record it and the fix in the walk file.

- [ ] **Step 3: Measure stage 0** (spec §11).
  - Open an indexed PDF and ask the same document question five times with prefetch on (default), then five times with `CHAT_PREFETCH_MIN_SCORE=1` (restart between the two runs).
  - Per run, record: turn duration (the `chat turn end … duration_ms` log line), steps, prompt+completion tokens, and whether `search_document` was called after a prefetch hit (DEBUG log, `LOG_LEVEL=DEBUG`).
  - Report medians. State the hardware and model.
  - Don't generalize beyond them. If prefetch hits cost more than they save, say so and recommend a threshold from the logged top scores.

- [ ] **Step 4: Answer the journey question in the walk file:** "Can the user do what they asked for, start to finish, without curl?" For each thing C1 still doesn't let a user do, list it plainly:
  - Providers are configured only in `.env`; there's no admin screen.
  - Audio attachments are disabled.
  - Old images show as chips after a reload, even though the model still sees them.

- [ ] **Step 5: Commit the walk file** (ask the user first)

```bash
git add docs/superpowers/plans/2026-09-27-chat-orchestrator-walk.md
git commit -m "docs: C1 running-app walk and stage-0 measurement"
```
