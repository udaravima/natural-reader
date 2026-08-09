# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- **Pinned chat context:** "Ask AI" on a selection (or "Ask page") now *pins* the
  excerpt to the conversation — it stays attached across follow-ups instead of
  being sent once. Multiple pins accumulate as removable chips, are saved with the
  chat session (restored on reload), and are bounded (max 6 pins / 12k chars).
  Whole-document breadth continues to come from semantic retrieval.

- **Per-model Ollama inference settings.** A new `Inference` block in the chat sidebar
  exposes four request parameters — **context window** (`options.num_ctx`), **keep-alive**
  (`keep_alive`, including *Always* = `-1`), **thinking level** (`think`), and **max reply
  tokens** (`options.num_predict`) — stored per model name, because a 9.7B and a 3B want
  different context sizes on the same machine. The thinking control replaces the old
  boolean toggle with five states: `Off`/`On` send booleans (identical to previous
  behaviour, and the old `enableThinking` preference migrates into it automatically),
  while `Low`/`Medium`/`High` send Ollama's graduated levels. A model that accepts the
  boolean but rejects a level is retried once with plain thinking rather than losing it.
  Every setting defaults to **unset**, which omits the key from the request entirely — so
  installing this changes nothing until you opt in. That is deliberate: Ollama already
  sizes context from available memory (4k under 24 GB, 32k at 24–48 GB, 256k above), and a
  hardcoded default would *downgrade* well-equipped machines. All of it is per-request, so
  no `OLLAMA_CONTEXT_LENGTH`, no systemd override, and no daemon restart is needed.
  ([src/hooks/inference.js](src/hooks/inference.js))
- **Context meter + truncation warning.** A `~3.3k / 16k ctx` estimate sits above the
  composer (amber past 75% of the window; no denominator when the window is left on Auto),
  and a cut-off reply now raises a toast naming the real token counts.

### Fixed
- **Silently truncated replies.** A reply could stop mid-sentence with the only evidence
  being `done_reason: length` inside a collapsed stats disclosure. The cause was that the
  chat engine never sent an `options` object at all, so Ollama applied its own default
  context window — a real case hit prompt 3317 + generation 779 = exactly 4096. The window
  is now configurable and exhaustion is surfaced, on both the initial response and the
  tool follow-up (whose history is strictly larger, and so more likely to overflow).
- Ask-AI context is no longer stranded at the front of a multi-turn chat (the model
  previously reported "no content attached" on follow-up questions).

## [1.8.0] - 2026-07-28

### Added
- **Chrome "Read Aloud" extension (`extension/`, v0.1).** A Manifest V3 browser extension that reads the selected text or the whole page aloud on any site through the same local Kokoro TTS backend the app uses (`http://localhost:8000`). Vanilla JS, **no build step** — load `extension/` unpacked. A floating Shadow-DOM toolbar (play/pause, stop, seek, voice, speed, drag, close) is injected on demand via the right-click menu or the popup; whole-page reads are split into ~30-sentence chunks and played back-to-back with prefetch-one-ahead, so audio starts after the first chunk instead of the whole page. Includes a popup (read buttons + backend status dot) and an options page (default voice, speed, backend URL, test-connection). Loopback-only host permissions; page/selection text is rendered `textContent`-only (XSS-safe); the backend is unchanged. Pure `shared/` modules are unit-tested (20 tests); built TDD via subagent-driven development. See [extension/README.md](extension/README.md); design spec + plan in [docs/superpowers/](docs/superpowers/).
- **Markdown folder workspace.** Open a folder (File System Access API, with a `webkitdirectory` picker fallback) so a Markdown/text document's cross-file links actually navigate: relative `.md`/`.txt` links open the target in the reader, `#heading` anchors scroll to the heading (via `rehype-slug` ids), and relative images render from the folder. Adds Back/Forward history with a header workspace badge; workspace state is persisted in IndexedDB (new v4 store) and restored on load (File System Access re-permission prompt, or a re-pick for the snapshot fallback). Link hrefs pass through a safe-scheme allowlist that rejects `javascript:` / `data:` / other dangerous schemes. New Vitest + Testing Library test infrastructure underpins it. ([src/lib/workspace.js](src/lib/workspace.js), [src/lib/WorkspaceContext.jsx](src/lib/WorkspaceContext.jsx), [src/components/WorkspaceLink.jsx](src/components/WorkspaceLink.jsx), [src/utils/resolvePath.js](src/utils/resolvePath.js))

