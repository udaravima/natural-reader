# Pinned document context ("pins") — design

**Date:** 2026-08-02
**Status:** Approved (brainstorming) — pending spec review
**Area:** Chat (frontend `useChatEngine` + `ChatView`, backend chat-sessions router + a DB migration)

## Problem

When the user clicks **Ask AI** on a text selection (or **Ask page**), the excerpt
is attached to exactly one message and then discarded: [`ChatView.handleSend`](../../../src/components/ChatView.jsx)
calls `clearPendingDocContext()` after the send, and the excerpt is never stored
in the message history. So follow-up questions in the same conversation lose the
context — the model reports "no content attached" — and the user must re-select
and re-attach every time.

(Separately, a recently-fixed ordering bug had the excerpt buried at the front of
multi-turn histories; see `src/hooks/chatHistory.js`. This spec builds on that fix.)

## Goal

Let the user **keep chatting about attached context without re-attaching it**, via
persistent **pins**:

- Ask AI on a selection/page turns that excerpt into a **pin** that stays attached
  to the conversation and is sent on every turn until removed.
- **Multiple pins accumulate**, each shown as its own removable chip.
- Pins are **saved with the chat session** and restored on reload / when the chat
  is reopened, and **persisted instantly** when added or removed.
- **Retrieval stays available** (the "hybrid"): the existing autonomous
  `search_document` tool already fires on any turn once the open document is
  indexed ([`src/lib/chatTools/searchDocument.js`](../../../src/lib/chatTools/searchDocument.js)),
  so the model can pull additional passages beyond the pinned excerpts. No change
  needed there.

## Non-goals (YAGNI)

- Editing a pin's text, or reordering pins.
- Per-pin retrieval toggles or a cross-document pin library.
- A *new* manual retrieval control. (The current per-chip "use retrieval"
  synchronous-search toggle was bound to the one-shot `docContext` and is retired
  with it — see Retrieval below. Retrieval is instead served by the autonomous
  `search_document` tool, which needs no toggle.)

## Data model

A **pin** reuses the existing `docContext` shape plus a stable id:

```js
{
  id: string,        // `pin-${Date.now()}-${rand}` — React key + removal handle
  doc_id: string|null,
  fileName: string,
  page: number|null,
  kind: 'selection' | 'page',
  text: string,      // already truncated at CONTEXT_CHAR_CAP (8000) per pin
}
```

The conversation holds `pins: Pin[]`.

## Frontend

### State ownership — `useChatEngine`

Context ownership moves into `useChatEngine` (where session save/restore/clear
already live), replacing the single `pendingChatContext` in `App.jsx`.

- New state `pins` (array), plus actions:
  - `addPin(pin)` — append; **dedupe** by `(doc_id, kind, text)` so re-Asking the
    same selection/page is a no-op (never attached twice); enforce guardrails
    (below); on success, if a session is active, **persist instantly** (see
    Persistence).
  - `removePin(id)` — drop by id; persist instantly if a session is active.
  - `clearPins()` — empty (used by `newSession`).
- `switchToSession` sets `pins` from `record.pins || []`.
- `newSession` calls `clearPins()`.
- `saveActiveSession` includes `pins` in the full record (belt-and-suspenders with
  the instant PATCH).

### Ask AI handlers — `App.jsx`

`handleAskAboutSelection` / `handleAskAboutPage` build a pin object (same text
extraction + `CONTEXT_CHAR_CAP` truncation as today) and call `addPin(pin)` then
`setViewMode('chat')`, instead of `setPendingChatContext(...)`. The Ask-AI button
stays hidden while `inChat` (unchanged).

### Send path — `useChatEngine.sendMessage`

