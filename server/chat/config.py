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
    max_tool_rounds: int = 3               # tool rounds before the final tools-off step (RAG spec §9)
    tool_result_budget_chars: int = 24000  # tool results one answer may add to the prompt
    prefetch_min_score: float = 0.6        # cosine similarity; >= 1 disables prefetch. Measured
                                            # 2026-10-05 (nomic-embed-text, v2.3 prefixes; CHAT_WITH_PDF
                                            # §6.5): a short user guide's answering passages scored
                                            # 0.72-0.83 and unrelated messages ("thanks!") 0.47-0.60; on a
                                            # 600-passage book 0.66-0.78 against 0.51-0.65, so there some
                                            # small talk still passes. No value separates both.
    prefetch_k: int = 4                    # passages at most
    read_pages_max_chars: int = 12000      # read_document_pages: characters per call
    search_min_score: float = 0.45         # search_documents drops passages below this: plain
                                            # noise only. Every message measured 2026-10-05 had a
                                            # passage above 0.47, so it rarely drops anything.
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
        tool_result_budget_chars=num("CHAT_TOOL_RESULT_BUDGET_CHARS", d.tool_result_budget_chars,
                                     int, 1000, 1_000_000),
        prefetch_min_score=num("CHAT_PREFETCH_MIN_SCORE", d.prefetch_min_score, float, 0.0, 1.0),
        prefetch_k=num("CHAT_PREFETCH_K", d.prefetch_k, int, 1, 20),
        read_pages_max_chars=num("CHAT_READ_PAGES_MAX_CHARS", d.read_pages_max_chars, int, 1000, 200_000),
        search_min_score=num("CHAT_SEARCH_MIN_SCORE", d.search_min_score, float, 0.0, 1.0),
        reply_reserve_tokens=num("CHAT_REPLY_RESERVE_TOKENS", d.reply_reserve_tokens, int, 0, 1_000_000),
        attachment_token_estimate=num("CHAT_ATTACHMENT_TOKEN_ESTIMATE", d.attachment_token_estimate,
                                      int, 0, 100_000),
        max_request_mb=num("CHAT_MAX_REQUEST_MB", d.max_request_mb, int, 1, 1024),
    )


def get_chat_config() -> ChatConfig:
    return load_chat_config(os.environ)
