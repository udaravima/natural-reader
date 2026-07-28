# Chrome Read-Aloud Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Manifest V3 Chrome extension that reads the selected text or the whole page aloud on any website via the project's local Kokoro TTS backend.

**Architecture:** Self-contained `extension/` folder loaded unpacked — no build step. A background service worker registers context menus and injects a content script on demand; the content script renders a floating Shadow-DOM toolbar, fetches audio from the backend, and plays it in the page. Pure logic (text, API, settings) lives in ESM `shared/` modules that are unit-tested with the existing Vitest and loaded into the content script via dynamic `import(chrome.runtime.getURL(...))`. Popup and options are plain HTML/JS extension pages.

**Tech Stack:** Vanilla JS (ESM), Chrome Extension Manifest V3 (`contextMenus`, `scripting`, `activeTab`, `storage`), Vitest + jsdom for unit tests. No new runtime dependencies.

## Global Constraints

- **No build step.** The `extension/` folder is loaded directly via `chrome://extensions` → "Load unpacked". Do not add a bundler.
- **Backend is unchanged.** Never modify `server/` or `run.py`. The extension only calls existing endpoints: `GET /v1/health`, `POST /v1/batch_synthesize`.
- **ESM everywhere in `shared/`.** `shared/*.js` use `export`. Background (service worker), popup, and options are ES modules and static-import them. The **content script cannot static-import** (it is injected as a classic script) — it uses dynamic `import(chrome.runtime.getURL('shared/<x>.js'))`, which requires those files in `web_accessible_resources`.
- **Default settings (verbatim):** `{ voice: 'af_heart', speed: 1.0, baseUrl: 'http://localhost:8000' }`.
- **Host permissions:** `http://localhost/*`, `http://127.0.0.1/*` only (loopback). Non-loopback URLs are out of scope.
- **Security:** page-derived text is only ever assigned via `textContent` (never `innerHTML`).
- **Testing reality:** Tasks 1–3 (pure `shared/` modules) are TDD with real Vitest tests. Tasks 4–7 are browser-runtime glue that Vitest cannot exercise; they are verified with the explicit manual checklists in each task. Do not fake unit tests for browser glue.
- **Commits require explicit user approval.** This project's rule (`~/.claude/CLAUDE.md`) forbids committing without per-action permission. Each task's final "Commit" step means: stage the listed files, show the diff, and **ask the user** before running `git commit`. Never commit unprompted.
- **Test commands:** run one file with `npx vitest run <path>`; run everything with `npm run test:run`; lint with `npm run lint`.

---

### Task 1: Text utilities + extension scaffold

**Files:**
- Create: `extension/shared/text.js`
- Test: `extension/shared/text.test.js`
- Modify: `eslint.config.js` (add a config block so `extension/**` lints with webextension/service-worker globals)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `splitSentences(text: string): string[]`
  - `chunkSentences(sentences: string[], size = 30): string[][]`
  - `extractSelectionOrPage(doc: Document, mode: 'selection' | 'page'): string`

- [ ] **Step 1: Add the eslint block for extension files**

Edit `eslint.config.js`. After the existing main config object (the one with `files: ['**/*.{js,jsx}']`), add a new object inside the `defineConfig([...])` array:

```js
  {
    files: ['extension/**/*.js'],
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.webextensions,
        ...globals.serviceworker,
      },
    },
  },
```

- [ ] **Step 2: Write the failing test**

Create `extension/shared/text.test.js`:

```js
import { describe, it, expect } from 'vitest';
import { splitSentences, chunkSentences, extractSelectionOrPage } from './text.js';

describe('splitSentences', () => {
    it('returns [] for empty/whitespace', () => {
        expect(splitSentences('')).toEqual([]);
        expect(splitSentences('   \n  ')).toEqual([]);
    });
    it('splits on . ! ? and trims', () => {
        expect(splitSentences('One. Two! Three?')).toEqual(['One.', 'Two!', 'Three?']);
    });
    it('keeps a final sentence with no terminal punctuation', () => {
        expect(splitSentences('No terminal punctuation here')).toEqual(['No terminal punctuation here']);
    });
    it('collapses whitespace/newlines', () => {
        expect(splitSentences('Hi.\n\n   There.')).toEqual(['Hi.', 'There.']);
    });
    it('does not drop text around decimals/numbers/phone numbers (regression)', () => {
        expect(splitSentences('The price is $3.99 today.')).toEqual(['The price is $3.99 today.']);
        expect(splitSentences('Call 1.800.555.1234 now.')).toEqual(['Call 1.800.555.1234 now.']);
    });
});

describe('chunkSentences', () => {
    it('groups into chunks of the given size, preserving order', () => {
        const s = Array.from({ length: 65 }, (_, i) => `s${i}`);
        const chunks = chunkSentences(s, 30);
        expect(chunks.map((c) => c.length)).toEqual([30, 30, 5]);
        expect(chunks[0][0]).toBe('s0');
        expect(chunks[2][4]).toBe('s64');
    });
    it('returns [] for empty input', () => {
        expect(chunkSentences([], 30)).toEqual([]);
    });
});

describe('extractSelectionOrPage', () => {
    it('returns the trimmed selection string in selection mode', () => {
        const doc = { getSelection: () => ({ toString: () => '  picked text  ' }) };
        expect(extractSelectionOrPage(doc, 'selection')).toBe('picked text');
    });
    it('reads article/main text in page mode', () => {
        document.body.innerHTML = '<nav>Menu</nav><main>Hello world. This is the body.</main>';
        expect(extractSelectionOrPage(document, 'page')).toBe('Hello world. This is the body.');
    });
    it('falls back to body when there is no article/main', () => {
        document.body.innerHTML = 'Just body text.';
        expect(extractSelectionOrPage(document, 'page')).toBe('Just body text.');
    });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npx vitest run extension/shared/text.test.js`
