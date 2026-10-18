"""Stage 0 (spec §5.2): build the model input with the cheap, bounded work
first, so a document question can be answered in ONE model call.

Prompt order is fixed for prefix-cache reuse: system rules + project map + pins -> history
-> volatile context (passages) -> the new message. The date is in the rules
only to the day (v2.4: the minute clock that used to lead the user's message
was mistaken for what "that" referred to), so the prefix changes once a day,
not every turn. The system rules
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
from ..llm.types import Attachment, Message, ToolCall
from ..services.doc_search import ReadableDoc, search_scope
from ..services.embeddings import embed_query
from . import store
from .config import ChatConfig
from .project_scope import ScopeDoc
from .prompt import PASSAGES_TAG, PIN_TAG, display_name, fence, system_rules
from .store import StoredMessage
from .tools import result_text
from .tools.search_documents import PREFETCH_CALL_ID, passages_result

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


def today_line(now: datetime, tz_name: str | None) -> str:
    """Today's date in the browser's timezone (anything unusable is UTC), for
    the system rules. v2.4: to the day, not the minute — it used to lead the
    user's message as "Current time: …", where a follow-up like "say that
    more simply" was read as being about the clock (eval, 2026-10-05); in the
    rules it changes once a day, so the prefix cache survives the day."""
    tz, label = timezone.utc, "UTC"
    if tz_name:
        try:
            tz, label = ZoneInfo(tz_name), tz_name
        except Exception:  # noqa: BLE001 — ZoneInfo raises several types for bad names
            tz, label = timezone.utc, "UTC"
    day = now.astimezone(tz)
    return f"Today is {day:%A}, {day.day} {day:%B %Y} ({label})."


@dataclass
class Prefetch:
    passages: list[dict[str, Any]] = field(default_factory=list)
    top_score: float | None = None
    note: dict[str, Any] | None = None


def _cap(text: str) -> str:
    return text if len(text) <= PASSAGE_TEXT_CAP else text[:PASSAGE_TEXT_CAP] + " [truncated]"


async def prefetch(scope: list[ScopeDoc], question: str, cfg: ChatConfig) -> Prefetch:
    """Search the scope's documents before the model runs: the open document,
    or a project chat's searchable ones as one balanced ranking (C2.1 spec
    5.5). Meaning only, as ever. Cost when it doesn't help: one embedding
    call. Every decision is logged at DEBUG so a deployer can tune
    CHAT_PREFETCH_MIN_SCORE on their own documents.

    Each passage carries its document's `ref` and `doc_id`. The note keeps
    its one-document shape for a single document; for several it names each
    document that gave passages (`documents`), with no top-level docId."""
    docs = [d for d in scope if d.doc.state == "indexed"]
    if not docs or cfg.prefetch == "off" or cfg.prefetch_min_score >= 1.0 or not question.strip():
        return Prefetch()
    by_doc = {d.doc.doc_id: d for d in docs}
    project = len(docs) > 1
    try:
        qvec = await embed_query(question)
        async with get_pool().connection() as conn:
            rows = await search_scope(conn, list(by_doc), qvec, cfg.prefetch_k,
                                      per_doc=cfg.search_per_doc if project else None)
    except Exception as e:  # noqa: BLE001 — prefetch is an optimization; the tool is still offered
        logger.warning("Prefetch failed for %d document(s): %r", len(docs), e)
        return Prefetch()
    top = max((float(r["score"]) for r in rows), default=None)
    kept = [r for r in rows if float(r["score"]) >= cfg.prefetch_min_score]
    logger.debug("prefetch docs=%d top=%s kept=%d/%d threshold=%s",
                 len(docs), top, len(kept), len(rows), cfg.prefetch_min_score)
    if not kept:
        return Prefetch(top_score=top)
    passages = [{"id": r.get("id"), "ref": by_doc[r["doc_id"]].ref, "doc_id": r["doc_id"],
                 "page": r.get("page"), "score": float(r["score"]), "text": _cap(r["text"] or "")}
                for r in kept]
    if not project:
        doc = docs[0].doc
        note = {"kind": "prefetch", "docId": doc.doc_id, "docName": doc.name, "count": len(kept),
                "topScore": round(top, 4),
                "pages": sorted({r["page"] for r in kept if r.get("page") is not None})}
        return Prefetch(passages, top, note)
    documents = []
    for d in docs:   # in the project's order
        mine = [r for r in kept if r["doc_id"] == d.doc.doc_id]
        if mine:
            documents.append({"docId": d.doc.doc_id, "docName": d.name, "count": len(mine),
                              "pages": sorted({r["page"] for r in mine if r.get("page") is not None})})
    note = {"kind": "prefetch", "documents": documents, "count": len(kept), "topScore": round(top, 4)}
    return Prefetch(passages, top, note)


def _passages_block(passages: list[dict[str, Any]], scope: list[ScopeDoc]) -> str:
    """The prefetched passages as data: fenced, labelled with their pages, and
    no instructions (the rules explain the tag). From one document the label
    is its page and the tag names the document; from several, each passage
    names its own document."""
    names = {d.ref: d.name for d in scope}
    several = len({p["ref"] for p in passages}) > 1
    source = "project documents" if several else display_name(names.get(passages[0]["ref"], "document"))
    lines = [f'<{PASSAGES_TAG} source="{source}">']
    for i, p in enumerate(passages, start=1):
        where = [display_name(names.get(p["ref"], "document"))] if several else []
        if p["page"] is not None:
            where.append(f"page {p['page']}")
        lines += [f"[{i}]" + (f" ({', '.join(where)})" if where else ""), fence(p["text"]), ""]
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
    profile: str = ""             # the deployment's assistant profile: leads the system message
    scope: tuple[ScopeDoc, ...] = ()   # the documents the tools read (tools.scope.scope_docs); prefetch searches them
    project_map: str = ""              # a project chat's map of its documents (chat/project_map.py): after the rules


@dataclass
class BuiltContext:
    messages: list[Message]
    notes: list[dict[str, Any]]
    prefetch_hit: bool
    shown_chunk_ids: tuple[int, ...] = ()   # the prefetched chunks: search_documents won't repeat them
    shown_texts: tuple[str, ...] = ()       # their text: web_search won't send it out
    evidence: tuple[tuple[str, int | None, str], ...] = ()   # (doc_id, page, text): for the source chips (v2.4 Task D)


async def build_context(turn: TurnInput, cfg: ChatConfig) -> BuiltContext:
    # "auto" (the default): only for a model with no document tools; one that
    # has them searches for itself (gemma4:e4b, handed passages, answered from
    # them instead: 5/9 against 9/9 without, 2026-10-05).
    has_doc_tools = any(t.name == "search_documents" for t in turn.tools)
    runs = cfg.prefetch == "on" or (cfg.prefetch == "auto" and not has_doc_tools)
    pre = await prefetch(list(turn.scope), turn.text, cfg) if runs else Prefetch()
    # v2.4 Task B: with the search tool offered, the passages arrive as that
    # tool's result (a call the app made for the model), not inside the
    # user's message, where a PDF's planted instruction would speak with the
    # user's voice. Without tools (strict templates reject tool messages
    # then) they stay a fenced block in the user's message.
    as_tool = bool(pre.passages) and any(t.name == "search_documents" for t in turn.tools)
    exchange: list[Message] = []
    volatile = ""
    if as_tool:
        args = {"query": turn.text, "k": cfg.prefetch_k}
        result = passages_result(turn.text, list(turn.scope),
                                 [{"ref": p["ref"], "page": p["page"], "text": p["text"]} for p in pre.passages])
        exchange = [Message("assistant", "", tool_calls=(ToolCall(PREFETCH_CALL_ID, "search_documents", args),)),
                    Message("tool", result_text(result), tool_call_id=PREFETCH_CALL_ID, name="search_documents")]
    elif pre.passages:
        volatile = _passages_block(pre.passages, list(turn.scope))
    rules = system_rules(turn.doc, turn.tools, has_pins=bool(turn.pins),
                         prefetch=cfg.prefetch == "on" and cfg.prefetch_min_score < 1.0,
                         today=today_line(turn.now, turn.timezone), profile=turn.profile,
                         has_map=bool(turn.project_map))
    system = Message("system", "\n\n".join(c for c in (rules, turn.project_map, pin_text(turn.pins)) if c))
    fixed_chars = len(system.content) + len(volatile) + 2 + len(turn.text) + sum(len(m.content) for m in exchange)
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
    return BuiltContext([system, *history_messages, current, *exchange], notes, bool(pre.passages),
                        tuple(p["id"] for p in pre.passages if p.get("id") is not None),
                        tuple(p["text"] for p in pre.passages),
                        tuple((p["doc_id"], p["page"], p["text"]) for p in pre.passages))
