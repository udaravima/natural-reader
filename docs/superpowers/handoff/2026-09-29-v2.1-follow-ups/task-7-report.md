# Task 7 report — Reader console errors (cloud session, controller-implemented)

BASE 4018753. Task 3's awaited `saveBook` ordering in `usePdfEngine` is untouched.

## Running-app check
Headless Chromium (playwright-core against `/opt/pw-browsers`). The SPA ran on vite :5199, and the backend in dev bypass on :8000 with the Kokoro module stubbed (silence, then a 4 s tone). Scripts are in the session scratchpad: `reopen.mjs` (open a PDF → reload → reopen from Your Library → reopen through the picker) and `play2.mjs` (open → Play → reopen mid-playback → Play).
- **Old code (git stash):** "Cannot use the same canvas during multiple render() operations" on every PDF open, and React's `<button>` cannot be a descendant of `<button>` warning. **No `blob:` / ERR_FILE_NOT_FOUND in any scenario.**
- **New code:** the console is clean in both scripts, apart from the expected 502/404s (no Ollama; the doc isn't on the server).

## 1. The pdf.js canvas error
- **Cause:** `renderPageVisual` started `page.render()` on the shared canvas while the previous render (a page step, zoom, remount or StrictMode double effect) was still drawing.
- **Fix** (`src/hooks/usePdfEngine.js`):
  - `renderTaskRef` holds the running `RenderTask`. It is `cancel()`ed right before the next render starts (pdf.js releases the canvas synchronously).
  - `renderSeqRef` lets a superseded render return before painting its text layer.
  - `RenderingCancelledException` is swallowed.
  - A render still running at unmount is cancelled.
- **Test:** `src/hooks/usePdfEngine.renderCancel.test.js`. Two renders in quick succession: the first task is cancelled, and there is no `console.error` or "Render Error". It was red before the fix.

## 2. Nested button in WelcomeScreen
- **Fix:**
  - The row is a `div` with `role="button"`, `tabIndex=0` and `aria-label="Open {name}"`.
  - Enter and Space open it. Keys pressed on the inner remove button are ignored by the row (`e.target !== e.currentTarget`).
  - The remove button stops propagation itself (it relied on the parent handler before), has `aria-label`, and becomes visible on keyboard focus.
- **Test:** `src/components/WelcomeScreen.test.jsx` (3). There is no nested-button warning, click/Enter/Space open the row, and remove doesn't open it. It was red before the fix.

## 3. Revoked `blob:` URL on reopening a PDF: investigation
- **Not reproduced**, even on the old code, in any of the scenarios above.
- **Found by reading the code:** the one place a `blob:` URL is revoked while something may still use it is the reader's `<audio>` element in `useTtsEngine`:
  - `onended` revoked the URL that had just played, and it stays as the element's `src`;
  - `clearCache()`, which runs on every `textItems` change (a new page, or opening/reopening a document), revoked every cached URL, including the loaded one.
- **Why it would show up:** a later seek or reload of the element (e.g. `currentTime = 0` at the end of a page, a new `play()`) can fetch its `src` again. Chrome only needs to when it hasn't buffered the whole clip, which is likely with real Kokoro output and not with the short stub here.
- **Fix, the plan's "revoke on replacement or unmount":**
  - `loadReaderAudio(url)` sets `src` and revokes the previous loaded URL only after the element has moved on;
  - `clearCache` skips the loaded URL;
  - `onended` no longer revokes;
  - an unmount effect revokes the cache and the loaded URL.
- **Test:** `src/hooks/useTtsEngine.blobUrls.test.js` (2):
  - the loaded URL survives a document change and its own end, and is revoked on unmount (red before the fix);
  - the previous URL is revoked once the next sentence replaces it.
- **Status:** a plausible root cause is fixed, but it is **not confirmed as the walk's error.** It is on the list for the local walk with real TTS.

## Suites
- frontend: 380 passed;
- backend: unchanged (591);
- eslint: 1 error (baseline).

CHANGELOG: one Fixed line.
