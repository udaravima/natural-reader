# Neural PDF Reader

A modern, feature-rich PDF reader with **neural text-to-speech** powered by **[Kokoro TTS](https://github.com/hexgrad/kokoro)**. Upload PDFs and have them read aloud with natural-sounding neural voices.

> 🎯 **This project is a web frontend for Kokoro TTS.** It provides an intuitive interface for reading PDF documents aloud using Kokoro's neural voice synthesis. A browser-based fallback mode is also available for testing without the backend.

![Neural Reader](https://img.shields.io/badge/React-19.x-blue) ![PDF.js](https://img.shields.io/badge/PDF.js-5.x-orange) ![Kokoro TTS](https://img.shields.io/badge/Kokoro-TTS-green) ![Vite](https://img.shields.io/badge/Vite-Rolldown-purple) ![Offline](https://img.shields.io/badge/Offline-Ready-brightgreen)

> 🔌 **Works 100% Offline!** Once installed, the app runs completely without internet. PDF.js is bundled locally, and Kokoro TTS runs on your machine.

---

## ✨ Features

### 📖 PDF Viewing
- **Drag & Drop Upload** - Drop PDFs directly onto the window
- **PDF Rendering** - View PDF documents with smooth page navigation
- **Table of Contents** - Navigate using PDF chapter outline (if available)
- **Text Selection** - Select text directly on PDF for copying or reading
- **Zoom Controls** - Zoom in/out, fit to page, fit to width
- **Page Jump** - Type any page number to jump directly
- **Framed Viewer** - Professional document viewer layout with toolbar

### 🎙️ Text-to-Speech
- **Kokoro TTS Integration** - High-quality neural text-to-speech via local backend
- **27 Voice Options** - Wide selection of US and UK male/female voices
- **Speed Control** - Adjust playback speed from 0.5x to 2x
- **Volume Control** - Adjustable audio volume slider
- **Audio Buffering** - Pre-fetches upcoming sentences for seamless playback
- **Download Page Audio** - Export current page as WAV file
- **Selective Read** - Select any text and read only that selection
- **Continue From Here** - Right-click any sentence to start from that point
- **Browser Fallback** - Uses Web Speech API for quick testing without backend
- **Auto-Failover** - Automatically switches to browser voice if backend unavailable

### 🎨 User Experience
- **Dark Mode** - Beautiful dark/light theme toggle with smooth transitions
- **Sentence Highlighting** - Visual highlighting of current sentence during playback
- **Auto-Scroll** - Sidebar automatically scrolls to current sentence
- **Reading Progress** - Visual progress bar showing completion percentage
- **Estimated Time** - Shows remaining reading time
- **Toast Notifications** - Informative feedback for user actions

### 💾 Memory & Persistence
- **Library (IndexedDB)** - PDFs saved locally for instant resume (up to 5 books)
- **One-Click Resume** - Click any book in library to continue reading
- **Settings Saved** - Voice, speed, volume, zoom, and theme persist across sessions
- **Reading Progress** - Remembers your position in each PDF (page + sentence)
- **Resume Reading** - Automatically resumes from where you left off

### ⌨️ Keyboard Shortcuts
| Key | Action |
|-----|--------|
| `Space` | Play / Pause |
| `Escape` | Stop playback |
| `Shift + ←` | Previous sentence |
| `Shift + →` | Next sentence |
| `Page Up` | Previous page |
| `Page Down` | Next page |
| `Ctrl + +` | Zoom in |
| `Ctrl + -` | Zoom out |
| `Ctrl + D` | Toggle dark mode |

---

## 🛠️ Tech Stack

- **Frontend**: React 19, Vite (Rolldown)
- **PDF Parsing**: PDF.js 5.x (bundled locally for offline use)
- **Styling**: Tailwind CSS 4.x
- **Icons**: Lucide React
- **Storage**: localStorage + IndexedDB for persistence
- **Offline**: No internet required after installation

---

## 🚀 Getting Started

### Prerequisites

- Node.js 18+ 
- npm or yarn
- Python 3.8+ (for Kokoro TTS backend)

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd natural-reader
```
#### Install dependencies
```bash
npm install
python3 -m venv .venv
.venv\Scripts\activate # For Windows
source .venv/bin/activate # For Linux/Mac
pip install -r requirements.txt
```
#### Download Voice Models
```bash
# Download models from GitHub releases

## kokoro-V1.0.onnx
wget https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0/kokoro-v1.0.onnx

## voices-V1.0.bin
wget https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0/voices-v1.0.bin
```

#### Start Python Kokoro TTS server
```bash
python server.py
```
#### Start development server
```bash
npm run dev
```

The app will be available at `http://localhost:5173`

---

## 📖 Usage

### Browser Mode (Testing)

1. Click **Upload** button or the upload area to select a PDF
2. Click the **"SYSTEM"** toggle in the header (uses browser's built-in TTS)
3. Click the **Play** button to start reading
4. Use the sidebar to adjust voice and speed settings

### Kokoro Mode (High-Quality Neural TTS)

For high-quality neural TTS, run the Kokoro backend server:

1. **Start the Kokoro TTS server:**
   ```bash
   python server.py
   ```
   The server runs on port 8000.

2. **Use the app:**
   - Ensure **"KOKORO"** is shown in the header toggle (green)
   - Upload a PDF and click Play
   - Enjoy neural-quality voice synthesis!

### API Contract

The backend expects:
```json
POST /v1/synthesize
{
  "text": "Text to synthesize",
  "voice": "af_heart",  
  "speed": 1.0
}
```

Response:
```json
{
  "audio_base64": "<base64-encoded-wav>"
}
```

---

## 🎭 Available Voice Models

### US Voices
| Voice ID | Name | Gender |
|----------|------|--------|
| `af_heart` | Heart | Female (default) |
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

### UK Voices
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

---

## 📜 Scripts

```bash
npm run dev      # Start development server
npm run build    # Build for production
npm run preview  # Preview production build
npm run lint     # Run ESLint
```

---

## 📁 Project Structure

```
natural-reader/
├── src/
│   ├── App.jsx       # Main application component
│   ├── db.js         # IndexedDB utilities for library persistence
│   ├── main.jsx      # React entry point
│   └── index.css     # Global styles (Tailwind)
├── dist/             # Production build output
├── vite.config.js    # Vite configuration with chunk splitting
├── tailwind.config.js
├── server.py         # Kokoro TTS backend server
└── package.json
```

---

## 💡 Tips

- **Resume Reading**: Open the same PDF file to automatically resume from your last position
- **Library**: Your recent books are saved in browser storage - click to instantly resume
- **Keyboard Navigation**: Use keyboard shortcuts for faster control
- **Prefetching**: The app pre-fetches the next 2 sentences for seamless playback
- **Dark Mode**: Toggle with the moon/sun icon or press `Ctrl+D`
- **Jump to Page**: Click on the page number in the toolbar and type any page
- **Right-Click Menu**: Right-click sentences for quick actions like "Continue from here"
- **Read Selection**: Select any text, then click the floating "Read Selection" button

---

## 🔧 Build Optimization

The project uses Rolldown (via rolldown-vite) with optimized chunk splitting:
- React is split into a separate vendor chunk for better caching
- PDF.js is loaded from CDN to reduce bundle size
- Main app bundle is typically under 25KB (gzipped: ~7KB)

---

## 📄 License

MIT
