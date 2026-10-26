"""/v1/inference/models — every configured provider's models, tagged
provider:name, with capabilities and the caller's budget (C1 spec §7.5). Chat
turns are POST /v1/chat/sessions/{id}/turns."""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends
from ..auth import deps
from ..http_errors import refusal
from ..llm.router import get_router
from ..services import inference_budget, inference_limits, model_router, model_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/inference", tags=["inference"])


@router.get("/models")
async def list_models(
    principal: deps.Principal = Depends(deps.require_capability("chat")),
    conn=Depends(deps.get_conn, scope="function"),
):
    """Every configured provider's models, tagged `provider:name`, with
    capabilities (spec §7.5). One failing provider is omitted; all failing is 502."""
    llm = get_router()
    if not llm.has_providers():
        raise refusal(503, "no_providers", "No model provider is configured.")
    models, failed = await llm.list_models()
    if failed and not models and len(failed) == len(llm.providers):
        raise refusal(502, "providers_unreachable", "Can't reach any model provider.")
    cfg = model_router.get_config()
    ms = await _models_settings(conn)
    # The env's list, then the embedding model out, then the console's selection (it can only narrow).
    models = model_settings.offered(model_router.offered_chat_models(models, cfg.embed_model), ms)
    # v2.4 Task F: the limits the server clamps an Ollama model's context size
    # and keep-alive to, so the Settings page offers only what it will honour.
    limits = inference_limits.get_limits()
    limits_out = inference_limits.limits_json(limits)
    out: dict = {"models": [_entry(m, llm, ms, limits, limits_out) for m in models]}
    try:
        out["budget"] = await inference_budget.budget_state(
            conn, principal.user_id, cfg.daily_token_budget)
    except Exception:
        # Fail open — the model list must survive a Postgres outage.
        logger.warning("Budget lookup failed (fail-open)", exc_info=True)
    return out


async def _models_settings(conn) -> model_settings.ModelsSettings:
    """The admin's model settings on this request's connection. Fail-open: if the row can't be read, the
    environment's values apply."""
    try:
        return await model_settings.load_models_settings(conn, os.environ)
    except Exception:  # noqa: BLE001 — the model list must survive a Postgres outage
        logger.warning("The model settings couldn't be read: using the environment's", exc_info=True)
        return model_settings.from_env()


def _entry(m, llm, ms: model_settings.ModelsSettings, limits: inference_limits.InferenceLimits,
           limits_out: dict) -> dict:
    """One model for the picker. An Ollama model carries the limits; one the server must size (any Ollama you host
    yourself, `sizes_context`) also says what context size it will be loaded with: `fixed` when the admin or the
    environment decided it (already within the limits), else `auto`, the default within the limits."""
    out = m.to_json()
    if m.kind == "ollama":
        out["limits"] = limits_out
    if model_settings.sizes_context(llm.providers[m.provider].config, m.name):
        fixed, _ = model_settings.deployment_num_ctx(ms, m.id, limits)
        auto, _ = inference_limits.clamp(model_settings.DEFAULT_LOCAL_NUM_CTX, None, limits)
        out["context_size"] = {"fixed": fixed, "auto": auto}
    return out
