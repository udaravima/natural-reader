# Neural Reader

A modern, feature-rich document reader with **neural text-to-speech** powered by **[Kokoro TTS](https://github.com/hexgrad/kokoro)**, an **optional local-AI chat mode** powered by **[Ollama](https://ollama.com/)**, and (new in `v1.6.0`) **document-aware chat with RAG + autonomous tool calling** backed by **Postgres + pgvector**. Open PDFs, `.txt`, or `.md` files, have them read aloud with natural-sounding voices, ask the model about what you're reading, or let the model search the indexed doc on its own.

> 🎯 **A web frontend for Kokoro TTS — now with chat that knows what you're reading.** Beyond the TTS reader and the standalone Ollama chat side-mode, the app can index a loaded document into pgvector and expose a `search_documents` tool that your local LLM calls autonomously when a question warrants it. Out of the box everything stays local: Ollama for the LLM + embeddings, Postgres in a container for chat sessions and vectors. A deployment can add cloud model providers for chat, and web search asks before anything leaves the server. A browser-based Web Speech fallback is also available for testing without any backend.

![Neural Reader](https://img.shields.io/badge/React-19.x-blue) ![PDF.js](https://img.shields.io/badge/PDF.js-5.x-orange) ![Kokoro TTS](https://img.shields.io/badge/Kokoro-TTS-green) ![Ollama](https://img.shields.io/badge/Ollama-Chat-orange) ![Vite](https://img.shields.io/badge/Vite-Rolldown-purple) ![Offline](https://img.shields.io/badge/Offline-Ready-brightgreen)

> 🔌 **Works 100% Offline!** Once installed, the app runs completely without internet. PDF.js is bundled locally, and Kokoro TTS / Ollama run on your machine.

---

## ✨ Features

### 📖 Document Viewing
- **Drag & Drop Upload** — Drop PDFs, `.txt`, or `.md` files directly onto the window
- **PDF Rendering** — Smooth page-by-page rendering with zoom controls
- **Plain Text (.txt) Support** — Text files are paginated into pseudo-pages (~40 sentences) and use the same reading pipeline as PDFs (sentence highlight, library, resume progress, selection-read)
- **Markdown (.md) Support** — Markdown files render with proper formatting (headings, lists, code blocks, tables, blockquotes) via `react-markdown` + `remark-gfm`. Pagination is paragraph-aware (~40 sentence soft cap, breaks only on block boundaries). The block currently being read is highlighted at the paragraph level, and TTS strips Markdown markup so `**bold**` reads as "bold" and code blocks are skipped entirely.
- **Table of Contents** — Navigate using the PDF's chapter outline (if available)
- **Text Selection** — Select text directly on the rendered page for copying or reading
- **Zoom Controls** — Zoom in/out, fit to page, fit to width (font size for `.txt` and `.md`)
- **Page Jump** — Click the page indicator and type any page number

### 🎙️ Text-to-Speech
- **Kokoro TTS Integration** — High-quality neural text-to-speech via local backend
- **50 Voice Options** — US and UK English, Hindi, Spanish, French, Italian, Portuguese, Japanese and Chinese voices with live preview. Any voice can read Sinhala and Tamil
- **Speed Control** — Adjust playback speed from 0.5× to 2×
- **Volume Control** — Adjustable audio volume slider
- **Audio Buffering** — Pre-fetches upcoming sentences for seamless playback
- **Auto Page Advance** — Automatically continues reading across pages
- **Download Page Audio** — Export the current page as a WAV file
- **Selective Read** — Select any text and read only that selection
- **Continue From Here** — Right-click any sentence to start reading from that point
- **Browser Fallback** — Uses Web Speech API when backend is unavailable
- **Auto-Failover** — Automatically switches to browser voice if backend is unreachable

### 💬 Local AI Chat (Ollama)
- **Reader ↔ Chat Toggle** — Switch the main view between document reader and chat mode from the header
- **Streaming Replies** — Every chat turn runs on the server (`server/chat/`) and streams token-by-token over `POST /v1/chat/sessions/{id}/turns` (server-sent events); the browser never talks to a model provider directly
- **Model Picker** — Grouped by provider (native Ollama, plus any OpenAI-compatible server an admin has configured — vLLM, OpenRouter, LiteLLM), with capability badges per model. Adding or changing providers is an operator `.env` task (`INFERENCE_PROVIDERS`, see [.env.example](.env.example)); there's no in-app provider screen yet
- **Image Attachments** — Paperclip button, drag & drop onto the chat view, and `Ctrl + V` paste images from the clipboard. Thumbnails preview above the prompt and persist in user bubbles. Sent to vision-capable models via the per-message `images: [base64]` field. An audio file dropped onto the chat is still refused: speaking a message is the microphone button below.
- **Text files in a message** — The same paperclip (also drag & drop, and pasting a file) takes small text files: `.env`, `.txt`, `.md`, `.json`, `.yaml`, `.toml`, `.csv`, `.log`, source files and similar, up to 64 KB each and 128 KB per message. The browser masks secret values **before anything is uploaded**: in an env file every value except ones like `true`, a port or a short number; in other files, only what looks like a secret (private keys, well-known tokens, JWTs, `password:` lines, a password in a URL). Click the file's chip to see exactly what will be sent, and **Send real values** to send the original instead (it asks first and names the model, and says when it runs on a cloud service). It is a safety net, not a guarantee: read the chip view. The model gets each file as an `<attached_file>` block in the message, so every model and provider can read it.
- **Voice input** — A microphone button beside the paperclip records up to 2 minutes, the server turns it into text (faster-whisper on the CPU by default, or any OpenAI-compatible transcription server), and the text lands in the message box for you to edit and send. Nothing is sent for you, and the app never writes a recording to disk, the database or a log (a reverse proxy may buffer an upload: see DEPLOYMENT). **Off by default**: an admin turns it on under **Admin → Chat options → Voice input** (or `CHAT_VOICE_INPUT=on`), and the first time it downloads a ~464 MB model. Needs https (or localhost) for the microphone. **Settings → Chat → Speech language** picks a language, or **Auto**. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#voice-input) for the setup and its costs. Admins choose the speech model under **Admin → Chat options → Speech model**, and can add Whisper models from Hugging Face there: fine-tunes for Sinhala and Tamil are in the list, converted on first use.
- **Model Response Stats** — Every assistant bubble has a collapsible footer showing token count, total time, and tokens/sec. Expanded view breaks out load / prompt-eval / generation phases for fine-grained latency inspection.
- **Stick-to-Bottom Scroll** — The chat list auto-tails streaming tokens when you're at the bottom; scrolling up pauses the auto-follow so you can read history during a long response, and resumes when you scroll back down.
- **Chat TTS** — Replies are read aloud through the same Kokoro pipeline, with bounded prefetch to avoid gaps
- **Streaming vs After-Complete TTS** — Read sentences as they stream in, or wait for the full reply before reading
- **Per-Message Read Aloud** — A `🔊 Read aloud` / `■ Stop` button on every assistant bubble — works even with auto-TTS off, or to re-read finished messages later
- **Markdown Rendering** — Lists, code blocks, tables, headings, links render natively in chat bubbles
- **Markdown-Aware TTS** — Markup is stripped before synthesis so audio reads visible text only (no "star star bold")
- **Per-Model Inference Settings** — **Settings → Chat** sets the **context window** (`num_ctx`), **keep-alive**, **thinking level**, and **max reply tokens** for the selected model, saved per model so a 9.7B and a 3B can differ on the same machine. Every control defaults to **Auto**. For a self-hosted Ollama model **Auto** means the server sends 16,384 tokens (Ollama's own default cuts long prompts without saying so), and an admin can fix the size under **Admin → Models**, which locks the control. Changing the context window makes Ollama reload the model.
- **Reasoning Trace Control** — The `Thinking` setting sends `think` to Ollama and surfaces the reasoning trace (deepseek-r1, qwen3-thinking, gpt-oss, …) in a collapsible disclosure that auto-expands while streaming. Five states: `Off` / `On` (booleans) plus the graduated `Low` / `Medium` / `High` levels — useful on slow hardware, where reasoning tokens are the most expensive output a model can produce. Models that accept the boolean but reject a level are retried automatically.
- **Context Meter & Truncation Warning** — A `~3.3k / 16k ctx` readout above the composer estimates how full the window is (amber past 75%), and if a reply is cut off because the context filled up, a toast names the actual numbers instead of leaving `done_reason: length` buried in the stats disclosure.
- **Per-Message Copy** — One-click copy of any chat message to clipboard

### 🧩 Chrome Read-Aloud Extension & Markdown Folder Workspace *(new in `v1.8.0`)*

- **Chrome "Read Aloud" extension** — A Manifest V3 browser extension (in [`extension/`](extension/)) that reads the selected text or the whole page aloud on any site through this project's local Kokoro TTS backend. No build step — load `extension/` unpacked. Floating Shadow-DOM toolbar (play/pause, stop, seek, voice, speed) via right-click or a popup; whole-page reads chunk into sentences and stream with prefetch. It reaches `localhost` out of the box; any other server needs the site permission Chrome asks for, and a server with sign-in needs an access token. Setup + known site limits in [extension/README.md](extension/README.md).
- **Markdown folder workspace** — Open a folder so a Markdown/text file's cross-file links navigate in-reader: relative `.md`/`.txt` links open the target, `#heading` anchors scroll, relative images render, with Back/Forward history. Uses the File System Access API (with a folder-picker fallback), persists across reloads, and rejects dangerous link schemes.

### 🪄 Docling Conversion, Audiobook & Chat Audio *(new in `v1.7.x`)*

- **Convert PDF → Markdown with Docling** — Optional backend pipeline (gated by `DOCLING_ENABLED=true`) that turns a PDF into layout-aware Markdown using [Docling](https://github.com/DS4SD/docling). Click **Convert** in the PDF toolbar to pick a quality preset (Fast / Standard / Accurate — the Accurate path uses GraniteDocling VLM), force-enable OCR for scanned PDFs, toggle table extraction, choose what happens to pictures (**Drop**, **Keep** them beside the document, or **Describe** them too), or limit a page range. After conversion the doc is auto re-chunked + re-embedded so RAG picks up tables and headings the native pdf.js extractor missed.
- **PDF ↔ MD reader toggle** — Once converted, a small `PDF | MD` segmented control in the toolbar swaps the canvas for a wide Markdown reader with anchored per-page separators and the pictures you kept. The Download / Trash icons next to it export the converted MD (`{filename}.md`) or wipe it server-side.
- **Audiobook export** — Library icon in the header synthesises every page through `/v1/batch_synthesize`, stitches the WAVs client-side, and downloads `{filename}_audiobook.wav`. Progress + cancel UI; up to 3 pages in flight at once. Set `WORKERS=N python run.py` to fan synthesis across CPU cores (one Kokoro model per worker; see [docs/CHAT_WITH_PDF.md §10](docs/CHAT_WITH_PDF.md#10-performance--audiobook-export--multi-worker)).
- **Per-message chat audio export** — Every assistant chat message has a new **Audio** button that downloads it as `.wav` (filename derived from session title + message index).
- **Home button** — Header icon (reader mode, doc open) returns to the library; reading progress was already persisted so reopening resumes where you left off.
- **Distraction-free mode** — Press `F` (or click the `Maximize2` button in the header) to hide Header, sidebar, mobile bottom nav, and the PDF toolbar. Small floating Exit pill in the top-right brings everything back. Persisted across reloads; works in both reader and chat.
- **Mobile overflow menu** — On phones, secondary header actions (dark mode, TTS backend, page audio, audiobook, shortcuts, home, distraction-free) collapse into a `⋯` dropdown so the top bar stops feeling cramped.

### 📑 Document Chat & RAG *(new in `v1.6.0`)*

A full end-to-end walkthrough lives in [docs/CHAT_WITH_PDF.md](docs/CHAT_WITH_PDF.md). Headline capabilities:

- **Ask page** — One toolbar click *pins* the current page text (~8000 char cap) to the chat. No indexing required.
- **Ask AI on a selection** — Highlight any text on the rendered page and *pin* just that snippet. Paired with the existing "Read Selection" TTS button.
- **Pinned context** — Ask page / Ask AI create **pins**: excerpts that stay attached to the conversation and are re-sent to the model on **every** turn — positioned at the very top of the prompt so they never get buried — until you remove them. Multiple pins accumulate as removable chips, dedupe by content, are bounded (**6 pins / ~12 000 chars**), and are **saved with the chat session** (restored on reload). Whole-document breadth comes from autonomous retrieval (below), not a giant pin.
- **Index this document** — Backed by **Postgres + pgvector**. The file is uploaded, and the **server** extracts per-page (PDF), per-block (Markdown) or per-pseudo-page (TXT) chunks on the reader's own pagination, embeds them via Ollama's `nomic-embed-text` (768-dim) and stores them in an HNSW-indexed `vector` column. A file that's already indexed on the server is indexed for you at once.
- **Autonomous tool calling** — When a doc is indexed and the chat model reports tool support, the model gets a `search_documents` tool it can invoke on its own, plus tools to read pages and go through many of them for one goal; when SearXNG is configured it can also offer a web search (see Web search below). The turn runs server-side (`server/chat/`): the server executes the call, hands the result back, and the model streams the final answer. Up to 3 tool rounds by default (`CHAT_MAX_TOOL_ROUNDS`; 5 in a chat about a whole project, `CHAT_PROJECT_MAX_TOOL_ROUNDS`), so the model can follow a trail through the document, then one last step with tools switched off, so the turn always ends in an answer. Tool results share a per-answer budget (`CHAT_TOOL_RESULT_BUDGET_CHARS`), and web search refuses queries that copy the document; a model without tool support just never sees the tool. Tool calls are persisted in a `tool_calls` JSONB column and re-rendered as a 🔎 disclosure on the assistant bubble.
- **Postgres-backed chat sessions** — Sessions previously stored in IndexedDB now write to Postgres via a new `src/lib/sessionStore.js` abstraction. Legacy IDB sessions stay readable with a small **LOCAL** badge; the first message you send on one copies it onto the server (`POST /v1/chat/sessions/import`), leaving the original intact.
- **Server-side tool registry** — `server/chat/tools/` houses one tool per file (`search_documents`, `read_document_pages`, `extract_from_documents`, `document_outline`, `web_search`, `read_web_pages`, `offer_web_search`). Adding a tool later is one new file + one registry line; there is no browser-side tool code anymore.

### 👥 Accounts, Document Library & App Shell *(new in `v2.0.0`)*

Natural Reader is now a **multi-user, authenticated application**. End-user walkthrough: [docs/USER_GUIDE.md](docs/USER_GUIDE.md); operator setup: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) and [docs/IDENTITY_AND_ROLES.md](docs/IDENTITY_AND_ROLES.md).

- **Sign in (OIDC / Keycloak)** — Every API route requires an authenticated principal. First login becomes admin; others wait for activation. **Personal access tokens** (`nrp_…`) let the Chrome extension and scripts authenticate outside the browser, each limited to what it's for (read aloud, documents, chat, admin) and optionally expiring. Set `AUTH_ENABLED=false` for a loopback-only single-user box.
- **Capabilities (reader / chat / admin)** — Feature access is governed by Keycloak realm roles enforced on the server and reflected in the UI: you only see the views you're entitled to, and an activated user with no capabilities gets a clear "access not yet granted" screen.
- **Admin console** — A dedicated Admin view (shield icon in the top switcher, admins only): enroll users (invite or one-time temp password), edit capabilities, disable/delete — propagated to Keycloak, with a last-active-admin guard so you can't lock the org out. **People** has a search and status/capability filters; **Models** sets the reader, the context size of self-hosted Ollama models, declared context windows and which chat models people are offered (see [docs/USER_GUIDE.md](docs/USER_GUIDE.md#models-the-reader-context-sizes-and-which-models-people-get)).
- **Model providers & daily budgets** — Chat always runs on the server, never in the browser. An admin configures one or more model providers in `.env` (native Ollama by default, plus any OpenAI-compatible server — see [.env.example](.env.example)); there's a deployment **model allowlist** per provider and an optional **per-user daily token budget** (a "N tokens left today" meter; send disables at zero; a reply you stop or that fails still counts, from the provider's own count or an estimate).
- **Document Library** — Documents are private by default. The server verifies every upload, and a file someone already indexed is added to your library instantly. In the app you can create a project (Library → New project), add people to it with a role (Reader, Contributor, Maintainer, Owner; **Members → + Add people**), file your uploads into it, keep every version of a document (upload a new version, restore or merge; the Library shows only the current one), and share a single document with one person (**Share** in the Library). A **Library** view lists, searches and filters your documents by project and tags, and shows what was shared with you and by whom. Removing a document only removes your copy. Non-readers get a 404, never a hint that the document exists.
- **Consolidated Settings + profile menu** — Voice, chat/inference (per-model), connection, appearance, and account (tokens) settings live in one **Settings** window that opens over whatever you have open, so changing a setting never stops the narration or a reply. It's reached from a header gear and a profile menu (identity, Settings, dark mode, log out). The reader and chat sidebars are trimmed to navigation and model/sessions. The chat composer draft survives switching tabs.

### 🗂️ Project Chat, Web Search That Asks & Profile *(new in `v2.4.0`)*

- **Ask a whole project** — **Ask about this project** on a project page (or **Choose a project…** on any chat's scope chip) starts a chat about all of the project's current documents. The assistant gets a map of the project (names, pages, headings), searches every document as one ranked list, and cites each fact as **(Guide.md, page 7)**; the link opens that document at that page, and the source chips are grouped by document. Old versions are never searched.
- **A reader for "every step, in order"** — For questions that need many pages, a separate model call (the *reader*) reads them for one goal and returns only facts whose quotes the server finds on the page. Its calls count toward the daily token budget. By default it is the chat model itself, so a CPU-only box loads one model; name a separate (smaller) one under **Admin → Models → Reader** or in `READER_MODEL`.
- **Web search asks first** — By default the assistant can only *offer* a search: a **Search the web?** card shows its reason and the query in an editable box, and nothing leaves the server until you press **Search the web**. Facts from the web cite their site. Admins pick *Ask first*, *Search without asking* or *Off* under **Admin → Chat options**; `CHAT_WEB_SEARCH` sets the starting value. In a project chat the card doesn't always appear; typing "search the web for …" brings it up.
- **Code blocks, outlines and table cells are searchable** — A Markdown code block is its own passage, so an exact command can be found and quoted.
- **Narration everywhere, chat beside the page** — The narration keeps playing in Chat, Library and Admin, with a small header control. On a wide screen the chat opens in a column next to the page.
- **Profile** — A profile picture, a **Manage your account** link to the sign-in provider, and your signed-in browsers with **Sign out everywhere else**. Admins set defaults for everyone (voice, speed, auto-read, thinking) under **Admin → Defaults**.

Walkthroughs: [docs/USER_GUIDE.md](docs/USER_GUIDE.md#asking-a-whole-project); operator knobs: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#project-chat-the-reader-and-web-search).

### 🎙️ Voice Input, Text Files & the Admin Console *(new in `v2.5.0`)*

- **Speak a message** — The microphone beside the paperclip records up to 2 minutes and puts the text in the box to edit and send. Nothing is sent for you. It runs on your own server by default, and it's off until an admin turns it on.
- **Choose the speech model** — **Admin → Chat options → Voice input → Speech model** switches models with no restart. Stock Whisper stays the default and the right choice for English. Fine-tuned Sinhala and Tamil models are in the list and convert themselves on first use, and an admin can add any Whisper model from Hugging Face. One model runs for the whole server.
- **Attach a text file, with its secrets hidden first** — Drop in an `.env` and ask how it should change. The browser masks secret values before anything is uploaded, and clicking the file shows exactly what will be sent.
- **An admin console that runs the deployment** — An **Overview** that checks whether everything works, then **Models**, **Documents**, usage by model and person, a timezone and defaults. Long lists scroll and search, and Save stays on screen.
- **Keep a PDF's pictures** — **Convert → Images → Keep** keeps them, shown in **MD** view.

Walkthroughs: [docs/USER_GUIDE.md](docs/USER_GUIDE.md); speech models: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#choosing-a-model).

### 🎨 User Experience
- **Dark Mode** — Beautiful dark/light theme toggle with smooth transitions
- **Sentence Highlighting** — Visual highlighting of the current sentence during playback
- **Auto-Scroll** — Sidebar automatically scrolls to the current sentence
- **Reading Progress** — Visual progress bar showing page completion percentage
- **Estimated Time** — Shows remaining reading time for the current page
- **Responsive Layout** — Full mobile support with dedicated bottom navigation
- **Collapsible Sidebar Sections** — Settings, Sessions, Conversation, and Session log are independently collapsible with their own scroll containers
- **Toast Notifications** — Informative feedback for user actions

### 💾 Memory & Persistence
- **Document Library (IndexedDB)** — PDFs, `.txt`, and `.md` files saved locally for instant resume (up to 5 docs)
- **Chat Sessions (Postgres)** — Every chat is saved on the server as it streams; switch / rename / delete from the sidebar; auto-named from the first prompt. Chats from before `v1.6.0` stay readable from IndexedDB with a **LOCAL** badge
- **Per-Session Event Log** — `sent`, `received`, `aborted`, `error` events with timestamps, viewable as a collapsible log
- **One-Click Resume** — Click any document in the library to continue reading
- **Settings Saved** — Voice, speed, volume, zoom, theme, API host/port, model, TTS mode all persist across sessions
- **Reading Progress** — Remembers your position in each document (page + sentence)

### ⌨️ Keyboard Shortcuts
| Key | Action | Mode |
|-----|--------|------|
| `Space` | Play / Pause | Reader only |
| `Escape` | Stop playback | Reader only |
| `Shift + ←` | Previous sentence | Reader only |
| `Shift + →` | Next sentence | Reader only |
| `Page Up` | Previous page | Reader only |
| `Page Down` | Next page | Reader only |
| `Ctrl + +` | Zoom in | Both |
| `Ctrl + -` | Zoom out | Both |
| `Ctrl + D` | Toggle dark mode | Both |
| `F` | Distraction-free mode | Both |
| `Enter` | Send message | Chat (in prompt box) |
| `Shift + Enter` | New line in prompt | Chat (in prompt box) |

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|------------|
| **Frontend** | React 19, Vite (Rolldown) |
| **PDF Parsing** | PDF.js 5.x (bundled locally) |
| **Markdown** | `react-markdown` + `remark-gfm` (chat replies) |
| **Styling** | Tailwind CSS 4.x |
| **Icons** | Lucide React |
| **Storage** | Server: Postgres + files under `DOC_STORAGE_DIR`. Browser: localStorage + IndexedDB (settings, a local library, legacy chats) |
| **TTS Backend** | FastAPI, Kokoro ONNX, Uvicorn |
| **TTS Inference** | ONNX Runtime (CPU by default; CUDA / OpenVINO builds opt-in on bare metal) |
| **Chat Backend (optional)** | [Ollama](https://ollama.com/) — local LLM server (default `:11434`) |
| **Doc Chat Backend (optional, new in 1.6)** | FastAPI routers: `/v1/chat/sessions/*` and `/v1/docs/*` |
| **Doc Storage / RAG** | Postgres 16 + [pgvector](https://github.com/pgvector/pgvector) (HNSW, cosine), embedded via Ollama `nomic-embed-text` |
| **DB driver** | `psycopg[binary,pool]` (async) with `pgvector` codec registered per-connection |
| **PDF → Markdown (optional, new in 1.7)** | [Docling](https://github.com/DS4SD/docling) — layout-aware extraction with optional GraniteDocling VLM. Gated by `DOCLING_ENABLED=true`. |

---

## 🚀 Getting Started

### Quickstart (Linux)

```bash
git clone https://github.com/udaravima/natural-reader.git && cd natural-reader
./startup.sh quickstart            # or: ./startup.sh quickstart --single-user
```

It checks for a container engine (podman with podman-compose 1.1+, or Docker with the Compose plugin 2.24+) and Node 20.19+/22.12+. **No host Python is needed:** the backend runs in a container. It writes a local-dev `.env` (never touching an existing one), builds the backend image, starts Postgres, Keycloak, SearXNG and the backend, then the frontend dev server. Open <http://localhost:5173> and sign in as `admin-user` / `password`. `--single-user` turns sign-in off and skips Keycloak. Re-running it after a failure picks up where it stopped.

| Command | What it does |
|---|---|
| `./startup.sh start` | live mode: the whole stack, detached, the backend running the code baked into its image |
| `./startup.sh stop` | stop and remove the containers (data stays in volumes and `data/`) |
| `./startup.sh rebuild` | rebuild the backend image after code changes; recreate only the backend |
| `./startup.sh logs` | follow the backend's output |
| `./startup.sh enable-autostart` | podman: come back after a reboot (enables lingering and `podman-restart.service`) |
| `./startup.sh up` / `up-with-dev-auth` | dev: the backend container runs your working tree; Ctrl-C stops everything |
| `BACKEND_RUNTIME=venv ./startup.sh up` | the bare-metal backend, as before the container (needs `./startup.sh init` and Python 3.12–3.13). Put `BACKEND_RUNTIME=venv` in `.env` to make it the default for `up` / `up-with-dev-auth` |

**Supported:** Linux on x86_64. **Not supported in the container:** ARM machines, including Apple Silicon Macs (untested there), and macOS in general is untested (its stock bash 3.2 can't run `startup.sh`). **Windows** and macOS use the bare-metal flow (`startup.ps1` / `startup.cmd` on Windows, Python 3.12–3.13) described below.

### Software Requirements

Install this before you start. The app is split into a **React frontend** (Vite) and a **Python TTS backend** (FastAPI). Chat and RAG layer on optional extras.

#### Required — Reader + Neural TTS

| Software | Version | Why |
|----------|---------|-----|
| **Node.js** + **npm** | `20.19+` **or** `22.12+` | Frontend dev server / build. Vite 7 (Rolldown) and `@vitejs/plugin-react` declare `engines: ^20.19.0 \|\| >=22.12.0` — older Node will fail to start. npm ships with Node. |
| **Python** | `3.12`–`3.13` | The backend (`run.py`). The chat server needs 3.12+ (`inspect.getasyncgenstate`); 3.14 is untested. |
| **Git** | any recent | Clone the repository |
| **wget** or **curl** | any | Download the Kokoro voice model files (~335 MB total — see step 2) |

> 💡 Tip: use `nvm` to pin Node and a `.venv` to isolate Python — the install steps below assume both.

#### Optional — Local AI Chat (Ollama)

| Software | Version | Why |
|----------|---------|-----|
| **[Ollama](https://ollama.com/)** | latest | Runs the local LLM for chat mode (and embeddings for RAG). Reader + TTS work without it. |

#### Optional — Document Chat & RAG

| Software | Version | Why |
|----------|---------|-----|
| **Docker + Docker Compose**, *or* **Podman + podman-compose** | recent | Runs Postgres in a container via `docker-compose.yml`. `startup.sh` supports either engine. |
| *— or —* host **PostgreSQL** + **pgvector** | `pg16` | Stores chat sessions and document embeddings. The provided container image is `pgvector/pgvector:pg16`. |
| Ollama embedding model **`nomic-embed-text`** | 768-dim | Indexing / retrieval / autonomous `search_documents` tool calling. The schema is hard-locked to 768 dims. |

> 📦 **Disk:** budget ~335 MB for the Kokoro model + voice pack, and (only if you enable `DOCLING_ENABLED=true`) an extra ~500 MB–2 GB downloaded on first conversion for the Docling layout/table models.

### 1. Clone & Install Dependencies

> Bare-metal setup (Windows, macOS, or `BACKEND_RUNTIME=venv`). On Linux, the Quickstart above is the shorter path.

```bash
git clone https://github.com/udaravima/natural-reader.git
cd natural-reader

# Frontend
npm install

# Backend
python3 -m venv .venv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate         # Windows
pip install -r requirements.txt
pip install -r requirements-docling.txt   # optional: only for DOCLING_ENABLED=true
```

### 2. Download Voice Models

```bash
# Kokoro v1.0 ONNX model (~310 MB)
wget https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0/kokoro-v1.0.onnx

# Voice pack (~25 MB)
wget https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0/voices-v1.0.bin
```

Place both files in the project root directory.

### 3. Start the Servers

> **Auth note:** since the multi-user OIDC work, the backend **enables
> authentication by default** (`AUTH_ENABLED=true`, see `.env.example`). The two
> `startup.sh` modes below pick the posture for you: `up` runs with the
> single-user dev bypass (no login screen), `up-with-dev-auth` runs the full
> local OIDC rig against a Keycloak container.

```bash
# Terminal 1 — quick single-user dev (Postgres + SearXNG + the backend container
# running your working tree, auth off; BACKEND_RUNTIME=venv for the bare-metal one)
./startup.sh up

# Or, the full local OIDC rig (also starts Keycloak on :18080, creates .env on
# first run with the local realm values + a generated SESSION_SECRET, waits for
# the realm import, then runs the backend with auth on):
./startup.sh up-with-dev-auth
# → sign in at http://localhost:5173 as Keycloak user admin-user / password
# Walkthrough (adding a second user, approval flow, PATs): deploy/README.md
# End-user guide (signing in, personal access tokens, admin section,
# chat budgets): docs/USER_GUIDE.md

# Or, to run the backend manually (no containers; TTS works, chat and documents need Postgres):
python run.py
# To fan TTS / audiobook synthesis across CPU cores (one Kokoro model
# loaded per worker — budget ~300–500 MB each on the ONNX-CPU build):
#   WORKERS=4 python run.py
# HOST and PORT env vars are also honoured.

# Terminal 2 — Start the frontend dev server (port 5173)
npm run dev
```

`up-with-dev-auth` sources `.env` (created from the local-dev rig values on
first run — see `deploy/README.md`); edit it to change ports or point at a
different IdP. `up` also reads `.env` but forces `AUTH_ENABLED=false` (the
backend's startup guard allows this only on a loopback bind). Ctrl-C on either
stops the backend and the containers cleanly; `./startup.sh stop` (or `down`)
does the same without starting anything.

#### On Windows

`startup.cmd` (or `startup.ps1` in PowerShell) has the same commands as
`startup.sh`. It runs on the Windows PowerShell 5.1 built into Windows 10/11,
and on PowerShell 7.

```bat
startup.cmd init           :: or: startup.cmd init podman
startup.cmd up             :: quick single-user dev, auth off
startup.cmd up-with-dev-auth
startup.cmd down
```

You need Docker Desktop (or Podman Desktop), Python 3.12 or 3.13 from
python.org with **Add to PATH** ticked, and Node.js. `startup.cmd` runs the
script with `-ExecutionPolicy Bypass` for that one run, so the machine's
execution policy can stay as it is. From a PowerShell prompt you can also run
`.\startup.ps1 up` directly if your policy allows local scripts.

These parts work differently from Linux:
- **Python:** the script uses the `py` launcher when it can, because
  `python.exe` on a fresh Windows is often the Microsoft Store stub.
- **The virtual environment** lives in `.venv\Scripts\`.
- **Stopping:** Ctrl-C reaches the backend directly, and it shuts down
  cleanly. `down` from another window has no gentler option on Windows: after
  5 seconds it ends the backend and every worker process it started
  (`taskkill /T`).

Open **http://localhost:5173** in your browser.

### 4. (Optional) Local AI Chat with Ollama

The chat mode talks to a locally running [Ollama](https://ollama.com/) server. Reader mode works without it — chat is purely opt-in.

```bash
# Install Ollama (https://ollama.com/download), then pull a chat model
ollama pull gemma3
# Reasoning model that produces a thinking trace
ollama pull deepseek-r1:1.5b
```

Ollama serves at `http://localhost:11434` by default. In the app, open **Chat** in the header; the model picker is in the chat sidebar. There is no host or port to set in the browser: the server finds Ollama at `OLLAMA_URL`.

### 5. (Optional) Document Chat & RAG

To use **Ask page**, **pinned context**, **Index this document**, and **autonomous tool calling** (see the [walkthrough](docs/CHAT_WITH_PDF.md) for the full tour), you need Postgres + an embedding model.

```bash
# Bring up Postgres + pgvector (port 5433 on the host to avoid colliding with a system Postgres on 5432)
docker-compose up -d postgres

# Pull the embedding model (768-dim — the schema is hard-locked to this)
ollama pull nomic-embed-text

# Optional: pull a chat model that supports Ollama's tools parameter (for autonomous search_documents)
ollama pull qwen2.5    # or llama3.1 / llama3.2 / mistral / gemma2
```

`python run.py` applies migrations on startup and exposes the new endpoints under `/v1/chat/sessions/*` and `/v1/docs/*` — the existing Kokoro routes are unchanged. **Chat itself needs Postgres now** (every turn is written to it as it streams): if Postgres is unreachable, sending a chat message returns `503 db_unavailable` instead of a reply. TTS (`/v1/synthesize`, `/v1/batch_synthesize`) doesn't touch Postgres and keeps working regardless.

### 6. (Optional) Web search (`web_search` tool)

The chat can use the live web via a self-hosted **SearXNG** instance. A search returns numbered results (title, site, address, snippet) and fetches nothing. By default the assistant only *offers* a search: a card under its reply shows the query in an editable box, and nothing leaves the server until you press **Search the web**. After a yes it can read 1-3 of the results: a small separate model call (the reader: the chat model itself unless you name another under **Admin → Models → Reader** or in `READER_MODEL`) reads each page for the assistant's goal and returns only facts with quotes the server has checked against the page, and the answer cites the sites. Admins choose how it asks under **Admin → Chat options → Web search** (*Ask first*, *Search without asking*, *Off*); `CHAT_WEB_SEARCH=ask|auto|off` is the starting value.

> `./startup.sh up` does the `settings.yml` bootstrap below automatically (copies the
> template and injects a `secret_key`) and brings SearXNG up alongside Postgres.
> The manual steps here are for running SearXNG on its own.

```bash
# Create the SearXNG config from the template and set a real secret_key
cp searxng/settings.yml.example searxng/settings.yml
sed -i "s/CHANGE_ME_openssl_rand_hex_32/$(openssl rand -hex 32)/" searxng/settings.yml

# Start SearXNG on 127.0.0.1:18043 (settings.yml is gitignored — it holds the secret_key)
docker-compose up -d searxng

# Optional: pull a separate, small reader model. Without READER_MODEL (or Admin → Models → Reader)
# the reader is the chat model itself, so there is nothing to pull.
ollama pull llama3.2:3b

# Verify JSON search works — must return JSON, not a 403/HTML page
curl -s "http://localhost:18043/search?q=test&format=json" | head -c 80
```

**A 403 means SearXNG's JSON format is disabled** — check that `searxng/settings.yml` lists `json` under `search.formats`.

All knobs are optional; see the `WEB_SEARCH_*` and `SEARXNG_URL` entries in `.env.example`. `WEB_SEARCH_RESULT_COUNT` (default 5) is how many results one search lists; pages are fetched only when the assistant reads them. The reader's knobs are the `READER_*` entries; see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#project-chat-the-reader-and-web-search).

**Security note:** the backend only fetches URLs whose host resolves to a public IP — private/loopback/link-local addresses (including cloud metadata endpoints) are refused, and every redirect hop is re-checked.

### Hardware Acceleration

The TTS backend uses the best execution provider that the installed ONNX
Runtime build offers. `requirements.txt` installs the **CPU** build, and so
does the container image. To accelerate a bare-metal install, swap the build
in your venv. Remove `onnxruntime` first: `kokoro-onnx` pulls it in, and two
builds in one venv overwrite each other, which leaves you silently on CPU.

| Priority | Hardware | Package | Notes |
|----------|----------|---------|-------|
| 1 | **NVIDIA GPU** | `onnxruntime-gpu` | CUDA acceleration |
| 2 | **Intel Arc GPU** | `onnxruntime-openvino openvino` | OpenVINO discrete GPU |
| 3 | **Intel NPU** | `onnxruntime-openvino openvino` | Core Ultra Neural Processing Unit |
| 4 | **Intel CPU** | `onnxruntime-openvino openvino` | AVX/VNNI optimizations |
| 5 | **CPU** | `onnxruntime` | Standard fallback |

```bash
# Intel Arc GPU / NPU / CPU via OpenVINO
pip uninstall -y onnxruntime && pip install onnxruntime-openvino openvino
# NVIDIA GPU
pip uninstall -y onnxruntime && pip install onnxruntime-gpu
```

Check the backend's startup line `Available ONNX Runtime providers: [...]`:
it must list `OpenVINOExecutionProvider` or `CUDAExecutionProvider`, or you are
on CPU. The container is CPU-only (see `docs/DEPLOYMENT.md`).

---

## 📖 Usage

### System voices (no neural voice needed)

1. Upload a PDF via the upload button or drag-and-drop
2. Open **Settings → Voice & reading** and click **Voice backend** until it reads **System voices**
3. Press **Play** — the browser's built-in Web Speech API reads the text

The backend still has to be running: sign-in and the app's gate depend on it, and without it the app stops at "The backend isn't responding."

### Kokoro (neural voices)

1. Start the backend (`./startup.sh up`, or `python run.py`)
2. **Voice backend** under **Settings → Voice & reading** reads **Kokoro (server)**, the default
3. Upload a PDF and press Play

### API Endpoints

The frontend talks to one backend, set under **Settings → Connection**. **Leave the host field blank to hit the same origin the page was served from** — the default, and the only setup in which sign-in cookies work. The backend is the only thing that talks to model providers.

#### Kokoro TTS (FastAPI server in this repo, default `localhost:8000`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/health` | `GET` | Health check — verifies the model is loaded. Cheap; safe to call during playback. |
| `/v1/synthesize` | `POST` | Synthesize one block of text → Base64 WAV audio. Used for per-sentence reader playback, selection-read, voice preview, and chat TTS. |
| `/v1/batch_synthesize` | `POST` | Synthesize multiple sentences → single merged WAV with 0.3 s silence between. Used by "Download Page Audio". |

#### Document Chat & RAG (same FastAPI server, new in `v1.6.0`)

All endpoints return `503` when Postgres is unreachable.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/chat/sessions` | `GET` | List session metadata, newest first. |
| `/v1/chat/sessions/{id}` | `GET / PATCH / DELETE` | Per-session read / rename / delete. The server writes messages itself as a turn streams; there's no client-side upsert of the whole record anymore. |
| `/v1/chat/sessions/import` | `POST` | Create-only: copies a legacy browser-only (IndexedDB) chat onto the server the first time you send a message on it. |
| `/v1/chat/sessions/{id}/turns` | `POST` | **Run one chat turn.** Server-sent events (`text/event-stream`): text/reasoning deltas, tool calls and their results, a final `finish`, or a terminal `error`; always ends with `data: [DONE]`. One turn per session at a time — a second send gets `409 turn_in_progress`. |
| `/v1/docs` | `POST / GET` | `POST`: upload a file (multipart). The server hashes the bytes itself (that SHA-256 is the `doc_id`) and extracts + indexes new content in the background; known bytes just add it to your library. `GET`: the documents you can read (`?q=`, `?project_id=`, `?tag=`). |
| `/v1/docs/{doc_id}` | `GET / PATCH / DELETE` | Status (`state`, `chunk_count`, `embedded_count`, model, dim); rename/retag **your** entry; remove it from **your** library (the content goes when nobody holds it). |
| `/v1/docs/{doc_id}/file` | `GET` | The stored file, for anyone who can read the document (404 otherwise). The Library's **Open** button. |
| `/v1/docs/{doc_id}/index` | `POST` | Resume, or re-index from the stored file; returns 202. Poll the doc status endpoint for progress. |
| `/v1/docs/{doc_id}/search` | `POST` | `{query, k}` → the top-k chunks by cosine similarity, an exact search within the document. It is meaning-only: the chat's `search_documents` tool adds exact-word matching. Answers 409 `reindexing` while the document is rebuilt for a new embedding model. |

Sharing, projects, conversion and who may call what: [docs/LIBRARY.md § API surface](docs/LIBRARY.md#api-surface). There is no chunk-upload route: chunks are always derived on the server.

#### Voice input (same FastAPI server)

Both routes need the `chat` capability (a personal access token, the `chat` scope). Docs: [docs/DEPLOYMENT.md § Voice input](docs/DEPLOYMENT.md#voice-input).

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/speech/status` | `GET` | `{enabled, ready, state, max_seconds, languages, model_languages}` for the message box (`model_languages` once voice is on: what the model is made for, empty for every language). Always `200`; builds nothing while voice input is off. |
| `/v1/speech/transcriptions` | `POST` | Multipart `file` (a recording, up to 10 MB and 125 s) and an optional `language` (a Whisper code, or `auto`). Answers `{text, language, duration_s}`. `404` when voice input is off, `409` while this person already has one running, `413` too big or too long, `503` while the engine is getting ready, busy for over 60 s, or failed. The recording is read in memory and never stored. |

#### Model providers (same FastAPI server)

Chat always runs on the server (`server/chat/`, `server/llm/`) against one or more configured **model providers** — native Ollama by default, plus any OpenAI-compatible server (vLLM, OpenRouter, LiteLLM) an admin adds in `.env`. OpenRouter has been run end to end (paid Gemma 4 and Mistral Small 3.2: chat, pins, tools, images, usage); vLLM and LiteLLM speak the same protocol but haven't been run. A refused request says what happened (rate-limited, key rejected, out of credit) — see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#configuring-providers). The browser never talks to a provider directly; there is no "local mode" and no in-app screen for adding providers (`.env` + restart, see [.env.example](.env.example) and [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)).

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/inference/models` | `GET` | Every provider's models as `{id, provider, kind, name, capabilities}` (`id` is `<provider>:<model>`; an Ollama model also carries `limits`, and a self-hosted one `context_size`), plus the caller's daily budget `{remaining_tokens, reset_at}`. |

Admin knobs: `INFERENCE_PROVIDERS` + `INFERENCE_<NAME>_*` (providers), `INFERENCE_MODELS` (legacy single-Ollama allowlist, still the default with no providers configured), `INFERENCE_DAILY_TOKEN_BUDGET` (default per-user daily tokens; resets at midnight in the deployment's timezone, **Admin → Deployment** or `TZ`, UTC if neither — a reply you stop or that fails still counts), per-user overrides under **Admin → People**, usage by model, person and day under **Admin → Usage** (`/v1/admin/users/{id}` and `/v1/admin/inference/usage` from a script). See [.env.example](.env.example).

#### Ollama (default local model provider, `localhost:11434`)

Ollama is the no-config default chat provider **and** always runs document/chat embeddings (`OLLAMA_URL`), even after other providers are added — removing it breaks indexing, not just chat:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/tags` | `GET` | Lists installed models. The backend calls this server-side; the browser never does. |
| `/api/chat` | `POST` | Streaming chat, called by the backend's Ollama adapter (`server/llm/providers/ollama.py`). Per-message `images: [...]` carries image attachments. |

<details>
<summary><strong>Request / Response Examples</strong></summary>

**`POST /v1/synthesize`**
```json
{
  "text": "Text to synthesize",
  "voice": "af_heart",
  "speed": 1.0
}
```

**Response:**
```json
{
  "audio_base64": "<base64-encoded-wav>",
  "duration_seconds": 2.5
}
```

**`POST /v1/batch_synthesize`**
```json
{
  "sentences": ["First sentence.", "Second sentence."],
  "voice": "af_heart",
  "speed": 1.0
}
```

**Response:**
```json
{
  "audio_base64": "<base64-encoded-wav>",
  "duration_seconds": 5.2,
  "sentence_count": 2
}
```

</details>

---

## 🌐 Reverse Proxy / Production Deployment

For production, the typical setup is to serve the frontend as static files from a web server (nginx, Caddy, …) and reverse-proxy the backend on the same hostname. The frontend supports this natively: leaving the **API host** field blank under **Settings → Connection** causes requests to be issued as same-origin paths (`/v1/synthesize`, `/v1/chat/sessions/{id}/turns`, …). nginx (or whatever sits in front) handles the routing. Chat always goes through the backend; no `/api/` proxy to Ollama is needed — see [docs/DEPLOYMENT.md § Model providers and chat streaming](docs/DEPLOYMENT.md#model-providers-and-chat-streaming) for the streaming-through-a-proxy trap.

### Example nginx config

A complete, battle-tested config (Ed25519 + RSA fallback, gzip, the works) lives at [`docs/sample_configs/chat.example.com.sample`](docs/sample_configs/chat.example.com.sample). The minimal version below is what's actually load-bearing:

> **Running multi-user (OIDC/Keycloak)?** This section covers the SPA + backend vhost only. For the full production picture — the second vhost that fronts Keycloak ([`docs/sample_configs/auth.example.com.sample`](docs/sample_configs/auth.example.com.sample)), the same-origin cookie rule, `COOKIE_SECURE`, the Keycloak proxy-header env, and the live-realm redirect-URI trap — see **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

```nginx
server {
    listen 80;
    server_name chat.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name chat.example.com;

    ssl_certificate     /etc/nginx/ssl/example.com.crt;
    ssl_certificate_key /etc/nginx/ssl/example.com.key;

    # Static frontend (output of `npm run build`)
    root  /var/www/chat.example.com/dist;
    index index.html;
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Backend (Kokoro TTS + doc RAG + server-side chat, server/chat/ + server/llm/)
    location /v1/ {
        proxy_pass http://127.0.0.1:8000/v1/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # Streaming (chat turns are text/event-stream) — disable response
        # buffering so tokens arrive live; long-lived synthesis needs the
        # generous timeout. The backend also sends X-Accel-Buffering: no.
        proxy_buffering off;
        proxy_read_timeout 86400;
        # Document uploads (POST /v1/docs) exceed nginx's 1 MB default → 413.
        client_max_body_size 100m;
    }

    # Ollama: NO location block. Chat always runs server-side against the
    # provider(s) configured in .env (native Ollama by default); the browser
    # never calls Ollama directly, and there is no "local mode" to proxy for.
    # Ollama stays backend-only (loopback bind).
}
```

### App configuration

Under **Settings → Connection**, leave **API host** blank (the default; its placeholder reads *blank = same origin*). The port is then ignored. Everything, chat included, goes to that one backend, and the backend is the only thing that ever calls Ollama.

Notes:

- **Ollama bind address.** By default Ollama listens on `127.0.0.1:11434`. That's fine here since only the backend talks to it. If you change `OLLAMA_HOST` to bind on a different interface, mirror it in the backend's provider config (`OLLAMA_URL` / `INFERENCE_<NAME>_URL`, see [.env.example](.env.example)).
- **Streaming.** `proxy_buffering off` on `/v1/` is required so chat turns stream token-by-token instead of arriving as one buffered chunk.
- **CORS.** Same-origin requests don't need CORS at all, and with `FRONTEND_ORIGIN` unset the backend allows no cross-origin caller.
- **Custom hostnames during development.** To test against a backend on another machine without proxying, set **API host** to an IP / hostname (e.g. `192.168.1.10`) and the matching port under **Settings → Connection**. Bare hostnames default to `http://`; you can also paste a full `https://example.com`. This crosses origins, so it only works with sign-in off or with `FRONTEND_ORIGIN` set on that backend. To move a model server, point the backend's `OLLAMA_URL` / provider `URL` at it instead.
- **Firefox + PDF.js worker (`.mjs` MIME type).** PDF.js's worker file is a `.mjs` ES module. nginx's stock `mime.types` doesn't list `.mjs`, so it serves it as `application/octet-stream`, and Firefox refuses to load it as a module — PDF parsing then falls back to a slow main-thread "fake worker" that often fails. Fix: serve `.mjs` with a JS MIME type. Add this **before** your `location /` block:
  ```nginx
  location ~ \.mjs$ {
      types { } default_type text/javascript;
      add_header Cache-Control "public, max-age=31536000, immutable";
      try_files $uri =404;
  }
  ```
  Chrome is more lenient and works without this; Firefox is doing the spec-correct thing.

---

## 🔒 Security & Hardening

### Authentication (OIDC, multi-user)

The backend authenticates via **OpenID Connect** — it's an OIDC Relying Party, so you point it at any provider (Keycloak, Authentik, Auth0, …) and it stores no passwords. Every document and chat session is owned by a user; you only ever see your own. Set the `OIDC_*` vars plus `SESSION_SECRET` (see [.env.example](.env.example)) to turn it on.

- **First-user-admin:** the first identity to log in becomes admin; everyone after is `pending` until an admin activates them (Admin → People). Set `BOOTSTRAP_ADMIN_EMAIL` to pre-designate the admin by email and inherit any pre-existing single-user data. **Caveat:** doing so routes that founder through the email-claim path, which does *not* force-grant capabilities — the pre-designated user must actually hold the `reader`/`chat`/`admin` realm roles in your IdP (or `KC_ADMIN_*` must be set for the app to self-heal them), or they log in to "active but no access". See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) Trap 4.
- **The web app** uses a revocable, `HttpOnly` session cookie. **The read-aloud extension and scripts** use a **personal access token** sent as `Authorization: Bearer …`. Create it under **Settings → Access tokens** with only the access it needs (the extension needs only *Read aloud*), then paste it into the extension's Options → Access token.
- **Local dev without an IdP:** `AUTH_ENABLED=false` treats every request as the admin — but the server **refuses to start** with this set on a non-loopback bind.

`/api/*` (Ollama) is **never used by the SPA** — chat always runs server-side ([`server/chat/`, `server/llm/`](#model-providers-same-fastapi-server)), so the nginx `/api/` proxy block should be **deleted**: Ollama becomes backend-only (loopback bind, nothing proxied). There is no per-user "bring your own Ollama" option in the browser.

### Threat model (pre-auth baseline)

The table below is the *un-authenticated* exposure — i.e. what OIDC now closes for `/v1/*`. The old `/api/*` rows (direct Ollama) are gone: the SPA no longer calls them, and the proxy block is removed.



| Endpoint | What an unauthenticated caller can do | Cost to you |
|---|---|---|
| `POST /v1/synthesize` | Generate arbitrary TTS audio of any length | GPU/CPU burn, electricity |
| `POST /v1/batch_synthesize` | Submit huge sentence arrays; with **Unlimited batch timeout** on, a single request can pin Kokoro's inference lock for hours | Same, amplified — practical DoS surface |

`/api/*` (direct Ollama) is closed now that chat runs on the authenticated backend — **delete the nginx `/api/` block** and keep Ollama bound to loopback. If you still proxy `/api/*` for other clients, everything in the old rows applies: any unauthenticated caller could run any installed model (`POST /api/chat`) or list models (`GET /api/tags`) — proxy-gate it or remove it.

CORS no longer helps either: with `FRONTEND_ORIGIN` unset, [server/app.py](server/app.py) lets no other website call the API from a browser. (It used to default to `allow_origins=["*"]`, for the read-aloud extension, which let any website drive your Kokoro endpoint from JavaScript. The extension now calls from its service worker and needs no CORS entry.) Set `FRONTEND_ORIGIN` only if the SPA is served from another origin; `*` brings the old behavior back. TTS payloads are now size-capped (`TTS_MAX_*`, see [.env.example](.env.example)) so a single request can no longer pin the inference lock indefinitely.

### What is *not* a vulnerability (worth saying out loud)

- **Chat history, sessions, document library** — in Postgres, behind sign-in and per-user access checks; what the browser keeps (settings, a local library) is in localStorage and IndexedDB, sandboxed per origin.
- **Bind addresses** — Kokoro and Ollama listen on `127.0.0.1` only by default (both now; Kokoro via [run.py](run.py)). The backend container uses host networking and keeps that `127.0.0.1` bind, so leave `HOST` unset behind nginx; `HOST=0.0.0.0` exposes the port on every interface. Only the proxy is internet-facing.
- **TLS** — terminated at nginx with a real cert; in-transit traffic is fine.
- **Input shapes** — there's no shell-out and no eval, and SQL is parameterized. The risk from model and TTS requests is *resource consumption*, not RCE.

### Mitigations (effort-ordered, stack as needed)

#### 1. HTTP Basic Auth at nginx — biggest payoff, smallest effort

The browser prompts once, remembers credentials for the session, and the backend becomes useless to anyone without them. An option for a single-user deployment that runs with sign-in off.

```bash
# One-time: create the password file
sudo htpasswd -B -c /etc/nginx/htpasswd you
# To add another user later, omit -c
sudo htpasswd -B    /etc/nginx/htpasswd teammate
```

In the backend `location` block:

```nginx
location /v1/ {
    auth_basic           "Neural Reader";
    auth_basic_user_file /etc/nginx/htpasswd;
    proxy_pass           http://127.0.0.1:8000/v1/;
    # ... existing headers / timeouts ...
}
```

`sudo nginx -t && sudo systemctl reload nginx`. Done.

#### 2. IP allowlist — for static-IP / VPN setups

Zero credentials to manage. Brittle if your IP changes.

```nginx
location /v1/ {
    allow 203.0.113.42;     # your home IP
    allow 10.0.0.0/8;       # internal LAN / VPN
    deny  all;
    proxy_pass http://127.0.0.1:8000/v1/;
    # ...
}
```

#### 3. Rate limiting — layer this under any auth

Caps how fast a single client can hammer the daemons. Useful even for "just you" deployments — keeps a runaway script from pegging the GPU.

In the top-level `http {}` block of `/etc/nginx/nginx.conf`:

```nginx
limit_req_zone $binary_remote_addr zone=tts:10m  rate=5r/s;
limit_req_zone $binary_remote_addr zone=chat:10m rate=2r/s;
```

Then inside each location:

```nginx
location /v1/ {
    limit_req zone=tts burst=10 nodelay;
    # ...
}
location /v1/chat/ {
    limit_req zone=chat burst=3 nodelay;
    # ...
}
```

#### 4. Keep CORS closed

With `FRONTEND_ORIGIN` unset the backend lets no other website call it from a browser. Set it only if the SPA is served from another origin, to that exact origin (`FRONTEND_ORIGIN=https://chat.example.com`); `*` lets every website call the API, without cookies.

#### 5. Personal access tokens — built in

Scripts and the read-aloud extension authenticate with `Authorization: Bearer nrp_…`, created under **Settings → Access tokens**, each limited to what it's for and optionally expiring. See [docs/USER_GUIDE.md](docs/USER_GUIDE.md#access-tokens).

### Quick picks by deployment shape

| Deployment | Recommended stack |
|---|---|
| **Personal — just you** | Basic auth with sign-in off, or sign-in alone. Five lines of nginx. |
| **Small team / family** | Sign-in (OIDC) + rate limit. Each person gets their own account and capabilities. |
| **Public-ish demo** | Sign-in + rate limit + `INFERENCE_MODELS` allowlist (the server never offers models you don't list) + `INFERENCE_DAILY_TOKEN_BUDGET`. |

The sample [docs/sample_configs/chat.example.com.sample](docs/sample_configs/chat.example.com.sample) includes the above mitigations as **commented-out blocks at the bottom of the file** — uncomment what you need and reload nginx.

---

## 🎭 Available Voices

<details>
<summary><strong>US Voices (20)</strong></summary>

| Voice ID | Name | Gender |
|----------|------|--------|
| `af_heart` | Heart | Female *(default)* |
| `af_bella` | Bella | Female |
| `af_alloy` | Alloy | Female |
| `af_aoede` | Aoede | Female |
| `af_jessica` | Jessica | Female |
| `af_kore` | Kore | Female |
| `af_nicole` | Nicole | Female |
| `af_nova` | Nova | Female |
| `af_river` | River | Female |
| `af_sarah` | Sarah | Female |
| `af_sky` | Sky | Female |
| `am_michael` | Michael | Male |
| `am_adam` | Adam | Male |
| `am_echo` | Echo | Male |
| `am_eric` | Eric | Male |
| `am_fenrir` | Fenrir | Male |
| `am_liam` | Liam | Male |
| `am_onyx` | Onyx | Male |
| `am_puck` | Puck | Male |
| `am_santa` | Santa | Male |

</details>

<details>
<summary><strong>UK Voices (8)</strong></summary>

| Voice ID | Name | Gender |
|----------|------|--------|
| `bf_emma` | Emma | Female |
| `bf_alice` | Alice | Female |
| `bf_isabella` | Isabella | Female |
| `bf_lily` | Lily | Female |
| `bm_daniel` | Daniel | Male |
| `bm_fable` | Fable | Male |
| `bm_george` | George | Male |
| `bm_lewis` | Lewis | Male |

</details>

<details>
<summary><strong>Other languages (22)</strong></summary>

| Voice ID | Name | Language | Gender |
|----------|------|----------|--------|
| `hf_alpha` | Alpha | Hindi | Female |
| `hf_beta` | Beta | Hindi | Female |
| `hm_omega` | Omega | Hindi | Male |
| `hm_psi` | Psi | Hindi | Male |
| `ef_dora` | Dora | Spanish | Female |
| `em_alex` | Alex | Spanish | Male |
| `em_santa` | Santa | Spanish | Male |
| `ff_siwis` | Siwis | French | Female |
| `if_sara` | Sara | Italian | Female |
| `im_nicola` | Nicola | Italian | Male |
| `pf_dora` | Dora | Portuguese | Female |
| `pm_alex` | Alex | Portuguese | Male |
| `pm_santa` | Santa | Portuguese | Male |
| `jf_alpha` | Alpha | Japanese | Female |
| `jf_gongitsune` | Gongitsune | Japanese | Female |
| `jf_nezumi` | Nezumi | Japanese | Female |
| `jf_tebukuro` | Tebukuro | Japanese | Female |
| `jm_kumo` | Kumo | Japanese | Male |
| `zf_xiaobei` | Xiaobei | Chinese | Female |
| `zf_xiaoni` | Xiaoni | Chinese | Female |
| `zf_xiaoxiao` | Xiaoxiao | Chinese | Female |
| `zf_xiaoyi` | Xiaoyi | Chinese | Female |

Each voice reads its own language best. Sinhala and Tamil have no voices of their own, but any voice can read them. Japanese voices read kana but not kanji.

</details>

---

## 📁 Project Structure

```
natural-reader/
├── src/
│   ├── App.jsx                # Main application — wires hooks and components
│   ├── main.jsx               # React entry point
│   ├── constants.js           # Voice definitions, keyboard shortcuts
│   ├── db.js                  # IndexedDB: document library + legacy chat sessions (v3)
│   ├── index.css              # Global styles (Tailwind)
│   ├── hooks/
│   │   ├── usePdfEngine.js       # PDF + .txt + .md loading, rendering, text extraction, library, extractAllChunks
│   │   ├── useTtsEngine.js       # TTS playback loop, caching, voice preview, chat audio channel
│   │   ├── useChatEngine.js      # Session state, SSE turn streaming (chatStream.js + chatEvents.js reducer), pins, per-session event log, chat TTS queue
│   │   ├── useVoiceInput.js      # The microphone: record, Stop/Cancel/Escape, upload to /v1/speech/transcriptions, put the text in the box
│   │   ├── usePersistedState.js  # localStorage-backed state + reading progress
│   │   ├── useKeyboardShortcuts.js
│   │   ├── useMobileDetect.js
│   │   └── useTheme.js
│   ├── lib/
│   │   ├── sessionStore.js       # Postgres-or-IndexedDB session dispatcher (legacy IDB sessions → read-only LOCAL badge, imported to Postgres on first send)
│   │   ├── serverDocFile.js      # GET /v1/docs/{id}/file → a File the reader opens (Library → Open)
│   │   ├── openDoc.js            # Open a server document (Library row or chat citation), then go to a page
│   │   ├── citations.js          # "(page N)" citations in replies → buttons (remark plugin)
│   │   ├── chatStream.js         # POST a turn + read its SSE response (partial lines, [DONE], abort)
│   │   ├── chatEvents.js         # Pure reducer: turn events → message state (text, thinking, tool panel, status)
│   │   └── chatTransport.js      # GET /v1/inference/models, budget parsing (no tool code here anymore — tools run server-side)
│   ├── utils/
│   │   ├── attachment.js         # File → Attachment helper, size caps, strip-on-save for IndexedDB
│   │   ├── textAttachment.js     # Reading a text file, and masking its secrets (detectors report ranges; one render writes the placeholders)
│   │   ├── docHash.js            # sha256 of file bytes → stable doc_id (Web Crypto, lazy + memoized)
│   │   ├── markdownToSpeech.js   # Strips Markdown markup before chat TTS synthesis
│   │   ├── url.js                # Builds API URLs, returns relative paths when host is blank
│   │   └── wavConcat.js          # Stitches multiple PCM WAV blobs into one (audiobook export, v1.7)
│   └── components/
│       ├── Header.jsx              # Top toolbar with Reader/Chat toggle + playback + audiobook + home + distraction-free
│       ├── HeaderOverflowMenu.jsx  # `⋯` dropdown for secondary actions on mobile (sm:hidden)
│       ├── Sidebar.jsx             # Reader sidebar: sentence list, chapters, settings, voice picker
│       ├── ChatSidebar.jsx         # Chat sidebar: model picker (grouped by provider), sessions (with LOCAL badge), log — no host/port config; providers are a server .env setting
│       ├── PdfViewer.jsx           # Branches between PDF canvas / TextPageRenderer / MarkdownReader; toolbar hosts Ask page, Index, Convert, PDF|MD toggle
│       ├── IndexButton.jsx         # Toolbar control: idle → uploading → indexing N/M → indexed (or failed)
│       ├── ConvertButton.jsx       # Toolbar control for docling conversion lifecycle (idle → uploading → converting → converted)
│       ├── DoclingConvertDialog.jsx # Options modal (quality preset, OCR, tables, images, page range)
│       ├── MarkdownReader.jsx      # Reader view for docling-converted documents (per-page anchors + page-nav sync)
│       ├── TextPageRenderer.jsx    # Renders a .txt pseudo-page with sentence highlighting
│       ├── MarkdownPageRenderer.jsx # Renders a .md page (react-markdown + remark-gfm) with paragraph-level highlighting
│       ├── ChatView.jsx            # Chat list, prompt box, attachments, DocContextChip, ToolCallsDisclosure, per-message stats / read-aloud / copy / audio download
│       ├── AttachmentPreview.jsx   # Image-thumbnail / text-file chip (name, size, "N values masked") used in pending bar + bubbles
│       ├── TextFileViewer.jsx      # Click a text chip: the exact text that will be sent, plus Send real values
│       ├── chat/MicButton.jsx      # The microphone / Stop / Cancel control beside the paperclip (state lives in hooks/useVoiceInput.js)
│       ├── MobileBottomNav.jsx
│       ├── WelcomeScreen.jsx       # Library and upload landing page
│       ├── admin/, library/, projects/, people/, settings/, account/, auth/, chat/ # The console, Library, project pages, people picker, Settings, tokens, sign-in screens, scope chip + web card
│       └── overlays/               # Drag, toast, context menu, shortcuts modal, ReadSelectionButton (+ Ask AI)
├── server/
│   ├── __init__.py
│   ├── app.py                 # FastAPI app factory — mounts TTS + chat_sessions/chat_turns + docs routers, runs init_db + start_router on startup
│   ├── db.py                  # psycopg async pool + migration runner + pgvector codec registration
│   ├── endpoints.py           # /v1/synthesize, /v1/batch_synthesize, /v1/health
│   ├── model.py               # Kokoro ONNX loading with GPU/NPU/CPU fallback
│   ├── schemas.py             # Pydantic request models (TTS)
│   ├── sql/                   # 001-023, applied in order at startup; the interesting ones are in docs/ARCHITECTURE.md
│   ├── routers/
│   │   ├── chat_sessions.py   # /v1/chat/sessions/* — list / get / patch / delete / import (legacy-chat copy)
│   │   ├── chat_turns.py      # POST /v1/chat/sessions/{id}/turns — runs one turn, frames orchestrator events as SSE
│   │   ├── docs.py            # /v1/docs/* — upload / list / status / file / shares / index / search / convert / markdown
│   │   ├── inference.py       # GET /v1/inference/models — every provider's models + the caller's budget
│   │   ├── projects.py, project_documents.py # /v1/projects/* — projects, members, activity, document versions
│   │   ├── people.py, profile.py, settings.py, tools.py # people search, avatars, per-user settings, POST /v1/tools/web_search
│   │   ├── admin.py           # /v1/admin/* — the admin console's API (people, overview, settings sections, usage)
│   │   └── auth.py            # /v1/auth/* — OIDC login/callback, sessions, PATs
│   ├── auth/                  # OIDC, sessions, tokens, capabilities, access checks (authz.py)
│   ├── chat/                  # The chat orchestrator (server-side turn loop, C1)
│   │   ├── orchestrator.py    # run_turn: one step per model call, tool rounds, failures, event emission
│   │   ├── context.py         # Stage 0: document prefetch, time-in-prompt, history trimming
│   │   ├── store.py           # Turn claims/heartbeat/recovery, message + event persistence
│   │   ├── config.py          # CHAT_* env knobs (tool rounds, prefetch, trimming, request size cap)
│   │   └── tools/              # search_documents, read_document_pages, extract_from_documents, document_outline, web_search, read_web_pages, offer_web_search — one file per tool, server-side only
│   ├── llm/                   # Model provider layer (C1)
│   │   ├── router.py          # Loads INFERENCE_PROVIDERS config, resolves "<provider>:<model>" ids
│   │   ├── types.py           # Internal message/event types every adapter maps to/from
│   │   └── providers/         # ollama.py, openai_compat.py adapters + shared base.py helpers
│   ├── speech/                # Voice input: a Transcriber interface, the local (faster-whisper) and openai engines, a bounded audio decoder
│   └── services/
│       ├── embeddings.py      # httpx client → Ollama /api/embeddings via model_router, Semaphore(4), dim assertion
│       ├── model_router.py    # Legacy single-Ollama config: allowlist, embedding model, daily budget
│       ├── inference_budget.py # per-user daily token accounting, per model and kind, in the deployment's timezone — also charges stopped/failed replies
│       ├── web_search.py      # SearXNG results + SSRF-guarded page fetch and passage picking
│       └── docling_convert.py # PDF → per-page Markdown via Docling (Fast/Standard/Accurate presets)
│       # plus doc_* (content, storage, search, pipeline, versions), model_settings, chat_options, console_* and more: docs/ARCHITECTURE.md
├── data/
│   └── pdfs/                  # Uploaded document bytes (one file per doc_id)
├── docker-compose.yml         # Postgres (host port 5433), Keycloak, SearXNG and the backend
├── Containerfile              # The backend image
├── deploy/                    # The local OIDC rig: realm export, Postgres init, deploy/README.md
├── extension/                 # The Chrome read-aloud extension
├── scripts/                   # Chat evals and the project-search benchmark
├── docs/
│   ├── README.md              # The docs index: start here
│   ├── USER_GUIDE.md          # For people using the app, admin console included
│   ├── ARCHITECTURE.md        # The whole system map
│   ├── CHAT_WITH_PDF.md       # Document and project chat, end to end (+ §10 perf)
│   ├── LIBRARY.md             # Documents, sharing, projects, versions
│   ├── IDENTITY_AND_ROLES.md  # Keycloak users and app users
│   ├── TESTING.md             # What covers what, and how to run it safely
│   ├── DEPLOYMENT.md          # Production behind nginx + TLS: topology, config table, the traps
│   ├── Release_notes/         # Tag-page notes per release
│   └── sample_configs/        # nginx vhosts (SPA + /v1, Keycloak) and a deploy script sample
├── run.py                     # Server entry point (uvicorn) — honours WORKERS / HOST / PORT env vars
├── requirements.txt           # Python dependencies
├── vite.config.js             # Vite + Rolldown config with chunk splitting
├── tailwind.config.js
├── package.json
└── index.html
```

---

## 📜 Scripts

```bash
npm run dev      # Start Vite dev server
npm run build    # Build for production
npm run preview  # Preview production build
npm run lint     # Run ESLint

# How well does a chat model answer questions about a document? Runs the real
# chat path against a document with planted facts (needs Postgres, the
# embedding model and the chat model; see docs/CHAT_WITH_PDF.md §6.5).
python scripts/eval_doc_qa.py --model ollama:llama3.2:3b
```

---

## 💡 Tips

### Reader
- **Resume Reading** — Reopen the same PDF to automatically continue from your last position
- **Library** — Recent books are persisted in IndexedDB — click to instantly resume
- **Keyboard Navigation** — Use keyboard shortcuts for hands-free control
- **Prefetching** — The next 2 sentences are pre-fetched for seamless playback
- **Dark Mode** — Toggle with the moon/sun icon or `Ctrl + D`
- **Right-Click Menu** — Right-click any sentence for "Continue from here" and copy options
- **Read Selection** — Select text on the PDF, then click the floating "Read Selection" button
- **Download Audio** — Export the current page's audio as a WAV file for offline listening

### Chat
- **Drop / paste an image** — Drop an image file anywhere on the chat view, or `Ctrl + V` a screenshot directly into the prompt textarea. Multiple images per message are supported.
- **Model stats** — Click the small `⚡` chevron under any reply to expand a per-turn breakdown of load time, prompt eval, generation throughput, and `done_reason`. Useful for comparing models or spotting cold-start cost.
- **Scroll while streaming** — Scroll up at any time during a reply; the chat will stop tailing tokens and let you read history. Scroll back near the bottom and tailing resumes automatically.
- **Sessions** — Every chat is saved automatically; switch / rename / delete from the sidebar. Click "New chat" to start a fresh thread without losing the current one.
- **Reasoning models** — For `deepseek-r1`, `qwen3-thinking`, etc., set **Thinking** under **Settings → Chat**. The reasoning trace appears in a collapsible disclosure above the answer (auto-expanded while streaming).

---

## 📋 Logging

The backend logs to **both the console and a time-rotating file** at
`logs/server.log` (a new file at midnight, 5 old ones kept; `logs/` is gitignored). This captures TTS,
chat, embedding, and `web_search` activity — including SSRF blocks, failed or
slow page fetches, and the reader's failed or timed-out calls and dropped facts (counts and error types, never page text). Tune it with env vars (see `.env.example`):

| Var | Default | Controls |
|-----|---------|----------|
| `LOG_LEVEL` | `INFO` | Verbosity (`DEBUG` / `INFO` / `WARNING` / …) |
| `LOG_DIR` | `./logs` | Directory for the log file |
| `LOG_FILE_ROLL_OVER_TIME` | `midnight` | When a new log file starts: `midnight`, `H` (hourly), `D`, `W0`–`W6` (weekly) |
| `LOG_FILE_BACKUPS` | `5` | How many old files to keep (at midnight: days) |

Frontend logs stay in the browser devtools console; Postgres and SearXNG keep their
own container logs (`docker-compose logs <service>`).

---

## 🔧 Build Optimization

The project uses Rolldown (via `rolldown-vite`) with optimized chunk splitting:
- **vendor-react** — React core split into a separate chunk for long-term caching
- **vendor-pdfjs** — PDF.js bundled locally (no CDN dependency)
- **vendor-icons** — Lucide icons isolated for efficient tree-shaking

---

## 🤝 Contributing

Contributions are welcome! See **[CONTRIBUTING.md](CONTRIBUTING.md)** for setup, the
test/lint commands, branch and commit conventions, and how larger features are
designed. **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** explains how the
components fit together (the same-origin `/v1` rule, auth flow, TTS lock, chat
engine, doc pipelines, and the invariants to keep intact). In short:
`./startup.sh init && ./startup.sh up`, keep both test suites
green (`npm run test:run`, and `.venv/bin/python -m pytest server/tests` with
`TEST_DATABASE_URL` and `TEST_ADMIN_DATABASE_URL` set: a bare run connects to the
app's own Postgres, see [docs/TESTING.md](docs/TESTING.md#how-to-run)), and open PRs against
`master` using [Conventional Commits](https://www.conventionalcommits.org/).

## 📄 License

Released under the [MIT License](LICENSE) © 2026 Udara Vimarsha.
