# Changelog

All notable changes to this project will be documented in this file.

## [1.6.0] - 2026-05-16

### Added
- **Document Chat & RAG end-to-end** — five-PR arc that lets the local LLM see, search, and autonomously query the document you're reading. Full walkthrough at [docs/CHAT_WITH_PDF.md](docs/CHAT_WITH_PDF.md). Headline bits below.
- **Postgres + pgvector backend (PR 1)** — `docker-compose.yml` brings up `pgvector/pgvector:pg16` on host port 5433. New `server/db.py` runs an async `psycopg` pool with retry/backoff, applies file-based SQL migrations (`server/sql/001_init.sql`, `002_tool_calls.sql`) on startup, and resets any stale `state='indexing'` rows to `chunks_uploaded` so a crash mid-job is recoverable. Pool registers the pgvector codec per-connection so Python lists bind as `vector(...)`.
- **Chat sessions move to Postgres (PR 1)** — five new tables (`chat_sessions`, `chat_messages`, `chat_events`, `documents`, `doc_chunks`) and a `schema_migrations` version table. New endpoints: `GET/PUT/PATCH/DELETE /v1/chat/sessions/{id}`, `GET /v1/chat/sessions`. New `src/lib/sessionStore.js` abstraction merges Postgres (new) and IndexedDB (legacy read-only) under one API; writes always target Postgres, and editing a legacy IDB session forks to a fresh pg session leaving the original intact. Legacy IDB chats show with a small amber **LOCAL** badge in the sidebar.
- **Per-message doc context (PR 2)** — new toolbar **Ask page** button (sends the current page text, capped ~8000 chars) and new floating **Ask AI** action paired with the existing **Read Selection** button (sends the highlighted snippet). Both prefill a removable `DocContextChip` above the chat input and land in `useChatEngine.sendMessage(text, attachments, docContext)` as a synthetic system preamble. The chip persists on user messages so reloading a session re-renders the chip. Lazy sha256 of file bytes via Web Crypto (`src/utils/docHash.js`) gives every doc a stable `doc_id` independent of filename.
- **Manual document indexing (PR 3)** — `IndexButton` in the PDF toolbar runs `POST /v1/docs` (register) → `POST /v1/docs/{id}/chunks` (batches of 50) per file type: one chunk per PDF page, one per Markdown block (code blocks skipped), one per TXT pseudo-page. Idempotent on `(doc_id, text_hash)` so re-indexing is safe. New `extractAllChunks()` helper in `usePdfEngine.js` handles all three formats.
- **Embeddings + semantic retrieval (PR 4)** — `server/services/embeddings.py` wraps Ollama's `/api/embeddings` with `Semaphore(4)` concurrency and a 768-dim assertion (hard-locked to `nomic-embed-text` for v1). `POST /v1/docs/{id}/index` runs a FastAPI `BackgroundTasks` job under a per-doc `asyncio.Lock`, embeds chunks 16 at a time, and flips the doc state to `indexed` or `failed`. `POST /v1/docs/{id}/search` returns top-k chunks by cosine similarity (HNSW). Frontend polls status every 2 s (300-poll / 10-min cap). New **"Use whole document"** checkbox on the doc-context chip folds top-3 retrieved chunks into a second system preamble with bracketed `[1] (page 5) …` citations the model is told to use.
- **Autonomous tool calling (PR 5)** — once a doc is indexed and a tools-capable chat model is selected, the model gets a `search_document` tool in every `/api/chat` request and can decide on its own whether to invoke it. Tool-call loop is frontend-orchestrated (no backend changes): Ollama returns `message.tool_calls`, the frontend executes against `/v1/docs/{id}/search`, then re-POSTs `/api/chat` (without `tools`) so the model produces the final text answer. Single round-trip cap to prevent loops. Models without tool support silently ignore the field. Thinking + tools fallback: on a 4xx from the initial request, retry once without `tools` (keep thinking on) and toast once per session. A new transient `toolStatus` indicator ("🔄 Searching document…") shows on the streaming bubble, and a collapsible **🔎 ToolCallsDisclosure** mirrors the existing stats disclosure.
- **Pluggable tool registry** — `src/lib/chatTools/` houses one tool per file; each exports `{name, definition, when(ctx), execute(args, ctx)}` and the registry filters by `when(ctx)` on every send. Adding `web_search`, `read_url`, etc. later is one new file + one registry-index line. `src/lib/chatTools/_example.js` documents the shape end-to-end.
- **Persisted tool calls** — new migration `server/sql/002_tool_calls.sql` adds a `tool_calls JSONB` column on `chat_messages`. Each invocation is stored as `[{name, arguments, result_summary}]` so the 🔎 disclosure re-renders correctly on session reload.
- **docker-compose service** for Postgres so first-run is a single command.

