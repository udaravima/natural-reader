"""/v1/inference/models — every configured provider's models, tagged
provider:name, with capabilities and the caller's budget (C1 spec §7.5). Chat
turns are POST /v1/chat/sessions/{id}/turns."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from ..auth import deps
from ..http_errors import refusal
from ..llm.router import get_router
from ..services import inference_budget, inference_limits, model_router

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/inference", tags=["inference"])


def _strip_latest(name: str) -> str:
    return name[:-len(":latest")] if name.endswith(":latest") else name


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
    cfg = model_router.get_config()
    embed_model = _strip_latest(cfg.embed_model)
    models = [m for m in models if not (m.kind == "ollama" and _strip_latest(m.name) == embed_model)]
    # v2.4 Task F: the limits the server clamps an Ollama model's context size
    # and keep-alive to, so the Settings page offers only what it will honour.
    limits = inference_limits.limits_json(inference_limits.get_limits())
    out: dict = {"models": [{**m.to_json(), "limits": limits} if m.kind == "ollama" else m.to_json()
                            for m in models]}
    try:
        out["budget"] = await inference_budget.budget_state(
            conn, principal.user_id, cfg.daily_token_budget)
    except Exception:
        # Fail open — the model list must survive a Postgres outage.
        logger.warning("Budget lookup failed (fail-open)", exc_info=True)
    return out