- Extract a pure helper `buildPinPreamble(pins)` → an array of `system` messages
  (one block per pin, same wording as today's excerpt block: `The user is reading
  "<file>". Relevant excerpt (<kind>[, page N]): """…"""`). Empty `pins` → `[]`.
- `contextPreamble = buildPinPreamble(pins)`, then
  `buildChatHistory({ priorMessages, contextPreamble, userMsg })` — so pins land
  **immediately before the current user turn** every send (the ordering fix),
  guaranteeing the model sees them while keeping the historical prefix
  KV-cacheable.
- The one-shot `docContext` argument to `sendMessage` is retired, and with it the
  inline `useRetrieval` synchronous-search block (lines ~499–556 today).
  (`MessageIn.docContext` stays in the schema for backward-compat with old saved
  messages; it is simply no longer written.)

### Retrieval (the "hybrid" half)

No new code. When the open document is indexed, the autonomous `search_document`
tool is advertised on every turn ([`chatTools/searchDocument.js`](../../../src/lib/chatTools/searchDocument.js)),
and the model calls it when it needs passages beyond the pinned excerpts; results
come back via the existing tool-call follow-up round-trip. Pins supply the explicit,
always-present context; the tool supplies on-demand breadth.

**Whole-document coverage is retrieval, not a pin.** There is deliberately no
"whole-document" pin kind — pinning an entire document's text verbatim would blow
the char budget for all but tiny files. "Cover the whole document" is served by
**indexing** it (`handleIndexDocument`), which is idempotent (indexing the same
`doc_id` twice is a no-op), so the whole document is effectively attached exactly
once and never duplicated. Pins remain scoped to specific selections/pages.

### UX — `ChatView`

- The single `DocContextChip` becomes a **row of pin chips** (`pins.map`), each with
  an X calling `removePin(id)`.
- `canSend` becomes true when `draft` / attachments / **`pins.length > 0`** — so the
  user can send with pins and no typed text.
- Near the budget, show a subtle "context full" hint on the pin row.

### Token guardrails

Pins re-send every turn, so bound them:

- `MAX_PINS = 6`.
- `PINNED_CONTEXT_CHAR_BUDGET = 12000` total across all pins (per-pin still capped
  at the existing `CONTEXT_CHAR_CAP = 8000`).
- `addPin` that would exceed either cap is rejected with a toast ("Pinned context is
  full — remove a pin first"); nothing is silently truncated or dropped.

## Backend (`server/`)

Sessions are normalized Postgres rows, so persistence needs a schema + model change.

### Migration — `server/sql/004_chat_pins.sql`

```sql
ALTER TABLE chat_sessions ADD COLUMN pins JSONB NOT NULL DEFAULT '[]'::jsonb;
```

Existing rows default to `[]`; no data migration needed.

### Models — `server/routers/chat_sessions.py`

- `SessionIn` gains `pins: list[dict[str, Any]] = Field(default_factory=list)`.
- `upsert_session` writes `pins` on the `chat_sessions` row (INSERT + `ON CONFLICT
  DO UPDATE SET pins = EXCLUDED.pins`), storing `Jsonb(payload.pins)`.
- The session **load** query (get-by-id) selects `pins` and includes it in the
  response object (default `[]` when null).
- Extend the existing `PATCH /v1/chat/sessions/{id}` (currently title-only) to also
  accept an optional `pins` field, for the instant save-on-pin. Title-only PATCHes
  keep working (both fields optional; update only what's provided).

### Frontend persistence wiring — `sessionStore` + `useChatEngine`

- `sessionStore` gains a light `updateSessionPins(id, pins)` → `PATCH {pins}`
  (mirrors the existing `renameSession` PATCH; degrades to falsy/offline the same
  way; IDB/local sessions update in place).
- `addPin` / `removePin` call it when a session is active. When **no** session
  exists yet (no message sent), pins live in memory and are written by the first
  full `saveActiveSession` on send (which creates the session).

## Testing

- **Pure unit (Vitest), extends `chatHistory.test.js` / new `pins.test.js`:**
  - `buildPinPreamble`: N pins → N system blocks in order; correct wording incl.
    page label; empty → `[]`.
  - Pin reducers: `addPin` dedupe by `(doc_id, kind, text)`; `removePin` by id;
    `MAX_PINS` and `PINNED_CONTEXT_CHAR_BUDGET` rejection paths.
  - `buildChatHistory` with a multi-pin preamble on a multi-turn history → all pin
    blocks sit immediately before the final user message (regression guard).
- **Backend round-trip** (if the server test harness supports a live/ephemeral DB):
  `PUT` a session with pins then `GET` → pins preserved; `PATCH {pins}` updates
  them without touching messages.

## Rollout / compatibility

- Old saved sessions load with `pins: []` — no behavior change until the user pins
  something.
- The retired one-shot path is fully replaced by pins; there is no user-visible
  regression (Ask AI still opens chat with the excerpt attached — it now simply
  persists).

## Files touched (estimate)

- `src/hooks/useChatEngine.js` — pins state/actions, send-path, save/restore.
- `src/hooks/chatHistory.js` (+ tests) — add `buildPinPreamble`.
- `src/hooks/pins.js` (+ tests) — pure pin reducers + guardrail constants (new).
- `src/components/ChatView.jsx` — pin chip row, `canSend`.
- `src/App.jsx` — Ask AI handlers call `addPin`; drop `pendingChatContext`.
- `src/lib/sessionStore.js` — `updateSessionPins`.
- `server/sql/004_chat_pins.sql` — migration (new).
- `server/routers/chat_sessions.py` — `SessionIn.pins`, upsert, load, PATCH.