### Changed
- **Python deps** — adds `psycopg[binary,pool]>=3.2`, `pgvector>=0.3`, `httpx>=0.27` to `requirements.txt`. Kokoro / TTS deps unchanged.
- **`useChatEngine` API** — now accepts `apiHost`, `apiPort`, `currentDocId`, `currentDocIndexState`. `sendMessage` gained a third `docContext` argument. Backward-compatible — old callers pass `null` and get pre-1.6 behavior.
- **`server/app.py`** — mounts two new routers (`chat_sessions`, `docs`), starts/stops the embedding HTTP client + DB pool around startup/shutdown.

### Caveats
- **Embedding dimension is hard-locked at 768** (`nomic-embed-text`). Switching models requires dropping `doc_chunks.embedding` and re-indexing every document. Per-model column sharding is on the roadmap; not in scope for 1.6.
- **Tool calling requires a tools-capable Ollama model** (qwen2.5, llama3.1+, mistral, gemma2, …). Other models still chat fine — the `tools` field is silently dropped server-side.
- **Backend down** still doesn't break chat: Ollama is independent, so streaming + TTS keep working. Only Postgres-dependent calls (session save, document register/search) return 503, with a one-time toast on the first failure.
- **Multi-iteration tool calls** are not supported — PR 5 caps at a single round-trip to prevent runaway loops. Multi-step agent workflows need budget/loop controls before they're safe.
- **No-chip retrieval** isn't wired — the "Use whole document" toggle only appears when a chip is attached. Autonomous tool calling (PR 5) covers the same ground without manual setup; a manual no-chip toggle stays on the deferred list.

## [1.5.1] - 2026-05-10

### Added
- **Markdown (`.md`) reader support** — drop or pick a `.md` / `.markdown` file and it joins the existing library alongside PDFs and `.txt` files. Renders properly via `react-markdown` + `remark-gfm` (headings, lists, code blocks, tables, blockquotes) instead of showing raw `**bold**` / `# heading` markup.
- **Paragraph-level highlight while reading** — the block (paragraph, list, heading, table, blockquote) containing the currently-spoken sentence gets a subtle background tint and auto-scrolls into view. Pagination is paragraph-aware: pages cap at ~40 sentences but always end on a block boundary. Block detection is AST-driven (`mdast-util-from-markdown` + GFM) so the highlight aligns with what react-markdown actually renders, including non-blank-separated headings and HRs. Offset-based block lookup keeps alignment correct under arbitrary nesting (e.g. nested lists inside list items).
- **Markdown-aware TTS for the reader** — sentences are passed through `mdast-util-to-string` per block before being handed to the TTS engine, so `**bold**` reads as "bold" (not "star star bold star star"), `[link text](url)` reads as "link text", and fenced code blocks are skipped entirely.
- **Library tile for markdown** — `.md` files in the recents list show a `FileCode` icon with a purple gradient to distinguish them from `.txt` (teal) and `.pdf` (blue).

## [1.5.0] - 2026-05-10

