# Pinned Document Context ("pins") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an Ask-AI document excerpt a persistent, removable "pin" that stays attached to a chat and is re-sent every turn, saved with the session, so follow-ups keep the context without re-attaching.

**Architecture:** A pin is the existing `docContext` shape plus an id. Pins live in `useChatEngine` state (session-scoped), are turned into `system` preamble messages positioned immediately before the current user turn (via the existing `buildChatHistory`), and are persisted to a new `chat_sessions.pins` JSONB column — instantly on add/remove via `PATCH`, and in the full session upsert. Whole-document coverage is served by the existing autonomous `search_document` retrieval tool, not by pins.

**Tech Stack:** React 19 + vanilla-ESM hooks, Vitest + jsdom + @testing-library/react; FastAPI + psycopg + numbered SQL migrations (Postgres/pgvector); Ollama `/api/chat`.

## Global Constraints

- **Commits require explicit, per-action user approval** (repo policy — global `~/.claude/CLAUDE.md`). Each task ends with a Commit step; the executor MUST stop and ask the user before running `git commit`. Do not chain commits.
- **Do not run system-wide commands** (no sudo / global installs). Backend runs project-local.
- Frontend: vanilla ESM, no new deps. Run unit tests with `npm run test:run`, lint with `npm run lint`, build with `npm run build`.
- Backend: start with `env -u XDG_DATA_HOME ./startup.sh up` (snap-terminal env fix — podman store + `podman-compose`). Migrations auto-apply on startup via `server/db.py` `_run_migrations()`.
- Token guardrails (exact): `MAX_PINS = 6`, `PINNED_CONTEXT_CHAR_BUDGET = 12000` (total across pins), per-pin cap stays `CONTEXT_CHAR_CAP = 8000` (already applied in `App.jsx` before a pin is built).
- Pin dedupe key: `(doc_id, kind, text)`. There is no "whole-document" pin kind.
- Spec: `docs/superpowers/specs/2026-08-02-chat-pinned-context-design.md`.

---

### Task 1: Pure pin reducers + guardrail constants

**Files:**
- Create: `src/hooks/pins.js`
- Test: `src/hooks/pins.test.js`

**Interfaces:**
- Produces:
  - `MAX_PINS: number` (6), `PINNED_CONTEXT_CHAR_BUDGET: number` (12000)
  - `makePin({ doc_id=null, fileName='', page=null, kind, text }) -> Pin` where `Pin = { id, doc_id, fileName, page, kind, text }`
  - `totalPinnedChars(pins: Pin[]) -> number`
  - `addPin(pins: Pin[], pin: Pin) -> { pins: Pin[], added: boolean, reason?: 'duplicate'|'max-pins'|'budget' }`
  - `removePin(pins: Pin[], id: string) -> Pin[]`

- [ ] **Step 1: Write the failing test**