### Changed
- **`startup.sh` modernised into a real lifecycle script.** Rewritten with `set -euo pipefail`, container-engine auto-detection (docker / podman), and pre-flight **version checks** (Node `≥20.19` or `≥22.12`, Python `3.10–3.13`) before it runs anything. Model downloads are now **atomic and idempotent** — the Kokoro ONNX model and voice pack are fetched to a `.partial` temp and moved into place only on success, and already-present files are skipped instead of re-downloaded. `up` starts the `run.py` backend under a pidfile; `down` — and Ctrl-C during `up` — **SIGTERM** the backend and bring the containers down cleanly via a trap (engine-aware, so nothing is left running). The README gained a tiered **Software Requirements** section.

### Caveats / known limitations
- **Extension site compatibility.** The extension works on ordinary pages (Wikipedia, blogs, docs) but can fail on sites with a **strict Content-Security-Policy** (e.g. `claude.ai`) — the content script's dynamic module import gets blocked and a small "could not start on this page" notice shows instead of the toolbar. It also only runs in the **top page**, so text inside a **sandboxed iframe** (e.g. an embedded artifact) reports "nothing to read", and the localhost fetch from an HTTPS page may be gated by Chrome's **Private Network Access** depending on Chrome version. These are documented in [extension/README.md](extension/README.md#site-compatibility-known) and slated for a follow-up (route synthesis through the service worker).
- **No highlight-follow** in the extension (no word-by-word highlight), and **loopback backend only** (`localhost` / `127.0.0.1`) — remote/hosted backends are not yet supported.

## [1.7.3] - 2026-05-23

### Fixed
- **PDF canvas went blank after toggling Reader ↔ Chat.** Toggling to Chat unmounts `PdfViewer`; toggling back mounts a fresh `<canvas>` DOM node, but the render `useEffect` only re-fired when `[pdfDoc, currentPage, scale, fileType]` changed — none of which moved on a view-mode toggle — so the new canvas stayed empty until the user manually stepped the page. Fixed by exposing `canvasRef` from `usePdfEngine` as a callback ref: every time the `<canvas>` attaches, a `canvasMountNonce` state bumps and the render effect re-paints the current page against the fresh node. ([src/hooks/usePdfEngine.js](src/hooks/usePdfEngine.js))

## [1.7.2] - 2026-05-23

### Fixed
- **Tablet header was cut off.** The secondary-action buttons hid at `sm:` (640 px) and the inline group did not have enough room between 640 px and 1024 px — the buttons that didn't fit were just clipped. Inline buttons now hide at `lg:` (1024 px) instead, and the `⋯` overflow menu activates `lg:hidden` so phones AND tablets / squeezed desktop windows reach every action through the dropdown. Full desktop (≥ 1024 px) layout is unchanged. ([src/components/Header.jsx](src/components/Header.jsx), [src/components/HeaderOverflowMenu.jsx](src/components/HeaderOverflowMenu.jsx))
- **Floating "Read Selection" / "Ask AI" buttons overlapped the mobile bottom nav.** Their `bottom-6` position sat directly on top of `MobileBottomNav`, so taps on the next-sentence / play buttons were stealing the floating-button area. Bumped to `bottom-24 right-4 md:bottom-6 md:right-6` so phones get clearance over the nav and desktop is unchanged. ([src/components/overlays/ReadSelectionButton.jsx](src/components/overlays/ReadSelectionButton.jsx))

### Changed
- **Distraction-free mode keeps basic playback controls visible.** The earlier bare "Exit" pill was too minimal for actual reading sessions. New floating pill at bottom-center carries **prev sentence / play-pause / next sentence**, a clickable **page-number input** (jump pages without leaving the mode), and **Exit**. In chat mode or with no document open, the pill collapses to just Exit. `F` still toggles the mode. New component [src/components/DistractionFreeBar.jsx](src/components/DistractionFreeBar.jsx).

## [1.7.1] - 2026-05-23

### Fixed
- **Chat session reload — messages appeared in the wrong order.** The save path bulk-inserts every message in one transaction (same `created_at`), and the user prompt + the empty assistant placeholder are created in the same `Date.now()` tick (same `timestamp`). The final tiebreak fell to `id`, where `a-<ts>` sorts before `u-<ts>` lexicographically — producing the `[response, query, response, query]` pattern. Fixed in two layers: the SELECT now adds a role-based `CASE` between `timestamp` and `id` (`user` before `assistant`) so existing saved sessions read back correctly without a migration, and `sendMessage` bumps the assistant's `timestamp + id` suffix by 1 ms so new pairs are monotonic without depending on the tiebreak. ([server/routers/chat_sessions.py](server/routers/chat_sessions.py), [src/hooks/useChatEngine.js](src/hooks/useChatEngine.js))

### Added
- **Distraction-free reading mode.** Press `F` (or click the new `Maximize2` button in the header) to hide the Header, sidebar, mobile bottom nav, and the PDF toolbar — leaving just the document content and a small floating **Exit** pill in the top-right. Persisted across reloads. Listed in the keyboard-shortcuts modal. Works in both reader and chat. ([src/App.jsx](src/App.jsx), [src/hooks/useKeyboardShortcuts.js](src/hooks/useKeyboardShortcuts.js))
- **Mobile overflow menu (`⋯`) in the header.** Several actions were `hidden sm:*` and simply vanished on phones (dark mode toggle, TTS-backend switch, page-audio download, audiobook export, keyboard shortcuts, home, distraction-free). They're now all reachable through a small dropdown rendered only on small screens (`sm:hidden`). Desktop layout unchanged. ([src/components/HeaderOverflowMenu.jsx](src/components/HeaderOverflowMenu.jsx))
- **Audiobook export — parallel page synthesis.** The page-by-page loop in `downloadBookAudio` now runs up to 3 page-synth requests in flight at once via a small worker-pool pattern. Pages are slotted by index so the final WAV concatenation preserves document order even though synthesis completes out-of-order. Cancel still works mid-pool. ([src/hooks/useTtsEngine.js](src/hooks/useTtsEngine.js))
- **Multi-worker uvicorn support.** `run.py` honours a new `WORKERS=N` env var (also `HOST` and `PORT`). With one worker the audiobook pipelining is a small overlap win; with N workers (each loading its own Kokoro model) it actually fans out across CPU cores so audiobook jobs scale roughly linearly. Trade-offs (RAM per worker, Postgres pool sizing, GPU caveat) documented in [docs/CHAT_WITH_PDF.md](docs/CHAT_WITH_PDF.md) §10.

### Caveats
- **Multi-worker uvicorn loads one Kokoro model per worker** (~300–500 MB on the ONNX-CPU build). Budget RAM accordingly; on a 4 GB host stick to 1–2 workers.
- **Postgres pool is per-process** — default `max_size=10` × N workers = up to 10 N connections. Default Postgres `max_connections` is 100, so re-tune above ~9 workers.
- **The mobile overflow menu only renders below the `sm` breakpoint** (640 px). It's deliberately invisible on tablets / desktops where the inline buttons handle the same actions.

## [1.7.0] - 2026-05-23

### Added
- **Docling PDF → Markdown conversion** — new backend pipeline that converts PDFs into layout-aware Markdown using [Docling](https://github.com/DS4SD/docling) (MIT, IBM/DS4SD). Click **Convert** in the PDF toolbar to open an options dialog with a **Fast / Standard / Accurate** quality preset (the Accurate path uses the VLM `granite_docling` pipeline when available), an OCR force-toggle for scanned PDFs, table extraction toggle, image handling (drop / embed-base64 / VLM describe), and an optional page-range limiter. After conversion the doc is automatically re-chunked and re-embedded from the cleaner Markdown text so retrieval picks up tables and headings the native pdf.js extractor was missing.
- **PDF ↔ Markdown reader toggle** — once a doc is converted, the PDF toolbar gains a small **PDF | MD** segmented control. **MD** swaps the PDF canvas for a new `MarkdownReader` that renders the docling output per page with anchored separators, syncs the toolbar's page number with whichever page is in view, and lets TTS / "Read selection" / "Ask AI" work on the cleaner text. Reader view is per-doc and persists across navigation.
- **Export converted Markdown** — Download icon in the toolbar (visible when `conversion_state='converted'`) saves the full document MD as `{filename}.md` via blob download.
- **Delete converted Markdown** — Trash icon (with confirm prompt) wipes `doc_pages` + the chunks/embeddings derived from them and resets `conversion_state` so the **Convert** button reappears. The retained PDF on disk stays in place so reconversion is one click.
- **Retained PDF on the server** — `POST /v1/docs/{id}/pdf` (multipart, ≤ `PDF_UPLOAD_MAX_MB`, default 50 MB) parks the bytes in `./data/pdfs/{doc_id}.pdf` so reconversion with different settings doesn't require a re-upload. New `DELETE /v1/docs/{id}/pdf` removes them. Deleting the document row cleans up the file automatically.
- **Per-message audio export in chat** — every assistant chat message gets an **Audio** action button (next to Read aloud / Copy). Click it to synthesize the whole message through Kokoro and download a `.wav` named after the active session title + assistant-message index (e.g. `Session Title_msg2.wav`). Shows an inline spinner while generating; only one message synthesizes at a time. Hidden when Kokoro isn't selected.
- **Audiobook export — full document as a single WAV** — new **Library** icon in the header (reader mode + Kokoro only) extracts every page's text and synthesizes them one page at a time via `/v1/batch_synthesize`, stitches the WAVs client-side into one file, and downloads `{filename}_audiobook.wav`. While exporting, the button morphs into a progress card showing `current/total` + the current label ("Synthesizing page 14…") with an **X** to cancel. Failed pages are skipped with a toast at the end instead of aborting the whole job. New client-side WAV concatenator (`src/utils/wavConcat.js`) parses RIFF chunks, reuses the first file's header, concatenates `data` payloads, and rewrites RIFF + `data` sizes.
- **Home button to return to the library** — new Home icon in the header (reader mode, doc open) stops TTS, drops the loaded document, clears the active doc id, and brings up the welcome screen with the recent-books list. Reading progress was already persisted, so reopening the doc resumes where you left off.

### Changed
- **MarkdownReader and MarkdownPageRenderer width** widened from `min(820px, 80vw)` to `min(1200px, 95vw)` so MD prose, tables, and code blocks have room to breathe on wide screens.
- **MarkdownReader no longer maintains an inner scroll container** — scrolling and page-nav scroll happen via the existing PdfViewer outer scroller. Combined with a small "programmatic-scroll quiet window" (`PROG_SCROLL_QUIET_MS = 800`), this fixes the feedback loop where a "next page" click was getting overridden mid-scroll by the IntersectionObserver. The observer now uses the viewport (`root: null`) and respects the quiet window.
- **Server schema** — new migration `server/sql/003_docling.sql` adds `documents.pdf_path`, `documents.conversion_state`, `documents.conversion_options` (JSONB), `documents.conversion_error`, `documents.converted_at`, and a new `doc_pages` table (`doc_id`, `page`, `markdown`). `_fetch_doc_status` now returns `conversion_state` / `converted_page_count` / `conversion_options` / `has_pdf` so the frontend can mirror the full lifecycle. Stale `conversion_state='converting'` rows are reset on startup the same way stale `state='indexing'` already was.
- **`_run_index_job` lock** renamed from `_index_locks` to `_doc_job_locks` and shared with `_run_convert_job` since they touch the same `doc_chunks` rows. The old `_get_index_lock` / `_index_locks` names remain as aliases.
- **Python deps** — adds `docling>=2.0` and `python-multipart` to `requirements.txt`. Docling pulls in transformers + torch (CPU); first conversion downloads layout/table model weights (~500 MB to ~2 GB total). Gate the feature with `DOCLING_ENABLED=true` env var so the rest of the server boots without the heavy stack installed.
- **Persistent storage** — `./data/pdfs/` is created on app startup and added to `.gitignore`. Set `PDF_STORAGE_DIR` to override.
- **ESLint config** — `globalIgnores` now also covers `.venv` and `node_modules` so vendored minified JS inside the Python virtualenv (e.g. the `mpire` dashboard's bundled bootstrap.js) doesn't pollute lint output.

### Fixed
- **MD-file zoom & toolbar controls now feel responsive** — the box is much wider, page next/prev actually scrolls to the requested page in the converted MD view, and the observer no longer fights programmatic scrolls.

### New endpoints
- `POST /v1/docs/{doc_id}/pdf` — multipart upload, persists raw PDF bytes for later reconversion.
- `DELETE /v1/docs/{doc_id}/pdf` — remove retained PDF bytes (keeps markdown + chunks).
- `POST /v1/docs/{doc_id}/convert` — `202 Accepted`; kicks off a docling background job with the supplied options.
- `GET /v1/docs/{doc_id}/markdown` (optionally `?page=N`) — returns the converted Markdown as `text/markdown`.
- `DELETE /v1/docs/{doc_id}/markdown` — wipe converted markdown + derived chunks/embeddings; doc row + retained PDF stay in place.

### Caveats
- **Docling conversion is heavy.** Standard preset on a 50-page text PDF takes a few minutes on CPU; Accurate (VLM) is slower still. The Convert button polls every 2 s for up to 20 min before warning the user. Cancel isn't wired yet — kill the backend if you need to abort.
- **Audiobook export is single-threaded** because kokoro-onnx is. A long book may take many minutes; the rest of the app stays usable while it runs.
- **WAV concat assumes identical PCM format across pages.** Since every page in a single export uses the same voice + speed, that holds. Don't change voice mid-export.
- **Page-range applies to conversion only**, not RAG retrieval. Search still returns top-K across all converted pages.
- **Reconverting a doc** wipes `doc_pages` then `doc_chunks` then re-embeds. The PDF on disk stays.

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
