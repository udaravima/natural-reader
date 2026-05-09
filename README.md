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
- **Model Picker** — Auto-populated from `/api/tags`; configurable host/port (defaults to `localhost:11434`)
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

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/health` | `GET` | Health check — verifies model is loaded |
| `/v1/synthesize` | `POST` | Synthesize text → Base64 WAV audio |
| `/v1/batch_synthesize` | `POST` | Synthesize multiple sentences → merged WAV |

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
│   │   └── markdownToSpeech.js   # Strips Markdown markup before chat TTS synthesis
│   └── components/
│       ├── Header.jsx            # Top toolbar with Reader/Chat toggle + playback controls
│       ├── Sidebar.jsx           # Reader sidebar: sentence list, chapters, settings, voice picker
│       ├── ChatSidebar.jsx       # Chat sidebar: Ollama config, model picker, sessions, log
│       ├── PdfViewer.jsx         # Branches between PDF canvas and TextPageRenderer
│       ├── TextPageRenderer.jsx  # Renders a .txt pseudo-page with sentence highlighting
│       ├── ChatView.jsx          # Chat message list, prompt box, per-message read aloud + copy
│       ├── MobileBottomNav.jsx
│       ├── WelcomeScreen.jsx     # Library and upload landing page
│       └── overlays/             # Drag, toast, context menu, shortcuts modal, etc.
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

- **Resume Reading** — Reopen the same PDF to automatically continue from your last position
- **Library** — Recent books are persisted in IndexedDB — click to instantly resume
- **Keyboard Navigation** — Use keyboard shortcuts for hands-free control
- **Prefetching** — The next 2 sentences are pre-fetched for seamless playback
- **Dark Mode** — Toggle with the moon/sun icon or `Ctrl + D`
- **Right-Click Menu** — Right-click any sentence for "Continue from here" and copy options
- **Read Selection** — Select text on the PDF, then click the floating "Read Selection" button
- **Download Audio** — Export the current page's audio as a WAV file for offline listening

---

## 🔧 Build Optimization

The project uses Rolldown (via `rolldown-vite`) with optimized chunk splitting:
- **vendor-react** — React core split into a separate chunk for long-term caching
- **vendor-pdfjs** — PDF.js bundled locally (no CDN dependency)
- **vendor-icons** — Lucide icons isolated for efficient tree-shaking

---

## 📄 License

MIT