Expected: FAIL — cannot resolve `./text.js` / functions not defined.

- [ ] **Step 4: Write the implementation**

Create `extension/shared/text.js`:

```js
// Pure text helpers for the read-aloud extension. No chrome.* or live globals —
// callers pass in the Document so this is unit-testable under jsdom.

export function splitSentences(text) {
    const clean = (text || '').replace(/\s+/g, ' ').trim();
    if (!clean) return [];
    // Split at whitespace that follows a sentence terminator. Keeps every
    // character, so a terminator not followed by a space (e.g. "$3.99",
    // "1.800.555.1234") stays inside its sentence — no data loss.
    return clean.split(/(?<=[.!?])\s+/).map((s) => s.trim()).filter(Boolean);
}

export function chunkSentences(sentences, size = 30) {
    const step = size > 0 ? size : sentences.length || 1; // guard non-positive size
    const out = [];
    for (let i = 0; i < sentences.length; i += step) {
        out.push(sentences.slice(i, i + step));
    }
    return out;
}

function readableText(el) {
    if (!el) return '';
    // innerText respects rendering in a real browser; jsdom leaves it undefined,
    // so fall back to textContent for tests.
    return (el.innerText != null ? el.innerText : el.textContent) || '';
}

export function extractSelectionOrPage(doc, mode) {
    if (mode === 'selection') {
        const sel = doc.getSelection && doc.getSelection();
        return ((sel && sel.toString()) || '').trim();
    }
    const main = doc.querySelector('article, main') || doc.body;
    return readableText(main).replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
}
```

Note: sentence splitting is intentionally simple — abbreviations like "Dr." may over-split. That is acceptable for v1 (see spec Non-Goals).

- [ ] **Step 5: Run tests + lint**

Run: `npx vitest run extension/shared/text.test.js` → Expected: PASS.
Run: `npm run lint` → Expected: no errors in `extension/`.

- [ ] **Step 6: Commit** (ask first — see Global Constraints)

```bash
git add extension/shared/text.js extension/shared/text.test.js eslint.config.js
git commit -m "feat(ext): text utilities (sentence split, chunk, extraction) + eslint scaffold"
```

---

### Task 2: TTS API client

**Files:**
- Create: `extension/shared/api.js`
- Test: `extension/shared/api.test.js`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `health({ baseUrl, fetchImpl? }): Promise<boolean>`
  - `synthesize({ sentences, voice, speed, baseUrl, fetchImpl?, signal? }): Promise<Blob>` — posts `batch_synthesize`, returns a WAV `Blob`. Throws `Error` with a human-readable message on HTTP/network failure.

- [ ] **Step 1: Write the failing test**

Create `extension/shared/api.test.js`:

```js
import { describe, it, expect, vi } from 'vitest';
import { health, synthesize } from './api.js';

const BASE = 'http://localhost:8000';

describe('health', () => {
    it('returns true when status ok and model loaded', async () => {
        const fetchImpl = vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({ status: 'ok', model_loaded: true }),
        });
        expect(await health({ baseUrl: BASE, fetchImpl })).toBe(true);
        expect(fetchImpl).toHaveBeenCalledWith(`${BASE}/v1/health`);
    });
    it('returns false on non-ok or model not loaded', async () => {
        const fetchImpl = vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({ status: 'error', model_loaded: false }),
        });
        expect(await health({ baseUrl: BASE, fetchImpl })).toBe(false);
    });
    it('returns false when fetch throws', async () => {
        const fetchImpl = vi.fn().mockRejectedValue(new Error('connection refused'));
        expect(await health({ baseUrl: BASE, fetchImpl })).toBe(false);
    });
});

describe('synthesize', () => {
    it('posts the batch payload and decodes base64 to a WAV Blob', async () => {
        const b64 = btoa('RIFFfake'); // 8 bytes
        const fetchImpl = vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({ audio_base64: b64, duration_seconds: 1 }),
        });
        const blob = await synthesize({
            sentences: ['Hi.', 'There.'], voice: 'af_heart', speed: 1.0, baseUrl: BASE, fetchImpl,
        });
        expect(blob).toBeInstanceOf(Blob);
        expect(blob.type).toBe('audio/wav');
        expect(blob.size).toBe(8);
        const [url, opts] = fetchImpl.mock.calls[0];
        expect(url).toBe(`${BASE}/v1/batch_synthesize`);
        expect(opts.method).toBe('POST');
        expect(JSON.parse(opts.body)).toEqual({
            sentences: ['Hi.', 'There.'], voice: 'af_heart', speed: 1.0,
        });
    });
    it('throws with the server detail on HTTP error', async () => {
        const fetchImpl = vi.fn().mockResolvedValue({
            ok: false, status: 500, json: async () => ({ detail: 'boom' }),
        });
        await expect(synthesize({
            sentences: ['x'], voice: 'af_heart', speed: 1, baseUrl: BASE, fetchImpl,
        })).rejects.toThrow(/boom/);
    });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run extension/shared/api.test.js`
