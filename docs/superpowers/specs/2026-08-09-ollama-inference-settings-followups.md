# Ollama Inference Settings — deferred follow-ups

*Recorded 2026-08-09, after the final whole-feature review. Everything here was
found during the build, judged non-blocking, and consciously deferred. Nothing on
this list blocks merge; all of it is real.*

The feature shipped in 12 commits (`74be6da..44c0422`), 126 tests, build clean.

---

## Ranked by what I'd actually do next

### 1. No test covers the fallback retry sequencing — the highest regression risk

`sendMessage` in `src/hooks/useChatEngine.js` now has two sequential fallbacks: a
thinking-level downgrade, then a tools drop. Getting their **order** wrong is a real
bug that shipped once already — the tools fallback used to run first and its retry
hand-rolled a body without `tools`, so a model that rejects graduated think levels
silently lost `search_document` and answered from memory while a toast blamed the
wrong cause.

That is fixed, and it is verified only by hand-tracing. Nothing stops a future edit
from collapsing or reordering the two blocks and reintroducing it. The failure is
quiet, model-specific, and would not surface in casual manual testing.

The wider context: `useChatEngine.js` is ~846 lines with **zero hook-level test
coverage**. This is not a gap carved out of a tested file — it is one narrow patch of
an already-untested surface. A `fetch`-mocked test of `sendMessage`'s retry ordering
is the single highest-value test this codebase could gain.

### 2. The context meter systematically under-reads

`estimateTokens` in the meter counts message contents plus the full pin preamble. It
does **not** count:

- **Tool definition schemas** (~500 estimated tokens). `getToolDefinitions` ships
  `search_document` / `web_search` / `current_time_date` in the `tools` field of the
  first request whenever a doc is indexed. This is *larger* than the ~342-token pin
  framing gap that was rated Important and fixed during the build. Deferred only
  because `ChatView` has no access to `toolCtx` — closing it needs new plumbing, not
  a one-line change.
- **Image attachments.** `buildApiMessage` ships `images: [base64]`, worth hundreds
  to thousands of real tokens on a vision model. Same defect class, gated on
  attachments.

The readout is prefixed `~` and is a guide, not a gauge — but the inconsistency is
recorded here so the next reviewer doesn't have to rediscover it.

### 3. Context presets top out at 32768; qwen3.5's native window is 262144

A user whose prompt exceeds 32k reads "Raise the context window in Inference
settings" and has no next step. One more preset (65536) closes the gap.

### 4. The thinking-level fallback repeats forever, in silence

The toast is capped once per app lifetime by `thinkLevelFallbackToastedRef`, and the
rejected level is never written back to settings. A model that rejects levels pays a
permanent extra round-trip on **every** message, with no further signal and a sidebar
that still claims the level is active. Calling `setInference({ think: 'on' })` on
fallback would make the setting self-heal and the UI tell the truth.

---

## Recorded, but genuinely fine

**The dropdown can lie about what's on the wire.** Each `<select>` falls back to
displaying `Auto` when the stored value isn't in its fixed option set. A hand-edited
`localStorage` entry of `numCtx: 65536` therefore shows **"Auto"** while the request
sends `num_ctx: 65536`. Not merely a display omission — the UI actively misreports.
Unreachable without hand-editing localStorage, so deferring is right, but it is a lie
rather than a gap.

**`s.think in THINK_WIRE` walks the prototype chain.** A `think` value of
`'constructor'` would resolve to `Object`. Harmless even if reached: `JSON.stringify`
omits function-valued keys entirely, so the body just loses `think`. `Object.hasOwn`
would tidy it. The same pattern exists at `App.jsx`'s `prev[selectedModel]` and is
equally unreachable.

**`ctxTokens` recomputes on every render**, including every streamed token. Sub-
millisecond at realistic sizes. Note that the obvious fix does **not** work:
`useMemo` keyed on `[messages, pins]` would not help during streaming, because
`setMessages` produces a new array reference per token, so the dependency array
changes every token anyway. It would only help composer keystrokes. Anyone
implementing the "obvious" memo would be writing a no-op.

---

## Verified against live Ollama 0.30.6 (not assumed)

Two premises that the review flagged as unverified were settled with real requests
against the running daemon:

| Question | Answer | Consequence |
|---|---|---|
| Does `num_predict` also yield `done_reason: "length"`? | **Yes** — `num_predict:10` returned `done_reason:"length"`, `eval_count:10` | The truncation toast genuinely needed to distinguish a reply cap from a full context. It does now. |
| Does `prompt_eval_count` report the full prompt on a warm turn, or only newly-evaluated tokens? | **Full prompt** — 64 for a ~64-token prompt whose first 23 tokens were already cached | The toast's arithmetic is trustworthy on every turn, not just the first. No further action. |

## Environmental, not code

`npm run lint` reports **4 pre-existing errors** on `development` — 3 in
`src/lib/chatTools/currentTimeDate.js`, 1 in `src/lib/chatTools/webSearch.js`, all
`no-unused-vars` from the recent chat-tools commits. Verified by stashing: identical
count with and without this feature's changes. Out of scope here, but the branch does
not lint clean.
