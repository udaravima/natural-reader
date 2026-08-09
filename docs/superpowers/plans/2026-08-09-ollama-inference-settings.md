# Ollama Inference Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users set Ollama's context window, keep-alive, thinking level, and max reply tokens per model, and make context-exhaustion visible instead of silent.

**Architecture:** A pure module `src/hooks/inference.js` owns all logic (defaults, per-model resolution, request-body construction, token estimation). `App.jsx` holds one `usePersistedState('inferenceByModel', {})` map and resolves it for the selected model. `useChatEngine` spreads the resulting fields into its `/api/chat` bodies. `ChatSidebar` gets a collapsible Inference section; `ChatView` gets a context meter. This mirrors how `enableThinking` already flows (preference in App.jsx → engine + sidebar) while borrowing the pure-module structure of `src/hooks/pins.js`.

**Tech Stack:** React 19, Vite (Rolldown), Tailwind, Vitest + jsdom + @testing-library/react, lucide-react icons.

## Global Constraints

- **Unset means omit.** Every setting defaults to unset; unset removes the key from the request body entirely. An all-unset settings object MUST produce a request body deep-equal to the one the app sends today. Never send `null` or `0` as an "unset" marker — it would clobber a model's tuned Modelfile values.
- **No hardcoded `num_ctx` default.** Ollama sizes context from available memory (4k under 24 GB, 32k at 24–48 GB, 256k above). A fixed default would downgrade well-equipped users.
- Thinking control states: `off | on | low | medium | high`. `off` → `think: false`, `on` → `think: true` (boolean, byte-identical to today), levels → the string. `"max"` is deliberately not offered.
- `keep_alive` values on the wire: `"5m"` / `"30m"` / `"1h"` strings, or `-1` for Always. Unset → key omitted.
- Test commands: `npm run test:run` (all), `npx vitest run <path>` (one file). Lint: `npm run lint`.
- **Lint baseline is 4 pre-existing errors, not zero.** `no-unused-vars` in `src/lib/chatTools/currentTimeDate.js` (3) and `src/lib/chatTools/webSearch.js` (1), both from the recent chat-tools commits and unrelated to this work. Verified by stashing: identical count with and without our changes. Tasks must not ADD errors — `npm run lint` must still report exactly `✖ 4 problems`. Do NOT fix those four; they are out of scope for this plan.
- Do not commit without asking the user first — this repo requires explicit per-commit approval.
- Existing 77 tests must stay green.

---

### Task 1: Pure inference module

**Files:**
- Create: `src/hooks/inference.js`
- Test: `src/hooks/inference.test.js`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `INFERENCE_DEFAULTS: { numCtx: null, keepAlive: null, think: 'off', numPredict: null }`
  - `resolveForModel(map: object, model: string) → settings`
  - `patchForModel(map: object, model: string, patch: object) → object`
  - `buildRequestFields(settings: object) → { think, keep_alive?, options? }`
  - `migrateLegacyThinking(bool) → 'on' | 'off'`
  - `estimateTokens(texts: string[]) → number`
  - `truncationMessage(stats: object) → string | null`

- [ ] **Step 1: Write the failing test**

Create `src/hooks/inference.test.js`:

