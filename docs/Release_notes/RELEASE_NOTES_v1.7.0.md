# v1.7.0 — Docling PDF→Markdown, audiobook export, chat audio export

Three big additions on top of the v1.6.0 document-chat backbone: clean Markdown extraction from PDFs, audio export for entire documents and individual chat messages, and several reader-UX fixes.

## Highlights

### Docling PDF → Markdown conversion

Convert any PDF to layout-aware Markdown on the backend using [Docling](https://github.com/DS4SD/docling). Click **Convert** in the toolbar to pick a quality preset (Fast / Standard / Accurate — Accurate uses the GraniteDocling VLM pipeline), force-enable OCR for scanned PDFs, toggle table extraction, choose how to handle images (drop / embed-base64 / VLM-describe), and optionally limit the page range. After conversion the doc is automatically re-chunked and re-embedded so retrieval picks up tables and headings the native pdf.js extractor was missing. A new **PDF | MD** segmented control in the toolbar lets you read the converted Markdown inline, while the page navigation, TTS, and Ask AI selection actions all keep working on the cleaner text.

### Export and delete converted Markdown

- **Download icon** — saves the full document MD as `{filename}.md`.
- **Trash icon** — wipes `doc_pages` and the chunks/embeddings derived from them and resets the conversion state. The retained PDF on disk stays so reconversion with new options is a single click.

### Audiobook export — full document as one WAV

New **Library** icon in the header synthesizes every page through `/v1/batch_synthesize`, stitches the WAVs in the browser, and downloads `{filename}_audiobook.wav`. Shows a live `current/total` progress label with a Cancel button while running, and skips any page that fails to synthesize instead of aborting the whole job.

### Per-message audio export in chat

Every assistant chat message gets a new **Audio** action next to *Read aloud* / *Copy*. Click it to download the message as a `.wav` named after the active session title and the message's position in the conversation. Hidden when Kokoro isn't the selected backend (system voice can't be captured to a file).

### Home button

New Home icon in the header (reader mode, doc open) stops TTS, drops the current document, and returns to the library / welcome screen. Reading progress was already persisted, so reopening the doc resumes where you left off.

### Wider, properly-scrolling MD reader

- Width bumped from `min(820px, 80vw)` → `min(1200px, 95vw)` so MD prose, tables, and code blocks have room to breathe.
- The converted-MD reader no longer maintains its own nested scroll container — the PDF viewer's outer scroller handles everything, so page-nav, zoom, Fit, and Width buttons all behave consistently.
- Added a programmatic-scroll quiet window so the IntersectionObserver no longer overrides a "next page" click mid-scroll.

## New API endpoints

| Endpoint | Purpose |
|---|---|
| `POST /v1/docs/{doc_id}/pdf` | Multipart upload — persist raw PDF bytes for reconversion. |
| `DELETE /v1/docs/{doc_id}/pdf` | Remove the retained PDF (keeps MD + chunks). |
| `POST /v1/docs/{doc_id}/convert` | `202` — kicks off a docling job with the supplied options. |
| `GET /v1/docs/{doc_id}/markdown` (`?page=N` optional) | Returns the converted Markdown as `text/markdown`. |
| `DELETE /v1/docs/{doc_id}/markdown` | Wipe the converted MD + derived chunks. |

## Upgrade notes

1. `pip install -r requirements.txt` — adds `docling>=2.0` and `python-multipart`. Docling pulls in transformers + torch (CPU); the first conversion downloads layout / table models (~500 MB to ~2 GB total).
2. Set `DOCLING_ENABLED=true` to enable the conversion routes (the rest of the server boots regardless).
3. New migration `003_docling.sql` is applied automatically on next startup — adds conversion columns on `documents` and a new `doc_pages` table.
4. Optional env vars: `PDF_STORAGE_DIR` (default `./data/pdfs`), `PDF_UPLOAD_MAX_MB` (default `50`).
5. Reading view's MD box is wider — if you were relying on the narrower `820px` layout for any custom CSS, retune.

## Caveats

- **Docling conversion is heavy.** Standard preset on a 50-page text PDF takes a few minutes on CPU; Accurate (VLM) is slower still. There's no client-side abort yet — kill the backend if you need to stop a run.
- **Audiobook export is single-threaded by default** because the server runs one uvicorn worker and Kokoro holds it for each call. Run `uvicorn server.app:app --workers 4` (or whatever fits your RAM) to actually fan synthesis out across cores. See `docs/CHAT_WITH_PDF.md` for the recipe.
- **WAV concat assumes identical PCM format** across pages — fine as long as voice + speed don't change mid-export.
- **Page-range applies to conversion only**, not RAG retrieval. Search still returns top-K across all converted pages.

## Full changelog

[CHANGELOG.md#170---2026-05-23](../CHANGELOG.md#170---2026-05-23) · diff: [v1.6.0…v1.7.0](https://github.com/udaravima/natural-reader/compare/v1.6.0...v1.7.0)
