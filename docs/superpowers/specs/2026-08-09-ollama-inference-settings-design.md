# Ollama Inference Settings — Design

*Date: 2026-08-09. Status: approved, ready for planning.*

---

## 1. Why

A chat reply was silently truncated mid-sentence. The stats footer read:

```
779 tok · 318.16 s · 4.0 tok/s
Prompt      3317 tok in 108.62 s
Generation   779 tok in 195.32 s (4.0 tok/s)
Reason      length
```

`3317 + 779 = 4096` exactly. The reply hit the **context ceiling**, not a token budget.

The cause is that `buildBody` in `src/hooks/useChatEngine.js` sends only
`{model, messages, stream, think, tools}`. There is no `options` object anywhere in
`src/`, so `num_ctx` is never set and Ollama applies its own memory-sized default —
4096 on a machine with under 24 GB available. The model's native context length is
262144.

Two things follow. Users have no way to raise the window, and when they hit it the
only evidence is a `done_reason` buried in a collapsed disclosure
(`src/components/ChatView.jsx:629`, rendered only when it isn't `"stop"`).

This design adds per-model inference controls and makes truncation visible.

## 2. Scope

**In scope** — four request parameters, chosen because they are the ones implicated
in the failure above:

| Setting | Wire form | Effect |
|---|---|---|
| Context window | `options.num_ctx` | the truncation fix |
| Keep model warm | `keep_alive` | removes repeated cold-load cost (14.16 s observed) |
| Thinking | `think` | reasoning tokens are the most expensive output on slow hardware |
| Max reply tokens | `options.num_predict` | hard cap on reply length |

**Out of scope** — sampler parameters (`temperature`, `top_k`, `top_p`, `min_p`,
`repeat_penalty`, `repeat_last_n`, `seed`, `stop`). Models ship tuned values in their
Modelfile (`qwen3.5:latest` sets `temperature 1`, `top_k 20`, `top_p 0.95`,
`presence_penalty 1.5`) and anything sent in `options` replaces them. Exposing these
is a separate decision with its own risk of silently degrading output quality.

**Also out of scope** — coupling `PINNED_CONTEXT_CHAR_BUDGET` to `num_ctx`. See §9.

## 3. Governing principle: unset means omit

Ollama sizes its default context from available memory — 4k under 24 GB, 32k at
24–48 GB, 256k above. A hardcoded default such as `num_ctx: 16384` would therefore
**downgrade** any user whose machine already earns 32k or more, and risks an
out-of-memory condition for a user running a large model on a small GPU.

Therefore:

> Every setting defaults to unset. Unset means the key is omitted from the request
> body entirely. An all-unset settings object must produce a request body
> byte-identical to the one the app sends today.

Installing this feature changes nothing until a user opts in. What prompts them to
opt in is the truncation toast in §7.

This also rules out sending `null` or `0` as an "unset" marker — a stray
`temperature: 0` would clobber a model-tuned default without any error.

## 4. Architecture

State follows the existing **preference** path, not the pins path. The distinction
already exists in this codebase:

- `enableThinking` lives in `App.jsx` as `usePersistedState` and is passed both into
  `useChatEngine` and into `ChatSidebar`. It is a user preference.
- Pins live inside `useChatEngine`, with pure reducers extracted to `src/hooks/pins.js`.
  They are conversation state.

Inference settings are preferences, so they take the first path — while borrowing
pins' *structure*: a small pure module holding all the logic, unit-tested in
isolation, with React holding nothing but state.

### 4.1 New module — `src/hooks/inference.js`

Pure, no React imports.

```js
export const INFERENCE_DEFAULTS = {
    numCtx: null,      // null = let Ollama size it
    keepAlive: null,   // null = Ollama default (5m)
    think: 'off',      // 'off' | 'on' | 'low' | 'medium' | 'high'
    numPredict: null,  // null = -1, infinite
};

resolveForModel(map, model)       // → settings; INFERENCE_DEFAULTS for unknown model
patchForModel(map, model, patch)  // → new map; immutable, never mutates input
buildRequestFields(settings)      // → partial request body, set keys only
migrateLegacyThinking(bool)       // → 'on' | 'off'
estimateTokens(texts)             // → integer; chars/4 heuristic, no tokenizer dep
```

`estimateTokens` takes a flat array of strings, not message objects. The caller
concatenates message contents and pin texts before passing them in, so the function
stays independent of both shapes.

`buildRequestFields` is the core contract:

- `think: 'off'` → `{ think: false }`
- `think: 'on'` → `{ think: true }` (boolean — byte-identical to current behavior)
- `think: 'low' | 'medium' | 'high'` → `{ think: 'low' }` etc. (string)
- `keepAlive` non-null → adds `keep_alive`
- `numCtx` or `numPredict` non-null → adds an `options` object containing **only**
  the non-null members
- neither set → **no `options` key at all**

### 4.2 The thinking control has five states

`off | on | low | medium | high`.

Ollama's `think` field accepts a boolean or one of `"low"`, `"medium"`, `"high"`,
`"max"`. Keeping a distinct `on` state that emits the boolean makes migration from
the existing `enableThinking` boolean lossless (`false → 'off'`, `true → 'on'`)
rather than guessing which level is equivalent to "on".

`"max"` is deliberately not offered — it is the most expensive setting available and
has no obvious use in a document-reader chat.

### 4.3 Wiring

| File | Change |
|---|---|
| `src/App.jsx` | `usePersistedState('inferenceByModel', {})`; `useMemo` resolving for `selectedModel`; passes `inference` + `setInference` to `ChatSidebar`, `inference` into `useChatEngine` |
| `src/hooks/useChatEngine.js` | `buildBody` (line ~614) spreads `buildRequestFields(inference)` in place of the bare `think:` field; value locked at send time, matching the existing `thinkForThisMsg` pattern so mid-stream toggling applies to the next message |
| `src/components/ChatSidebar.jsx` | new collapsible section via the existing `Section` component (line 368); removes the Brain toggle at line 175 and the `enableThinking` / `setEnableThinking` props |
| `src/components/ChatView.jsx` | context meter above the composer; truncation toast |

Two new props to `ChatSidebar`, one new param to `useChatEngine`. `App.jsx` grows by
roughly six lines.

## 5. UI

### 5.1 Sidebar section

A collapsible `Inference` section, `defaultOpen={false}`, placed after the existing
model picker and before the TTS preferences.

```
⚙ INFERENCE
   Context window    [ Auto ▾ ]      Auto / 4096 / 8192 / 16384 / 32768
   Keep model warm   [ Auto ▾ ]      Auto / 5m / 30m / 1h / Always
   Thinking          [ Off ▾ ]       Off / On / Low / Medium / High
   Max reply tokens  [ Unlimited ▾ ] Unlimited / 512 / 1024 / 2048 / 4096

   Changing the context window reloads the model.
```

Every control's first option is the unset state, labelled `Auto` (or `Unlimited`),
and selecting it removes the key from the stored settings rather than storing a
sentinel value.

Stored-value mapping for the two non-obvious controls:

| Control option | Stored | On the wire |
|---|---|---|
| Keep model warm — `Auto` | `null` | key omitted (Ollama uses 5m) |
| Keep model warm — `5m` / `30m` / `1h` | `"5m"` / `"30m"` / `"1h"` | `keep_alive: "30m"` |
| Keep model warm — `Always` | `-1` | `keep_alive: -1` (never unloaded) |
| Max reply tokens — `Unlimited` | `null` | key omitted (Ollama uses -1) |
| Max reply tokens — `512`…`4096` | integer | `options.num_predict` |

`Always` is the one option that can hold a model in memory indefinitely. It is
offered because the observed 14.16 s cold load is paid on every request once the
default 5-minute window expires, but it is not a default — it permanently occupies
memory the user may want back.

Discrete dropdowns rather than free-text or sliders: `num_ctx` changes force a model
reload, so a value that changes on every keystroke or drag would thrash the runner.

The reload hint is permanent helper text, not a toast — it explains a cost the user
pays every time they change that one control.

### 5.2 Context meter

A single line above the composer in `ChatView.jsx`, near the existing pin-chip row
(line 288):

```
~3.3k / 16k ctx
```

Neutral colour below 75% of the window, amber at or above 75%. Uses
`estimateTokens()` over the message array plus pins. When `numCtx` is unset the
denominator is unknown, so the meter shows `~3.3k ctx` with no denominator and no
colour change.

The estimate is `chars / 4` — deliberately not a real tokenizer. Adding one would
mean shipping model-specific vocabularies to the browser for a readout whose whole
job is to say "you are getting close".

## 6. Per-model persistence

One localStorage key, `inferenceByModel`, holding a map keyed by the Ollama model
name:

```js
{
  "qwen3.5:latest": { numCtx: 16384, keepAlive: "30m", think: "low", numPredict: null },
  "gemma4:latest":  { numCtx: 8192,  keepAlive: null,  think: "off", numPredict: null }
}
```

A 9.7B model and a 3B model want different context sizes on the same machine, so
settings follow the model, not the app. Switching models in the picker swaps the
resolved settings. A model with no stored entry resolves to `INFERENCE_DEFAULTS`.

Written through the existing `usePersistedState`, which already prefixes keys with
`neural-pdf-` and try/catches corrupt JSON.

### 6.1 Migrating `enableThinking`

On first load, if `neural-pdf-enableThinking` exists and `inferenceByModel` has no
entry for the currently selected model, seed that model's `think` from
`migrateLegacyThinking(storedBool)`. The old key is left in place — harmless, and it
keeps the migration idempotent across a downgrade.

## 7. Error handling

| Condition | Behavior |
|---|---|
| Unknown / newly pulled model | resolves to `INFERENCE_DEFAULTS`, all unset |
| Corrupt `inferenceByModel` JSON | already caught by `usePersistedState`; falls back to `{}` |
| Model rejects a thinking *level* (4xx) | retry once with `think: true`, toast once per session — mirrors the existing tools fallback at `useChatEngine.js:628` |
| `done_reason === 'length'` | toast naming the real numbers |
| `num_ctx` changed | model reloads; covered by the permanent hint in §5.1 |

The truncation toast, via `showToast(message, duration)` (`App.jsx:80`):

> Reply was cut off — context full (3317 prompt + 779 reply = 4096). Raise the
> context window in Inference settings.

Numbers come from the stats already captured off the final NDJSON chunk
(`useChatEngine.js:596-601`). The toast fires once per truncated message.

## 8. Testing

Following the `src/hooks/pins.test.js` pattern — pure module, no rendering:

**`src/hooks/inference.test.js`**
- `resolveForModel` returns defaults for an unknown model; returns stored values for a known one; merges partial entries over defaults
- `patchForModel` returns a new map and does not mutate the input
- `buildRequestFields`: all-unset produces `{ think: false }` and **no** `options` key;
  `numCtx` alone produces `options: { num_ctx }` with no `num_predict`; `think: 'on'`
  produces boolean `true`; `think: 'low'` produces the string
- `migrateLegacyThinking` maps both booleans
- `estimateTokens` is monotonic in input length

**`src/hooks/chatHistory.test.js` or a new `useChatEngine` test** — an all-unset
settings object produces a request body deep-equal to the one built today. This is
the regression guard for §3.

**`src/components/ChatSidebar.test.jsx`** — the Inference section renders its four
controls; selecting `Auto` clears the stored key rather than storing a sentinel.

The existing 77 tests must stay green.

## 9. Known gap, accepted

`PINNED_CONTEXT_CHAR_BUDGET` stays fixed at 12000 characters
(`src/hooks/pins.js:3`) rather than scaling with `num_ctx`. At roughly 3000–4000
tokens, a full pin set can still exceed a 4096-token window on its own — the pin
budget was designed without a matching context window.

The meter and the truncation toast now make that visible, but the guardrail itself
does not move. Deriving the budget from `num_ctx` was considered and deferred; it is
a reasonable follow-up if the condition proves common in practice.

## 10. Verified facts this design rests on

Confirmed against a live Ollama 0.30.6 install and the current upstream docs:

- `POST /api/chat` top-level fields: `model`, `messages`, `tools`, `think`, `format`,
  `options`, `stream`, `keep_alive` (`docs/api.md`)
- `think` accepts a boolean or `"low"` / `"medium"` / `"high"` / `"max"` (`docs/api.md`,
  and `ollama run --help`)
- `keep_alive` default `5m` (`docs/api.md`)
- `num_predict` default `-1`, infinite (`docs/modelfile.mdx`) — which is why the
  observed truncation was the context ceiling and not a token cap
- Default context is memory-sized: 4k under 24 GB, 32k at 24–48 GB, 256k above
  (`docs/context-length.mdx`)
- `qwen3.5:latest` — 9.7B, Q4_K_M, native context 262144, capabilities
  `completion` / `vision` / `tools` / `thinking` (`ollama show`)
- All four settings are per-request and override both the daemon default and the
  model's Modelfile, so no `OLLAMA_CONTEXT_LENGTH`, no systemd override, and no
  daemon restart is required.