```js
import { describe, it, expect } from 'vitest';
import {
    INFERENCE_DEFAULTS, resolveForModel, patchForModel, buildRequestFields,
    migrateLegacyThinking, estimateTokens, truncationMessage,
} from './inference';

describe('resolveForModel', () => {
    it('returns defaults for an unknown model', () => {
        expect(resolveForModel({}, 'nope:latest')).toEqual(INFERENCE_DEFAULTS);
    });
    it('returns defaults when the map is null or undefined', () => {
        expect(resolveForModel(null, 'a')).toEqual(INFERENCE_DEFAULTS);
        expect(resolveForModel(undefined, 'a')).toEqual(INFERENCE_DEFAULTS);
    });
    it('merges a partial stored entry over the defaults', () => {
        const map = { 'qwen:latest': { numCtx: 16384 } };
        expect(resolveForModel(map, 'qwen:latest')).toEqual({
            numCtx: 16384, keepAlive: null, think: 'off', numPredict: null,
        });
    });
});

describe('patchForModel', () => {
    it('stores a patch under the model key', () => {
        const next = patchForModel({}, 'qwen:latest', { numCtx: 8192 });
        expect(next['qwen:latest'].numCtx).toBe(8192);
    });
    it('does not mutate the input map', () => {
        const map = { 'a:latest': { numCtx: 4096 } };
        const next = patchForModel(map, 'a:latest', { numCtx: 8192 });
        expect(map['a:latest'].numCtx).toBe(4096);
        expect(next).not.toBe(map);
    });
    it('leaves other models untouched', () => {
        const map = { 'a:latest': { numCtx: 4096 } };
        const next = patchForModel(map, 'b:latest', { numCtx: 8192 });
        expect(next['a:latest']).toEqual({ numCtx: 4096 });
    });
});

describe('buildRequestFields', () => {
    it('emits only think:false when everything is unset', () => {
        expect(buildRequestFields(INFERENCE_DEFAULTS)).toEqual({ think: false });
    });
    it('treats a missing settings object as all-unset', () => {
        expect(buildRequestFields(undefined)).toEqual({ think: false });
    });
    it('maps think "on" to the boolean true', () => {
        expect(buildRequestFields({ ...INFERENCE_DEFAULTS, think: 'on' })).toEqual({ think: true });
    });
    it('passes think levels through as strings', () => {
        expect(buildRequestFields({ ...INFERENCE_DEFAULTS, think: 'low' })).toEqual({ think: 'low' });
    });
    it('adds options.num_ctx alone without num_predict', () => {
        expect(buildRequestFields({ ...INFERENCE_DEFAULTS, numCtx: 16384 })).toEqual({
            think: false, options: { num_ctx: 16384 },
        });
    });
    it('adds options.num_predict alone without num_ctx', () => {
        expect(buildRequestFields({ ...INFERENCE_DEFAULTS, numPredict: 512 })).toEqual({
            think: false, options: { num_predict: 512 },
        });
    });
    it('adds keep_alive as a string', () => {
        expect(buildRequestFields({ ...INFERENCE_DEFAULTS, keepAlive: '30m' })).toEqual({
            think: false, keep_alive: '30m',
        });
    });
    it('adds keep_alive as -1 for Always', () => {
        expect(buildRequestFields({ ...INFERENCE_DEFAULTS, keepAlive: -1 })).toEqual({
            think: false, keep_alive: -1,
        });
    });
    it('never emits an options key when both options are unset', () => {
        expect(buildRequestFields({ ...INFERENCE_DEFAULTS, keepAlive: '5m' })).not.toHaveProperty('options');
    });
});

describe('migrateLegacyThinking', () => {
    it('maps true to on and false to off', () => {
        expect(migrateLegacyThinking(true)).toBe('on');
        expect(migrateLegacyThinking(false)).toBe('off');
    });
});

describe('estimateTokens', () => {
    it('is zero for an empty array', () => {
        expect(estimateTokens([])).toBe(0);
    });
    it('approximates four characters per token', () => {
        expect(estimateTokens(['a'.repeat(400)])).toBe(100);
    });
    it('sums across entries and ignores non-strings', () => {
        expect(estimateTokens(['a'.repeat(8), null, undefined, 'b'.repeat(4)])).toBe(3);
    });
});

describe('truncationMessage', () => {
    it('is null when the model stopped normally', () => {
        expect(truncationMessage({ doneReason: 'stop', promptEvalCount: 10, evalCount: 5 })).toBeNull();
    });
    it('is null when there are no stats at all', () => {
        expect(truncationMessage(null)).toBeNull();
        expect(truncationMessage(undefined)).toBeNull();
    });
    it('names the prompt, reply, and total when the context filled up', () => {
        const msg = truncationMessage({ doneReason: 'length', promptEvalCount: 3317, evalCount: 779 });
        expect(msg).toContain('3317');
        expect(msg).toContain('779');
        expect(msg).toContain('4096');
    });
    it('treats missing counts as zero rather than printing undefined', () => {
        const msg = truncationMessage({ doneReason: 'length' });
        expect(msg).toContain('0 prompt + 0 reply = 0');
        expect(msg).not.toContain('undefined');
    });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/hooks/inference.test.js`
Expected: FAIL — `Failed to resolve import "./inference"`.

- [ ] **Step 3: Write minimal implementation**

Create `src/hooks/inference.js`:

```js
// Pure helpers for Ollama inference settings, stored per model.
// Nothing here touches React or localStorage — App.jsx owns the state.
//
// Governing rule: an unset setting is OMITTED from the request body. Sending
// `null` or `0` instead would replace a model's tuned Modelfile values (for
// example qwen3.5 ships temperature 1 / top_k 20 / top_p 0.95) with no error.

export const INFERENCE_DEFAULTS = {
    numCtx: null,      // null = let Ollama size the context window from free memory
    keepAlive: null,   // null = Ollama default (5m); -1 = never unload
    think: 'off',      // 'off' | 'on' | 'low' | 'medium' | 'high'
    numPredict: null,  // null = -1 (infinite generation)
};

// 'on' sends the boolean, which is byte-identical to the legacy enableThinking
// behaviour. The three levels send strings. Keeping both means migrating the
// old boolean is lossless instead of a guess at which level equals "on".
const THINK_WIRE = { off: false, on: true };

const isSet = (v) => v !== null && v !== undefined;

export const resolveForModel = (map, model) => ({
    ...INFERENCE_DEFAULTS,
    ...((map && map[model]) || {}),
});

export const patchForModel = (map, model, patch) => ({
    ...map,
    [model]: { ...resolveForModel(map, model), ...patch },
});

export const buildRequestFields = (settings) => {
    const s = { ...INFERENCE_DEFAULTS, ...(settings || {}) };
    const fields = {
        think: s.think in THINK_WIRE ? THINK_WIRE[s.think] : s.think,
    };
    if (isSet(s.keepAlive)) fields.keep_alive = s.keepAlive;

    const options = {};
    if (isSet(s.numCtx)) options.num_ctx = s.numCtx;
    if (isSet(s.numPredict)) options.num_predict = s.numPredict;
    if (Object.keys(options).length > 0) fields.options = options;

    return fields;
};

export const migrateLegacyThinking = (bool) => (bool ? 'on' : 'off');

// Rough token estimate for the context meter. chars/4 is deliberate — a real
// tokenizer would mean shipping per-model vocabularies to the browser for a
// readout whose only job is to say "you are getting close".
export const estimateTokens = (texts = []) =>
    Math.ceil(texts.reduce((n, t) => n + (typeof t === 'string' ? t.length : 0), 0) / 4);

// Ollama reports done_reason "length" when the context window filled up — the
// model did not finish, it ran out of room. Returns null for every other
// reason so the caller can toast unconditionally on a non-null result.
export const truncationMessage = (stats) => {
    if (!stats || stats.doneReason !== 'length') return null;
    const prompt = stats.promptEvalCount || 0;
    const reply = stats.evalCount || 0;
    return `Reply was cut off — context full (${prompt} prompt + ${reply} reply = ${prompt + reply}). `
        + 'Raise the context window in Inference settings.';
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/hooks/inference.test.js`
Expected: PASS — 23 tests.

