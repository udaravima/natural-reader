"""Stage 0 (spec §5.2): build the model input with the cheap, bounded work
first, so a document question can be answered in ONE model call.

Prompt order is fixed for prefix-cache reuse: system rules + pins -> history
-> volatile context (passages, time) -> the new message. Counterintuitive: the
time line belongs at the END; at the top it would change the prefix every turn
and defeat the provider's reuse for the whole conversation. The system rules
(server/chat/prompt.py; ruling R9 revised in v2.3) are stable within a turn.
They name the open document, so opening another document mid-chat changes
the prefix (one full re-read of the history by the provider), once.
Document text only ever appears inside a delimited block (prompt.fence).

The shape is also what strict chat templates accept (final review I3: vLLM's
Gemma/Mistral templates raise on any system message but the first, and on two
same-role messages in a row): ALL pins are one leading system message; the
volatile block is not a message of its own but the head of the new user
message's content; and consecutive same-role history messages are merged, so
roles strictly alternate. Ollama accepts the same shape, so there is one shape
everywhere. Only the model input changes: the stored user message is what the
user typed.
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
from .prompt import PASSAGES_TAG, PIN_TAG, display_name, fence, system_rules
from .store import StoredMessage

logger = logging.getLogger(__name__)

PASSAGE_TEXT_CAP = 1500   # characters per prefetched passage
_MARKER = "[{kind} {name} from earlier; no longer attached]"


def _pin_block(p: dict[str, Any]) -> str:
    """One pin: an excerpt the user chose, fenced as document text (the rules
    say what the tag means). `where` reads "page 4" or "selection, page 9"."""
    # Pins are client-sent dicts: every field is untrusted (review I4).
    kind = p.get("kind") if p.get("kind") in ("page", "selection") else ("page" if p.get("kind") is None else "excerpt")
    page = p.get("page") if isinstance(p.get("page"), int) and not isinstance(p.get("page"), bool) else None
    if page is None:
        where = "excerpt" if kind == "page" and p.get("page") is not None else kind
    elif kind == "page":
        where = f"page {page}"
    else:
        where = f"{kind}, page {page}"
    source = display_name(p.get("fileName") or "a document")
    return ("The user pinned this excerpt as primary context for their question:\n"
            f'<{PIN_TAG} source="{source}" where="{where}">\n{fence(p.get("text") or "")}\n</{PIN_TAG}>')


def pin_text(pins: list[dict[str, Any]]) -> str:
    """All pins' blocks, separated by a blank line ('' for none). They follow
    the rules in the ONE leading system message: strict chat templates (vLLM's
    Gemma/Mistral) accept a single leading system message only (final review I3)."""
    return "\n\n".join(_pin_block(p) for p in pins)


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
    """The prefetched passages as data: fenced, labelled with their pages, and
    no instructions (the rules explain the tag)."""
    source = display_name(name)
    lines = [f'<{PASSAGES_TAG} source="{source}">']
    for i, p in enumerate(passages, start=1):
        page = f" (page {p['page']})" if p["page"] is not None else ""
        lines += [f"[{i}]{page}", fence(p["text"]), ""]
    return "\n".join(lines).rstrip() + f"\n</{PASSAGES_TAG}>"


def _merge_same_role(messages: list[Message]) -> list[Message]:
    """Join consecutive same-role messages (content with a blank line between,
    attachments in order), so user/assistant strictly alternate. History can
    hold two user turns in a row: an empty or failed reply isn't sent back."""
    out: list[Message] = []
    for m in messages:
        if out and out[-1].role == m.role:
            prev = out[-1]
            out[-1] = Message(prev.role, "\n\n".join(c for c in (prev.content, m.content) if c),
                              attachments=prev.attachments + m.attachments)
        else:
            out.append(m)
    return out


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
    tools: tuple[Any, ...] = ()   # offered on the turn's first step: the rules describe exactly these
    tool_ctx: Any = None


@dataclass
class BuiltContext:
    messages: list[Message]
    notes: list[dict[str, Any]]
    prefetch_hit: bool


async def build_context(turn: TurnInput, cfg: ChatConfig) -> BuiltContext:
    pre = await prefetch(turn.doc, turn.text, cfg)
    parts = [_passages_block(pre.note["docName"], pre.passages)] if pre.passages else []
    parts.append(time_line(turn.now, turn.timezone))
    volatile = "\n\n".join(parts)
    rules = system_rules(turn.doc, turn.tools, turn.tool_ctx, has_pins=bool(turn.pins))
    system = Message("system", "\n\n".join(c for c in (rules, pin_text(turn.pins)) if c))
    fixed_chars = len(system.content) + len(volatile) + 2 + len(turn.text)
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
    history_messages = _merge_same_role(history_messages)
    # The new message: the volatile block first, then what the user typed. An
    # unanswered user turn at the end of history (its reply failed) joins it
    # between the two, so the prompt still ends on exactly one user message
    # that leads with the volatile block.
    current = Message("user", turn.text, attachments=turn.attachments)
    if history_messages and history_messages[-1].role == "user":
        current = _merge_same_role([history_messages.pop(), current])[0]
    current = Message("user", "\n\n".join(c for c in (volatile, current.content) if c),
                      attachments=current.attachments)
    notes = [pre.note] if pre.note else []
    if n_messages or n_attachments:
        notes.append({"kind": "trimmed", "messages": n_messages, "attachments": n_attachments})
        logger.debug("history trimmed: %d messages, %d attachments (window %s)",
                     n_messages, n_attachments, turn.window)
    return BuiltContext([system, *history_messages, current], notes, bool(pre.passages))
