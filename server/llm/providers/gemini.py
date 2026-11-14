"""Google Gemini adapter (kind `gemini`): the Interactions API, stateless and streamed.

`config.url` is the API base INCLUDING the version path, like the OpenAI adapter's (ruling R10):
https://generativelanguage.googleapis.com/v1beta. Three things here are not what they look like:

- `store: false` is sent on every request. Google's default keeps each interaction (the whole
  conversation) for 1 to 55 days; this app holds the history itself and sends it each time.
- Gemini has no "thinking off". "off" asks for `minimal` ("little to no thinking") and no summaries;
  a model that refuses the level answers at its own default, with a notice.
- A function call the model made must come back with the `thought` step before it, signature
  included, or the next request is refused. Those steps ride on the ToolCall (`extra`), which the
  chat loop hands back unchanged. A call the app made itself (document prefetch, web consent) has
  none, and carries the signature Google documents for skipping that check.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncIterator

import httpx

from ..types import (Capabilities, CallSettings, Chunk, FeatureDropped, Finish, Message, ProviderError,
                     ProviderUnavailable, ReasoningDelta, TextDelta, ToolCall, ToolCallReady, ToolSpec, Usage)
from .base import (FeatureMemory, ProviderConfig, TTLCache, is_feature_rejection, is_overflow, iter_lines,
                   json_object, open_stream, parse_arguments, provider_error, safe_error_message)

logger = logging.getLogger(__name__)

DEFAULT_URL = "https://generativelanguage.googleapis.com/v1beta"
API_REVISION = "2026-05-20"   # the steps schema this adapter reads
# Google's documented value for a function call no model produced (thought-signatures FAQ).
DUMMY_SIGNATURE = "skip_thought_signature_validator"
MAX_MODEL_PAGES = 10

_LEVEL = {"off": "minimal", "low": "low", "medium": "medium", "high": "high"}   # "on": the model's default
# Listed with generateContent but not for a chat: left out of the picker unless an allow-list names them.
_NOT_CHAT = re.compile(r"tts|image|embedding|live|audio|transcribe|robotics|computer-use|banana|aqa")
_AUDIO_MIME = {"audio/x-wav": "audio/wav", "audio/wave": "audio/wav", "audio/mpeg": "audio/mp3"}
_STREAM_ERROR_STATUS = {"invalid_argument": 400, "unauthenticated": 401, "permission_denied": 403,
                        "not_found": 404, "resource_exhausted": 429, "rate_limit_exceeded": 429,
                        "unavailable": 503, "gateway_timeout": 504, "deadline_exceeded": 504}
_BAD_KEY = re.compile(r"api[ _]key", re.IGNORECASE)


def _headers(cfg: ProviderConfig) -> dict[str, str]:
    """The key goes in a header, never the URL: httpx logs URLs."""
    headers = {"Api-Revision": API_REVISION}
    if cfg.api_key:
        headers["x-goog-api-key"] = cfg.api_key
    return headers


def _user_content(m: Message) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = [{"type": "text", "text": m.content}] if m.content else []
    for a in m.attachments:
        if a.kind == "image":
            parts.append({"type": "image", "mime_type": a.mime, "data": a.base64})
        elif a.kind == "audio":
            parts.append({"type": "audio", "mime_type": _AUDIO_MIME.get(a.mime, a.mime), "data": a.base64})
    return parts


def _steps(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    """(system instruction, input steps). Leading system messages are the instruction; a later one is
    sent as user text, since the API has one instruction per request."""
    system: list[str] = []
    steps: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system" and not steps:
            system.append(m.content)
        elif m.role in ("user", "system"):
            steps.append({"type": "user_input", "content": _user_content(m)})
        elif m.role == "assistant":
            carried = any((c.extra or {}).get("gemini_thoughts") for c in m.tool_calls)
            calls: list[dict[str, Any]] = []
            thoughts: list[dict[str, Any]] = []
            for n, c in enumerate(m.tool_calls):
                thoughts.extend((c.extra or {}).get("gemini_thoughts") or [])
                if n == 0 and not carried:
                    thoughts.append({"type": "thought", "signature": DUMMY_SIGNATURE})
                calls.append({"type": "function_call", "id": c.id, "name": c.name, "arguments": c.arguments})
            # As the model sent them: thoughts, then what it said, then every call (results follow, never
            # between two calls: Google answers 400 to interleaved calls and results).
            steps.extend(thoughts)
            if m.content:
                steps.append({"type": "model_output", "content": [{"type": "text", "text": m.content}]})
            steps.extend(calls)
        elif m.role == "tool":
            step: dict[str, Any] = {"type": "function_result", "call_id": m.tool_call_id,
                                    "result": [{"type": "text", "text": m.content}]}
            if m.name:
                step["name"] = m.name
            steps.append(step)
    return "\n\n".join(s for s in system if s), steps


class GeminiProvider:
    def __init__(self, config: ProviderConfig, client: httpx.AsyncClient) -> None:
        self.config = config
        self._client = client
        self._features = FeatureMemory()
        self._models = TTLCache()

    # ---------- models ----------

    async def _entries(self) -> dict[str, dict]:
        cached = self._models.get("models")
        if cached is not None:
            return cached
        entries: dict[str, dict] = {}
        token: str | None = None
        for _ in range(MAX_MODEL_PAGES):
            params = {"pageSize": "1000", **({"pageToken": token} if token else {})}
            try:
                resp = await self._client.get(f"{self.config.url}/models", params=params,
                                              headers=_headers(self.config))
            except httpx.HTTPError as e:
                raise ProviderUnavailable(type(e).__name__) from e
            if resp.status_code != 200:
                raise self._explain(provider_error(resp.status_code, resp.content))
            body = json_object(resp)
            for e in body.get("models") or []:
                if isinstance(e, dict) and isinstance(e.get("name"), str):
                    entries[e["name"].removeprefix("models/")] = e
            token = body.get("nextPageToken")
            if not token:
                break
        self._models.put("models", entries)
        return entries

    async def list_models(self) -> list[str]:
        names = []
        for name, entry in (await self._entries()).items():
            methods = entry.get("supportedGenerationMethods")
            if isinstance(methods, list) and "generateContent" not in methods:
                continue
            if self.config.models is None and _NOT_CHAT.search(name):
                continue
            names.append(name)
        return names

    async def capabilities(self, model: str) -> Capabilities:
        try:
            entry = (await self._entries()).get(model) or {}
        except (ProviderUnavailable, ProviderError):
            return Capabilities()
        thinking, window = entry.get("thinking"), entry.get("inputTokenLimit")
        # The list says nothing about tools, pictures or audio: None, so nothing is refused up front.
        caps = Capabilities(thinking=thinking if isinstance(thinking, bool) else None,
                            context_window=window if isinstance(window, int) else None)
        return self._features.apply(model, caps)

    # ---------- chat ----------

    async def stream_chat(self, model: str, messages: list[Message], tools: list[ToolSpec],
                          settings: CallSettings) -> AsyncIterator[Chunk]:
        caps = await self.capabilities(model)
        thinks = caps.thinking is not False
        level = _LEVEL.get(settings.think) if thinks else None
        if level and self._features.rejected(model, f"level:{level}"):
            level = None
        use_tools = bool(tools) and caps.tools is not False
        dropped: list[str] = []
        refused_level: str | None = None
        overflowed = False
        while True:
            body = self._body(model, messages, tools if use_tools else [], settings, level, thinks)
            request = self._client.build_request("POST", f"{self.config.url}/interactions", json=body,
                                                 headers=_headers(self.config))
            try:
                resp = await open_stream(self._client, request)
                break
            except ProviderError as err:
                err = self._explain(err)
                if not is_feature_rejection(err):
                    raise err
                overflowed = overflowed or is_overflow(err)
                if level is not None:
                    refused_level, level, feature = level, None, "think_level"
                elif use_tools:
                    use_tools, feature = False, "tools"
                else:
                    raise
                dropped.append(feature)
        # Only the feature whose removal made the request pass is believed (ruling R12), and not at all
        # when one of the errors was a prompt that didn't fit.
        if dropped and not overflowed:
            self._features.remember(model, "tools" if dropped[-1] == "tools" else f"level:{refused_level}")
        for feature in dropped:
            yield FeatureDropped(feature)   # type: ignore[arg-type]
        async for chunk in self._read(resp):
            yield chunk

    @staticmethod
    def _explain(err: ProviderError) -> ProviderError:
        """Google answers a wrong key with 400 INVALID_ARGUMENT. Left as 400 it would be taken for a
        rejected feature, retried twice, and shown as Google's raw sentence."""
        if err.status == 400 and _BAD_KEY.search(err.safe_message):
            return provider_error(401, b"")
        return err

    @staticmethod
    def _body(model: str, messages: list[Message], tools: list[ToolSpec], settings: CallSettings,
              level: str | None, thinks: bool) -> dict[str, Any]:
        system, steps = _steps(messages)
        body: dict[str, Any] = {"model": model, "store": False, "stream": True, "input": steps}
        if system:
            body["system_instruction"] = system
        if tools:
            body["tools"] = [{"type": "function", "name": t.name, "description": t.description,
                              "parameters": t.parameters} for t in tools]
        config: dict[str, Any] = {}
        if thinks:
            config["thinking_summaries"] = "none" if settings.think == "off" else "auto"
            if level:
                config["thinking_level"] = level
        if settings.num_predict is not None:
            config["max_output_tokens"] = settings.num_predict
        if settings.temperature is not None:
            config["temperature"] = settings.temperature
        # num_ctx and keep_alive are Ollama's: a hosted model has neither.
        if config:
            body["generation_config"] = config
        return body

    @staticmethod
    async def _read(resp: httpx.Response) -> AsyncIterator[Chunk]:
        steps: dict[int, dict[str, Any]] = {}
        thoughts: list[dict[str, Any]] = []   # finished thought steps not yet attached to a call
        called = False
        status: str | None = None
        usage: Usage | None = None
        try:
            async for line in iter_lines(resp):
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                kind = event.get("event_type")
                index = event.get("index")
                if kind == "error" or (kind is None and event.get("error")):
                    raw = json.dumps(event)
                    logger.warning("Gemini stream error: %s", safe_error_message(raw))
                    err = event.get("error") if isinstance(event.get("error"), dict) else {}
                    code = err.get("code")
                    http = code if isinstance(code, int) and code >= 400 else \
                        _STREAM_ERROR_STATUS.get(str(code).lower(), 502)
                    raise provider_error(http, raw)
                if kind == "step.start":
                    step = event.get("step") if isinstance(event.get("step"), dict) else {}
                    whole = step.get("arguments")
                    steps[index] = {"type": step.get("type"), "id": step.get("id") or "",
                                    "name": step.get("name") or "", "args": "",
                                    "whole": whole if isinstance(whole, dict) and whole else None,
                                    "signature": step.get("signature") or "", "summary": ""}
                elif kind == "step.delta" and index in steps:
                    step, delta = steps[index], event.get("delta") or {}
                    dtype = delta.get("type")
                    if dtype == "text" and delta.get("text"):
                        yield TextDelta(delta["text"])
                    elif dtype == "thought_summary":
                        content = delta.get("content") or {}
                        if content.get("type") == "text" and content.get("text"):
                            step["summary"] += content["text"]
                            yield ReasoningDelta(content["text"])
                    elif dtype == "thought_signature":
                        step["signature"] = delta.get("signature") or step["signature"]
                    elif dtype == "arguments_delta":
                        fragment = delta.get("arguments")
                        if isinstance(fragment, dict):
                            step["whole"] = fragment
                        elif isinstance(fragment, str):
                            step["args"] += fragment
                elif kind == "step.stop" and index in steps:
                    step = steps.pop(index)
                    if step["type"] == "thought":
                        thought: dict[str, Any] = {"type": "thought", "signature": step["signature"]}
                        if step["summary"]:
                            thought["summary"] = [{"type": "text", "text": step["summary"]}]
                        thoughts.append(thought)
                    elif step["type"] == "function_call":
                        called = True
                        arguments = parse_arguments(step["args"]) if step["args"] else (step["whole"] or {})
                        extra = {"gemini_thoughts": thoughts} if thoughts else None
                        thoughts = []
                        yield ToolCallReady(ToolCall(id=step["id"] or f"call_{index}", name=step["name"],
                                                     arguments=arguments, extra=extra))
                elif kind == "interaction.completed":
                    interaction = event.get("interaction") or {}
                    status = interaction.get("status")
                    u = interaction.get("usage")
                    if isinstance(u, dict):
                        # Thought tokens are billed as output and counted apart from it.
                        usage = Usage(int(u.get("total_input_tokens") or 0),
                                      int(u.get("total_output_tokens") or 0) + int(u.get("total_thought_tokens") or 0))
        finally:
            await resp.aclose()
        if status is None:
            raise ProviderUnavailable("stream ended before the reply finished")
        if status in ("failed", "cancelled"):
            raise ProviderError(502, f"Gemini ended the reply as {status}.")
        if usage is not None:
            yield usage
        yield Finish("tool_calls" if called else ("length" if status == "incomplete" else "stop"))