- [ ] **Step 5: Run the full suite and lint**

Run: `npm run test:run && npm run lint`
Expected: 100 passed (77 existing + 23 new), lint clean.

- [ ] **Step 6: Ask the user for permission to commit, then commit**

```bash
git add src/hooks/inference.js src/hooks/inference.test.js
git commit -m "feat(chat): add pure inference-settings module"
```

---

### Task 2: Per-model state in App.jsx with legacy thinking migration

**Files:**
- Modify: `src/App.jsx:65` (add state next to `enableThinking`), `src/App.jsx:165` (engine call), `src/App.jsx:1063` (sidebar call)

**Interfaces:**
- Consumes: `resolveForModel`, `patchForModel`, `migrateLegacyThinking`, `INFERENCE_DEFAULTS` from Task 1.
- Produces: an `inference` object (resolved settings for `selectedModel`) and `setInference(patch)` — passed to `useChatEngine` as the `inference` param and to `ChatSidebar` as `inference` / `setInference`. Both consumers ignore them until Tasks 3 and 4.

This task is intentionally behaviour-neutral: it adds state and passes it to consumers that do not read it yet. That keeps thinking working through the swap in Task 3.

- [ ] **Step 1: Add the import**

In `src/App.jsx`, next to the existing `import { OLLAMA_DEFAULTS } from './constants';` (line 15):

```jsx
import { resolveForModel, patchForModel, migrateLegacyThinking } from './hooks/inference';
```

- [ ] **Step 2: Add the persisted map after `enableThinking`**

After `src/App.jsx:65`:

```jsx
  const [enableThinking, setEnableThinking] = usePersistedState('enableThinking', false);
  // Per-model Ollama inference settings (context window, keep-alive, thinking
  // level, max reply tokens). Keyed by model name because a 9.7B and a 3B want
  // different context sizes on the same machine.
  const [inferenceByModel, setInferenceByModel] = usePersistedState('inferenceByModel', {});
```

- [ ] **Step 3: Resolve for the selected model and seed the legacy thinking value**

Add below the state declarations (after line 70, before the TRANSIENT UI STATE block):

```jsx
  // One-time seed: carry the old global `enableThinking` boolean into this
  // model's `think` setting the first time we see the model, so upgrading
  // users keep the thinking behaviour they had. Read straight from
  // localStorage rather than from React state — the `enableThinking` state is
  // deleted in Task 4 and this effect must keep working after that.
  useEffect(() => {
    if (!selectedModel) return;
    setInferenceByModel(prev => {
      if (prev[selectedModel]) return prev;
      let legacy = false;
      try {
        legacy = JSON.parse(localStorage.getItem('neural-pdf-enableThinking') || 'false');
      } catch { legacy = false; }
      return patchForModel(prev, selectedModel, { think: migrateLegacyThinking(legacy) });
    });
  }, [selectedModel, setInferenceByModel]);

  const inference = useMemo(
    () => resolveForModel(inferenceByModel, selectedModel),
    [inferenceByModel, selectedModel]
  );
  const setInference = useCallback(
    (patch) => setInferenceByModel(prev => patchForModel(prev, selectedModel, patch)),
    [selectedModel, setInferenceByModel]
  );
```

`useEffect`, `useMemo`, and `useCallback` are already imported in `App.jsx`.

- [ ] **Step 4: Pass to both consumers**

At `src/App.jsx:165`, add `inference` to the `useChatEngine` argument object:

```jsx
    chatTtsMode, chatAutoTts, enableThinking, inference,
```

At `src/App.jsx:1063`, add two props to `<ChatSidebar>`:

```jsx
            enableThinking={enableThinking} setEnableThinking={setEnableThinking}
            inference={inference} setInference={setInference}
```

- [ ] **Step 5: Verify nothing broke**

Run: `npm run test:run && npm run lint && npm run build`
Expected: 100 passed, lint clean, build succeeds. No behaviour change — the new props are unread.

- [ ] **Step 6: Ask the user for permission to commit, then commit**

