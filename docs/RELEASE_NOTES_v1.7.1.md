# v1.7.1 — Session-order fix, distraction-free reading, mobile menu, parallel audiobook

Patch release on top of v1.7.0. One real bug fix, two small UX additions, and a meaningful perf knob for audiobook export.

## Fixed

### Chat session reload no longer shows messages in `[response, query, response, query]` order

The save path inserts every message in one transaction (so all rows share `created_at`), and the user prompt + the assistant placeholder are created in the same `Date.now()` tick (so they share `timestamp`). The final tiebreak fell to `id`, where `a-<ts>` sorts lexicographically before `u-<ts>`, which is exactly the pattern users reported.

Two-layer fix:

- **Backend**: the `GET /v1/chat/sessions/{id}` SELECT now adds a role-based `CASE` between `timestamp` and the `id` fallback (`user` → 1, `assistant` → 3, with `system`/`tool` flanking). Existing saved sessions read back in correct order without any data migration.
- **Frontend**: `sendMessage` bumps the assistant message's `timestamp + id` suffix by 1 ms past the user's so new pairs are monotonic and never rely on the tiebreak.

Streaming and continuation are unchanged.

## Added

### Distraction-free reading mode

Press `F` — or click the `Maximize2` button in the header — and the Header, sidebar, mobile bottom nav, and PDF toolbar all disappear, leaving just the document content with a small floating **Exit** pill in the top-right. Mode is persisted across reloads, listed in the keyboard-shortcuts modal, and works in chat too.

### Mobile overflow menu (`⋯`)

Several header actions were hidden behind `sm:*` classes and simply vanished on phones — dark-mode toggle, Kokoro/System switch, page-audio download, audiobook export, keyboard shortcuts, home, distraction-free. A new `HeaderOverflowMenu` (`sm:hidden`) surfaces all of them inside a small dropdown so mobile users can still reach everything. Desktop layout is byte-identical.

### Parallel audiobook synthesis + multi-worker uvicorn

`downloadBookAudio` now runs up to **3 page-synth requests in flight at once** via a worker-pool pattern. Output blobs are slotted by page index so concatenation preserves document order even when synthesis completes out-of-order. Cancel still works mid-pool.

With the default single-worker uvicorn server the parallelism is mostly a small startup-overlap win — three requests still queue at the single worker. To actually fan synthesis across CPU cores, `run.py` now honours `WORKERS=N`:

```bash
WORKERS=4 python run.py        # four kokoro models in RAM, four pages in flight
```

Trade-offs (RAM per worker, Postgres pool sizing, GPU caveat) are documented in [docs/CHAT_WITH_PDF.md §10](../docs/CHAT_WITH_PDF.md#10-performance--audiobook-export--multi-worker).

## Upgrade notes

- **No new dependencies, no new env vars** required.
- **No schema migration** — the chat-order fix is at the SELECT layer.
- **Restart the FastAPI backend** to pick up the SQL change. Frontend changes ship with a normal `npm run build`.
- The new `WORKERS`, `HOST`, and `PORT` env vars on `run.py` are optional and default to single-worker / `0.0.0.0:8000` (unchanged from prior behaviour).

## Full changelog

[CHANGELOG.md#171---2026-05-23](../CHANGELOG.md#171---2026-05-23) · diff: [v1.7.0…v1.7.1](https://github.com/udaravima/natural-reader/compare/v1.7.0...v1.7.1)
