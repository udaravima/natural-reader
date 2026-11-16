"""The helper model (docs/superpowers/specs/2026-11-15-helper-model-design.md §4): one small model the admin names in
Models, for short writing jobs that have no conversation of their own (phase 1: a document's tags and description).

This module is its only caller. `ask` makes one call with the job's fixed brief, no tools, a low temperature and a
capped reply, and returns the reply text or raises HelperError with a kind:

- "off": no helper model is set (Admin -> Models, else HELPER_MODEL); nothing is called.
- "over_budget": the person the call is for has used today's tokens (the chat's daily budget); nothing is called.
- "timeout": the call took longer than HELPER_TIMEOUT_S.
- "provider_error": the provider refused or failed.

A call is charged to the person who caused it (the uploader, the person who pressed Generate) like a chat step:
the provider's own count when it sent one, else characters / 4; a call that failed before producing anything is not
charged. The usage row's kind is "helper", so the admin's usage page tells it from chat and reader calls.

Logs carry lengths and error kinds only, never a prompt or a reply: both are document text.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from dataclasses import dataclass
from typing import Mapping

from ..db import get_pool
from ..llm.router import Received, UnknownModel, get_router
from ..llm.types import CallSettings, Message, Usage
from . import inference_budget, model_router, model_settings
from .inference_limits import get_limits

logger = logging.getLogger(__name__)

TIMEOUT_S = 120.0          # seconds one call may take (HELPER_TIMEOUT_S, 5-900)
INPUT_CHARS = 6000         # characters of document text per call (HELPER_INPUT_CHARS, 500-50000)
TEMPERATURE = 0.2          # short factual writing: little variety wanted
CHARS_PER_OUTPUT_TOKEN = 2  # the reply cap, as the reader's: JSON tokenises badly


@dataclass(frozen=True)
class HelperConfig:
    model: str              # canonical "provider:name" when the router can place it; "" = off
    timeout_s: float        # seconds
    input_chars: int        # characters of document text per call
    num_ctx: int | None     # tokens the model is loaded with; None = send none (cloud, openai-kind, unknown)
    local: bool             # runs on hardware the deployer controls (model_settings.is_local)

    @property
    def on(self) -> bool:
        return bool(self.model)


class HelperError(Exception):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def _number(env: Mapping[str, str], key: str, cast, lo, hi, default):
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = cast(raw)
    except ValueError:
        value = None
    if value is None or not lo <= value <= hi:
        logger.warning("%s=%r is outside %s-%s: %s applies", key, raw, lo, hi, default)
        return default
    return value


async def load_config(conn, env: Mapping[str, str] = os.environ) -> HelperConfig:
    """The helper as it applies now: the model from the Models settings (the admin's, else HELPER_MODEL), named by
    its canonical id, with the context size it is loaded with (the same rule as the reader's, so a helper that is
    also someone's chat model isn't reloaded at another size) and whether it is local."""
    timeout_s = float(_number(env, "HELPER_TIMEOUT_S", float, 5, 900, TIMEOUT_S))
    input_chars = _number(env, "HELPER_INPUT_CHARS", int, 500, 50_000, INPUT_CHARS)
    ms = await model_settings.load_models_settings(conn, env)
    if not ms.helper:
        return HelperConfig("", timeout_s, input_chars, None, False)
    try:
        provider, name = get_router().resolve(ms.helper)
    except UnknownModel:
        # Kept by name: its calls fail as provider_error. Counted as cloud, the careful answer for the title rule.
        return HelperConfig(ms.helper, timeout_s, input_chars, None, False)
    key = f"{provider.config.name}:{name}"
    local = model_settings.is_local(provider.config, name, ms.provider_locality)
    num_ctx = None
    if model_settings.sizes_context(provider.config, name):
        num_ctx = model_settings.num_ctx_for(ms, key, local=True, user_value=None, limits=get_limits())
    return HelperConfig(key, timeout_s, input_chars, num_ctx, local)


async def chat_model_is_local(conn, model_id: str | None) -> bool:
    """Whether a chat's model runs on hardware the deployer controls (the same judgement as the helper's own `local`).
    A model the router can't place, or none, counts as local: the careful answer for §4.3's rule that a cloud helper
    never reads a local chat."""
    if not model_id:
        return True
    try:
        provider, name = get_router().resolve(model_id)
    except UnknownModel:
        return True
    ms = await model_settings.load_models_settings(conn)
    return model_settings.is_local(provider.config, name, ms.provider_locality)


def _deployment_budget() -> int | None:
    return model_router.get_config().daily_token_budget


async def over_budget(conn, user_id: str) -> bool:
    """Whether `user_id` has used today's tokens: a route refuses a Generate with 429 before queueing it."""
    return await inference_budget.over_budget(conn, user_id, _deployment_budget())


async def _over_budget(user_id: str) -> bool:
    async with get_pool().connection() as conn:
        return await over_budget(conn, user_id)


async def _charge(user_id: str, model: str, messages: list[Message], received: Received) -> None:
    usage = received.usage
    if usage is None:
        usage = Usage(math.ceil(sum(len(m.content) for m in messages) / 4), math.ceil(len(received.text) / 4),
                      estimated=True)
    try:
        async with get_pool().connection() as conn:
            await inference_budget.record_usage(conn, user_id, usage.prompt_tokens, usage.completion_tokens,
                                                model=model, kind="helper", estimated=usage.estimated)
    except Exception:  # noqa: BLE001 — accounting fails open, as the chat's does
        logger.warning("Helper usage accounting failed (fail-open)", exc_info=True)


async def ask(cfg: HelperConfig, system: str, user: str, *, max_output_chars: int, user_id: str | None) -> str:
    """One helper call. `user_id` is charged for it and held to their daily budget; None (a job nobody asked for)
    is neither."""
    if not cfg.on:
        raise HelperError("off")
    if user_id and await _over_budget(user_id):
        raise HelperError("over_budget")
    messages = [Message(role="system", content=system), Message(role="user", content=user)]
    settings = CallSettings(think="off", num_ctx=cfg.num_ctx, temperature=TEMPERATURE,
                            num_predict=math.ceil(max_output_chars / CHARS_PER_OUTPUT_TOKEN))
    received = Received()
    finished = False
    try:
        reply = await asyncio.wait_for(get_router().complete(cfg.model, messages, settings, received=received),
                                       cfg.timeout_s)
        finished = True
        return reply
    except TimeoutError:
        logger.warning("helper call timed out after %.0f s (%d characters in)", cfg.timeout_s, len(user))
        raise HelperError("timeout") from None
    except Exception as e:  # noqa: BLE001 — type and status only: a provider's text could echo the prompt
        logger.warning("helper call failed (%s, status %s)", type(e).__name__, getattr(e, "status", None))
        raise HelperError("provider_error") from None
    finally:
        if user_id and (finished or received.text):
            await asyncio.shield(_charge(user_id, cfg.model, messages, received))