```bash
git add src/App.jsx
git commit -m "feat(chat): hold per-model inference settings in App state"
```

---

### Task 3: Send the settings in both /api/chat request bodies

**Files:**
- Modify: `src/hooks/useChatEngine.js:44` (param), `:470` (lock at send time), `:614-621` (buildBody), `:712-717` (tool follow-up body)
- Modify: `src/App.jsx:165` (drop `enableThinking` from the engine call)
- Test: `src/hooks/inference.requestBody.test.js`

**Interfaces:**
- Consumes: `buildRequestFields`, `INFERENCE_DEFAULTS` from Task 1; the `inference` param from Task 2.
- Produces: `/api/chat` bodies that carry `think`, and conditionally `keep_alive` and `options`.

After this task the sidebar's Brain toggle is inert — it still renders but no longer reaches the request. Task 4 replaces it. This is a known one-task window.

- [ ] **Step 1: Write the failing regression test**

Create `src/hooks/inference.requestBody.test.js`. This is the guard for the "unset means omit" constraint — it pins the exact body shape rather than rendering the hook.

```js
// Regression guard, not a red-green step. This pins the exact /api/chat body
// shape so a future change to buildRequestFields cannot silently start sending
// keys we promised to omit (see the "unset means omit" constraint). It is green
// on first run by design — it protects a contract rather than driving new code.
import { describe, it, expect } from 'vitest';
import { buildRequestFields, INFERENCE_DEFAULTS } from './inference';

// The body shape useChatEngine sends today, before inference settings existed.
const legacyBody = ({ model, messages, think }) => ({ model, messages, stream: true, think });

const newBody = ({ model, messages, settings }) => ({
    model, messages, stream: true, ...buildRequestFields(settings),
});

describe('/api/chat body construction', () => {
    const model = 'qwen3.5:latest';
    const messages = [{ role: 'user', content: 'hi' }];

    it('is identical to the legacy body when every setting is unset', () => {
        expect(newBody({ model, messages, settings: INFERENCE_DEFAULTS }))
            .toEqual(legacyBody({ model, messages, think: false }));
    });

    it('is identical to the legacy thinking-on body when think is "on"', () => {
        expect(newBody({ model, messages, settings: { ...INFERENCE_DEFAULTS, think: 'on' } }))
            .toEqual(legacyBody({ model, messages, think: true }));
    });

    it('carries num_ctx nested under options, never at the top level', () => {
        const body = newBody({ model, messages, settings: { ...INFERENCE_DEFAULTS, numCtx: 16384 } });
        expect(body.options).toEqual({ num_ctx: 16384 });
        expect(body).not.toHaveProperty('num_ctx');
    });

    it('carries keep_alive at the top level, never under options', () => {
        const body = newBody({ model, messages, settings: { ...INFERENCE_DEFAULTS, keepAlive: '30m' } });
        expect(body.keep_alive).toBe('30m');
        expect(body.options).toBeUndefined();
    });
});
```

- [ ] **Step 2: Run test to verify it passes already**

Run: `npx vitest run src/hooks/inference.requestBody.test.js`
Expected: PASS — this test exercises Task 1's module directly, so it is green immediately. It exists to fail loudly if a later change breaks the omit contract. Confirm it passes, then wire the engine.

- [ ] **Step 3: Add the import and swap the engine parameter**

In `src/hooks/useChatEngine.js`, add to the imports at the top of the file:

```js
import { buildRequestFields, INFERENCE_DEFAULTS } from './inference';
```

Replace line 44:

```js
    enableThinking,     // bool — sends `think: true` to Ollama and surfaces message.thinking
```

with:

```js
    inference = INFERENCE_DEFAULTS,  // per-model settings → think / keep_alive / options
```

- [ ] **Step 4: Lock the settings at send time**

Replace line 470:

```js
        const thinkForThisMsg = !!enableThinking;
```

with:

```js
        const inferenceForThisMsg = inference;
```

Update the comment on line 468 to read:

```js
        // Lock TTS mode + inference settings for THIS message — changing them mid-stream applies to the next message.
```

- [ ] **Step 5: Spread the fields into the first request body**

Replace lines 614-620:

```js
            const buildBody = (extra = {}) => ({
                model: selectedModel,
                messages: history,
                stream: true,
                think: thinkForThisMsg,
                ...extra,
            });
```

with:

```js
            const buildBody = (extra = {}) => ({
                model: selectedModel,
                messages: history,
                stream: true,
                ...buildRequestFields(inferenceForThisMsg),
                ...extra,
            });
```

- [ ] **Step 6: Spread the fields into the tool follow-up body**

Replace lines 712-717:

```js
                    body: JSON.stringify({
                        model: selectedModel,
                        messages: followupHistory,
                        stream: true,
                        think: thinkForThisMsg,
                    }),
```

with:

```js
                    body: JSON.stringify({
                        model: selectedModel,
                        messages: followupHistory,
                        stream: true,
                        ...buildRequestFields(inferenceForThisMsg),
                    }),
```

- [ ] **Step 7: Drop `enableThinking` from the engine call**

In `src/App.jsx:165`, change:

```jsx
    chatTtsMode, chatAutoTts, enableThinking, inference,
```