```js
// src/hooks/pins.test.js
import { describe, it, expect } from 'vitest';
import { makePin, addPin, removePin, totalPinnedChars, MAX_PINS, PINNED_CONTEXT_CHAR_BUDGET } from './pins';

const pin = (over = {}) => ({ id: 'x', doc_id: 'd1', fileName: 'f.md', page: 1, kind: 'selection', text: 'hello', ...over });

describe('makePin', () => {
    it('builds a pin with a unique id and the given fields', () => {
        const a = makePin({ doc_id: 'd1', fileName: 'f.md', page: 2, kind: 'page', text: 'abc' });
        const b = makePin({ doc_id: 'd1', fileName: 'f.md', page: 2, kind: 'page', text: 'abc' });
        expect(a).toMatchObject({ doc_id: 'd1', fileName: 'f.md', page: 2, kind: 'page', text: 'abc' });
        expect(a.id).toBeTruthy();
        expect(a.id).not.toBe(b.id);
    });
});

describe('addPin', () => {
    it('appends a new pin', () => {
        const res = addPin([], pin());
        expect(res.added).toBe(true);
        expect(res.pins).toHaveLength(1);
    });
    it('is a no-op for a duplicate (same doc_id + kind + text)', () => {
        const existing = [pin({ id: 'a' })];
        const res = addPin(existing, pin({ id: 'b' })); // different id, same content
        expect(res.added).toBe(false);
        expect(res.reason).toBe('duplicate');
        expect(res.pins).toBe(existing);
    });
    it('rejects past MAX_PINS', () => {
        const many = Array.from({ length: MAX_PINS }, (_, i) => pin({ id: String(i), text: `t${i}` }));
        const res = addPin(many, pin({ id: 'new', text: 'unique' }));
        expect(res.added).toBe(false);
        expect(res.reason).toBe('max-pins');
    });
    it('rejects when the total char budget would be exceeded', () => {
        const big = pin({ id: 'big', text: 'a'.repeat(PINNED_CONTEXT_CHAR_BUDGET - 2) });
        const res = addPin([big], pin({ id: 'small', text: 'aaa' }));
        expect(res.added).toBe(false);
        expect(res.reason).toBe('budget');
    });
});

describe('removePin', () => {
    it('drops the pin with the matching id', () => {
        expect(removePin([pin({ id: 'a' }), pin({ id: 'b', text: 'y' })], 'a')).toEqual([pin({ id: 'b', text: 'y' })]);
    });
});

describe('totalPinnedChars', () => {
    it('sums text lengths', () => {
        expect(totalPinnedChars([pin({ text: 'ab' }), pin({ text: 'cde' })])).toBe(5);
    });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test:run -- src/hooks/pins.test.js`
Expected: FAIL — `Failed to resolve import "./pins"`.

- [ ] **Step 3: Write minimal implementation**

```js
// src/hooks/pins.js
// Pure reducers + guardrails for chat "pins" (persistent document excerpts).
export const MAX_PINS = 6;
export const PINNED_CONTEXT_CHAR_BUDGET = 12000;

let _seq = 0;
export const makePin = ({ doc_id = null, fileName = '', page = null, kind, text }) => ({
    id: `pin-${Date.now().toString(36)}-${(_seq++).toString(36)}`,
    doc_id, fileName, page, kind, text,
});

export const totalPinnedChars = (pins) =>
    pins.reduce((n, p) => n + (p.text ? p.text.length : 0), 0);

const samePin = (a, b) => a.doc_id === b.doc_id && a.kind === b.kind && a.text === b.text;

export const addPin = (pins, pin) => {
    if (pins.some((p) => samePin(p, pin))) return { pins, added: false, reason: 'duplicate' };
    if (pins.length >= MAX_PINS) return { pins, added: false, reason: 'max-pins' };
    if (totalPinnedChars(pins) + (pin.text?.length || 0) > PINNED_CONTEXT_CHAR_BUDGET) {
        return { pins, added: false, reason: 'budget' };
    }
    return { pins: [...pins, pin], added: true };
};

export const removePin = (pins, id) => pins.filter((p) => p.id !== id);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test:run -- src/hooks/pins.test.js`
Expected: PASS (all cases).

- [ ] **Step 5: Commit** — STOP: ask the user before committing.

```bash
git add src/hooks/pins.js src/hooks/pins.test.js
git commit -m "feat(chat): pure pin reducers + guardrail constants"
```

---

### Task 2: `buildPinPreamble` — pins → system messages

**Files:**
- Modify: `src/hooks/chatHistory.js` (add export; keep `buildApiMessage` + `buildChatHistory`)
- Test: `src/hooks/chatHistory.test.js` (append cases)

**Interfaces:**
- Consumes: `Pin` shape from Task 1.
- Produces: `buildPinPreamble(pins: Pin[]) -> Array<{ role:'system', content:string }>` (empty array for no pins). Fed as `contextPreamble` to the existing `buildChatHistory`.

- [ ] **Step 1: Write the failing test** (append to `src/hooks/chatHistory.test.js`)

