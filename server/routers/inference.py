"""/v1/inference/* — the authenticated chat gateway to Ollama (sub-project E).

A *chat* gateway, never a general Ollama proxy: the only upstream calls are
GET /api/tags (model list) and POST /api/chat (generation). The request
envelope is validated down to the per-message fields the SPA actually sends;
anything else is a 422. /api/pull, /api/delete, /api/run & co. stay
unreachable through app auth, forever. A future multi-provider router changes
the target of the passthrough, not this contract.
"""
from __future__ import annotations

import json
import logging
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from ..auth import deps
from ..db import get_pool
from ..services import inference_budget, model_router

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/inference", tags=["inference"])

_client: httpx.AsyncClient | None = None


async def start_client(transport: httpx.AsyncBaseTransport | None = None) -> None:
    """Called from app startup. `transport` exists so tests inject a MockTransport."""
    global _client
    if _client is None:
        cfg = model_router.get_config()
        # Short connect timeout so a dead Ollama fails fast; the read timeout
        # spans whole streamed replies (slow models can think for minutes).
        _client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(connect=5.0, read=cfg.timeout_s, write=30.0, pool=5.0),
        )


async def stop_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _get_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("Inference client not started — call start_client() first")
    return _client


class ChatOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    num_ctx: int | None = None
    num_predict: int | None = None


class ToolCallFunction(BaseModel):
    # Tool schemas are model-facing passthrough JSON — permissive on purpose.
    model_config = ConfigDict(extra="allow")
    name: str
    arguments: str | dict | None = None


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="allow")
    function: ToolCallFunction


class ChatMessage(BaseModel):
    # `images` carries base64 (image AND audio attachments — Ollama's message
    # struct has a single binary field); the tool follow-up round carries
    # `tool_calls` on the assistant message. Both must survive validation or
    # the SPA's attachment and tool loops break.
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    images: list[str] | None = None
    tool_calls: list[ToolCall] | None = None


class ChatRequest(BaseModel):
    # Exactly the fields buildRequestFields (src/hooks/inference.js) emits,
    # plus the tool round's additions. extra="forbid" is what makes this a
    # chat gateway: unknown fields are a 422, not a silent passthrough.
    model_config = ConfigDict(extra="forbid")
    model: str
    messages: list[ChatMessage]
    stream: bool = True
    tools: list[dict] | None = None
    think: bool | str | None = None
    keep_alive: str | int | None = None
    options: ChatOptions | None = None


class _UsageTap:
    """Scans the NDJSON stream for the final chunk's token counts WITHOUT
    altering a single forwarded byte. Only a completed generation (done:true
    seen) counts — aborted streams never account (spec §8 retry semantics)."""

    def __init__(self) -> None:
        self._buf = b""
        self.usage: dict | None = None

    def feed(self, chunk: bytes) -> None:
        if self.usage is not None:
            return
        self._buf += chunk
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if payload.get("done"):
                self.usage = {
                    "prompt_eval_count": payload.get("prompt_eval_count"),
                    "eval_count": payload.get("eval_count"),
                }


@router.get("/models")
async def list_models(
    principal: deps.Principal = Depends(deps.require_capability("chat")),
    conn=Depends(deps.get_conn),
):
    cfg = model_router.get_config()
    try:
        resp = await _get_client().get(f"{cfg.ollama_url}/api/tags")
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Ollama unreachable")
    if resp.status_code != 200:
        return JSONResponse(status_code=resp.status_code, content={"detail": "Ollama error"})
    names = [
        m["name"] for m in resp.json().get("models", [])
        if isinstance(m, dict) and m.get("name")
    ]
    if cfg.allowed_models is not None:
        names = [n for n in names if n in cfg.allowed_models]
    out = {"models": names}
    try:
        out["budget"] = await inference_budget.budget_state(
            conn, principal.user_id, cfg.daily_token_budget
        )
    except Exception:
        # Fail open — the model list must survive a Postgres outage.
        logger.warning("Budget lookup failed (fail-open)", exc_info=True)
    return out


@router.post("/chat")
async def chat(
    body: ChatRequest,
    principal: deps.Principal = Depends(deps.require_capability("chat")),
    conn=Depends(deps.get_conn),
):
    cfg = model_router.get_config()
    if not model_router.is_model_allowed(cfg, body.model):
        raise HTTPException(
            status_code=422,
            detail=f"Model '{body.model}' is not allowed on this deployment",
        )
    # Pre-check (advisory): a 429 before any upstream call saves the tokens a
    # doomed request would spend. Accounting of actuals happens after the
    # stream completes, from the final chunk's real counts.
    try:
        if await inference_budget.over_budget(conn, principal.user_id, cfg.daily_token_budget):
            state = await inference_budget.budget_state(
                conn, principal.user_id, cfg.daily_token_budget
            )
            raise HTTPException(status_code=429, detail=state)
    except HTTPException:
        raise
    except Exception:
        # Fail open — chat must survive a Postgres outage (like session
        # persistence: degrade, don't die).
        logger.warning("Budget pre-check failed (fail-open)", exc_info=True)

    client = _get_client()
    req = client.build_request(
        "POST",
        f"{cfg.ollama_url}/api/chat",
        json=body.model_dump(exclude_none=True),
    )
    try:
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Ollama unreachable")

    if upstream.status_code != 200:
        # Forward status AND body: the SPA's think-level / tools fallback
        # chain branches on res.status, and its toasts read the error JSON.
        raw = (await upstream.aread()).decode("utf-8", errors="replace")
        await upstream.aclose()
        try:
            content = json.loads(raw)
        except ValueError:
            content = {"detail": raw[:2000]}
        return JSONResponse(status_code=upstream.status_code, content=content)

    tap = _UsageTap()

    async def _passthrough():
        # STREAMING TRAP: StreamingResponse consumes this generator AFTER the
        # handler returns — an `async with client.stream(...)` here would
        # close the upstream before the first byte is forwarded. Close it only
        # when the last byte has gone out (or the client hung up).
        try:
            async for chunk in upstream.aiter_raw():
                tap.feed(chunk)
                yield chunk
        finally:
            await upstream.aclose()
            if tap.usage is not None:
                # The handler's get_conn context is long closed by now — open
                # a fresh pooled connection. A DB failure here only loses
                # accounting, never the chat.
                try:
                    pool = get_pool()
                    async with pool.connection() as conn2:
                        await inference_budget.record_usage(
                            conn2,
                            principal.user_id,
                            tap.usage["prompt_eval_count"] or 0,
                            tap.usage["eval_count"] or 0,
                        )
                except Exception:
                    logger.warning(
                        "Usage accounting failed (fail-open)", exc_info=True
                    )

    return StreamingResponse(_passthrough(), media_type="application/x-ndjson")
