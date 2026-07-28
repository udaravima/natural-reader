# Chrome Extension: Read-Aloud — Design Spec

**Date:** 2026-07-27
**Status:** Approved (design), pending implementation plan

## Summary

A general-purpose "read aloud" Chrome extension (Manifest V3) that speaks the
selected text or the whole page on **any** website, using this project's local
Kokoro TTS backend at `http://localhost:8000`. It ships a floating on-page
toolbar for playback control, a popup for quick actions and backend status, and
an options page for defaults. It is a **personal, unpacked** developer extension
— not published to the Chrome Web Store — and the backend URL is configurable.

## Goals

- Select text on any site (or "read whole page") → hear it via the local TTS.
- Floating, draggable, dismissible toolbar with real playback controls.
- Popup + options page for quick actions and settings.
- Zero new build tooling; the extension is a self-contained folder loaded unpacked.
- Reuse the existing backend **without modifying it**.

## Non-Goals (v1)

- No Chrome Web Store distribution, hosted backend, auth, or rate limiting.
- No sentence/word **highlight-follow** or scroll-follow on the page.
- No chat / RAG / document-library integration (TTS backend only).
- No remote (non-loopback) backend URL support (noted as a follow-up).
- No readability-grade main-content extraction (basic extraction only in v1).

## Backend context (existing, unchanged)

Local FastAPI server on `http://localhost:8000`:

- `GET /v1/health` → `{ status, model_loaded }`
- `POST /v1/synthesize` → `{ text, voice, speed }` → `{ audio_base64, duration_seconds }`
- `POST /v1/batch_synthesize` → `{ sentences: string[], voice, speed }` →
  `{ audio_base64, duration_seconds, sentence_count }` (server merges the
  sentences into one WAV with 0.3s gaps).

CORS is wide open on the server, but that is irrelevant here: an MV3
content-script fetch uses the extension's `host_permissions`, which bypasses
page CORS entirely. **No server change is required.**

There is **no "list voices" endpoint** — the 27 Kokoro voices live in the
frontend's `src/constants.js`. The extension bundles its own copy of that list
(default voice `af_heart`, default speed `1.0`).

## Approach

**Vanilla MV3, no build step.** The extension is a self-contained `extension/`
folder loaded unpacked. A background service worker registers context menus and
injects the content script **on demand**; the content script renders the
toolbar in a **Shadow DOM** (isolating it from host-page CSS), fetches audio
from the backend, and plays it in the page. Popup and options are plain
HTML/JS. Pure logic is unit-tested with the project's existing Vitest.

Rejected alternatives: React+Vite/CRXJS (build step + framework overkill for a
small toolbar); popup-only with an offscreen document (no on-page toolbar, which
was a requirement).

## Components

```
extension/
├─ manifest.json          MV3 manifest
├─ background.js          service worker: context menus, on-demand injection, relay
├─ content/
│  ├─ content.js          extract text, split, call API, PLAY audio, drive toolbar
│  └─ toolbar.css         styles injected into the shadow root
├─ popup/
│  ├─ popup.html
│  └─ popup.js            Read Selection / Read Page, voice+speed, backend status
├─ options/
│  ├─ options.html
│  └─ options.js          default voice, speed, backend URL, Test connection
└─ shared/
   ├─ api.js              synthesize(sentences, opts), health(opts)  — testable
   ├─ voices.js           the voice list (copied from src/constants.js)
   ├─ text.js             extractSelectionOrPage(), splitSentences(), chunkSentences()
   └─ settings.js         get/set via chrome.storage.sync
```