```js
import { buildPinPreamble } from './chatHistory';

describe('buildPinPreamble', () => {
    const pin = (over = {}) => ({ id: 'x', doc_id: 'd', fileName: 'moon.md', page: 3, kind: 'selection', text: 'cheese', ...over });

    it('returns [] for no pins', () => {
        expect(buildPinPreamble([])).toEqual([]);
        expect(buildPinPreamble()).toEqual([]);
    });

    it('emits one system block per pin, preserving order', () => {
        const out = buildPinPreamble([pin({ text: 'one' }), pin({ text: 'two', page: null, kind: 'page' })]);
        expect(out).toHaveLength(2);
        expect(out.every((m) => m.role === 'system')).toBe(true);
        expect(out[0].content).toContain('moon.md');
        expect(out[0].content).toContain('one');
        expect(out[0].content).toContain('page 3');
        expect(out[1].content).toContain('two');
        expect(out[1].content).not.toContain('page '); // page is null → no page label
    });

    it('composes with buildChatHistory: pins land right before the user message', () => {
        // (buildChatHistory is already imported at the top of this file)
        const history = buildChatHistory({
            priorMessages: [{ role: 'user', content: 'q1' }, { role: 'assistant', content: 'a1' }],
            contextPreamble: buildPinPreamble([pin({ text: 'ctx' })]),
            userMsg: { role: 'user', content: 'now' },
        });
        expect(history.map((m) => m.role)).toEqual(['user', 'assistant', 'system', 'user']);
        expect(history[history.length - 1]).toEqual({ role: 'user', content: 'now' });
    });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test:run -- src/hooks/chatHistory.test.js`
Expected: FAIL — `buildPinPreamble is not a function` (import undefined).

- [ ] **Step 3: Write minimal implementation** (append to `src/hooks/chatHistory.js`)

```js
// Turn pins into `system` preamble messages (one per pin). Passed as the
// `contextPreamble` to buildChatHistory so they sit right before the user turn.
export const buildPinPreamble = (pins = []) =>
    pins.map((p) => ({
        role: 'system',
        content:
            `The user is reading "${p.fileName || 'a document'}".\n` +
            `Relevant excerpt (${p.kind || 'page'}` +
            `${p.page != null ? `, page ${p.page}` : ''}):\n\n` +
            `"""\n${p.text}\n"""\n\n` +
            `Use this excerpt as primary context for the user's question. If it does ` +
            `not contain the answer, say so or use the document search tool if available.`,
    }));
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test:run -- src/hooks/chatHistory.test.js`
Expected: PASS.

- [ ] **Step 5: Commit** — STOP: ask the user before committing.

```bash
git add src/hooks/chatHistory.js src/hooks/chatHistory.test.js
git commit -m "feat(chat): buildPinPreamble — pins to system preamble"
```

---

### Task 3: Backend — persist `pins` on chat sessions

**Files:**
- Create: `server/sql/004_chat_pins.sql`
- Modify: `server/routers/chat_sessions.py` (`SessionIn`, `upsert_session`, `get_session`, `patch_session`)

**Interfaces:**
- Produces: `chat_sessions.pins` JSONB column; `PUT /v1/chat/sessions/{id}` accepts + stores `pins`; `GET` returns `pins`; `PATCH` accepts optional `pins`.
- Consumed by: Task 4 (`updateSessionPins` → PATCH) and Task 5 (full record includes `pins`; restore reads `pins`).

- [ ] **Step 1: Add the migration**

```sql
-- server/sql/004_chat_pins.sql
-- Persist chat "pins": explicit document excerpts kept attached to a conversation.
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS pins JSONB NOT NULL DEFAULT '[]'::jsonb;
```

- [ ] **Step 2: Extend `SessionIn`** (`server/routers/chat_sessions.py`, in the `class SessionIn`)

Add this field after `events`:

```python
    pins: list[dict[str, Any]] = Field(default_factory=list)
```

- [ ] **Step 3: Store `pins` in `upsert_session`**

Replace the `INSERT INTO chat_sessions (...) ... ON CONFLICT ...` statement + its params tuple with:

```python
            await conn.execute(
                """
                INSERT INTO chat_sessions (id, title, model, created_at, updated_at, pins)
                VALUES (
                    %s, %s, %s,
                    COALESCE(to_timestamp(%s::double precision / 1000.0), now()),
                    now(),
                    %s
                )
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title,
                    model = EXCLUDED.model,
                    pins = EXCLUDED.pins,
                    updated_at = now()
                """,
                (
                    payload.id,
                    payload.title,
                    payload.model,
                    payload.createdAt,
                    Jsonb(payload.pins),
                ),
            )
