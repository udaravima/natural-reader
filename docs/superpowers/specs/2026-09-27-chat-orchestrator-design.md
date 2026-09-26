# Chat orchestrator foundation (C1)

**Date:** 2026-09-27
**Status:** Design, revised after review (2026-09-27: multi-turn attachments, `model_router.py`, stale-turn recovery, prefetch steering, idle timeout)
**Branch:** `development`
**Type:** backend (new `server/llm/` and `server/chat/` packages, migration `013`) + frontend (chat engine rewrite, Local mode removed) + docs
**Part of:** subsystem C, "agentic multi-document retrieval" (see the C0 spec, `2026-09-24-library-many-to-many-design.md`). C1 is the orchestrator foundation; C2 (agentic retrieval across the library) builds on it.
**Depends on:** nothing unmerged. A0 (projects, roles) is independent; its migration moves from `013` to `014`.
**Open-source rule:** this is deployed by many organizations. Nothing deployment-specific is hardcoded; every knob is an env var documented in `.env.example` with a safe default; docs use example.com.

## 1. Why

Today the **browser** runs the chat. `src/hooks/useChatEngine.js` (880 lines) builds the history, calls the model, parses Ollama's NDJSON, runs the tools (`src/lib/chatTools/`: `search_document` on the open document, `web_search`, `current_time_date`), sends one follow-up request, and uploads the whole conversation after every change. The server's `/v1/inference/chat` is a pass-through to one Ollama (`OLLAMA_URL`). A "Local" mode skips the server entirely.

That shape blocks three things:

1. **C2.** Multi-round, library-wide retrieval needs the loop next to the data and the access rules, not in a tab.
2. **Other providers.** vLLM, OpenRouter and LiteLLM speak OpenAI's API, not Ollama's. Provider quirks are spread through the browser today (audio is sent through Ollama's `images` field; think-level and no-tools retry chains key off Ollama's error codes).
3. **Cost.** Looking something up in the open document costs a whole extra model call: the model reads the prompt, decides to search, emits a tool call (for a thinking model, after a full reasoning pass), then reads everything again. The lookup itself is one embedding and one index query, milliseconds with no language model.

### Goals

- One **server-side orchestrator** runs every chat turn and streams **typed events**; the browser only renders them.
- A **provider layer**: the rest of the app speaks one internal format; one small adapter per provider API translates. A provider changing its API means editing its adapter, nothing else. Every server-side text-model call goes through it, including web search's page summaries.
- Two adapters ship working: **native Ollama** and **OpenAI-compatible** (vLLM, OpenRouter, LiteLLM).
- A **fast stage before the model** ("stage 0") that does the cheap, bounded work first: prefetching passages from the open document, putting the time in the prompt, ordering the prompt for cache reuse, fitting the history to the context window.
- The **server saves the conversation** as it streams.
- **Parity**: everything the chat does today still works, from the UI.
- **Local mode removed.**

### Non-goals (see §12)

Resumable streams, per-user API keys, an admin screen for providers, tool approval prompts, embeddings through the router, showing old attachments after reload, a per-reply length cap, library-wide retrieval (C2).

## 2. User journeys (each must work from the UI; checked in the running app, §11)

1. Send a message; the reply streams in, with thinking shown when the model produces it.
2. Hear the reply read aloud while it streams, or after it completes (both existing modes).
3. Stop a reply mid-stream; the partial reply stays, marked stopped.
4. Pin an excerpt; ask a question that needs it.
5. Attach an image; attach audio (to a model that accepts it). Ask a follow-up about it in the next turn, and again after reloading the chat: the model still sees it. Attach to a model that can't take it: a clear notice, no model call.
6. Ask about the open document: answered from prefetched passages (the chat shows "Used N passages from *name*"), or through the `search_document` tool when prefetch finds nothing strong enough.
7. Ask something that needs the web; ask the date.
8. Run out of the daily budget: the existing notice, no retry storm.
9. Pick a model from any configured provider; change per-model settings where that provider supports them.
10. Rename, reload, delete and export a chat. Reload while a reply is still generating: the partial reply shows "still generating…" and completes.

## 3. Architecture

```
browser ── POST /v1/chat/sessions/{id}/turns ──►  routers/chat_turns.py   (HTTP + SSE framing only)
   ▲                                                   │
   │  SSE: data: {type, seq, ...}                      ▼
   └────────────────────────────────────────  chat/orchestrator.run_turn()   → async iterator of events
                                                 │        │         │
                                        chat/context  chat/tools  chat/store     (stage 0, tools, persistence)
                                                 │        │
                                                 ▼        ▼
                                          llm/router ──► llm/providers/ollama.py
                                                     └─► llm/providers/openai_compat.py
```