to:

```jsx
    chatTtsMode, chatAutoTts, inference,
```

- [ ] **Step 8: Run tests, lint, build**

Run: `npm run test:run && npm run lint && npm run build`
Expected: 104 passed (100 + 4 new), lint clean, build succeeds. Lint will flag `enableThinking` as unused in `useChatEngine.js` if any reference was missed — grep to confirm: `grep -n enableThinking src/hooks/useChatEngine.js` should return nothing.

- [ ] **Step 9: Ask the user for permission to commit, then commit**

```bash
git add src/hooks/useChatEngine.js src/App.jsx src/hooks/inference.requestBody.test.js
git commit -m "feat(chat): send inference settings in /api/chat bodies"
```

---

### Task 4: Inference section in the chat sidebar

**Files:**
- Modify: `src/components/ChatSidebar.jsx:23` (props), `:171-189` (replace the Model options block), `:1-6` (icon import)
- Modify: `src/App.jsx:65` and `:1063` (retire `enableThinking`)
- Test: `src/components/ChatSidebar.inference.test.jsx`

**Interfaces:**
- Consumes: `inference` / `setInference` props from Task 2.
- Produces: UI that calls `setInference({ numCtx })`, `setInference({ keepAlive })`, `setInference({ think })`, `setInference({ numPredict })`.

- [ ] **Step 1: Write the failing test**

Create `src/components/ChatSidebar.inference.test.jsx`:

```jsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ChatSidebar from './ChatSidebar';
import { INFERENCE_DEFAULTS } from '../hooks/inference';

const theme = {
    bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '',
    text: '', textSecondary: '', textMuted: '', hover: '',
};

const baseProps = (over = {}) => ({
    theme, darkMode: false, effectiveIsMobile: false, sidebarOpen: true,
    ollamaHost: 'localhost', setOllamaHost: vi.fn(),
    ollamaPort: '11434', setOllamaPort: vi.fn(),
    selectedModel: 'qwen3.5:latest', setSelectedModel: vi.fn(),
    availableModels: ['qwen3.5:latest'], reachable: true, refreshModels: vi.fn(),
    chatTtsMode: 'streaming', setChatTtsMode: vi.fn(),
    chatAutoTts: true, setChatAutoTts: vi.fn(),
    inference: INFERENCE_DEFAULTS, setInference: vi.fn(),
    messages: [], clearHistory: vi.fn(),
    sessions: [], activeSessionId: null, events: [],
    newSession: vi.fn(), switchToSession: vi.fn(),
    deleteSession: vi.fn(), renameSession: vi.fn(),
    ...over,
});

describe('ChatSidebar inference controls', () => {
    it('renders the four inference controls', () => {
        render(<ChatSidebar {...baseProps()} />);
        expect(screen.getByLabelText('Context window')).toBeInTheDocument();
        expect(screen.getByLabelText('Keep model warm')).toBeInTheDocument();
        expect(screen.getByLabelText('Thinking')).toBeInTheDocument();
        expect(screen.getByLabelText('Max reply tokens')).toBeInTheDocument();
    });

    it('shows Auto as the selected context window when unset', () => {
        render(<ChatSidebar {...baseProps()} />);
        expect(screen.getByLabelText('Context window')).toHaveValue('auto');
    });

    it('sends a numeric num_ctx when a size is chosen', () => {
        const setInference = vi.fn();
        render(<ChatSidebar {...baseProps({ setInference })} />);
        fireEvent.change(screen.getByLabelText('Context window'), { target: { value: '16384' } });
        expect(setInference).toHaveBeenCalledWith({ numCtx: 16384 });
    });

    it('clears num_ctx back to null when Auto is chosen', () => {
        const setInference = vi.fn();
        render(<ChatSidebar {...baseProps({ inference: { ...INFERENCE_DEFAULTS, numCtx: 16384 }, setInference })} />);
        fireEvent.change(screen.getByLabelText('Context window'), { target: { value: 'auto' } });
        expect(setInference).toHaveBeenCalledWith({ numCtx: null });
    });

    it('stores keep_alive Always as -1', () => {
        const setInference = vi.fn();
        render(<ChatSidebar {...baseProps({ setInference })} />);
        fireEvent.change(screen.getByLabelText('Keep model warm'), { target: { value: '-1' } });
        expect(setInference).toHaveBeenCalledWith({ keepAlive: -1 });
    });

    it('stores keep_alive durations as strings', () => {
        const setInference = vi.fn();
        render(<ChatSidebar {...baseProps({ setInference })} />);
        fireEvent.change(screen.getByLabelText('Keep model warm'), { target: { value: '30m' } });
        expect(setInference).toHaveBeenCalledWith({ keepAlive: '30m' });
    });

    it('stores the thinking level as a string', () => {
        const setInference = vi.fn();
        render(<ChatSidebar {...baseProps({ setInference })} />);
        fireEvent.change(screen.getByLabelText('Thinking'), { target: { value: 'low' } });
        expect(setInference).toHaveBeenCalledWith({ think: 'low' });
    });

    it('no longer renders the legacy Enable thinking toggle', () => {
        render(<ChatSidebar {...baseProps()} />);
        expect(screen.queryByText('Enable thinking')).not.toBeInTheDocument();
    });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/ChatSidebar.inference.test.jsx`