### Added
- **Image attachments in chat** — paperclip button, drag & drop onto the chat view, and `Ctrl + V` paste from clipboard. Files preview as thumbnails above the prompt and inside user bubbles after sending; attached images are sent to Ollama via the per-message `images: [base64]` field. Size cap 10 MB per image.
- **Same-origin / reverse-proxy support** — leaving the **Host** field blank in either sidebar now issues requests as relative URLs (`/v1/synthesize`, `/api/chat`, …) so an nginx/Caddy front can serve the frontend and proxy both backends from a single hostname. New `src/utils/url.js` helper centralizes URL construction and accepts bare hosts, `http(s)://`-prefixed hosts, or no host at all.
- **Model response stats footer** — every assistant bubble has a small collapsible `⚡ N tok · X.X s · Y.Y tok/s` line. Expanded view shows model name, total time, load time, prompt eval (tokens + time), generation (tokens + time + throughput), and `done_reason` when non-trivial. Pulled directly from Ollama's final NDJSON chunk.
- **Stick-to-bottom scroll** — chat list auto-tails new tokens only when the user is already near the bottom (within 80 px). Scrolling up pauses the auto-tail so you can read history during a long stream; returning to the bottom resumes it.
- **Reverse-proxy production deployment docs** — README now includes a worked nginx example covering both `/v1/*` and `/api/*`, plus notes on `proxy_buffering off` for streaming, Ollama Host-header allowlist (override or `OLLAMA_ORIGINS`), and serving `.mjs` files with the right MIME type so Firefox can load PDF.js's worker.
- **Security & Hardening section in README** documenting the threat model for client-side API calls and ready-to-paste recipes for HTTP Basic Auth, IP allowlist, rate limiting, and tighter Kokoro CORS. The sample nginx config (`docs/chat.oraian.net.sample`) ships the same recipes as commented-out blocks so applying them is copy/paste.

### Changed
- **Voice recording / audio attachments paused** — Ollama's image projector chokes on non-image bytes (`unknown data type` on raw audio), and there's no native audio path in the daemon for this gemma4 build. The mic button + `<VoiceRecorder>` component were removed from the visible UI, and audio files are filtered out of drop / paste with a friendly toast. Component files and utilities remain on disk so re-enabling is a small re-toggle once a transcription step (Whisper) lands.

### Fixed
- **Chat bubble horizontal stretch** — the stats footer's grid `1fr` second column was forcing the bubble's flex column to expand to its `max-w-[85%]`. Switched to `max-content` for both columns and added `w-fit` to the disclosure wrapper, so bubbles size to their actual content again.

## [1.4.0] - 2026-05-09

### Added
- **Local AI Chat (Ollama integration)** — new top-level Reader↔Chat toggle in the header. Streams responses from a local Ollama server (`/api/chat`), with model picker auto-populated from `/api/tags` and configurable host/port (default `localhost:11434`).
- **Plain text (.txt) file support** — drag, drop, or pick a `.txt` file to read it the same way as PDFs. Files are paginated into pseudo-pages of ~40 sentences and use the existing TTS, sentence highlight, library, and resume-progress flow. New `TextPageRenderer` component; `fileType` discriminator on library records.
- **Multi-session chat memory** — every chat is auto-saved to IndexedDB (new `chat_sessions` store, DB migrated to v3). Sessions list in the sidebar with switch / rename / delete; auto-named from the first prompt; LRU-capped at 50 sessions.
- **Per-session event log** — each session records `sent` / `received` / `aborted` / `error` events with timestamps. Viewable as a collapsible "Session log" section in the sidebar.
- **Per-message read-aloud controls** — every assistant bubble has a "Read aloud" button to start/stop TTS for that message specifically. Works with auto-TTS off; you can re-read finished messages later.
- **Per-message copy button** — one-click copy of any chat message (user or assistant) to clipboard, with toast confirmation.
- **Markdown rendering in chat** — assistant replies render as proper Markdown (lists, code blocks, tables, headings, links) via `react-markdown` + `remark-gfm`. User messages stay plain.
- **Reasoning model support** — toggle "Enable thinking" in the chat sidebar to send `think: true` to Ollama and surface `message.thinking` traces (for deepseek-r1, qwen3-thinking, gpt-oss, etc.). Thinking renders in a collapsible disclosure that auto-expands while streaming and auto-collapses once the answer arrives.
- **Streaming vs after-complete TTS modes** — toggle between reading sentences as they stream in, or waiting for the full reply. Mode is locked at message-start so toggling mid-stream applies to the next message.
- **Markdown-aware TTS** — chat speech strips Markdown markup (`**`, `__`, `#`, fenced code, link URLs) before synthesis, so the audio reads visible text only — no more "star star bold star star".
- **Collapsible chat sidebar sections** — Settings, Sessions, Conversation, and Session log are independently collapsible with their own scroll containers, plus an outer scroll for the whole sidebar.