Each unit has one job and a narrow interface:

| Unit | Does | Depends on |
|---|---|---|
| `server/llm/types.py` | The internal format: messages, tool specs, call settings, stream chunks, capabilities | nothing |
| `server/llm/providers/*.py` | Translate the internal format to one provider API and back | `types`, `httpx` |
| `server/llm/router.py` | Parse config, pick the adapter for a model id, list models and capabilities | `types`, providers |
| `server/chat/context.py` | Stage 0: build the model input cheaply | `embeddings`, doc search service, `store` |
| `server/chat/tools/` | Server-side tools, one file each | services |
| `server/chat/store.py` | All chat reads/writes (including attachment bytes), each on a short connection | `db` |
| `server/chat/orchestrator.py` | The turn loop; yields events | all of the above via their interfaces; `inference_budget` + `model_router` for the budget |
| `server/services/model_router.py` (existing, narrowed) | Non-chat inference config: embeddings, the daily budget, the timeout, the summary model; the no-config provider fallback's `OLLAMA_URL` / `INFERENCE_MODELS` | env |
| `server/routers/chat_turns.py` | Auth, refusals before streaming, SSE framing | `orchestrator` |

## 4. Provider layer (`server/llm/`)

### 4.1 Internal format (`types.py`)

- **Messages** in OpenAI chat shape (the shape LiteLLM also normalizes to): `role` (`system|user|assistant|tool`), `content`, optional `tool_calls` (`id`, `name`, `arguments` as a dict), `tool_call_id` on tool messages, and typed **attachments** (`kind: image|audio`, `mime`, `base64`).
- **Tool spec**: `name`, `description`, JSON-schema `parameters`.
- **Call settings**: `think` (`off|on|low|medium|high`, today's values), `num_ctx`, `keep_alive`, `temperature` and friends; adapters use what they support and ignore the rest.
- **Stream chunks** an adapter yields: `TextDelta`, `ReasoningDelta`, `ToolCall` (complete: the adapter assembles streamed argument fragments), `Usage(prompt_tokens, completion_tokens, estimated: bool)`, `Finish(reason: stop|length|tool_calls|error)`.
- **Capabilities** per model: `tools`, `thinking`, `vision`, `audio`, `context_window` (tokens, or null if unknown).

### 4.2 Adapters

| | `ollama` | `openai` (OpenAI-compatible) |
|---|---|---|
| Chat | native `POST /api/chat` (never `/api/generate`, which bypasses the model's chat template) | `POST /v1/chat/completions`, `stream: true`, `stream_options.include_usage: true` |
| Models | `GET /api/tags` | `GET /v1/models` |
| Capabilities | `POST /api/show` (`capabilities`, context length) | OpenRouter: model metadata (`supported_parameters`, `context_length`); vLLM: `max_model_len`, no capability list |
| Think | `think` field (today's mapping) | `reasoning_effort` (`low|medium|high`; `on` → `medium`; `off` → omitted) |
| `num_ctx`, `keep_alive` | passed through | ignored (no equivalent) |
| Images | `images: [base64]` | `content` part `image_url` with a data URL |
| Audio | `images: [base64]` (Ollama has no audio field; audio-capable models read the bytes as audio; today's convention) | `content` part `input_audio` |
| Tool calls | complete objects | argument deltas assembled by `index` into complete calls |
| Usage | final chunk `prompt_eval_count` / `eval_count` | final usage chunk; if absent, estimated (§5.6) |

**Unknown capabilities** (vLLM reports none): the adapter assumes the feature is present, and if the provider rejects a call because of it (HTTP 400 naming tools or reasoning), retries **once** without that feature and caches the finding for that model for the process lifetime. This is today's browser retry chain, moved into the one place that knows the provider.

**Errors:** connection failure → `ProviderUnavailable`; non-2xx → `ProviderError(status, safe_message)`. Response bodies are logged at WARNING **without** request headers; API keys never appear in logs or events.

### 4.3 Router and model ids

- Model ids carry the provider name: `local:qwen2.5`, `openrouter:anthropic/claude-sonnet-4`. The router splits at the **first** `:` (model names may contain `:` themselves, e.g. `qwen2.5:7b` → `local:qwen2.5:7b`).
- `router.list_models()` → `[{id, provider, name, capabilities}]`, providers in configured order, each filtered by its allow-list. One failing provider is logged and omitted; the others still list.
- `router.stream_chat(model_id, messages, tools, settings)` → async iterator of chunks.
- `router.complete(model_id, messages, settings)` → text, for non-streaming internal calls (web search summaries now; C2's document descriptions later).
- **Old sessions** stored plain model names (`qwen2.5`). A name with no known provider prefix resolves to the first provider of kind `ollama`.

### 4.4 Configuration (env, admin-wide)

| Env key | Meaning | Default |
|---|---|---|
| `INFERENCE_PROVIDERS` | comma list of provider names, in display order | unset → one provider `ollama` built from `OLLAMA_URL` + `INFERENCE_MODELS` (today's config keeps working unchanged) |
| `INFERENCE_<NAME>_KIND` | `ollama` \| `openai` | required per provider |
| `INFERENCE_<NAME>_URL` | base URL | required per provider |
| `INFERENCE_<NAME>_API_KEY` | bearer key, server-side only | empty (none) |
| `INFERENCE_<NAME>_MODELS` | allow-list | unset → all the provider lists |
| `SUMMARIZE_MODEL` | model id for web-search page summaries | today's chain unchanged: `SUMMARIZE_MODEL` → `WEB_SEARCH_SUMMARY_MODEL` → `llama3.2:3b`; a bare name resolves as in §4.3 |

`<NAME>` is upper-cased. An invalid provider (unknown kind, no URL) → WARNING, skipped; the server still starts. No usable provider → chat routes return 503 `no_providers`; the rest of the app runs. `INFERENCE_TIMEOUT_S` (existing, seconds) is an **idle** timeout: the longest the server waits for the next bytes from a provider, including the wait for the first token. It is not a cap on a reply's length: a thinking model that streams for ten minutes is fine; a provider that goes silent for `INFERENCE_TIMEOUT_S` is cut off. **This is today's behaviour** (it is httpx's `read` timeout in `routers/inference.py`, which counts time between chunks, despite the comment there saying it "spans whole streamed replies"); C1 keeps it and says so in `.env.example`.

`.env.example` gains a commented block for a second provider with placeholder values (`https://openrouter.example.com/api/v1`, `sk-your-key`), and says where keys go.

### 4.5 Other callers

- `web_search.summarize_one` calls `router.complete(SUMMARIZE_MODEL, …)` instead of Ollama's `/api/generate`.
- The admin console's settings view (`routers/admin.py`, today `ollama_url`) lists providers: name, kind, URL host, model count. Never keys.
- **Embeddings stay outside the router** (`services/embeddings.py`, `EMBEDDING_*`): their dimension (768) is baked into the schema, so switching embedding providers is a re-index decision, not a config change. Named gap, §12.

### 4.6 Relationship to `services/model_router.py`

`model_router.py` already parses the inference env and is imported by `admin.py`, `inference.py`, `web_search.py`, `embeddings.py` and `doc_pipeline.py`. It **stays**, narrowed to what is not chat:

| Owner | Config |
|---|---|
| `services/model_router.py` | `OLLAMA_URL` (embeddings, and the no-config provider), `EMBEDDING_MODEL`, `INFERENCE_TIMEOUT_S`, `INFERENCE_DAILY_TOKEN_BUDGET`, `SUMMARIZE_MODEL` (with its `WEB_SEARCH_SUMMARY_MODEL` fallback), `INFERENCE_MODELS` (the no-config provider's allow-list) |
| `llm/router.py` | `INFERENCE_PROVIDERS`, `INFERENCE_<NAME>_*`; builds the fallback provider from `model_router.get_config()` so `OLLAMA_URL` / `INFERENCE_MODELS` have one parser |

- `is_model_allowed` moves to `llm/router` (allow-lists are per provider); `inference.py`'s `/chat` goes away (§7.5) and `/models` asks `llm/router`.
- `admin.py`'s settings view reads providers from `llm/router` and the budget and embedding settings from `model_router`.
- **Trap:** a deployer who sets `INFERENCE_PROVIDERS` and removes `OLLAMA_URL` still has embeddings calling `http://localhost:11434` (its default). Documents then fail to index while chat works. `.env.example` and `docs/DEPLOYMENT.md` say `OLLAMA_URL` is still where embeddings run, and indexing's existing error names the URL it tried.

## 5. Orchestrator (`server/chat/`)

### 5.1 A turn

`run_turn(ctx)` where `ctx` = principal, session id, the new user message, model id, call settings, the open document id (optional), the browser's IANA timezone.

1. **Stage 0** builds the model input (§5.2) and may emit one `data-context` event.
2. **Tools on offer**: each tool's `available(ctx)` decides (§5.3); none are offered to a model whose capabilities say `tools: false`.
3. **Step** = one model call. Text and reasoning deltas stream out as events. The adapter hands back complete tool calls.
4. **No tool calls** → the turn is done.
5. **Tool calls** → run them in the order given; stream each result; append the assistant tool-call message and one `tool` message per call; next step.
6. **Cap**: after `CHAT_MAX_TOOL_ROUNDS` tool rounds (default `1`, today's behaviour; C2 raises it), one final step with **tools switched off**, so the turn always ends in an answer. Its `finish.reason` is `max-steps` only if that final step still produced no text.
7. A failing tool, or a tool name the model invented, returns `{"error": "..."}` to the model (recoverable), as the OpenAI Agents SDK does; it is not a turn error.

### 5.2 Stage 0: the cheap work first (`chat/context.py`)

A pipeline of **context stages**, each `async run(turn) -> ContextResult` (items to place in the prompt + an optional note for the `data-context` event). C1 ships four; C2 adds its own (library routing, keyword + vector search, reranking) to the same list.

1. **Prefetch from the open document.** When a document is open, indexed and readable by this user (checked with `readable_docs_where`, never trusting the id alone): embed the question (one embedding call), search that document (the same query `search_document` uses, moved into a service function both call), keep up to `CHAT_PREFETCH_K` passages scoring ≥ `CHAT_PREFETCH_MIN_SCORE`. If any qualify, they go into the prompt and the answer can come from **one** model call; the `search_document` tool stays on offer either way.
   - **Steering is load-bearing.** A model handed passages *and* a search tool often searches anyway, paying the embedding and the extra round. The passages go in under a fixed preamble: `Passages retrieved from "<doc name>" for this question. Answer from them when they are enough; call search_document only if they don't contain what you need.` Without that line the saving mostly disappears.
   - Every turn logs (DEBUG) whether the model called `search_document` after a prefetch hit; the §11 measurement reports that rate.
   - **Trap:** cosine similarity is **not** a calibrated probability; a "good" score depends on the embedding model. The default threshold is conservative and unmeasured; every decision (top score, kept count) is logged at DEBUG so a deployer can tune it on their own documents. `CHAT_PREFETCH_MIN_SCORE=1` disables prefetch.
   - Cost when it doesn't help: one embedding call per turn with an open document.
2. **Time in the prompt.** One line, e.g. `Current time: 2026-09-27 00:54 (Asia/Colombo)`, from the browser's timezone. The `current_time_date` tool is removed: a date question no longer costs a tool round.
3. **Prompt order for reuse.** Ollama, vLLM and hosted providers reuse computation for a prompt's unchanged **prefix**. So the order is: system prompt → tool specs → pins → history → **volatile context** (prefetched passages, time) → the new message.
   - **Counterintuitive:** the obvious place for the time is the top of the system prompt; that changes the prefix every turn and defeats reuse for the whole conversation.
   - **Deliberate change:** today pins sit right before the latest message (`useChatEngine.js:568`). Moving them after the system prompt keeps them in the reusable prefix. The risk is weaker attention to pins in long chats; parity journey 4 checks it, and pins move back if it regresses.
4. **History budget.** Estimate tokens (characters ÷ 4, a deliberately rough heuristic, plus `CHAT_ATTACHMENT_TOKEN_ESTIMATE` per image or audio attachment) and fit the prompt to `context_window − CHAT_REPLY_RESERVE_TOKENS` in two passes:
   1. **Drop old attachments first:** from the oldest message forward, replace an attachment's bytes with a text marker (`[image a.png from earlier; no longer attached]`). Images are the costliest part of a long chat, so this keeps more conversation for the same budget.
   2. **Then drop the oldest turns.**
   - Trimming works on attachment metadata (`size`, `kind`); `store` fetches bytes only for the attachments that survive, so a long chat with many images doesn't read them all from Postgres every turn.
   - **Trap:** the per-attachment estimate is a guess. Real image cost depends on the model (a vision encoder may use a few hundred to a few thousand tokens per image), so the default is deliberately high. Unknown window (e.g. Ollama with `num_ctx` unset and no `/api/show` length) → no trimming, today's behaviour. Trimming emits a `data-context` note. The system prompt, pins and the new message are never trimmed.

### 5.3 Tools (`chat/tools/`)

One file per tool: `name`, `spec`, `available(ctx)`, `async execute(args, ctx)`, `summarize(result)` (the compact `tool_calls` summary saved on the message, today's shape: `{name, arguments, result_summary}`).

| Tool | Available when | Executes |
|---|---|---|
| `search_document` | a document is open, indexed, and readable by this user | the shared search service (the same code as `POST /v1/docs/{id}/search`), `k` capped |
| `web_search` | SearXNG is configured | `services/web_search.web_search` (existing SSRF guards unchanged) |

Adding a tool = one file plus one registry line.

### 5.4 Failures

| When | What happens |
|---|---|
| Before anything streams: not signed in, no `chat` capability, session owned by someone else, turn already running, model not allowed, attachment the model can't take, budget already spent, no providers | a normal HTTP refusal (§7.2); the SPA shows its notice |
| Provider unreachable or errors mid-turn | `error` event (`provider_unavailable` / `provider_error`), partial reply saved with status `error`, stream closed |
| Budget runs out between steps | checked before **every** step; `error` event `budget_exhausted`, partial saved |
| Stop button or closed tab (client disconnect) | the upstream call is cancelled; partial saved with `aborted` |
| The provider sends nothing for `INFERENCE_TIMEOUT_S` seconds (idle, §4.4; a slow but streaming reply is never cut off) | `error` event `provider_timeout`, partial saved |

### 5.5 Concurrency and connections

- **One turn per session.** A turn claims the session with a conditional update (`active_turn_id` set only if null **or stale**); a second turn gets 409 `turn_in_progress`. The claim is released in a `finally`.
- **Heartbeat, not startup sweep.** A running turn refreshes `active_turn_heartbeat_at` every 15 s from a timer (not from tokens: a long prompt can take minutes before the first token). A claim whose heartbeat is older than 60 s belongs to a dead worker. Its `streaming` message is marked `aborted` and the claim is cleared.
   - This happens **lazily**: when a new turn tries to claim that session, and when `GET` one loads it. A startup pass does the same for every stale row.
   - **Why not A1's pattern:** A1's `recover_states` resets every mid-state row at startup, unconditionally. For chat that would kill turns another live worker is streaming, whenever one worker restarts (a crash respawn, or `--reload` in dev). The staleness test makes recovery safe with `WORKERS > 1`.
- **No pooled connection is held while streaming.** Replies can run for minutes; each write opens a short connection (the pattern today's usage accounting uses). The pool is never drained by slow models.
- Works with `WORKERS > 1`: nothing in the turn depends on process memory beyond the running request, and recovery only touches claims whose heartbeat has stopped.

### 5.6 Budget

Usage is recorded **per step** as soon as the step finishes, from the provider's counts. A provider that returns no usage is recorded from the character estimate with `estimated: true`, so a provider that omits counts can't be used for free. The daily budget check (`inference_budget.over_budget`) runs before each step.

## 6. Event protocol

**Framing:** server-sent events over the POST response: each event is one `data: {json}` line plus a blank line; the stream ends with `data: [DONE]`. Headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no` (nginx must not buffer). The shape follows the Vercel AI SDK UI message stream and AG-UI; names match Vercel's where they overlap.

Every event has `type` and `seq` (1, 2, 3… per turn; OpenAI Responses' `sequence_number`), which makes resumable streams (§12) addable without a format change.

| `type` | Fields | Order rule |
|---|---|---|
| `start` | `runId`, `sessionId`, `userMessageId`, `messageId` (the assistant message) | first |
| `data-context` | `items`: e.g. `{kind: "prefetch", docId, docName, count, topScore}`, `{kind: "trimmed", messages}` | before the first step, at most once |
| `start-step` | `step` (1-based) | opens each model call |
| `reasoning-start` / `reasoning-delta` / `reasoning-end` | `id`, `delta` | start → deltas → end, inside a step |
| `text-start` / `text-delta` / `text-end` | `id`, `delta` | same |
| `tool-input-available` | `toolCallId`, `toolName`, `input` | after the step's text ends |
| `tool-output-available` / `tool-output-error` | `toolCallId`, `output` / `errorText` | after its input |
| `finish-step` | `step`, `usage`, `finishReason` | closes each model call |
| `finish` | `usage` (turn total), `finishReason`: `stop` \| `length` \| `max-steps` \| `aborted` | last before `[DONE]` on success |
| `error` | `code`, `message` | replaces `finish` on failure |

`tool-input-delta` is deliberately omitted: nothing is shown until arguments are complete, and the adapter assembles them.

## 7. API and persistence

### 7.1 Endpoint

`POST /v1/chat/sessions/{session_id}/turns` (capability `chat`). Body:

```json
{
  "message": {"content": "…", "attachments": [{"kind": "image", "mime": "image/png", "base64": "…", "name": "a.png", "size": 12345}]},
  "model": "local:qwen2.5:7b",
  "settings": {"think": "off", "num_ctx": null, "keep_alive": null},
  "context": {"doc_id": "<sha256 or null>", "timezone": "Asia/Colombo"}
}
```

- The **history is not sent**; the server loads it, attachment bytes included (§7.3).
- The browser keeps generating session ids (`s-…`); the first turn creates the session for the caller. An id owned by another user → 404 (nothing revealed).
- Size limits: the body is capped (`CHAT_MAX_REQUEST_MB`, default 25) so an attachment can't exhaust memory.

### 7.2 Refusals (before streaming; `refusal()` shape; mapped in `src/lib/apiErrors.js`)

| Status | `error` | Notice |
|---|---|---|
| 404 | `not_found` | "This chat doesn't exist or you don't have access." |
| 409 | `turn_in_progress` | "A reply is still being written in this chat." |
| 413 | `too_large` | "Attachments too large (limit N MB)." |
| 422 | `model_not_allowed` | "That model isn't available on this server." |
| 422 | `attachment_unsupported` | "This model can't read <images/audio>." |
| 429 | `budget_exhausted` (+ `remaining_tokens`, `reset_at`) | today's budget notice |
| 503 | `no_providers` | "No model provider is configured." |

### 7.3 What is written, when

1. **Turn start** (committed before the first byte): the session (created if new; `model` updated to this turn's model), the user message (metadata in `attachments`, bytes in `chat_attachments`), and the assistant message with `status='streaming'`.
   - **Why store the bytes (a change from today):** a model is stateless per request, so a follow-up about an image only works if the image is sent again. Today that works inside a live tab, because the browser keeps the bytes in memory and re-sends them, but it breaks after a reload, because the saved session has metadata only. With the history on the server, storing nothing would break follow-ups every time. Storing the bytes keeps the live-tab behaviour and also fixes the reload case (journey 5).
   - **Cost:** attachments now occupy Postgres, up to `CHAT_MAX_REQUEST_MB` per turn. Deleting a chat deletes them (`ON DELETE CASCADE`). Export stays metadata-only.
2. **During a step**: the partial `content` and `thinking` are flushed at most every 2 s, on a short connection, so a reload mid-reply (journey 10) shows the text so far. This applies even to a single-step reply with no tools.
3. **After each step**: the assistant message's `content`, `thinking`, `tool_calls` (compact summaries), `doc_context` (stage 0's prefetch record).
4. **End**: `status` (`complete` \| `aborted` \| `error`), `finish_reason`, `stats` (usage, timing, model id). Chat log entries (`budget`, `aborted`, …) are written to `chat_events` by the server.

### 7.4 Migration `013_chat_orchestrator.sql`

- `chat_messages`: `status TEXT NOT NULL DEFAULT 'complete'` (CHECK `streaming|complete|aborted|error`), `finish_reason TEXT`, `model TEXT`. Existing rows are complete.
- `chat_sessions`: `active_turn_id TEXT`, `active_turn_heartbeat_at TIMESTAMPTZ` (§5.5).
- New `chat_attachments`: `id`, `message_id` → `chat_messages(id) ON DELETE CASCADE`, `ordinal`, `kind`, `mime`, `name`, `size`, `data BYTEA`. Existing messages have no rows (their bytes were never stored); they keep showing metadata chips.
- Additive only; no data rewritten. Scratch-DB migration test like `test_migration_011.py`.

### 7.5 Existing endpoints

- `PUT /v1/chat/sessions/{id}` (whole-record save) is **removed** once the SPA stops calling it. `PATCH` (exists) covers title and pins; the plan verifies its fields cover what the SPA edits.
- `GET` list / `GET` one / `DELETE` stay. `GET` one returns `status` per message (for "still generating…").
- `POST /v1/inference/chat` (the raw pass-through) is **removed**, with Local mode. Only the SPA calls it in this repo (the extension doesn't), but it is a public endpoint of an OSS server, so `CHANGELOG.md` lists both removals (`PUT` and `/v1/inference/chat`) under **Upgrade notes** as breaking, naming the replacement (`POST …/turns`).
- `GET /v1/inference/models` stays; returns `[{id, provider, name, capabilities}]` plus today's `budget`.

## 8. Frontend

- **`useChatEngine.js` shrinks** to session state and the TTS queue. New pure modules:
  - `src/lib/chatStream.js`: POST + SSE reader (handles partial lines, `[DONE]`, abort).
  - `src/lib/chatEvents.js`: a pure reducer, events → message state (text, thinking, tool panel entries, context note, status). Unit-tested without React.
  - Speak-as-it-streams keeps its sentence flushing, fed from `text-delta`.
- **Deleted from the browser:** `src/lib/chatTools/`, the history builder's model-message assembly and pin preamble (`src/hooks/chatHistory.js`), the tools and think-level retry chains, NDJSON parsing, the whole-record `PUT` saves.
- **Local mode removed:** `src/components/chat/InferenceSourceSelect.jsx`; the Ollama host/port settings; the `local` paths in `src/lib/chatTransport.js`; their branches in `App.jsx`, `ChatSidebar.jsx`, `SettingsPage.jsx`. A saved `inferenceSource: 'local'` is ignored (server mode).
- **Rendering:** thinking in the existing collapsible; tool calls in the existing lookup panel; `data-context` as one muted line ("Used 4 passages from *Thesis.pdf*", "Trimmed 6 older messages to fit"); `finishReason` `length` → today's truncation notice, `aborted` → "stopped", `max-steps` → a short note; a message with `status='streaming'` after reload → "still generating…", re-fetching the session every few seconds until it settles.
- **Model picker:** grouped by provider, capability badges; per-model settings (`InferenceRow`) show only the knobs that provider supports (think, context size, keep-alive).
- **Refusals** go through `describeRefusal` with the §7.2 codes.

## 9. Configuration summary (`.env.example`)

| Key | Default | Notes |
|---|---|---|
| `INFERENCE_PROVIDERS` + `INFERENCE_<NAME>_*` | unset → today's single Ollama | §4.4 |
| `CHAT_MAX_TOOL_ROUNDS` | `1` | tool rounds before the final tools-off step |
| `CHAT_PREFETCH_MIN_SCORE` | `0.75` (unmeasured; tune from DEBUG logs) | `1` disables prefetch |
| `CHAT_PREFETCH_K` | `4` | passages at most |
| `CHAT_REPLY_RESERVE_TOKENS` | `2048` | kept free for the reply when trimming history |
| `CHAT_ATTACHMENT_TOKEN_ESTIMATE` | `1500` (unmeasured, deliberately high) | tokens counted per image/audio attachment when trimming |
| `CHAT_MAX_REQUEST_MB` | `25` | turn body cap |
| `INFERENCE_TIMEOUT_S` | `300` (existing, seconds) | idle: longest silence from a provider, first token included; not a reply-length cap |

Removed: the SPA's Ollama host/port settings. `OLLAMA_URL` / `INFERENCE_MODELS` stay as the no-config default, and `OLLAMA_URL` stays where embeddings run (§4.6).

**Deployment note (`docs/DEPLOYMENT.md`):** replies now arrive as `text/event-stream`. nginx honours `X-Accel-Buffering: no`, and the sample config already has `proxy_buffering off`. Some CDNs and tunnels (Cloudflare among them) may buffer event streams regardless. The symptom is a reply that appears all at once after a long pause. The docs name the symptom and tell deployers to turn off buffering for `/v1/chat/` in their proxy.

## 10. Logging

Builds on `server/logging_config.py` and the `server.audit` logger.

| Level | Events |
|---|---|
| DEBUG | stage 0 decisions (top score, kept, trimmed count), capability probes, retry-without-feature |
| INFO | turn start/finish: user id, session id, model id, steps, tokens, finish reason, duration |
| WARNING | provider errors (body, no headers), invalid provider config, usage estimated, recovered stale turns |
| ERROR | unexpected orchestrator failures |

No message text, prompts, attachment bytes or API keys at INFO or above.

## 11. Testing

- **Adapters:** recorded provider streams (fixtures) through each adapter: text, reasoning, complete vs fragmented tool calls, missing usage, mid-stream error, audio/image mapping, the 400 → retry-without-feature path.
- **Router:** prefix parsing (including `local:qwen2.5:7b`), allow-lists, bad-provider skip, the no-config fallback, bare old model names.
- **Orchestrator** with a fake provider: plain text; one tool round; cap then tools-off final step; failing tool; unknown tool; budget out between steps; client disconnect mid-step; model without tools; prefetch hit / miss / unreadable document; history trimming. Each asserts the exact event sequence and what was saved.
- **Persistence:** turn claim and 409; a fresh heartbeat blocks takeover, a stale one allows it (two claimers, no startup); the startup pass leaves fresh claims alone; attachment bytes stored, rehydrated into the next turn's history, and deleted with the chat; partial content visible mid-step; another user's session id → 404.
- **Timeout:** a fake provider that streams slowly past `INFERENCE_TIMEOUT_S` in total is not cut off; one that goes silent is.
- **Trimming:** old attachments replaced by markers before any turn is dropped.
- **Contract fixtures:** backend tests write the event sequences they assert to `server/tests/fixtures/chat_events/*.jsonl`; frontend tests feed the same files through `chatStream` + `chatEvents`. A format change on either side fails a test (the pattern A1 used for page layout).
- **Two adapters without a paid key:** Ollama serves an OpenAI-compatible API at `/v1`; the running-app walk configures it a second time as `kind=openai`. Not a full stand-in for OpenRouter/vLLM quirks, but it exercises the translation.
- **Running-app walk (required before "done"):** journeys 1–10 against local Ollama; journeys 1, 3, 5, 6 again through the OpenAI-compatible adapter. For each step, the control used.
- **Measure stage 0:** time and tokens for a document question with prefetch on vs off (`CHAT_PREFETCH_MIN_SCORE=1`) on the deployer's hardware, plus how often the model still called `search_document` after a prefetch hit (§5.2.1). Report the numbers, don't assume them.

## 12. Non-goals / deferred

- **Resumable streams** (LibreChat, Vercel `resumable-stream`): generation that outlives the connection and replays missed events. Needs shared storage (Redis) once `WORKERS > 1`. The `seq` field keeps the door open.
- **Per-user API keys (BYOK)** and an **admin screen for providers**: env config only in C1.
- **Tool approval** ("ask before web search").
- **Embeddings through the router**: the 768-dimension schema makes it a re-index decision.
- **A "System One" decider adapter** (Jev-style typed, calibrated decisions): Jev is closed early access and cloud-only, so not a dependency; the stage-0 pipeline is where such a model would plug in if a self-hostable one appears.
- **Showing old attachments after reload:** the bytes are stored (§7.3) and the model sees them, but the chat still shows metadata chips for them after a reload; serving them back to the browser needs its own endpoint and access check.
- **A per-reply length cap:** the budget is checked between steps, so one runaway step (a model stuck repeating) can overshoot the day's budget by that step's output. The Stop button and the idle timeout are the bounds in C1.
- **Library-wide retrieval, document descriptions, multi-round budgets**: C2.

## 13. Risks

| Risk | Mitigation |
|---|---|
| Parity regressions in a large SPA rewrite | contract fixtures; the running-app walk covers every existing chat journey |
| Pins lose salience after moving up the prompt (§5.2) | journey 4 checks it; revert placement if it regresses |
| Prefetch threshold wrong for a deployment | conservative default, DEBUG logging, `=1` disables, tool fallback always on offer |
| A provider streams tool calls in an unexpected shape | adapter fixtures per provider; recoverable tool errors |
| Long replies hold resources | no pooled connection held while streaming; per-step timeout |
| Provider omits usage, bypassing the budget | estimated usage recorded and flagged |
| Removing Local mode strands a user with no server Ollama | the no-config default is exactly today's server Ollama; upgrade notes say so |
| External callers of `/v1/inference/chat` or `PUT` sessions break | listed as breaking in CHANGELOG upgrade notes with the replacement |
| Attachment bytes grow the database | capped per turn; deleted with the chat; named in DEPLOYMENT.md |
| A worker restart kills other workers' live turns | recovery only takes claims whose heartbeat is > 60 s old (§5.5) |
| Deployer drops `OLLAMA_URL` after configuring providers; indexing breaks | §4.6 trap documented in `.env.example` and DEPLOYMENT.md |