```

- [ ] **Step 4: Return `pins` from `get_session`**

In `get_session`, change the session SELECT to include `pins`:

```python
        await cur.execute(
            "SELECT id, title, model, created_at, updated_at, pins FROM chat_sessions WHERE id = %s",
            (session_id,),
        )
```

And add `pins` to the returned dict (alongside `messages`/`events`):

```python
        "pins": srow[5] or [],
```

- [ ] **Step 5: Accept `pins` in `patch_session`**

Replace the body of `patch_session` from `title = body.get("title")` through the `UPDATE` params with:

```python
    title = body.get("title")
    model = body.get("model")
    pins = body.get("pins")
    if title is None and model is None and pins is None:
        return {"ok": True, "id": session_id, "noop": True}

    pool = get_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        # Only update provided fields; leave the rest untouched.
        await cur.execute(
            """
            UPDATE chat_sessions
            SET title = COALESCE(%s, title),
                model = COALESCE(%s, model),
                pins  = COALESCE(%s, pins),
                updated_at = now()
            WHERE id = %s
            RETURNING id
            """,
            (title, model, Jsonb(pins) if pins is not None else None, session_id),
        )
        row = await cur.fetchone()
```

(`Jsonb` is already imported at the top of the file.)

- [ ] **Step 6: Verify via a live round-trip**

Start the backend (migration auto-applies): `env -u XDG_DATA_HOME ./startup.sh up` (in a background shell). Then:

```bash
# PUT a session with pins
curl -s -X PUT localhost:8000/v1/chat/sessions/s-pintest \
  -H 'Content-Type: application/json' \
  -d '{"id":"s-pintest","title":"pin test","messages":[],"events":[],"pins":[{"id":"p1","doc_id":"d","fileName":"f.md","page":1,"kind":"selection","text":"hello"}]}'
# GET it back — expect the pins array present
curl -s localhost:8000/v1/chat/sessions/s-pintest | python3 -m json.tool | grep -A6 '"pins"'
# PATCH pins only — expect messages untouched
curl -s -X PATCH localhost:8000/v1/chat/sessions/s-pintest \
  -H 'Content-Type: application/json' -d '{"pins":[]}'
curl -s localhost:8000/v1/chat/sessions/s-pintest | python3 -m json.tool | grep '"pins"'
# cleanup
curl -s -X DELETE localhost:8000/v1/chat/sessions/s-pintest >/dev/null
```
Expected: first GET shows the 1-element pins array; after PATCH, `"pins": []`. Then stop the backend (SIGTERM the `startup.sh up` process so its trap tears down).

- [ ] **Step 7: Commit** — STOP: ask the user before committing.

```bash
git add server/sql/004_chat_pins.sql server/routers/chat_sessions.py
git commit -m "feat(chat): persist session pins (migration + upsert/get/patch)"
```

---

### Task 4: `sessionStore.updateSessionPins` — instant pin persistence

**Files:**
- Modify: `src/lib/sessionStore.js` (add method inside the returned object)
- Test: `src/lib/sessionStore.test.js` (create)

**Interfaces:**
- Consumes: `PATCH /v1/chat/sessions/{id}` with `{ pins }` (Task 3).
- Produces: `updateSessionPins(id: string, pins: Pin[]) -> Promise<boolean>` on the store object.

- [ ] **Step 1: Write the failing test**

```js
// src/lib/sessionStore.test.js
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { makeSessionStore } from './sessionStore';

