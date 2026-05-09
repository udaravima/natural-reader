# Neural Reader

A modern, feature-rich document reader with **neural text-to-speech** powered by **[Kokoro TTS](https://github.com/hexgrad/kokoro)** and an **optional local-AI chat mode** powered by **[Ollama](https://ollama.com/)**. Open PDFs or `.txt` files, have them read aloud with natural-sounding voices, or chat with a local LLM and have its replies streamed back as speech.

> 🎯 **A web frontend for Kokoro TTS — now with a built-in Ollama chat side-mode.** The reader provides an intuitive interface for reading PDF and plain-text documents aloud using Kokoro's neural voice synthesis. The chat mode talks to a local Ollama server you run yourself, with the same voice pipeline reading replies back to you. A browser-based Web Speech fallback is also available for testing without either backend.

![Neural Reader](https://img.shields.io/badge/React-19.x-blue) ![PDF.js](https://img.shields.io/badge/PDF.js-5.x-orange) ![Kokoro TTS](https://img.shields.io/badge/Kokoro-TTS-green) ![Ollama](https://img.shields.io/badge/Ollama-Chat-orange) ![Vite](https://img.shields.io/badge/Vite-Rolldown-purple) ![Offline](https://img.shields.io/badge/Offline-Ready-brightgreen)

> 🔌 **Works 100% Offline!** Once installed, the app runs completely without internet. PDF.js is bundled locally, and Kokoro TTS / Ollama run on your machine.

---

## ✨ Features

### 📖 Document Viewing
- **Drag & Drop Upload** — Drop PDFs or `.txt` files directly onto the window
- **PDF Rendering** — Smooth page-by-page rendering with zoom controls
- **Plain Text (.txt) Support** — Text files are paginated into pseudo-pages (~40 sentences) and use the same reading pipeline as PDFs (sentence highlight, library, resume progress, selection-read)
- **Table of Contents** — Navigate using the PDF's chapter outline (if available)
- **Text Selection** — Select text directly on the rendered page for copying or reading
- **Zoom Controls** — Zoom in/out, fit to page, fit to width (font size for `.txt`)
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
- **Document Library (IndexedDB)** — PDFs and `.txt` files saved locally for instant resume (up to 5 docs)
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

---

## 🚀 Getting Started

### Prerequisites

- **Node.js 18+** and npm
- **Python 3.8+** (for Kokoro TTS backend)

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
# Install Ollama (https://ollama.com/download), then pull a model
ollama pull gemma3
# Reasoning model that produces a thinking trace
ollama pull deepseek-r1:1.5b
```

Ollama serves at `http://localhost:11434` by default. In the app, toggle to **Chat** in the header — host/port and model picker live in the chat sidebar.

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
│   ├── db.js                  # IndexedDB: document library + chat sessions (v3)
│   ├── index.css              # Global styles (Tailwind)
│   ├── hooks/
│   │   ├── usePdfEngine.js       # PDF + .txt loading, rendering, text extraction, library
│   │   ├── useTtsEngine.js       # TTS playback loop, caching, voice preview, chat audio channel
│   │   ├── useChatEngine.js      # Ollama streaming, sessions, per-session event log, chat TTS queue
│   │   ├── usePersistedState.js  # localStorage-backed state + reading progress
│   │   ├── useKeyboardShortcuts.js
│   │   ├── useMobileDetect.js
│   │   └── useTheme.js
│   ├── utils/
│   │   ├── attachment.js         # File → Attachment helper, size caps, strip-on-save for IndexedDB
│   │   ├── markdownToSpeech.js   # Strips Markdown markup before chat TTS synthesis
│   │   └── url.js                # Builds API URLs, returns relative paths when host is blank
│   └── components/
│       ├── Header.jsx              # Top toolbar with Reader/Chat toggle + playback controls
│       ├── Sidebar.jsx             # Reader sidebar: sentence list, chapters, settings, voice picker
│       ├── ChatSidebar.jsx         # Chat sidebar: Ollama config, model picker, sessions, log
│       ├── PdfViewer.jsx           # Branches between PDF canvas and TextPageRenderer
│       ├── TextPageRenderer.jsx    # Renders a .txt pseudo-page with sentence highlighting
│       ├── ChatView.jsx            # Chat list, prompt box, attachments, per-message stats / read-aloud / copy
│       ├── AttachmentPreview.jsx   # Image-thumbnail / audio-player chip used in pending bar + bubbles
│       ├── VoiceRecorder.jsx       # MediaRecorder UI (kept for future re-enable; currently unused)
│       ├── MobileBottomNav.jsx
│       ├── WelcomeScreen.jsx       # Library and upload landing page
│       └── overlays/               # Drag, toast, context menu, shortcuts modal, etc.
├── server/
│   ├── __init__.py
│   ├── app.py                 # FastAPI app factory with CORS
│   ├── endpoints.py           # /v1/synthesize, /v1/batch_synthesize, /v1/health
│   ├── model.py               # Kokoro ONNX loading with GPU/NPU/CPU fallback
│   └── schemas.py             # Pydantic request models
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