Expected: FAIL — cannot resolve `./api.js`.

- [ ] **Step 3: Write the implementation**

Create `extension/shared/api.js`:

```js
// TTS backend client. fetchImpl is injectable for tests; defaults to global fetch.

function base64ToBlob(b64, type = 'audio/wav') {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
    return new Blob([bytes], { type });
}

export async function health({ baseUrl, fetchImpl = globalThis.fetch } = {}) {
    try {
        const res = await fetchImpl(`${baseUrl}/v1/health`);
        if (!res.ok) return false;
        const data = await res.json();
        return data.status === 'ok' && data.model_loaded === true;
    } catch {
        return false;
    }
}

export async function synthesize({
    sentences, voice, speed, baseUrl, fetchImpl = globalThis.fetch, signal,
} = {}) {
    const res = await fetchImpl(`${baseUrl}/v1/batch_synthesize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sentences, voice, speed }),
        signal,
    });
    if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
            const err = await res.json();
            if (err && err.detail) detail = err.detail;
        } catch { /* body not JSON — keep status */ }
        throw new Error(`TTS request failed: ${detail}`);
    }
    const data = await res.json();
    return base64ToBlob(data.audio_base64);
}
```

- [ ] **Step 4: Run tests + lint**

Run: `npx vitest run extension/shared/api.test.js` → Expected: PASS.
Run: `npm run lint` → Expected: clean.

- [ ] **Step 5: Commit** (ask first)

```bash
git add extension/shared/api.js extension/shared/api.test.js
git commit -m "feat(ext): TTS API client (health + batch synthesize → WAV Blob)"
```

---

### Task 3: Settings + voice list

**Files:**
- Create: `extension/shared/settings.js`, `extension/shared/voices.js`
- Test: `extension/shared/settings.test.js`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `DEFAULTS = { voice: 'af_heart', speed: 1.0, baseUrl: 'http://localhost:8000' }`
  - `getSettings(storage?): Promise<{voice, speed, baseUrl}>`
  - `setSettings(patch, storage?): Promise<{voice, speed, baseUrl}>`
  - `VOICES: Array<{ id: string, name: string }>` (27 entries), `DEFAULT_VOICE = 'af_heart'`

- [ ] **Step 1: Write the failing test**

Create `extension/shared/settings.test.js`:

```js
import { describe, it, expect } from 'vitest';
import { DEFAULTS, getSettings, setSettings } from './settings.js';
import { VOICES, DEFAULT_VOICE } from './voices.js';

function fakeStorage(initial = {}) {
    const store = { ...initial };
    return {
        get: async (keys) => {
            const out = {};
            for (const k of keys) if (k in store) out[k] = store[k];
            return out;
        },
        set: async (patch) => { Object.assign(store, patch); },
        _store: store,
    };
}

describe('settings', () => {
    it('returns defaults when storage is empty', async () => {
        expect(await getSettings(fakeStorage())).toEqual(DEFAULTS);
    });
    it('merges stored values over defaults', async () => {
        const s = fakeStorage({ voice: 'am_michael' });
        expect(await getSettings(s)).toEqual({ ...DEFAULTS, voice: 'am_michael' });
    });
    it('persists a patch and returns the merged result', async () => {
        const s = fakeStorage();
        const result = await setSettings({ speed: 1.5 }, s);
        expect(result.speed).toBe(1.5);
        expect(s._store.speed).toBe(1.5);
    });
});