Expected: FAIL — `Unable to find a label with the text of: Context window`.

- [ ] **Step 3: Swap the props**

In `src/components/ChatSidebar.jsx`, replace line 23:

```jsx
    enableThinking, setEnableThinking,
```

with:

```jsx
    inference, setInference,
```

Replace the `Brain` icon import on line 3 with `Sliders` (the `Brain` icon becomes unused and will trip lint):

```jsx
    Trash2, RefreshCw, Volume2, VolumeX, MessageSquare, Bot, Sliders,
```

- [ ] **Step 4: Replace the Model options block**

Replace lines 171-189 (the entire `{/* Model options */}` div) with:

```jsx
                    {/* Inference — per-model Ollama request parameters. Every
                        control's first option is the unset state, which removes
                        the key from the request entirely. */}
                    <div className="space-y-2">
                        <span className={`text-[10px] font-bold ${theme.textSecondary} ml-1 flex items-center gap-1.5`}>
                            <Sliders size={11} /> INFERENCE
                        </span>

                        <InferenceRow
                            theme={theme}
                            label="Context window"
                            value={inference.numCtx === null ? 'auto' : String(inference.numCtx)}
                            onChange={(v) => setInference({ numCtx: v === 'auto' ? null : Number(v) })}
                            options={[
                                ['auto', 'Auto'], ['4096', '4096'], ['8192', '8192'],
                                ['16384', '16384'], ['32768', '32768'],
                            ]}
                        />

                        <InferenceRow
                            theme={theme}
                            label="Keep model warm"
                            value={inference.keepAlive === null ? 'auto' : String(inference.keepAlive)}
                            onChange={(v) => setInference({ keepAlive: v === 'auto' ? null : (v === '-1' ? -1 : v) })}
                            options={[
                                ['auto', 'Auto (5m)'], ['5m', '5 minutes'], ['30m', '30 minutes'],
                                ['1h', '1 hour'], ['-1', 'Always'],
                            ]}
                        />

                        <InferenceRow
                            theme={theme}
                            label="Thinking"
                            value={inference.think}
                            onChange={(v) => setInference({ think: v })}
                            options={[
                                ['off', 'Off'], ['on', 'On'], ['low', 'Low'],
                                ['medium', 'Medium'], ['high', 'High'],
                            ]}
                        />

                        <InferenceRow
                            theme={theme}
                            label="Max reply tokens"
                            value={inference.numPredict === null ? 'auto' : String(inference.numPredict)}
                            onChange={(v) => setInference({ numPredict: v === 'auto' ? null : Number(v) })}
                            options={[
                                ['auto', 'Unlimited'], ['512', '512'], ['1024', '1024'],
                                ['2048', '2048'], ['4096', '4096'],
                            ]}
                        />

                        <p className={`text-[9px] ${theme.textMuted} px-1`}>
                            Settings are saved per model. Changing the context window reloads the model.
                        </p>
                    </div>
```

- [ ] **Step 5: Add the row component**

Add above the `Section` component definition (before line 366, `// Reusable collapsible section...`):

```jsx
// One labelled dropdown in the Inference block. Discrete options rather than a
// slider or free text: a num_ctx change forces Ollama to reload the model, so a
// value that changed on every keystroke would thrash the runner.
function InferenceRow({ theme, label, value, onChange, options }) {
    return (
        <div className="flex items-center gap-2">
            <label className={`text-[10px] font-bold ${theme.textMuted} flex-1 min-w-0 truncate`} htmlFor={`inf-${label}`}>
                {label}
            </label>
            <select
                id={`inf-${label}`}
                aria-label={label}
                value={value}
                onChange={(e) => onChange(e.target.value)}
                className={`text-[11px] font-bold p-1.5 rounded-lg border ${theme.border} ${theme.bgSecondary} ${theme.text} focus:ring-2 focus:ring-blue-500 outline-none transition-colors w-32 shrink-0`}
            >
                {options.map(([val, text]) => (
                    <option key={val} value={val}>{text}</option>
                ))}
            </select>
        </div>
    );
}
```

- [ ] **Step 6: Retire `enableThinking` from App.jsx**

Delete `src/App.jsx:65`:

```jsx
  const [enableThinking, setEnableThinking] = usePersistedState('enableThinking', false);
```

The seed effect from Task 2 already reads `neural-pdf-enableThinking` straight from localStorage, so it keeps working with the state gone — leave it alone. The stored key itself stays on disk: it is harmless and keeps the migration idempotent.

At `src/App.jsx:1063`, drop the two legacy props so only the new pair remains:

```jsx
            inference={inference} setInference={setInference}
```

- [ ] **Step 7: Run test to verify it passes**

Run: `npx vitest run src/components/ChatSidebar.inference.test.jsx`
Expected: PASS — 8 tests.

- [ ] **Step 8: Run the full suite, lint, build**

Run: `npm run test:run && npm run lint && npm run build`
Expected: 112 passed, lint clean (no unused `Brain` import, no unused `enableThinking`), build succeeds.