### Changed
- **TTS engine refactor** — extracted reusable `synthesizeText`, `playChatUrl`, `playChatSpeech`, and `stopChatPlayback` helpers; the chat side composes them on its own audio channel (`chatAudioRef`) so chat playback and reader playback can never collide. Reader playback is now gated by an `enabled` prop and pauses automatically when switching to chat mode.
- **Bounded chat TTS prefetch** — chat synthesis now mirrors the reader's pattern: only the current sentence plus the next two are in flight at any moment. Eliminates the cascading per-sentence timeouts that previously caused chat TTS to stop after a few sentences on long replies.
- **Centralized toast + IndexedDB transactions** — single transaction in `saveBook`/`updateBookMeta`, fewer round-trips, race-condition-free. Toast helper consolidated in `App.jsx`.

### Fixed
- **Stop button for chat TTS** — clicking Stop on a chat message now silences audio immediately, drops queued sentences, and resets the playback chain (previously the in-flight sentence and queued ones kept playing).
- **Long chat replies cutting off mid-message** — the prefetch bound (above) prevents Kokoro's serialized inference from timing out queued sentences and silently skipping them. Replies after horizontal rules and other mid-message structure now read all the way through.

## [1.3.0] - 2026-03-05

### Added
- Health check endpoint (`GET /v1/health`) for frontend availability detection.
- Configurable API request timeouts and unlimited batch timeout toggle.
- Collapsible settings section in the sidebar.
- Duplicate synthesis request prevention (deduplicates in-flight fetches).
- Backend auto-failover with toast notification when Kokoro is unreachable.

### Changed
- **Major refactor**: Frontend split into modular React hooks (`usePdfEngine`, `useTtsEngine`, `usePersistedState`, `useKeyboardShortcuts`, `useMobileDetect`, `useTheme`).
- **Major refactor**: Backend restructured from single `server.py` into a `server/` package (`app.py`, `endpoints.py`, `model.py`, `schemas.py`).
- Entry point changed from `python server.py` to `python run.py`.
- Removed verbose TTS endpoint logging for cleaner server output.
- State management optimized — removed unused variables and refined `useState`/`useEffect` patterns.

### Fixed
- **TTS auto-continue on page change** — reading now automatically continues on the next page without needing to pause and replay. Fixed race condition where the playback loop would re-run with stale text from the previous page.
- **StrictMode double-run** — playback index update reordered to prevent React StrictMode from skipping sentences.
- Improved `useEffect` dependency linting across hooks.

## [1.2.0] - 2026-02-02

### Added
- GPU support for Kokoro TTS (CUDA, OpenVINO for Intel Arc/NPU).
- "Continue from here" and "Selective Read" features in PDF Reader.
- Batch synthesis endpoint (`/v1/batch_synthesize`) for merging audio.

### Fixed
- UI overlaps on mobile devices.
- PDF frame resizing and header layout issues.

## [0.1.0] - 2026-01-31

### Added
- Initial release
