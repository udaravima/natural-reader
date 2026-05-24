# v1.7.3 — PDF canvas blank-after-toggle fix

One-line patch release.

## Fixed

### PDF canvas went blank after toggling Reader ↔ Chat

Switching from Reader to Chat unmounts `PdfViewer`; switching back mounts a *fresh* `<canvas>` DOM node. The render `useEffect` in `usePdfEngine` only re-fired when `[pdfDoc, currentPage, scale, fileType]` changed — none of those move on a view-mode toggle — so the new canvas stayed empty until the user manually stepped the page forward and back. Same symptom appeared in some Alt-Tab scenarios where the canvas backing buffer got freed.

`canvasRef` is now exposed from `usePdfEngine` as a **callback ref**. Each time React attaches a `<canvas>` element, the callback updates both `canvasRef.current` and bumps a `canvasMountNonce` state. The render effect picks up the nonce and re-paints the current page against the fresh node automatically.

Consumer code is unchanged — React accepts callback functions in `ref={...}` exactly like ref objects, so `PdfViewer`'s `<canvas ref={canvasRef} />` keeps working.

## Upgrade notes

- **Pure frontend.** No backend, schema, or env-var changes. `npm run build` (or HMR on a running dev server) is enough.

## Full changelog

[CHANGELOG.md#173---2026-05-23](../CHANGELOG.md#173---2026-05-23) · diff: [v1.7.2…v1.7.3](https://github.com/udaravima/natural-reader/compare/v1.7.2...v1.7.3)
