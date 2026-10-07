"""Deployment limits on two per-request Ollama settings (v2.4 Task F).

A user's Settings page sends a context size (`num_ctx`) and how long the model
stays loaded (`keep_alive`) with every message. On a shared Ollama both decide
everyone's memory:
- a context size allocates its KV cache up front (131k tokens on an 8B model
  is gigabytes);
- keep-alive "-1" holds the model in RAM until Ollama restarts, and "0"
  unloads it after every reply, so the next person waits for a reload.

So the deployment sets the ceiling, and a request above it is clamped (not
refused: a saved setting from before the limit must not break the chat). It
doesn't stop two users with different context sizes making Ollama reload the
model between them; Ollama's own scheduler settings are the deployer's tool
for that (OLLAMA_MAX_LOADED_MODELS, OLLAMA_KEEP_ALIVE; docs/CHAT_WITH_PDF.md).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Mapping

logger = logging.getLogger(__name__)

NUM_CTX_MAX_DEFAULT = 32768        # tokens: covers normal chats and the eval
KEEP_ALIVE_MAX_DEFAULT_S = 1800    # seconds: warm through a working session, then freed
_DURATION = re.compile(r"^(-?\d+(?:\.\d+)?)(ms|s|m|h)?$")
_UNIT_S = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, None: 1}


@dataclass(frozen=True)
class InferenceLimits:
    num_ctx_max: int | None        # tokens; None = no limit
    keep_alive_max_s: int | None   # seconds; None = no limit


def seconds(value) -> float | None:
    """An Ollama keep_alive (a duration like "30m", or seconds) in seconds;
    None if it can't be read. A negative value means "forever"."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = _DURATION.match(str(value).strip())
    return float(m.group(1)) * _UNIT_S[m.group(2)] if m else None


def load_inference_limits(env: Mapping[str, str]) -> InferenceLimits:
    num_ctx_max: int | None = NUM_CTX_MAX_DEFAULT
    raw = (env.get("INFERENCE_NUM_CTX_MAX") or "").strip()
    if raw:
        try:
            num_ctx_max = int(raw) or None            # 0 = no limit
        except ValueError:
            logger.warning("INFERENCE_NUM_CTX_MAX=%r is not a number; using %d", raw, NUM_CTX_MAX_DEFAULT)
    keep_alive_max: int | None = KEEP_ALIVE_MAX_DEFAULT_S
    raw = (env.get("INFERENCE_KEEP_ALIVE_MAX") or "").strip()
    if raw:
        s = seconds(raw)
        if s is None:
            logger.warning("INFERENCE_KEEP_ALIVE_MAX=%r is not a duration; using %ds", raw,
                           KEEP_ALIVE_MAX_DEFAULT_S)
        else:
            keep_alive_max = None if s < 0 else int(s)   # -1 = no limit
    return InferenceLimits(num_ctx_max, keep_alive_max)


def get_limits() -> InferenceLimits:
    return load_inference_limits(os.environ)


def clamp(num_ctx: int | None, keep_alive, limits: InferenceLimits):
    """(num_ctx, keep_alive) within the limits. keep_alive 0 or unreadable is
    dropped (Ollama's own default applies); above the limit, or "forever",
    becomes the limit in seconds."""
    if num_ctx is not None and limits.num_ctx_max is not None:
        num_ctx = min(num_ctx, limits.num_ctx_max)
    if keep_alive is not None:
        s = seconds(keep_alive)
        if s is None or s == 0:
            keep_alive = None
        elif limits.keep_alive_max_s is not None and (s < 0 or s > limits.keep_alive_max_s):
            keep_alive = limits.keep_alive_max_s
    return num_ctx, keep_alive


def limits_json(limits: InferenceLimits) -> dict:
    return {"numCtxMax": limits.num_ctx_max, "keepAliveMaxS": limits.keep_alive_max_s}