Design intent: `text.js` and `api.js` are **pure** (no `chrome.*`, no live DOM
globals — they take a `document`/`fetch` they're given) so they are unit-tested
directly. `content.js`, `popup.js`, `options.js` are thin DOM/`chrome.*` shells
around them.

### Unit responsibilities

- **`shared/text.js`**
  - `extractSelectionOrPage(doc, mode)` — `mode: 'selection' | 'page'`. Selection
    returns the current selection's text; page tries
    `doc.querySelector('article, main')?.innerText`, falling back to
    `doc.body.innerText`, with whitespace cleanup. Returns `''` when empty.
  - `splitSentences(text)` — split into sentences on sentence-ending
    punctuation, trimming empties. Handles common abbreviations minimally
    (good-enough, not perfect).
  - `chunkSentences(sentences, size = 30)` — group into arrays of ≤ `size`
    sentences, preserving order.
- **`shared/api.js`**
  - `health({ baseUrl, fetch })` → `boolean` (maps network/parse errors to `false`).
  - `synthesize({ sentences, voice, speed, baseUrl, fetch, signal })` → `Blob`
    (POSTs `batch_synthesize`, decodes `audio_base64` → WAV `Blob`). Maps HTTP
    and network errors to a typed error with a human-readable message.
- **`shared/settings.js`**
  - `getSettings()` / `setSettings(patch)` over `chrome.storage.sync`.
  - Defaults: `{ voice: 'af_heart', speed: 1.0, baseUrl: 'http://localhost:8000' }`.
- **`shared/voices.js`** — exported array of `{ id, name }` (27 entries, copied from `src/constants.js`).
- **`background.js`** — on install, create context-menu items ("Read selection
  aloud", "Read whole page aloud"); on menu/icon click, inject `content.js` into
  the active tab (guard against double-inject) and post a `start` message with
  `{ mode }`. Relays popup "read" requests to the active tab's content script.
- **`content/content.js`** — owns a small state machine (`idle → synthesizing →
  playing ⇄ paused → idle`); manages the `Audio` element and the chunk queue;
  renders/updates the shadow-DOM toolbar; reads settings; revokes `blob:` URLs
  when done.
- **`popup/`, `options/`** — DOM shells calling the shared modules.

## Data flow

1. **Trigger**: right-click context menu, popup button, or extension-icon click.
2. Background injects `content.js` into the active tab (`activeTab` + `scripting`)
   and sends `{ type: 'start', mode }`.
3. Content script loads settings, extracts text (`extractSelectionOrPage`), and
   splits it (`splitSentences`).
4. **Selection** → one `synthesize` call → one WAV. **Whole page** →
   `chunkSentences` → a **queue**: synthesize chunk 1, start playing it, and
   synthesize the next chunk while the current one plays; play chunks
   back-to-back. Time-to-first-audio is one chunk, not the whole page.
5. Each returned `Blob` becomes a `blob:` URL played by an `Audio` element in the
   page; the toolbar mirrors play/pause/stop/seek/progress and shows
   "chunk N / M" for page reads.
6. On stop / completion / toolbar close, playback halts and all `blob:` URLs are
   revoked.

**Mid-read setting changes**: changing voice/speed applies to the *next* chunk
synthesized, not audio already generated (we don't re-synthesize completed audio).

## UI surfaces

- **Floating toolbar** (shadow DOM, draggable, dismissible): ⏯ Play/Pause, ⏹
  Stop, progress/seek bar, voice dropdown (27 voices), speed control (0.5–2.0),
  ✕ close, and "chunk N / M" during page reads. Appears when a read starts.
- **Popup** (extension icon): "Read selection" + "Read whole page" buttons,
  compact voice + speed, a backend **status dot** (green/red via `GET /v1/health`),
  and a link to Options.
- **Options page**: default voice, default speed, backend URL (default
  `http://localhost:8000`), and a "Test connection" button. Persisted in
  `chrome.storage.sync`.

## Permissions & security

- **`permissions`**: `contextMenus`, `activeTab`, `scripting`, `storage`. No
  blanket content-script registration — inject only on demand, so a page is
  touched only when the user asks it to read.
- **`host_permissions`**: `http://localhost/*`, `http://127.0.0.1/*` — loopback
  wildcards cover any configured port without a permission re-prompt. Non-loopback
  URLs are out of scope for v1 (would need an optional-permission request flow).
- Content-script fetch uses the extension's host permission → bypasses page CORS;
  the server's CORS config is untouched.
- Page-derived text is rendered into the toolbar as **`textContent` only** (never
  `innerHTML`), so a hostile page's selection can't inject markup into the shadow
  root.

## Error handling

- **Backend unreachable / health fails**: toolbar and popup show
  "Can't reach TTS server at `<baseUrl>` — is it running?" with a retry; popup
  status dot goes red.
- **Empty selection/page**: a brief "Nothing to read" notice; no toolbar.
- **Synthesis error (HTTP 4xx/5xx)**: surface the server detail in the toolbar;
  stop the queue.
- **Very long page**: chunking handles latency; if total text exceeds a sane cap
  (e.g. a configurable max characters), show a "reading the first part of a very
  long page" notice rather than an unbounded job. (Exact cap decided in the plan.)
- **Navigation/reload mid-read**: audio stops (expected for a read-aloud tool);
  no persistence of playback across page loads.

## Testing

- **Vitest unit tests** for the pure modules:
  - `text.js`: sentence splitting (incl. abbreviations, whitespace, empty),
    selection-vs-page extraction against a jsdom document, `chunkSentences`
    grouping and order.
  - `api.js`: `batch_synthesize` payload shape, `audio_base64` → `Blob` decode,
    `health` parsing, HTTP/network error mapping — with a mocked `fetch`.
  - `settings.js`: defaults + round-trip with a mocked `chrome.storage`.
- **Manual end-to-end checklist** (documented, since manifest/injection/audio
  can't be meaningfully unit-tested): load unpacked; context-menu read of a
  selection; popup "Read whole page"; backend-down behavior (status dot + toolbar
  message); a long article (chunk queue, "chunk N / M", stop mid-queue); options
  round-trip + "Test connection".

## Follow-ups (explicitly deferred)

- Remote (non-loopback) backend URL via an optional-host-permission flow.
- Readability-grade main-content extraction.
- Sentence highlight-follow + scroll-follow (would need per-sentence synthesis
  and/or backend timing data).
- Chat / RAG / "ask about this page" integration.
- Chrome Web Store packaging (auth, rate limiting, privacy policy).
