# Natural Reader Chrome Extension

Natural Reader is a Chrome extension that reads selected text or entire web pages aloud using a local Kokoro text-to-speech backend. Right-click on any selection to play it immediately, or use the context menu to read the whole page with chunked playback, seeking, and voice/speed control. The extension includes an options page for customizing voice, playback speed, and backend URL.

## Install (Unpacked)

1. Open Chrome and navigate to `chrome://extensions`
2. Enable **Developer mode** (toggle in the top right)
3. Click **Load unpacked**
4. Navigate to the `extension/` directory in this repo and select it
5. The extension will appear in your extensions list; pin it to the toolbar for easy access

## Requirement: Local TTS Backend

This extension requires the local Kokoro TTS backend to be running on your machine:

```bash
cd /path/to/natural-reader
./startup.sh up
```

You can verify the backend is running by checking its health endpoint:

```bash
curl http://localhost:8000/v1/health
```

The extension will show a green status dot in the popup when the backend is healthy, and red if unreachable. Without the backend running, audio playback will not work.

## Usage

- **Read Selection:** Right-click on selected text → **"Read aloud"** (toolbar pops up with playback controls; audio plays immediately)
- **Read Whole Page:** Right-click on the page → **"Read page"** (text is chunked automatically; a toolbar shows the progress, e.g., "Chunk 3 / 10")
- **Playback Controls:** Play/Pause, Stop, and seek bar in the toolbar
- **Voice & Speed:** Click the extension popup → **Options** or right-click the extension icon → **Options page**
  - Choose a voice, adjust playback speed, and optionally change the backend URL
  - Click **Test connection** to verify the backend is reachable
- **Close:** Click the ✕ button on the toolbar to dismiss the audio player

## Limitations (v1)

- **Backend:** only works with the local Kokoro TTS backend at `http://localhost:8000`; remote backends are not yet supported
- **Highlight follow:** no visual highlight of words as they are read
- **Page extraction:** basic extraction; some complex layouts or dynamic content may not extract correctly
- **Navigation:** audio stops if you navigate away from the current page

## Manual E2E Checklist

- [ ] Load unpacked; no manifest/service-worker errors.
- [ ] Backend up: popup status dot is green.
- [ ] Selection → context menu → toolbar + audio plays.
- [ ] Whole page → chunked queue; "chunk N / M"; starts after first chunk.
- [ ] Play/Pause, Stop, seek, drag, close all work.
- [ ] Voice/speed change mid-read applies to a later chunk (not the one already being fetched).
- [ ] Backend down → toolbar shows error (red), popup dot red, no hang.
- [ ] Empty selection/page → "nothing to read" notice.
- [ ] Options: change voice/speed/URL persists; Test connection ✓ / ✗.
