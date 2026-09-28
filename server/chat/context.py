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
# Said on a prefetch miss: without it nothing in the prompt names the open
# document, and a small model sends questions about it to web_search.
OPEN_DOC_LINE = (
    'The user has "{name}" open. Questions about names, terms or facts you don\'t recognise '
    "are probably about it: use search_document before web_search.")
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
    # The budget pass can stop mid-turn (an odd number of drops from
    # alternating user/assistant history), leaving an assistant reply at the
    # head with no question before it — broken input, and some providers
    # reject history that doesn't start on 'user'. Keep dropping oldest-first
    # until the head IS a user turn (or history is empty; pins and the new
    # message are never in `items`, so that's still a valid prompt).
    while items and items[0].stored.role != "user":
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
    if pre.passages:
        parts = [_passages_block(pre.note["docName"], pre.passages)]
    elif turn.doc is not None and turn.doc.state == "indexed":
        parts = [OPEN_DOC_LINE.format(name=turn.doc.name)]
    else:
        parts = []
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
        # An attachment can survive trimming and still have no row in `blobs`
        # (e.g. its bytes were deleted independently of the message). Mark it
        # the same way a deliberately trimmed attachment is marked, rather
        # than silently dropping it — the model should know something was
        # there, not just see fewer attachments than the text implies.
        missing = [a for a in i.keep if (i.stored.id, a["ordinal"]) not in blobs]
        markers = [_marker(a) for a in (*i.dropped, *missing)]
        if markers:
            content = (content + "\n\n" + "\n".join(markers)).strip()
        kept = tuple(blobs[(i.stored.id, a["ordinal"])] for a in i.keep
                     if (i.stored.id, a["ordinal"]) in blobs)
        history_messages.append(Message(i.stored.role, content, attachments=kept))
    notes = [pre.note] if pre.note else []
    if n_messages or n_attachments:
        notes.append({"kind": "trimmed", "messages": n_messages, "attachments": n_attachments})
        logger.debug("history trimmed: %d messages, %d attachments (window %s)",
                     n_messages, n_attachments, turn.window)
    return BuiltContext([*pins, *history_messages, volatile, current], notes, bool(pre.passages))
