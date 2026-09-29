### Task 7: Reader console errors found in the walk

- **The pdf.js "Cannot use the same canvas during multiple render() operations" error** (`usePdfEngine.js:110`).
  - Keep the current `RenderTask` in a ref and `cancel()` it before starting a new render.
  - Ignore `RenderingCancelledException`.
  - Test: two renders in quick succession cancel the first.
- **A `<button>` inside a `<button>` in `WelcomeScreen`'s recent-book row.**
  - Make the row a `div` with `role="button"`, `tabIndex=0`, and an Enter/Space handler.
  - Test: no nested-button warning, and it opens with the keyboard.
- **A revoked `blob:` URL is fetched when a PDF is reopened** (`ERR_FILE_NOT_FOUND`).
  - Find the revoke that runs before its last use, and revoke on unmount or replacement instead.