- [ ] **Step 9: Ask the user for permission to commit, then commit**

```bash
git add src/components/ChatSidebar.jsx src/components/ChatSidebar.inference.test.jsx src/App.jsx
git commit -m "feat(chat): add per-model inference controls to the sidebar"
```

---

### Task 5: Thinking-level fallback and truncation toast

**Files:**
- Modify: `src/hooks/useChatEngine.js:60` (ref), `:622-641` (fallback), `:645-649` (toast)

**Interfaces:**
- Consumes: `inferenceForThisMsg` from Task 3; `showToast(message, duration)` already a hook param.
- Produces: no new exports.

- [ ] **Step 0: Extend the inference import**

In `src/hooks/useChatEngine.js`, add `truncationMessage` to the import Task 3 added:

```js
import { buildRequestFields, INFERENCE_DEFAULTS, truncationMessage } from './inference';
```

- [ ] **Step 1: Add the fallback ref**

In `src/hooks/useChatEngine.js`, beside the existing `toolFallbackToastedRef` (line 60):

```js
    // Toasted once per session so a model that rejects thinking levels doesn't spam.
    const thinkLevelFallbackToastedRef = useRef(false);
```

- [ ] **Step 2: Retry a rejected thinking level with the boolean**

Ollama returns a 4xx for models that accept `think: true` but not `think: "low"`. Mirror the existing tools fallback. Insert immediately after the existing tools-fallback block that ends at line 641, before `if (!res.ok || !res.body) throw ...`:

```js
            // Some thinking-capable models accept the boolean but reject the
            // graduated levels. Retry once with `think: true` rather than
            // dropping thinking entirely.
            const usedLevel = !['off', 'on'].includes(inferenceForThisMsg.think);
            if (!res.ok && usedLevel && res.status >= 400 && res.status < 500) {
                console.warn(`Ollama returned ${res.status} for think:"${inferenceForThisMsg.think}" — retrying with think:true.`);
                if (!thinkLevelFallbackToastedRef.current) {
                    thinkLevelFallbackToastedRef.current = true;
                    showToast?.('This model rejected the thinking level — used plain thinking instead.', 4000);
                }
                logEvent('think-fallback', `model rejected think level "${inferenceForThisMsg.think}" (HTTP ${res.status})`);
                res = await fetch(ollamaUrl('/api/chat'), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        model: selectedModel,
                        messages: history,
                        stream: true,
                        ...buildRequestFields({ ...inferenceForThisMsg, think: 'on' }),
                    }),
                    signal: controller.signal,
                });
            }
```

- [ ] **Step 3: Toast on truncation**

Replace lines 645-649:

```js
            const first = await consumeStream(res);
            if (first.stats) {
                setMessages(prev => prev.map(m =>
                    m.id === assistantId ? { ...m, stats: first.stats } : m
                ));
            }
```

with:

```js
            const first = await consumeStream(res);
            if (first.stats) {
                setMessages(prev => prev.map(m =>
                    m.id === assistantId ? { ...m, stats: first.stats } : m
                ));
                // done_reason "length" means the context window filled up, not
                // that the model finished. Without this the only evidence is a
                // line inside a collapsed stats disclosure.
                const truncated = truncationMessage(first.stats);
                if (truncated) {
                    showToast?.(truncated, 7000);
                    logEvent('truncated', truncated);
                }
            }
```

- [ ] **Step 4: Verify manually against a real model**

Run the app (`npm run dev` plus `python run.py`), set Context window to `4096`, pin enough document text to push the prompt past ~3000 tokens, and ask a question that needs a long answer.
Expected: the reply stops mid-sentence and a toast names the actual token counts.

This step cannot be automated — it needs a live Ollama daemon and a browser.

- [ ] **Step 5: Run the full suite, lint, build**

Run: `npm run test:run && npm run lint && npm run build`
Expected: 112 passed, lint clean, build succeeds.

- [ ] **Step 6: Ask the user for permission to commit, then commit**

```bash
git add src/hooks/useChatEngine.js
git commit -m "feat(chat): warn on truncated replies and fall back on rejected think levels"
```

---

### Task 6: Context meter above the composer

**Files:**
- Modify: `src/components/ChatView.jsx:52-53` (props), `:288-300` (meter above the pin row)
- Modify: `src/App.jsx:1130-1131` (pass `numCtx`)
- Test: `src/components/ChatView.meter.test.jsx`

**Interfaces:**
- Consumes: `estimateTokens` from Task 1; `messages` and `pins` props ChatView already receives.
- Produces: no new exports.

- [ ] **Step 1: Write the failing test**

Create `src/components/ChatView.meter.test.jsx`:

```jsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import ChatView from './ChatView';

const theme = {
    bg: '', bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '',
    text: '', textSecondary: '', textMuted: '', hover: '',
};

const baseProps = (over = {}) => ({
    theme, darkMode: false, effectiveIsMobile: false,
    messages: [], isStreaming: false, selectedModel: 'qwen3.5:latest', reachable: true,
    sendMessage: vi.fn(), stopStream: vi.fn(),
    speakingMessageId: null, speakMessage: vi.fn(), stopSpeaking: vi.fn(),
    downloadingMessageId: null, downloadMessageAudio: vi.fn(),
    showToast: vi.fn(), pins: [], onRemovePin: vi.fn(), numCtx: null,
    ...over,
});

describe('ChatView context meter', () => {
    it('is hidden when there is nothing to count', () => {
        render(<ChatView {...baseProps()} />);
        expect(screen.queryByTestId('context-meter')).not.toBeInTheDocument();
    });

    it('shows an estimate with no denominator when num_ctx is unset', () => {
        const messages = [{ role: 'user', content: 'a'.repeat(4000), id: 'u1' }];
        render(<ChatView {...baseProps({ messages })} />);
        expect(screen.getByTestId('context-meter')).toHaveTextContent('~1.0k ctx');
    });

    it('shows a denominator when num_ctx is set', () => {
        const messages = [{ role: 'user', content: 'a'.repeat(4000), id: 'u1' }];
        render(<ChatView {...baseProps({ messages, numCtx: 16384 })} />);
        expect(screen.getByTestId('context-meter')).toHaveTextContent('~1.0k / 16k ctx');
    });

    it('counts pin text as well as messages', () => {
        const pins = [{ id: 'p1', doc_id: 'd', kind: 'page', text: 'a'.repeat(4000), fileName: 'f', page: 1 }];
        render(<ChatView {...baseProps({ pins, numCtx: 16384 })} />);
        expect(screen.getByTestId('context-meter')).toHaveTextContent('~1.0k / 16k ctx');
    });

    it('turns amber at or above 75% of the window', () => {
        const messages = [{ role: 'user', content: 'a'.repeat(13000), id: 'u1' }];
        render(<ChatView {...baseProps({ messages, numCtx: 4096 })} />);
        expect(screen.getByTestId('context-meter').className).toMatch(/amber/);
    });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/ChatView.meter.test.jsx`
Expected: FAIL — `Unable to find an element by: [data-testid="context-meter"]`.

- [ ] **Step 3: Add the import and props**

In `src/components/ChatView.jsx`, add to the imports:

```jsx
import { estimateTokens } from '../hooks/inference';
```

Change lines 52-53 in the props destructure:

```jsx
  pins = [],
  onRemovePin,
  numCtx = null,
```

- [ ] **Step 4: Compute the estimate**

Add near the other derived values, beside `const canSend =` (line 185):

```jsx
  // Rough prompt-size readout. Counts what actually gets sent: message contents
  // plus pin text, which are re-sent in full on every turn.
  const ctxTokens = estimateTokens([
    ...messages.map((m) => m.content),
    ...pins.map((p) => p.text),
  ]);
  const ctxRatio = numCtx ? ctxTokens / numCtx : 0;
  // Window sizes read better whole ("16k"), running totals need the decimal
  // ("1.0k") so the number visibly moves as the conversation grows.
  const fmtK = (n) => {
    if (n >= 10000) return `${Math.round(n / 1000)}k`;
    if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
    return String(n);
  };
```

- [ ] **Step 5: Render the meter above the pin row**

Insert immediately after `<div className="max-w-3xl mx-auto flex flex-col gap-2">` (line 284), before the pin row comment:

```jsx
          {ctxTokens > 0 && (
            <div
              data-testid="context-meter"
              className={`text-[9px] font-bold px-1 ${ctxRatio >= 0.75 ? 'text-amber-500' : theme.textMuted}`}
              title="Estimated prompt size. Messages and pins are re-sent on every turn."
            >
              ~{fmtK(ctxTokens)}{numCtx ? ` / ${fmtK(numCtx)}` : ''} ctx
            </div>
          )}
```

- [ ] **Step 6: Pass `numCtx` from App.jsx**

At `src/App.jsx:1130-1131`, add a third prop to `<ChatView>`:

```jsx
            pins={chatPins}
            onRemovePin={chatRemovePin}
            numCtx={inference.numCtx}
```

- [ ] **Step 7: Run test to verify it passes**

Run: `npx vitest run src/components/ChatView.meter.test.jsx`
Expected: PASS — 5 tests.

- [ ] **Step 8: Run the full suite, lint, build**

Run: `npm run test:run && npm run lint && npm run build`
Expected: 117 passed, lint clean, build succeeds.

- [ ] **Step 9: Ask the user for permission to commit, then commit**

```bash
git add src/components/ChatView.jsx src/components/ChatView.meter.test.jsx src/App.jsx
git commit -m "feat(chat): show an estimated context meter above the composer"
```

---

## Final verification

- [ ] `npm run test:run` — 117 passed
- [ ] `npm run lint` — clean
- [ ] `npm run build` — succeeds
- [ ] Manual: with all controls on Auto/Off, open DevTools → Network → `/api/chat` and confirm the request body is `{model, messages, stream, think:false}` with **no** `options` and **no** `keep_alive` key. This is the Global Constraint made observable.
- [ ] Manual: set Context window to 16384, send a message, confirm `options.num_ctx` appears and the model reloads once.
- [ ] Manual: switch models in the picker and confirm each remembers its own settings.
- [ ] Update `README.md` chat section and `CHANGELOG.md` with the new Inference controls.
