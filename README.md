# Neural Reader

A modern, feature-rich document reader with **neural text-to-speech** powered by **[Kokoro TTS](https://github.com/hexgrad/kokoro)**, an **optional local-AI chat mode** powered by **[Ollama](https://ollama.com/)**, and (new in `v1.6.0`) **document-aware chat with RAG + autonomous tool calling** backed by **Postgres + pgvector**. Open PDFs, `.txt`, or `.md` files, have them read aloud with natural-sounding voices, ask the model about what you're reading, or let the model search the indexed doc on its own.

> 🎯 **A web frontend for Kokoro TTS — now with chat that knows what you're reading.** Beyond the TTS reader and the standalone Ollama chat side-mode, the app can index a loaded document into pgvector and expose a `search_document` tool that your local LLM calls autonomously when a question warrants it. Everything stays local: Ollama for the LLM + embeddings, Postgres in a container for chat sessions and vectors, no cloud round-trips. A browser-based Web Speech fallback is also available for testing without any backend.

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
- **27 Voice Options** — Wide selection of US and UK male/female voices with live preview
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
- **Streaming Replies** — Token-by-token streaming from a local Ollama server (`/api/chat`)
- **Model Picker** — Auto-populated from `/api/tags`; configurable host/port (defaults to `localhost:11434`, leave blank for same-origin)
- **Image Attachments** — Paperclip button, drag & drop onto the chat view, and `Ctrl + V` paste images from the clipboard. Thumbnails preview above the prompt and persist in user bubbles. Sent to vision-capable models via the per-message `images: [base64]` field. *(Audio attachments are temporarily paused — see CHANGELOG.)*
- **Model Response Stats** — Every assistant bubble has a collapsible footer showing token count, total time, and tokens/sec. Expanded view breaks out load / prompt-eval / generation phases for fine-grained latency inspection.
- **Stick-to-Bottom Scroll** — The chat list auto-tails streaming tokens when you're at the bottom; scrolling up pauses the auto-follow so you can read history during a long response, and resumes when you scroll back down.
- **Chat TTS** — Replies are read aloud through the same Kokoro pipeline, with bounded prefetch to avoid gaps
- **Streaming vs After-Complete TTS** — Read sentences as they stream in, or wait for the full reply before reading
- **Per-Message Read Aloud** — A `🔊 Read aloud` / `■ Stop` button on every assistant bubble — works even with auto-TTS off, or to re-read finished messages later
- **Markdown Rendering** — Lists, code blocks, tables, headings, links render natively in chat bubbles
- **Markdown-Aware TTS** — Markup is stripped before synthesis so audio reads visible text only (no "star star bold")
- **Reasoning Trace Toggle** — Enable thinking to send `think: true` to Ollama and see reasoning trace (deepseek-r1, qwen3-thinking, gpt-oss, …) in a collapsible disclosure that auto-expands while streaming
- **Per-Message Copy** — One-click copy of any chat message to clipboard

### 📑 Document Chat & RAG *(new in `v1.6.0`)*

A full end-to-end walkthrough lives in [docs/CHAT_WITH_PDF.md](docs/CHAT_WITH_PDF.md). Headline capabilities:

- **Ask page** — One toolbar click sends the current page text (~8000 char cap) to the model as a chat preamble. No indexing required.
- **Ask AI on a selection** — Highlight any text on the rendered page and send just that snippet as context. Paired with the existing "Read Selection" TTS button.
- **Index this document** — Backed by **Postgres + pgvector**. Frontend extracts per-page (PDF), per-block (Markdown), or per-pseudo-page (TXT) chunks; backend embeds them via Ollama's `nomic-embed-text` (768-dim) and stores them in an HNSW-indexed `vector` column. Re-indexing is idempotent (`UNIQUE (doc_id, text_hash)`).
- **Use whole document** — A checkbox on the doc-context chip folds the top-3 semantically-retrieved chunks from the indexed doc into the chat preamble with bracketed citations the model is told to use.
- **Autonomous tool calling** — When a doc is indexed and the chat model supports Ollama's `tools` parameter, the model gets a `search_document` tool it can invoke on its own. The frontend executes it, hands the result back, and the model streams the final answer. Single-iteration cap to prevent loops; falls back gracefully on models without tool support (the field is silently ignored). Tool calls are persisted in a `tool_calls` JSONB column and re-rendered as a 🔎 disclosure on the assistant bubble.
- **Postgres-backed chat sessions** — Sessions previously stored in IndexedDB now write to Postgres via a new `src/lib/sessionStore.js` abstraction. Legacy IDB sessions stay readable with a small **LOCAL** badge; the first edit on one forks to a fresh Postgres session, leaving the original intact.
- **Pluggable tool registry** — `src/lib/chatTools/` houses one tool per file with `{name, definition, when(ctx), execute(args, ctx)}`. Adding `web_search`, `read_url`, etc. later is one new file + one line in the registry index. See `src/lib/chatTools/_example.js`.

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
- **Chat Sessions (IndexedDB)** — Every chat is auto-saved; switch / rename / delete from the sidebar; auto-named from the first prompt; LRU-capped at 50 sessions
- **Per-Session Event Log** — `sent`, `received`, `aborted`, `error` events with timestamps, viewable as a collapsible log
- **One-Click Resume** — Click any document in the library to continue reading
- **Settings Saved** — Voice, speed, volume, zoom, theme, Ollama host/port, model, TTS mode all persist across sessions
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
| **Storage** | localStorage + IndexedDB (`books` + `chat_sessions` stores) |
| **TTS Backend** | FastAPI, Kokoro ONNX, Uvicorn |
| **TTS Inference** | ONNX Runtime (CUDA / OpenVINO / CPU) |
| **Chat Backend (optional)** | [Ollama](https://ollama.com/) — local LLM server (default `:11434`) |
| **Doc Chat Backend (optional, new in 1.6)** | FastAPI routers: `/v1/chat/sessions/*` and `/v1/docs/*` |
| **Doc Storage / RAG** | Postgres 16 + [pgvector](https://github.com/pgvector/pgvector) (HNSW, cosine), embedded via Ollama `nomic-embed-text` |
| **DB driver** | `psycopg[binary,pool]` (async) with `pgvector` codec registered per-connection |

---

## 🚀 Getting Started

### Prerequisites

- **Node.js 18+** and npm
- **Python 3.10+** (Kokoro TTS backend; new doc-chat routes use `|`-style unions)
- **(Optional, for Document Chat & RAG)** Docker + Docker Compose, *or* a host Postgres 16 with the `pgvector` extension
- **(Optional, for chat)** [Ollama](https://ollama.com/) — needs a chat model AND `nomic-embed-text` if you want indexing/retrieval/tool calling

### 1. Clone & Install Dependencies

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

```bash
# Terminal 1 — Start the Kokoro TTS backend (port 8000)
python run.py

# Terminal 2 — Start the frontend dev server (port 5173)
npm run dev
```

Open **http://localhost:5173** in your browser.

### 4. (Optional) Local AI Chat with Ollama

The chat mode talks to a locally running [Ollama](https://ollama.com/) server. Reader mode works without it — chat is purely opt-in.

```bash
# Install Ollama (https://ollama.com/download), then pull a chat model
ollama pull gemma3
# Reasoning model that produces a thinking trace
ollama pull deepseek-r1:1.5b
```

Ollama serves at `http://localhost:11434` by default. In the app, toggle to **Chat** in the header — host/port and model picker live in the chat sidebar.

### 5. (Optional) Document Chat & RAG

To use **Ask page**, **Index this document**, **Use whole document**, and **autonomous tool calling** (see the [walkthrough](docs/CHAT_WITH_PDF.md) for the full tour), you need Postgres + an embedding model.

```bash
# Bring up Postgres + pgvector (port 5433 on the host to avoid colliding with a system Postgres on 5432)
docker-compose up -d postgres

# Pull the embedding model (768-dim — the schema is hard-locked to this)
ollama pull nomic-embed-text

# Optional: pull a chat model that supports Ollama's tools parameter (for autonomous search_document)
ollama pull qwen2.5    # or llama3.1 / llama3.2 / mistral / gemma2
```

`python run.py` applies migrations on startup and exposes the new endpoints under `/v1/chat/sessions/*` and `/v1/docs/*` — the existing Kokoro routes are unchanged. If Postgres is unreachable, those new routes return `503` but **TTS and the regular Ollama chat keep working** (the chat layer streams against Ollama directly; only session persistence depends on Postgres).

### Hardware Acceleration

The TTS backend automatically detects and uses the best available hardware:

| Priority | Hardware | Package | Notes |
|----------|----------|---------|-------|
| 1 | **NVIDIA GPU** | `onnxruntime-gpu` | CUDA acceleration |
| 2 | **Intel Arc GPU** | `onnxruntime-openvino openvino` | OpenVINO discrete GPU |
| 3 | **Intel NPU** | `onnxruntime-openvino openvino` | Core Ultra Neural Processing Unit |
| 4 | **Intel CPU** | `onnxruntime-openvino openvino` | AVX/VNNI optimizations |
| 5 | **CPU** | `onnxruntime` | Standard fallback |

```bash
# For Intel Arc / NPU (default in requirements.txt)
pip install onnxruntime-openvino openvino>=2024.0.0

# For NVIDIA GPU
pip install onnxruntime-gpu
```

---

## 📖 Usage

### Browser Mode (No Backend Required)

1. Upload a PDF via the upload button or drag-and-drop
2. Toggle to **"SYSTEM"** mode in the header
3. Press **Play** — the browser's built-in Web Speech API reads the text

### Kokoro Mode (Neural TTS)

1. Start the backend: `python run.py`
2. Ensure the header shows **"KOKORO"** (green indicator)
3. Upload a PDF and press Play — enjoy neural-quality voices!

### API Endpoints

The frontend talks to two backends. Each has its own host/port (configurable in the sidebar). **Leave the host field blank to hit the same origin the page was served from** — useful when an nginx (or similar) reverse proxy is fronting both services on a single domain.

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
| `/v1/chat/sessions/{id}` | `GET / PUT / PATCH / DELETE` | Per-session CRUD; PUT is a transactional upsert of the whole record (messages + events) matching the frontend's contract. |
| `/v1/docs` | `POST` | Register a document by sha256 `doc_id` (idempotent). |
| `/v1/docs/{doc_id}` | `GET / DELETE` | Status (`state`, `chunk_count`, `embedded_count`, model, dim) or cascade delete. |
| `/v1/docs/{doc_id}/chunks` | `POST` | Bulk insert/upsert chunks (batches of ~50). Idempotent on `(doc_id, text_hash)`. |
| `/v1/docs/{doc_id}/index` | `POST` | Kick off the background embedding job; returns 202. Poll the doc status endpoint for progress. |
| `/v1/docs/{doc_id}/search` | `POST` | `{query, k}` → top-k chunks by cosine similarity (HNSW). Used both by the "Use whole document" toggle and by the autonomous `search_document` tool. |

#### Ollama (local LLM server, default `localhost:11434`, optional)

The chat side-mode talks to a locally running [Ollama](https://ollama.com/) daemon. Only needed if you use the Chat view — the Reader works without it.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/tags` | `GET` | Lists installed models. Populates the model dropdown in the chat sidebar. Polled when the host/port changes (debounced). |
| `/api/chat` | `POST` | Streaming NDJSON chat. Body includes `{model, messages, stream: true, think}`. Per-message `images: [base64]` field carries vision attachments (audio routing is paused — see code comments in [src/hooks/useChatEngine.js](src/hooks/useChatEngine.js)). |

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

For production, the typical setup is to serve the frontend as static files from a web server (nginx, Caddy, …) and reverse-proxy both backends on the same hostname. The frontend supports this natively: leaving the **Host** field blank in either the reader or chat sidebar settings causes requests to be issued as same-origin paths (`/v1/synthesize`, `/api/chat`, …). nginx (or whatever sits in front) handles the routing.

### Example nginx config

A complete, battle-tested config (Ed25519 + RSA fallback, gzip, the works) lives at [`docs/chat.oraian.net.sample`](docs/chat.oraian.net.sample). The minimal version below is what's actually load-bearing:

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

    # Kokoro TTS — running on 127.0.0.1:8000
    location /v1/ {
        proxy_pass http://127.0.0.1:8000/v1/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # Long-lived synthesis (especially batch) needs a generous timeout
        proxy_read_timeout 86400;
    }

    # Ollama — running on 127.0.0.1:11434
    location /api/ {
        proxy_pass http://127.0.0.1:11434/api/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # Streaming chat — disable response buffering so NDJSON tokens arrive live
        proxy_buffering off;
        proxy_read_timeout 86400;
    }
}
```

### App configuration

In **both** sidebars (Reader → Voice API, Chat → Ollama Server), **clear the Host field**. The placeholder will read *"localhost (blank = same origin)"* and a small hint will appear confirming the mode. The Port field is then ignored.

Notes:

- **Ollama bind address.** By default Ollama listens on `127.0.0.1:11434`. That's fine here since nginx is the only thing talking to it. If you change `OLLAMA_HOST` to bind on a different interface, mirror it in the `proxy_pass` line.
- **Streaming.** `proxy_buffering off` on `/api/` is required so chat responses stream token-by-token instead of arriving as one buffered chunk.
- **CORS.** Same-origin requests don't need CORS at all. The Kokoro server's permissive CORS header (set in [server/app.py](server/app.py)) is harmless but unused under this setup.
- **Custom hostnames during development.** If you want to test against a non-localhost machine without proxying, set Host to an IP / hostname (e.g. `192.168.1.10`) and the matching Port. Bare hostnames default to `http://`; you can also paste a full `https://example.com` if you have HTTPS terminating elsewhere.
- **Ollama 403 on the proxy.** Ollama has a built-in Host-header allowlist (defaults to `localhost` / `127.0.0.1`) that's separate from the bind address. When nginx forwards `Host: chat.example.com`, Ollama rejects with 403 and an empty body. Fix either by overriding the header at the proxy (`proxy_set_header Host localhost:11434;` inside the `/api/` block) or by adding your domain to `OLLAMA_ORIGINS` via systemd:
  ```bash
  sudo systemctl edit ollama
  # then add:
  # [Service]
  # Environment="OLLAMA_ORIGINS=https://chat.example.com"
  sudo systemctl restart ollama
  ```
  Recommended: do both — the proxy override unblocks `curl`, and `OLLAMA_ORIGINS` unblocks browser CORS preflights.
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

The frontend issues every API call directly from the browser — there's no auth gateway, no per-user gating. If your deployment is reachable on the public internet (any domain pointed at it), anyone can hit `/v1/synthesize`, `/api/chat`, etc. with no credentials. For a `localhost`-only dev box this is fine; for a public domain it's not. This section is the recipe for locking it down.

### Threat model

| Endpoint | What an unauthenticated caller can do | Cost to you |
|---|---|---|
| `POST /v1/synthesize` | Generate arbitrary TTS audio of any length | GPU/CPU burn, electricity |
| `POST /v1/batch_synthesize` | Submit huge sentence arrays; with **Unlimited batch timeout** on, a single request can pin Kokoro's inference lock for hours | Same, amplified — practical DoS surface |
| `POST /api/chat` | Run any installed model with any prompt for as long as they want | LLM inference cost (the expensive one) |
| `GET  /api/tags` | List the names + sizes of every model you have pulled | Information disclosure / fingerprinting |
| `GET  /api/version` | Probe the Ollama daemon version | Fingerprinting |

In addition, [server/app.py](server/app.py) ships with `allow_origins=["*"]`, so even *other websites* can drive your Kokoro endpoint from JavaScript without anyone visiting your site. That makes Kokoro a free TTS-as-a-service for whoever knows the URL.

### What is *not* a vulnerability (worth saying out loud)

- **Chat history, sessions, document library** — all in IndexedDB, sandboxed per origin. Other websites can't read them.
- **Bind addresses** — Kokoro and Ollama listen on `127.0.0.1` only (Ollama by default; Kokoro via [run.py](run.py) on `0.0.0.0` but firewalled by your nginx-only routing). Only the proxy is internet-facing.
- **TLS** — terminated at nginx with a real cert; in-transit traffic is fine.
- **Input shapes** — both backends do ML inference. There's no shell-out, no eval, no SQL. The risk is *resource consumption*, not RCE.

### Mitigations (effort-ordered, stack as needed)

#### 1. HTTP Basic Auth at nginx — biggest payoff, smallest effort

The browser prompts once, remembers credentials for the session, and both backends become useless to anyone without them. Recommended for any single-user deployment.

```bash
# One-time: create the password file
sudo htpasswd -B -c /etc/nginx/htpasswd you
# To add another user later, omit -c
sudo htpasswd -B    /etc/nginx/htpasswd teammate
```

In each backend `location` block:

```nginx
location /v1/ {
    auth_basic           "Neural Reader";
    auth_basic_user_file /etc/nginx/htpasswd;
    proxy_pass           http://127.0.0.1:8000/v1/;
    # ... existing headers / timeouts ...
}
location /api/ {
    auth_basic           "Neural Reader";
    auth_basic_user_file /etc/nginx/htpasswd;
    proxy_pass           http://127.0.0.1:11434/api/;
    # ...
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
location /api/chat {
    limit_req zone=chat burst=3 nodelay;
    # ...
}
```

#### 4. Tighten Kokoro CORS — server-side, one-line edit

Stops other websites from co-opting your TTS via cross-origin fetch. Same-origin requests from your own page are unaffected.

In [server/app.py](server/app.py):

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://chat.example.com"],   # not "*"
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
```

Reload uvicorn (`pkill -HUP -f "uvicorn"` or restart the service).

#### 5. Token-based auth (advanced)

For a more app-like UX than the browser's basic-auth dialog: nginx checks for `Authorization: Bearer <token>` and returns `401` when absent; the frontend stores the token in `localStorage` (set once via a small settings field) and threads it through `fetch`. Better UX, more code on both sides — pursue this if you graduate to multi-user or want the auth to be invisible after first login.

### Quick picks by deployment shape

| Deployment | Recommended stack |
|---|---|
| **Personal — just you** | Basic auth + tighter CORS. Five lines of nginx, one line in `server/app.py`. |
| **Small team / family** | Basic auth + rate limit + tighter CORS. Each user gets their own htpasswd entry. |
| **Public-ish demo** | Basic auth + rate limit + restrict `selectedModel` server-side (don't expose your most expensive model on `/api/tags`). Consider token auth instead of basic. |

The sample [docs/chat.oraian.net.sample](docs/chat.oraian.net.sample) includes the above mitigations as **commented-out blocks at the bottom of the file** — uncomment what you need and reload nginx.

---

## 🎭 Available Voices

<details>
<summary><strong>US Voices (19)</strong></summary>

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
| `af_river` | River | Male |
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

---

## 📁 Project Structure

```
natural-reader/
├── src/
│   ├── App.jsx                # Main application — wires hooks and components
│   ├── main.jsx               # React entry point
│   ├── constants.js           # Voice definitions, keyboard shortcuts, Ollama defaults
│   ├── db.js                  # IndexedDB: document library + legacy chat sessions (v3)
│   ├── index.css              # Global styles (Tailwind)
│   ├── hooks/
│   │   ├── usePdfEngine.js       # PDF + .txt + .md loading, rendering, text extraction, library, extractAllChunks
│   │   ├── useTtsEngine.js       # TTS playback loop, caching, voice preview, chat audio channel
│   │   ├── useChatEngine.js      # Ollama streaming, sessions, tool-call loop, per-session event log, chat TTS queue
│   │   ├── usePersistedState.js  # localStorage-backed state + reading progress
│   │   ├── useKeyboardShortcuts.js
│   │   ├── useMobileDetect.js
│   │   └── useTheme.js
│   ├── lib/
│   │   ├── sessionStore.js       # Postgres-or-IndexedDB session dispatcher (legacy IDB sessions → read-only LOCAL badge)
│   │   └── chatTools/
│   │       ├── index.js          # Tool registry (getToolDefinitions, executeToolCall)
│   │       ├── searchDocument.js # `search_document` tool — semantic search over the open doc
│   │       └── _example.js       # Stub showing the shape new tools follow (web_search, read_url, …)
│   ├── utils/
│   │   ├── attachment.js         # File → Attachment helper, size caps, strip-on-save for IndexedDB
│   │   ├── docHash.js            # sha256 of file bytes → stable doc_id (Web Crypto, lazy + memoized)
│   │   ├── markdownToSpeech.js   # Strips Markdown markup before chat TTS synthesis
│   │   └── url.js                # Builds API URLs, returns relative paths when host is blank
│   └── components/
│       ├── Header.jsx              # Top toolbar with Reader/Chat toggle + playback controls
│       ├── Sidebar.jsx             # Reader sidebar: sentence list, chapters, settings, voice picker
│       ├── ChatSidebar.jsx         # Chat sidebar: Ollama config, model picker, sessions (with LOCAL badge), log
│       ├── PdfViewer.jsx           # Branches between PDF canvas and TextPageRenderer; toolbar hosts Ask page + IndexButton
│       ├── IndexButton.jsx         # Toolbar control: idle → uploading → indexing N/M → indexed (or failed)
│       ├── TextPageRenderer.jsx    # Renders a .txt pseudo-page with sentence highlighting
│       ├── MarkdownPageRenderer.jsx # Renders a .md page (react-markdown + remark-gfm) with paragraph-level highlighting
│       ├── ChatView.jsx            # Chat list, prompt box, attachments, DocContextChip, ToolCallsDisclosure, per-message stats / read-aloud / copy
│       ├── AttachmentPreview.jsx   # Image-thumbnail / audio-player chip used in pending bar + bubbles
│       ├── VoiceRecorder.jsx       # MediaRecorder UI (kept for future re-enable; currently unused)
│       ├── MobileBottomNav.jsx
│       ├── WelcomeScreen.jsx       # Library and upload landing page
│       └── overlays/               # Drag, toast, context menu, shortcuts modal, ReadSelectionButton (+ Ask AI)
├── server/
│   ├── __init__.py
│   ├── app.py                 # FastAPI app factory — mounts TTS + chat_sessions + docs routers, runs init_db on startup
│   ├── db.py                  # psycopg async pool + migration runner + pgvector codec registration
│   ├── endpoints.py           # /v1/synthesize, /v1/batch_synthesize, /v1/health
│   ├── model.py               # Kokoro ONNX loading with GPU/NPU/CPU fallback
│   ├── schemas.py             # Pydantic request models (TTS)
│   ├── sql/
│   │   ├── 001_init.sql       # Core schema: documents, doc_chunks (vector(768)), chat_sessions, chat_messages, chat_events
│   │   └── 002_tool_calls.sql # Adds tool_calls JSONB to chat_messages
│   ├── routers/
│   │   ├── chat_sessions.py   # /v1/chat/sessions/* — list / get / upsert / patch / delete
│   │   └── docs.py            # /v1/docs/* — register / chunks / index (BackgroundTasks) / search / status
│   └── services/
│       └── embeddings.py      # httpx client → Ollama /api/embeddings, Semaphore(4), dim assertion
├── docker-compose.yml         # pgvector/pgvector:pg16 on host port 5433
├── docs/
│   ├── CHAT_WITH_PDF.md       # End-to-end walkthrough for the doc-chat / RAG / tool-calling feature
│   └── chat.oraian.net.sample # Production nginx config (TLS + proxy + commented hardening recipes)
├── run.py                     # Server entry point (uvicorn)
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
- **Reasoning models** — For `deepseek-r1`, `qwen3-thinking`, etc., enable "Thinking" in the chat sidebar. The reasoning trace appears in a collapsible disclosure above the answer (auto-expanded while streaming).

---

## 🔧 Build Optimization

The project uses Rolldown (via `rolldown-vite`) with optimized chunk splitting:
- **vendor-react** — React core split into a separate chunk for long-term caching
- **vendor-pdfjs** — PDF.js bundled locally (no CDN dependency)
- **vendor-icons** — Lucide icons isolated for efficient tree-shaking

---

## 📄 License

MIT