describe('updateSessionPins', () => {
    let fetchMock;
    beforeEach(() => {
        fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, text: async () => '{"ok":true}' });
        vi.stubGlobal('fetch', fetchMock);
    });
    afterEach(() => vi.unstubAllGlobals());

    it('PATCHes the session with a pins body and resolves true', async () => {
        const store = makeSessionStore({ apiHost: '', apiPort: '', onBackendOffline: () => {} });
        const pins = [{ id: 'p1', doc_id: 'd', fileName: 'f', page: 1, kind: 'selection', text: 't' }];
        const ok = await store.updateSessionPins('s-1', pins);
        expect(ok).toBe(true);
        const [url, init] = fetchMock.mock.calls[0];
        expect(url).toContain('/v1/chat/sessions/s-1');
        expect(init.method).toBe('PATCH');
        expect(JSON.parse(init.body)).toEqual({ pins });
    });

    it('returns false when the id is missing', async () => {
        const store = makeSessionStore({ apiHost: '', apiPort: '', onBackendOffline: () => {} });
        expect(await store.updateSessionPins('', [])).toBe(false);
        expect(fetchMock).not.toHaveBeenCalled();
    });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test:run -- src/lib/sessionStore.test.js`
Expected: FAIL — `store.updateSessionPins is not a function`.

- [ ] **Step 3: Write minimal implementation** — add to the object returned by `makeSessionStore` (next to `renameSession`)

```js
        /**
         * Persist just the pins for a session (instant save on pin add/remove).
         * Mirrors renameSession: local IDB sessions update in place; pg sessions
         * hit the PATCH endpoint. Degrades to falsy/offline without throwing.
         */
        updateSessionPins: async (id, pins) => {
            if (!id) return false;
            if (LOCAL_IDS.has(id)) {
                const record = await idb.getSession(id);
                if (!record) return false;
                record.pins = pins;
                record.updatedAt = Date.now();
                return idb.saveSession(record);
            }
            try {
                await fetchJson(`/v1/chat/sessions/${encodeURIComponent(id)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ pins }),
                });
                notifyOnline();
                return true;
            } catch (e) {
                notifyOffline(e);
                return false;
            }
        },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test:run -- src/lib/sessionStore.test.js`
Expected: PASS.

- [ ] **Step 5: Commit** — STOP: ask the user before committing.

```bash
git add src/lib/sessionStore.js src/lib/sessionStore.test.js
git commit -m "feat(chat): sessionStore.updateSessionPins (instant pin PATCH)"
```

---

### Task 5: Wire pins into `useChatEngine`

**Files:**
- Modify: `src/hooks/useChatEngine.js`

**Interfaces:**
- Consumes: `buildPinPreamble` (Task 2), `buildChatHistory` (existing), `addPin`/`removePin`/`makePin` reducers + toast reasons (Task 1), `sessionStore.updateSessionPins` (Task 4).
- Produces (added to the hook's return object): `pins: Pin[]`, `addPin(pin: Pin) -> boolean`, `removePin(id: string) -> void`, `clearPins() -> void`. `sendMessage` signature becomes `(userText, attachments = [])` — the `docContext` argument is removed.

- [ ] **Step 1: Add imports** (top of file, near the other hook imports)

```js
import { buildChatHistory, buildPinPreamble } from './chatHistory';
import { addPin as addPinReducer, removePin as removePinReducer, MAX_PINS } from './pins';
```
(There is already an `import { buildChatHistory } from './chatHistory';` from the earlier bugfix — merge it into the line above rather than duplicating.)

- [ ] **Step 2: Add pins state + a ref** (next to `const [messages, setMessages] = useState([]);` / `messagesRef`)

```js
    const [pins, setPins] = useState([]);
    const pinsRef = useRef([]);
    pinsRef.current = pins;
```

- [ ] **Step 3: Add pin actions** (near the other `useCallback` actions, after `setActive` is defined)

```js
    // Persist pins immediately when a session already exists. Before the first
    // message there is no session yet; pins ride along in the first full save.
    const persistPins = useCallback((next) => {
        const id = activeSessionIdRef.current;
        if (id) sessionStore.updateSessionPins(id, next);
    }, [sessionStore]);

    const addPin = useCallback((pin) => {
        const res = addPinReducer(pinsRef.current, pin);
        if (!res.added) {
            const msg = res.reason === 'duplicate' ? 'That passage is already pinned.'
                : res.reason === 'max-pins' ? `You can pin up to ${MAX_PINS} passages.`
                : 'Pinned context is full — remove a pin first.';
            showToast?.(msg, 3500);
            return false;
        }
        setPins(res.pins);
        persistPins(res.pins);
        return true;
    }, [showToast, persistPins]);

    const removePin = useCallback((id) => {
        const next = removePinReducer(pinsRef.current, id);
        setPins(next);
        persistPins(next);
    }, [persistPins]);

    const clearPins = useCallback(() => setPins([]), []);
```

- [ ] **Step 4: Retire `docContext` in `sendMessage`; build preamble from pins**

In `sendMessage(userText, attachments = [], docContext = null)`:
1. Change the signature to `sendMessage(userText, attachments = [])`.
2. Replace `const hasDocCtx = !!(docContext && docContext.text);` with `const hasPins = pinsRef.current.length > 0;`.
3. In the early-return guard, replace `!hasDocCtx` with `!hasPins`.
4. In `userMsg`, remove the `docContext: hasDocCtx ? docContext : undefined,` line.
5. Delete the whole retrieval/preamble block that starts at `let retrievalResults = [];` and ends at the close of the `if (hasRetrieval) { ... }` push (the `docContext.useRetrieval` sync-search + both `contextPreamble.push(...)` blocks), and replace it with:

```js
            // Persistent pins → system preamble, placed right before the user
            // turn by buildChatHistory. Whole-document breadth comes from the
            // autonomous search_document tool, not from here.
            const contextPreamble = buildPinPreamble(pinsRef.current);
```

6. The existing history line stays as-is (it already reads `contextPreamble`):

```js
            const history = buildChatHistory({
                priorMessages: messagesRef.current,
                contextPreamble,
                userMsg,
            });
```

- [ ] **Step 5: Save/restore/clear pins**

- In `saveActiveSession`, add `pins: pinsRef.current,` to the `record` object.
- In `switchToSession`, after `setMessages(record.messages || []);` add `setPins(record.pins || []);`.
- In `newSession`, after `setMessages([]);` add `setPins([]);`.

- [ ] **Step 6: Export the pin API** — add to the `return { ... }` object

```js
        pins,
        addPin,
        removePin,
        clearPins,
```

- [ ] **Step 7: Verify build, lint, and existing tests**

Run:
```bash
npm run test:run && npm run lint && npm run build
```
Expected: all unit tests PASS (incl. Tasks 1, 2, 4), lint clean, build succeeds. Grep-check no stray `docContext` request wiring remains: `grep -n "hasDocCtx\|docContext.useRetrieval" src/hooks/useChatEngine.js` → no matches.

- [ ] **Step 8: Commit** — STOP: ask the user before committing.

```bash
git add src/hooks/useChatEngine.js
git commit -m "feat(chat): pins state, actions, and pin-based send preamble"
```

---

### Task 6: Pin-chip UI in `ChatView`

**Files:**
- Modify: `src/components/ChatView.jsx`
- Test: `src/components/ChatView.pins.test.jsx` (create)

**Interfaces:**
- Consumes: `pins`, `onRemovePin(id)` props (from App, Task 7).
- Produces: a row of removable pin chips above the input; `canSend` true when `pins.length > 0`. `DocContextChip` is exported for testing and no longer renders a retrieval toggle.

- [ ] **Step 1: Write the failing test**

```jsx
// src/components/ChatView.pins.test.jsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { DocContextChip } from './ChatView';

const theme = { border: '', text: '', textMuted: '', textSecondary: '', hover: '' };

describe('DocContextChip', () => {
    it('renders the pin label + file and fires onRemove with no retrieval toggle', () => {
        const onRemove = vi.fn();
        render(<DocContextChip ctx={{ kind: 'selection', page: 2, fileName: 'moon.md', text: 'cheese' }} theme={theme} darkMode={false} onRemove={onRemove} />);
        expect(screen.getByText('Selection')).toBeInTheDocument();
        expect(screen.getByText(/moon\.md/)).toBeInTheDocument();
        expect(screen.queryByText('Use whole document')).not.toBeInTheDocument();
        fireEvent.click(screen.getByTitle('Remove context'));
        expect(onRemove).toHaveBeenCalledTimes(1);
    });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test:run -- src/components/ChatView.pins.test.jsx`
Expected: FAIL — `DocContextChip` is not exported (import is undefined).

- [ ] **Step 3: Implement the UI changes**

1. Export the chip: change `function DocContextChip(` to `export function DocContextChip(`.
2. Remove the retrieval toggle from `DocContextChip`: delete the whole `{onToggleRetrieval && ( ... )}` `<label>` block, and drop `useRetrieval, onToggleRetrieval` from its params.
3. In the `ChatView` props list, replace `pendingDocContext,` and `clearPendingDocContext,` with `pins,` and `onRemovePin,`.
4. Delete the `useRetrieval` state and `retrievalAvailable`:
   remove `const [useRetrieval, setUseRetrieval] = useState(false);` and `const retrievalAvailable = ...;`.
5. Update `canSend`:

```jsx
    const canSend = (draft.trim().length > 0 || pendingAttachments.length > 0 || pins.length > 0)
        && !isStreaming && !!selectedModel && reachable !== false;
```

6. Simplify `handleSend` (pins are read inside the hook):

```jsx
    const handleSend = () => {
        if (!canSend) return;
        sendMessage(draft, pendingAttachments);
        setDraft('');
        setPendingAttachments([]);
    };
```

7. Replace the single-chip block (`{pendingDocContext && ( <DocContextChip ... /> )}`) with a pin row:

```jsx
                    {pins.length > 0 && (
                        <div className="flex flex-col gap-1.5">
                            {pins.map((p) => (
                                <DocContextChip
                                    key={p.id}
                                    ctx={p}
                                    theme={theme}
                                    darkMode={darkMode}
                                    onRemove={() => onRemovePin?.(p.id)}
                                />
                            ))}
                        </div>
                    )}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run test:run -- src/components/ChatView.pins.test.jsx`
Expected: PASS.

- [ ] **Step 5: Verify the whole suite + lint + build**

Run: `npm run test:run && npm run lint && npm run build`
Expected: green. (If lint flags an unused `currentDocIndexState` / `FileText` etc., remove the now-dead references.)

- [ ] **Step 6: Commit** — STOP: ask the user before committing.

```bash
git add src/components/ChatView.jsx src/components/ChatView.pins.test.jsx
git commit -m "feat(chat): pin-chip row in ChatView; retire per-chip retrieval toggle"
```

---

### Task 7: Wire App handlers + props; CHANGELOG; manual E2E

**Files:**
- Modify: `src/App.jsx`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: `pins`/`addPin`/`removePin` from `useChatEngine` (Task 5); `makePin` (Task 1); `pins`/`onRemovePin` props of `ChatView` (Task 6).

- [ ] **Step 1: Import `makePin`** (with the other `./hooks`/util imports)

```js
import { makePin } from './hooks/pins';
```

- [ ] **Step 2: Destructure the pin API** — in the `const { … } = chatEngine;` block (the one that renames `newSession: chatNewSession`, `switchToSession: chatSwitchToSession`, …), add three lines:

```js
    pins: chatPins,
    addPin: chatAddPin,
    removePin: chatRemovePin,
```

- [ ] **Step 3: Remove the old one-shot context state**

Delete `const [pendingChatContext, setPendingChatContext] = useState(null);` and `const clearPendingChatContext = useCallback(() => setPendingChatContext(null), []);`. Remove the `setPendingChatContext(null);` line in `handleGoHome`.

- [ ] **Step 4: Convert the Ask-AI handlers to add pins**

In `handleAskAboutPage`, replace the `setPendingChatContext({...}); setViewMode('chat');` tail with:

```js
    const docId = await ensureDocHash();
    chatAddPin(makePin({ doc_id: docId, fileName: pdfFileName, page, kind: 'page', text }));
    setViewMode('chat');
```
Update its dependency array: drop `setPendingChatContext`, add `chatAddPin`.

In `handleAskAboutSelection`, replace the `setPendingChatContext({...}); setViewMode('chat');` tail with:

```js
    const docId = await ensureDocHash();
    chatAddPin(makePin({ doc_id: docId, fileName: pdfFileName, page: currentPage, kind: 'selection', text }));
    setViewMode('chat');
```
Update its dependency array: drop `setPendingChatContext`, add `chatAddPin`.

- [ ] **Step 5: Update the `ChatView` props**

Replace:
```jsx
            pendingDocContext={pendingChatContext}
            clearPendingDocContext={clearPendingChatContext}
            currentDocIndexState={pendingChatContext?.doc_id ? docIndexByDocId[pendingChatContext.doc_id]?.state : null}
```
with:
```jsx
            pins={chatPins}
            onRemovePin={chatRemovePin}
```

- [ ] **Step 6: Verify build, lint, full suite**

Run: `npm run test:run && npm run lint && npm run build`
Expected: green. Grep-check the old state is gone: `grep -rn "pendingChatContext\|setPendingChatContext" src/` → no matches.

- [ ] **Step 7: Manual end-to-end** (backend + Ollama running: `env -u XDG_DATA_HOME ./startup.sh up`, `npm run dev`)

Confirm each:
- [ ] Open a doc, select text → **Ask AI** → switches to chat, a pin chip appears; ask a question → answer uses it.
- [ ] Ask a **follow-up** (no re-attach) → the model still uses the pinned excerpt.
- [ ] Select different text → Ask AI → **second** chip appears; both ride along.
- [ ] Re-Ask the **same** selection → no duplicate chip (dedupe); a toast if it hit a cap.
- [ ] Remove a pin via its **X** → it stops affecting replies.
- [ ] **Reload** the page / reopen the chat → pins are still there (persisted).
- [ ] **New chat** / switch chats → pins reset to that chat's own pins.
- [ ] Add pins until the budget/`MAX_PINS` cap → rejection toast, no silent balloon.
- [ ] Send with **only** a pin and empty text box → allowed.
- [ ] Index the doc → ask something outside the pinned passage → model uses `search_document` (whole-doc breadth) alongside the pin.

- [ ] **Step 8: Update CHANGELOG.md** — add under a new `## [Unreleased]` (or the current working version) section:

```markdown
### Added
- **Pinned chat context:** "Ask AI" on a selection (or "Ask page") now *pins* the
  excerpt to the conversation — it stays attached across follow-ups instead of
  being sent once. Multiple pins accumulate as removable chips, are saved with the
  chat session (restored on reload), and are bounded (max 6 pins / 12k chars).
  Whole-document breadth continues to come from semantic retrieval.

### Fixed
- Ask-AI context is no longer stranded at the front of a multi-turn chat (the model
  previously reported "no content attached" on follow-up questions).
```

- [ ] **Step 9: Commit** — STOP: ask the user before committing.

```bash
git add src/App.jsx CHANGELOG.md
git commit -m "feat(chat): pin excerpts from Ask AI; persist + restore per session"
```

---

## Self-Review notes (author)

- **Spec coverage:** data model (T1), preamble/positioning (T2 + existing `buildChatHistory`), backend persistence + PATCH (T3), instant save (T4 + T5 `persistPins`), hook state/actions/save/restore + retiring `docContext`/`useRetrieval` (T5), multi-pin chip UX + `canSend` (T6), Ask-AI handlers + dedupe via `makePin`+reducer + guardrail toasts (T1/T5/T7), whole-doc = retrieval (no pin kind; unchanged tool), CHANGELOG (T7). All mapped.
- **Type consistency:** `Pin = { id, doc_id, fileName, page, kind, text }` used identically across `makePin`, `addPin`/`removePin`, `buildPinPreamble`, backend `pins`, and chip `ctx`. Hook exposes `pins/addPin/removePin/clearPins`; App consumes the first three (clearPins used internally by `newSession`).
- **No placeholders:** every code step has concrete code; backend verified by curl; frontend by unit/RTL tests + a manual checklist.