describe('voices', () => {
    it('has 27 well-formed entries', () => {
        expect(VOICES).toHaveLength(27);
        for (const v of VOICES) {
            expect(typeof v.id).toBe('string');
            expect(typeof v.name).toBe('string');
        }
    });
    it('default voice exists in the list and matches settings default', () => {
        expect(VOICES.some((v) => v.id === DEFAULT_VOICE)).toBe(true);
        expect(DEFAULTS.voice).toBe(DEFAULT_VOICE);
    });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run extension/shared/settings.test.js`
Expected: FAIL — modules not found.

- [ ] **Step 3: Write `voices.js`**

Create `extension/shared/voices.js` by copying the `id` + `name` of every entry from `src/constants.js` (the `VOICES` array). There are 27:

```js
// Kokoro voice list — copied from src/constants.js (id + name only).
export const VOICES = [
    { id: 'af_heart', name: 'Heart (US Female)' },
    { id: 'af_bella', name: 'Bella (US Female)' },
    { id: 'af_alloy', name: 'Alloy (US Female)' },
    { id: 'af_aoede', name: 'Aoede (US Female)' },
    { id: 'af_jessica', name: 'Jessica (US Female)' },
    { id: 'af_kore', name: 'Kore (US Female)' },
    { id: 'af_nicole', name: 'Nicole (US Female)' },
    { id: 'af_nova', name: 'Nova (US Female)' },
    { id: 'af_river', name: 'River (US Male)' },
    { id: 'af_sarah', name: 'Sarah (US Female)' },
    { id: 'af_sky', name: 'Sky (US Female)' },
    { id: 'am_michael', name: 'Michael (US Male)' },
    { id: 'am_adam', name: 'Adam (US Male)' },
    { id: 'am_echo', name: 'Echo (US Male)' },
    { id: 'am_eric', name: 'Eric (US Male)' },
    { id: 'am_fenrir', name: 'Fenrir (US Male)' },
    { id: 'am_liam', name: 'Liam (US Male)' },
    { id: 'am_onyx', name: 'Onyx (US Male)' },
    { id: 'am_puck', name: 'Puck (US Male)' },
    { id: 'bf_emma', name: 'Emma (UK Female)' },
    { id: 'bf_alice', name: 'Alice (UK Female)' },
    { id: 'bf_isabella', name: 'Isabella (UK Female)' },
    { id: 'bf_lily', name: 'Lily (UK Female)' },
    { id: 'bm_daniel', name: 'Daniel (UK Male)' },
    { id: 'bm_fable', name: 'Fable (UK Male)' },
    { id: 'bm_george', name: 'George (UK Male)' },
    { id: 'bm_lewis', name: 'Lewis (UK Male)' },
];

export const DEFAULT_VOICE = 'af_heart';
```

- [ ] **Step 4: Write `settings.js`**

Create `extension/shared/settings.js`:

```js
import { DEFAULT_VOICE } from './voices.js';

export const DEFAULTS = { voice: DEFAULT_VOICE, speed: 1.0, baseUrl: 'http://localhost:8000' };

export async function getSettings(storage) {
    const store = storage || chrome.storage.sync;
    const stored = await store.get(Object.keys(DEFAULTS));
    return { ...DEFAULTS, ...stored };
}

export async function setSettings(patch, storage) {
    const store = storage || chrome.storage.sync;
    await store.set(patch);
    return getSettings(store);
}
```

- [ ] **Step 5: Run tests + lint**

Run: `npx vitest run extension/shared/settings.test.js` → Expected: PASS (all 5).
Run: `npm run lint` → Expected: clean.

- [ ] **Step 6: Commit** (ask first)

```bash
git add extension/shared/settings.js extension/shared/voices.js extension/shared/settings.test.js
git commit -m "feat(ext): settings (chrome.storage) + bundled voice list"
```

---

### Task 4: Manifest, background service worker, and injection pipeline

**Files:**
- Create: `extension/manifest.json`, `extension/background.js`, `extension/content/content.js` (temporary stub — replaced in Task 5)

**Interfaces:**
- Consumes: nothing (uses `chrome.*` only).
- Produces: message contract the content script listens for — `{ type: 'read-aloud:start', mode: 'selection' | 'page' }` sent to the active tab; and a runtime message the popup will send to the background — `{ type: 'read-aloud:popup-read', tabId: number, mode }`.

- [ ] **Step 1: Write the manifest**

Create `extension/manifest.json`:

```json
{
  "manifest_version": 3,
  "name": "Natural Reader — Read Aloud",
  "version": "0.1.0",
  "description": "Read selected text or the whole page aloud using the local Kokoro TTS backend.",
  "permissions": ["contextMenus", "activeTab", "scripting", "storage"],
  "host_permissions": ["http://localhost/*", "http://127.0.0.1/*"],
  "background": { "service_worker": "background.js", "type": "module" },
  "action": { "default_popup": "popup/popup.html", "default_title": "Read Aloud" },
  "options_page": "options/options.html",
  "web_accessible_resources": [
    { "resources": ["shared/*.js", "content/toolbar.css"], "matches": ["<all_urls>"] }
  ]
}
```

- [ ] **Step 2: Write the background service worker**

Create `extension/background.js`:

```js
const MENU_SELECTION = 'read-aloud-selection';
const MENU_PAGE = 'read-aloud-page';

chrome.runtime.onInstalled.addListener(() => {
    chrome.contextMenus.create({ id: MENU_SELECTION, title: 'Read selection aloud', contexts: ['selection'] });
    chrome.contextMenus.create({ id: MENU_PAGE, title: 'Read whole page aloud', contexts: ['page'] });
});

async function startRead(tabId, mode) {
    // Injecting an already-injected file is a no-op re-run guarded inside content.js.
    await chrome.scripting.executeScript({ target: { tabId }, files: ['content/content.js'] });
    await chrome.tabs.sendMessage(tabId, { type: 'read-aloud:start', mode });
}

chrome.contextMenus.onClicked.addListener((info, tab) => {
    if (!tab || tab.id == null) return;
    if (info.menuItemId === MENU_SELECTION) startRead(tab.id, 'selection');
    else if (info.menuItemId === MENU_PAGE) startRead(tab.id, 'page');
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === 'read-aloud:popup-read' && msg.tabId != null) {
        startRead(msg.tabId, msg.mode)
            .then(() => sendResponse({ ok: true }))
            .catch((e) => sendResponse({ ok: false, error: String(e) }));
        return true; // keep the message channel open for the async response
    }
    return undefined;
});
```

- [ ] **Step 3: Write a temporary content-script stub**

Create `extension/content/content.js` (proves the pipeline; replaced in Task 5):

```js
if (!window.__readAloudInjected) {
    window.__readAloudInjected = true;
    chrome.runtime.onMessage.addListener((msg) => {
        if (msg && msg.type === 'read-aloud:start') {
            // eslint-disable-next-line no-console
            console.log('[read-aloud] start received, mode =', msg.mode);
        }
    });
}
```

- [ ] **Step 4: Manual verification**

1. Open `chrome://extensions`, enable Developer mode, "Load unpacked" → select the `extension/` folder. It loads with no manifest errors.
2. Open any normal web page (not `chrome://`). Select some text, right-click → "Read selection aloud" appears; right-click empty area → "Read whole page aloud" appears.
3. Open the page's DevTools console. Click "Read whole page aloud" → console shows `[read-aloud] start received, mode = page`. Select text → "Read selection aloud" → `mode = selection`.
4. Confirm the service worker has no errors (chrome://extensions → the extension → "service worker" link → console).

Expected: all four pass. If the menus don't appear after code changes, click the extension's reload icon on `chrome://extensions`.

- [ ] **Step 5: Commit** (ask first)

```bash
git add extension/manifest.json extension/background.js extension/content/content.js
git commit -m "feat(ext): MV3 manifest + background (context menus, on-demand injection)"
```

---

### Task 5: Content script — floating toolbar + queue playback

**Files:**
- Modify/replace: `extension/content/content.js`
- Create: `extension/content/toolbar.css`

**Interfaces:**
- Consumes: `read-aloud:start` message from Task 4; `shared/text.js`, `shared/api.js`, `shared/settings.js`, `shared/voices.js` via dynamic import.
- Produces: the on-page reading experience. No exports (browser-only).

**Behavior contract:**
- On `read-aloud:start`: load settings + shared modules; extract text (`extractSelectionOrPage`); `splitSentences`. If empty → show a transient "Nothing to read" message and stop.
- **Selection mode:** one `synthesize` call → play the single WAV.
- **Page mode:** `chunkSentences(sentences, 30)` → play chunks in a queue: synthesize chunk 0, start playing it, and prefetch chunk 1 while it plays; when a chunk's audio ends, advance. Show "chunk N / M".
- Toolbar (Shadow DOM on a host appended to `document.documentElement`): Play/Pause, Stop, seek/progress bar, voice `<select>` (from `VOICES`), speed control (0.5–2.0), close (✕), drag handle. Changing voice/speed applies to the **next** synthesized chunk.
- On stop/close/completion: pause audio, revoke all created `blob:` URLs, remove the toolbar host.
- On synthesize/network error: show the error message in the toolbar and stop the queue.
- Re-injection guard: wrap init in `if (!window.__readAloudInjected)`.

- [ ] **Step 1: Write the toolbar stylesheet**

Create `extension/content/toolbar.css` (injected into the shadow root, so selectors are local):

```css
:host { all: initial; }
.bar {
    position: fixed; z-index: 2147483647; bottom: 20px; left: 50%;
    transform: translateX(-50%);
    display: flex; align-items: center; gap: 10px;
    padding: 8px 12px; border-radius: 12px;
    background: #1f2430; color: #f5f7fa;
    font: 13px/1.4 system-ui, sans-serif;
    box-shadow: 0 6px 24px rgba(0,0,0,.35);
    user-select: none;
}
.bar button {
    background: #2d3444; color: inherit; border: 0; border-radius: 8px;
    padding: 6px 10px; cursor: pointer; font: inherit;
}
.bar button:hover { background: #3a4256; }
.bar button:disabled { opacity: .5; cursor: default; }
.bar select, .bar input[type="range"] { font: inherit; }
.handle { cursor: grab; padding: 0 4px; opacity: .6; }
.status { min-width: 70px; opacity: .8; }
.progress { width: 120px; }
.bar.error { background: #5b2330; }
```

- [ ] **Step 2: Write the content script**

Replace `extension/content/content.js` with:

```js
if (!window.__readAloudInjected) {
    window.__readAloudInjected = true;

    const RA = {};

    async function loadShared() {
        const base = chrome.runtime.getURL('shared/');
        const [text, api, settings, voices] = await Promise.all([
            import(base + 'text.js'),
            import(base + 'api.js'),
            import(base + 'settings.js'),
            import(base + 'voices.js'),
        ]);
        return { ...text, ...api, ...settings, VOICES: voices.VOICES };
    }

    // ---- Toolbar (Shadow DOM) -------------------------------------------
    async function buildToolbar(mods, state) {
        const host = document.createElement('div');
        host.id = '__read_aloud_host';
        document.documentElement.appendChild(host);
        const root = host.attachShadow({ mode: 'open' });

        const style = document.createElement('style');
        const cssUrl = chrome.runtime.getURL('content/toolbar.css');
        style.textContent = await (await fetch(cssUrl)).text();
        root.appendChild(style);

        const bar = document.createElement('div');
        bar.className = 'bar';
        bar.innerHTML = `
            <span class="handle" title="Drag">⠿</span>
            <button data-act="toggle">⏸</button>
            <button data-act="stop">⏹</button>
            <input class="progress" type="range" min="0" max="100" value="0" />
            <select data-act="voice"></select>
            <label>Speed <input data-act="speed" type="range" min="0.5" max="2" step="0.1" /></label>
            <span class="status"></span>
            <button data-act="close">✕</button>
        `;
        root.appendChild(bar);

        const voiceSel = bar.querySelector('[data-act="voice"]');
        for (const v of mods.VOICES) {
            const opt = document.createElement('option');
            opt.value = v.id;
            opt.textContent = v.name; // textContent — never innerHTML
            if (v.id === state.voice) opt.selected = true;
            voiceSel.appendChild(opt);
        }
        bar.querySelector('[data-act="speed"]').value = String(state.speed);

        makeDraggable(bar, bar.querySelector('.handle'));
        return { host, root, bar };
    }

    function makeDraggable(bar, handle) {
        let sx = 0; let sy = 0; let ox = 0; let oy = 0; let dragging = false;
        handle.addEventListener('pointerdown', (e) => {
            dragging = true; sx = e.clientX; sy = e.clientY;
            const r = bar.getBoundingClientRect(); ox = r.left; oy = r.top;
            handle.setPointerCapture(e.pointerId);
        });
        handle.addEventListener('pointermove', (e) => {
            if (!dragging) return;
            bar.style.left = `${ox + (e.clientX - sx)}px`;
            bar.style.top = `${oy + (e.clientY - sy)}px`;
            bar.style.bottom = 'auto'; bar.style.transform = 'none';
        });
        handle.addEventListener('pointerup', (e) => { dragging = false; handle.releasePointerCapture(e.pointerId); });
    }

    // ---- Reading session ------------------------------------------------
    async function startReading(mode) {
        const mods = RA.mods || (RA.mods = await loadShared());
        const state = await mods.getSettings();

        const raw = mods.extractSelectionOrPage(document, mode);
        const sentences = mods.splitSentences(raw);
        if (sentences.length === 0) { flashNoContent(); return; }

        const chunks = mode === 'selection' ? [sentences] : mods.chunkSentences(sentences, 30);
        const ui = await buildToolbar(mods, state);
        const audio = new Audio();
        const urls = [];
        let stopped = false;

        const statusEl = ui.bar.querySelector('.status');
        const toggleBtn = ui.bar.querySelector('[data-act="toggle"]');
        const progress = ui.bar.querySelector('.progress');

        const cleanup = () => {
            stopped = true;
            audio.pause();
            urls.forEach((u) => URL.revokeObjectURL(u));
            ui.host.remove();
        };

        ui.bar.querySelector('[data-act="close"]').addEventListener('click', cleanup);
        ui.bar.querySelector('[data-act="stop"]').addEventListener('click', cleanup);
        toggleBtn.addEventListener('click', () => {
            if (audio.paused) { audio.play(); toggleBtn.textContent = '⏸'; }
            else { audio.pause(); toggleBtn.textContent = '▶'; }
        });
        ui.bar.querySelector('[data-act="voice"]').addEventListener('change', (e) => { state.voice = e.target.value; });
        ui.bar.querySelector('[data-act="speed"]').addEventListener('input', (e) => {
            state.speed = parseFloat(e.target.value); audio.playbackRate = 1; // speed applies at synth time
        });
        audio.addEventListener('timeupdate', () => {
            if (audio.duration) progress.value = String((audio.currentTime / audio.duration) * 100);
        });
        progress.addEventListener('input', () => {
            if (audio.duration) audio.currentTime = (Number(progress.value) / 100) * audio.duration;
        });

        // Prefetch-one-ahead queue.
        const cache = new Map(); // index -> Promise<Blob>
        const fetchChunk = (i) => {
            if (i >= chunks.length) return null;
            if (!cache.has(i)) {
                cache.set(i, mods.synthesize({
                    sentences: chunks[i], voice: state.voice, speed: state.speed, baseUrl: state.baseUrl,
                }));
            }
            return cache.get(i);
        };

        async function playIndex(i) {
            if (stopped || i >= chunks.length) { if (!stopped && i >= chunks.length) cleanup(); return; }
            statusEl.textContent = chunks.length > 1 ? `chunk ${i + 1} / ${chunks.length}` : 'reading';
            let blob;
            try { blob = await fetchChunk(i); } catch (err) { showError(ui, statusEl, err); return; }
            if (stopped) return;
            const url = URL.createObjectURL(blob);
            urls.push(url);
            audio.src = url;
            fetchChunk(i + 1); // prefetch next while this plays
            audio.onended = () => playIndex(i + 1);
            try { await audio.play(); } catch { /* autoplay/user-gesture edge — ignore */ }
        }

        playIndex(0);
    }

    function showError(ui, statusEl, err) {
        ui.bar.classList.add('error');
        statusEl.textContent = String(err && err.message ? err.message : err);
    }

    function flashNoContent() {
        const n = document.createElement('div');
        n.textContent = 'Read Aloud: nothing to read';
        n.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);z-index:2147483647;background:#1f2430;color:#fff;padding:8px 12px;border-radius:10px;font:13px system-ui;';
        document.documentElement.appendChild(n);
        setTimeout(() => n.remove(), 2000);
    }

    chrome.runtime.onMessage.addListener((msg) => {
        if (msg && msg.type === 'read-aloud:start') startReading(msg.mode);
    });
}
```

- [ ] **Step 3: Reload and manually verify (backend running)**

Prerequisite: `./startup.sh up` (or the backend) is running and `curl -s http://localhost:8000/v1/health` returns `{"status":"ok",...}`.

1. `chrome://extensions` → reload the extension. Open a text-heavy article.
2. Select a paragraph → right-click → "Read selection aloud". The toolbar appears and audio plays. Play/Pause toggles; Stop removes the toolbar and halts audio.
3. Right-click → "Read whole page aloud". Audio starts after the first chunk (not the whole page); status shows "chunk 1 / N", advancing as it plays.
4. Change the voice mid-read → the **next** chunk uses the new voice. Drag the toolbar by the ⠿ handle. Close (✕) halts and removes it.
5. Confirm no console errors in the page or service worker.

- [ ] **Step 4: Manually verify the backend-down path**

Stop the backend. Trigger a read → the toolbar turns red and shows a "TTS request failed…" message; nothing hangs.

- [ ] **Step 5: Commit** (ask first)

```bash
git add extension/content/content.js extension/content/toolbar.css
git commit -m "feat(ext): floating toolbar + chunked-queue playback content script"
```

---

### Task 6: Popup

**Files:**
- Create: `extension/popup/popup.html`, `extension/popup/popup.js`

**Interfaces:**
- Consumes: `shared/settings.js`, `shared/voices.js`, `shared/api.js` (static ESM import — popup is an extension page); sends `{ type: 'read-aloud:popup-read', tabId, mode }` to the background.
- Produces: nothing.

- [ ] **Step 1: Write the popup HTML**

Create `extension/popup/popup.html`:

```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <style>
    body { width: 240px; margin: 0; padding: 12px; font: 13px system-ui, sans-serif; background: #1f2430; color: #f5f7fa; }
    h1 { font-size: 14px; margin: 0 0 10px; display: flex; align-items: center; gap: 6px; }
    .dot { width: 9px; height: 9px; border-radius: 50%; background: #888; display: inline-block; }
    .dot.ok { background: #35c46a; } .dot.bad { background: #e0526a; }
    button.read { width: 100%; margin: 4px 0; padding: 8px; border: 0; border-radius: 8px; background: #3a4256; color: #fff; cursor: pointer; }
    label { display: block; margin: 8px 0 2px; opacity: .8; }
    select, input { width: 100%; box-sizing: border-box; }
    a { color: #8ab4ff; font-size: 12px; display: inline-block; margin-top: 10px; cursor: pointer; }
  </style>
</head>
<body>
  <h1><span class="dot" id="dot"></span> Read Aloud</h1>
  <button class="read" data-mode="selection">Read selection</button>
  <button class="read" data-mode="page">Read whole page</button>
  <label>Voice</label>
  <select id="voice"></select>
  <label>Speed <span id="speedVal"></span></label>
  <input id="speed" type="range" min="0.5" max="2" step="0.1" />
  <a id="openOptions">Settings…</a>
  <script type="module" src="popup.js"></script>
</body>
</html>
```

- [ ] **Step 2: Write the popup logic**

Create `extension/popup/popup.js`:

```js
import { getSettings, setSettings } from '../shared/settings.js';
import { VOICES } from '../shared/voices.js';
import { health } from '../shared/api.js';

const voiceSel = document.getElementById('voice');
const speed = document.getElementById('speed');
const speedVal = document.getElementById('speedVal');
const dot = document.getElementById('dot');

async function init() {
    const s = await getSettings();
    for (const v of VOICES) {
        const opt = document.createElement('option');
        opt.value = v.id; opt.textContent = v.name;
        if (v.id === s.voice) opt.selected = true;
        voiceSel.appendChild(opt);
    }
    speed.value = String(s.speed);
    speedVal.textContent = `${s.speed}×`;

    const ok = await health({ baseUrl: s.baseUrl });
    dot.classList.add(ok ? 'ok' : 'bad');
}

voiceSel.addEventListener('change', () => setSettings({ voice: voiceSel.value }));
speed.addEventListener('input', () => { speedVal.textContent = `${speed.value}×`; setSettings({ speed: parseFloat(speed.value) }); });
document.getElementById('openOptions').addEventListener('click', () => chrome.runtime.openOptionsPage());

for (const btn of document.querySelectorAll('button.read')) {
    btn.addEventListener('click', async () => {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab || tab.id == null) return;
        await chrome.runtime.sendMessage({ type: 'read-aloud:popup-read', tabId: tab.id, mode: btn.dataset.mode });
        window.close();
    });
}

init();
```

- [ ] **Step 3: Manual verification**

1. Reload the extension. Click the toolbar icon → the popup opens; the status dot is green when the backend is up, red when down.
2. Voice/speed reflect saved settings; changing them persists (reopen popup → values retained).
3. On an article, click "Read whole page" → popup closes and the on-page toolbar starts reading. "Read selection" works with text selected.
4. "Settings…" opens the options page.

- [ ] **Step 4: Commit** (ask first)

```bash
git add extension/popup/popup.html extension/popup/popup.js
git commit -m "feat(ext): popup — read buttons, voice/speed, backend status"
```

---

### Task 7: Options page

**Files:**
- Create: `extension/options/options.html`, `extension/options/options.js`

**Interfaces:**
- Consumes: `shared/settings.js`, `shared/voices.js`, `shared/api.js` (static ESM import).
- Produces: nothing.

- [ ] **Step 1: Write the options HTML**

Create `extension/options/options.html`:

```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <style>
    body { max-width: 420px; margin: 40px auto; font: 14px system-ui, sans-serif; }
    label { display: block; margin: 14px 0 4px; font-weight: 600; }
    input, select { width: 100%; box-sizing: border-box; padding: 6px; }
    .row { display: flex; gap: 8px; align-items: center; }
    button { padding: 8px 14px; margin-top: 8px; cursor: pointer; }
    #testResult { margin-left: 10px; font-size: 13px; }
  </style>
</head>
<body>
  <h1>Read Aloud — Settings</h1>
  <label>Default voice</label>
  <select id="voice"></select>
  <label>Default speed <span id="speedVal"></span></label>
  <input id="speed" type="range" min="0.5" max="2" step="0.1" />
  <label>Backend URL</label>
  <input id="baseUrl" type="text" />
  <div class="row">
    <button id="test">Test connection</button>
    <span id="testResult"></span>
  </div>
  <script type="module" src="options.js"></script>
</body>
</html>
```

- [ ] **Step 2: Write the options logic**

Create `extension/options/options.js`:

```js
import { getSettings, setSettings } from '../shared/settings.js';
import { VOICES } from '../shared/voices.js';
import { health } from '../shared/api.js';

const voice = document.getElementById('voice');
const speed = document.getElementById('speed');
const speedVal = document.getElementById('speedVal');
const baseUrl = document.getElementById('baseUrl');
const testResult = document.getElementById('testResult');

async function init() {
    const s = await getSettings();
    for (const v of VOICES) {
        const opt = document.createElement('option');
        opt.value = v.id; opt.textContent = v.name;
        if (v.id === s.voice) opt.selected = true;
        voice.appendChild(opt);
    }
    speed.value = String(s.speed);
    speedVal.textContent = `${s.speed}×`;
    baseUrl.value = s.baseUrl;
}

voice.addEventListener('change', () => setSettings({ voice: voice.value }));
speed.addEventListener('input', () => { speedVal.textContent = `${speed.value}×`; setSettings({ speed: parseFloat(speed.value) }); });
baseUrl.addEventListener('change', () => setSettings({ baseUrl: baseUrl.value.trim() }));

document.getElementById('test').addEventListener('click', async () => {
    testResult.textContent = 'Testing…';
    const ok = await health({ baseUrl: baseUrl.value.trim() });
    testResult.textContent = ok ? '✓ Connected' : '✗ Not reachable';
});

init();
```

- [ ] **Step 3: Manual verification**

1. Reload the extension → right-click the icon → Options (or "Settings…" from the popup).
2. Change default voice/speed → persists (reopen options; popup reflects it).
3. Edit Backend URL, click "Test connection": with the backend up → "✓ Connected"; with a wrong port → "✗ Not reachable".
4. Note: a non-loopback host will fail to fetch from the content script (host-permission limit) — expected for v1.

- [ ] **Step 4: Commit** (ask first)

```bash
git add extension/options/options.html extension/options/options.js
git commit -m "feat(ext): options page — default voice/speed/backend URL + test connection"
```

---

### Task 8: Extension README + full verification pass

**Files:**
- Create: `extension/README.md`

- [ ] **Step 1: Write the README**

Create `extension/README.md` with:
- One-paragraph summary of what the extension does.
- **Install (unpacked):** `chrome://extensions` → Developer mode → Load unpacked → select `extension/`.
- **Requirement:** the local TTS backend must be running (`./startup.sh up`, health at `http://localhost:8000/v1/health`).
- **Usage:** right-click menus (selection / page), popup buttons, options page (voice, speed, backend URL).
- **Limitations (v1):** loopback backend only; no highlight-follow; basic page extraction; audio stops on navigation.
- **The manual E2E checklist** (copy the checklist below).

Manual E2E checklist to include:
```
[ ] Load unpacked; no manifest/service-worker errors.
[ ] Backend up: popup status dot is green.
[ ] Selection → context menu → toolbar + audio plays.
[ ] Whole page → chunked queue; "chunk N / M"; starts after first chunk.
[ ] Play/Pause, Stop, seek, drag, close all work.
[ ] Voice change mid-read applies to the next chunk.
[ ] Backend down → toolbar shows error (red), popup dot red, no hang.
[ ] Empty selection/page → "nothing to read" notice.
[ ] Options: change voice/speed/URL persists; Test connection ✓ / ✗.
```

- [ ] **Step 2: Run the full test suite + lint**

Run: `npm run test:run`
Expected: all tests pass, including the existing suite and the new `extension/shared/*.test.js` (text, api, settings — 3 files).

Run: `npm run lint`
Expected: no errors.

- [ ] **Step 3: Full manual E2E pass**

Walk the checklist above end-to-end with the backend running. Fix any issue in the relevant task's files before finishing.

- [ ] **Step 4: Commit** (ask first)

```bash
git add extension/README.md
git commit -m "docs(ext): extension README + manual E2E checklist"
```

---

## Notes for the implementer

- **Reloading after changes:** background/manifest changes need a reload on `chrome://extensions`; content-script changes take effect on the next injection (next context-menu/popup trigger), no page reload of the extension needed, but reload the target web page if a stale content script is cached.
- **`chrome://` and the Web Store pages** block content-script injection — test on normal `http(s)` pages.
- **Autoplay:** the first `audio.play()` is triggered by a user gesture (menu/popup click), so autoplay restrictions don't block it.
- **Why dynamic import in the content script:** it's injected as a classic script and cannot use static `import`; `import(chrome.runtime.getURL('shared/x.js'))` loads the same ESM the tests use, keeping one source of truth with no bundler.
- **Very-long-page decision (spec error-handling):** the spec floated an optional hard character cap. Plan decision: **no hard cap in v1.** The chunked queue with prefetch-one-ahead already bounds work-in-flight and gives fast time-to-first-audio, and the Stop/✕ button lets the user end a long read at any time. A configurable cap is left as a follow-up rather than adding a magic number now.
