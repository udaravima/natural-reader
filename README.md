# Neural Reader

A modern PDF reader frontend designed for **[Kokoro TTS](https://github.com/hexgrad/kokoro)** - a high-quality neural text-to-speech engine. Upload PDFs and have them read aloud with natural-sounding neural voices.

> 🎯 **This project is a web frontend for Kokoro TTS.** It provides an intuitive interface for reading PDF documents aloud using Kokoro's neural voice synthesis. A browser-based fallback mode is also available for testing without the backend.

![Neural Reader](https://img.shields.io/badge/React-19.x-blue) ![PDF.js](https://img.shields.io/badge/PDF.js-5.x-orange) ![Kokoro TTS](https://img.shields.io/badge/Kokoro-TTS-green) ![Vite](https://img.shields.io/badge/Vite-Rolldown-purple)

## Features

- 📄 **PDF Rendering** - View PDF documents with smooth page navigation
- 🧠 **Kokoro TTS Integration** - High-quality neural text-to-speech via local Kokoro backend
- 🎙️ **Dual TTS Modes**:
  - **Kokoro Backend** (Recommended) - Connects to Kokoro TTS server for neural voices
  - **Browser Fallback** - Uses Web Speech API for quick testing without backend
- 🎨 **Immersive Reading** - Visual sentence highlighting during playback
- ⚡ **Speed Control** - Adjust playback speed from 0.5x to 2x
- 🎭 **Multiple Voices** - Choose from Kokoro's voice models (Heart, Bella, Michael, Emma, Alice, Lewis)

## Tech Stack

- **Frontend**: React 19, Vite (Rolldown)
- **PDF Parsing**: PDF.js 5.x
- **Styling**: Tailwind CSS 4.x
- **Icons**: Lucide React

## Getting Started

### Prerequisites

- Node.js 18+ 
- npm or yarn

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd natural-reader

# Install dependencies
npm install

# Start development server
npm run dev
```

The app will be available at `http://localhost:5173`

## Usage

### Browser Mode (Default)

1. Click **"Open PDF"** to upload a PDF file
2. Ensure **"Browser Sim"** is selected in the header toggle
3. Click the **Play** button to start reading
4. Use the sidebar to adjust voice and speed settings

### Localhost Mode (Kokoro TTS)

For high-quality neural TTS, you'll need to run the Kokoro backend server:

1. Set up and start the Kokoro TTS server on port 8000:
   ```bash
   # The server should expose POST /v1/synthesize
   python server.py
   ```

2. Toggle to **"Localhost:8000"** in the header
3. Upload a PDF and click Play

#### API Contract

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

## Available Voice Models

| Voice ID | Name | Description |
|----------|------|-------------|
| `af_heart` | Heart | US Female (default) |
| `af_bella` | Bella | US Female |
| `am_michael` | Michael | US Male |
| `bf_emma` | Emma | UK Female |
| `bf_alice` | Alice | UK Female |
| `bm_lewis` | Lewis | UK Male |

## Scripts

```bash
npm run dev      # Start development server
npm run build    # Build for production
npm run preview  # Preview production build
npm run lint     # Run ESLint
```

## Project Structure

```
natural-reader/
├── src/
│   ├── App.jsx       # Main application component
│   ├── main.jsx      # React entry point
│   └── index.css     # Global styles
├── public/           # Static assets
├── vite.config.js    # Vite configuration
├── tailwind.config.js
└── package.json
```

## License

MIT
