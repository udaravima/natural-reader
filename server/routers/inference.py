"""/v1/inference/models — every configured provider's models, tagged
provider:name, with capabilities and the caller's budget (C1 spec §7.5). Chat
turns are POST /v1/chat/sessions/{id}/turns."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from ..auth import deps
from ..http_errors import refusal
from ..llm.router import get_router
from ..services import inference_budget, model_router

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/inference", tags=["inference"])


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
