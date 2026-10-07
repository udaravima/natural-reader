# Task 9 report — deferred minors and stale docs (cloud session, controller-implemented)

BASE 4b1654d.

## Backend (each has a test)
1. **`ollama.capabilities()` logs `/api/show` failures at DEBUG.** This was already done by df6f253 (C1 final-review fixes). Connect errors, non-200 responses and non-JSON bodies all log at DEBUG, and no failure is cached.
   - New test `test_an_api_show_failure_is_logged_at_debug_only` (`server/tests/test_llm_ollama.py`) pins all three: DEBUG only, `Capabilities()` returned.
   - It passes on the existing code, which is expected, since nothing was left to implement.
2. **`resolve("local:")` is refused** (`server/llm/router.py`). When the prefix names a provider and the rest is empty, it raises `UnknownModel`, so `is_allowed` returns False.
   - Before, it fell through to the first Ollama provider with the model name "local:".
   - A bare name that merely contains a colon ("qwen2.5:7b") is unchanged.
   - Test: `test_a_provider_prefix_with_no_model_is_refused_not_a_bare_name`. It was red before the fix.
3. **`recover_stale` logs the claims it cleared** (`server/chat/store.py`).
   - The CTE now returns two counts: claims cleared, and streaming replies marked aborted.
   - The WARNING reads "Cleared N stale chat claim(s); M streaming repl(ies) marked aborted", and the function returns the claims count.
   - Before, it counted only aborted messages, so a stale claim whose reply had already finished was cleared silently and returned 0.
   - The existing test (one stale, one fresh) still returns 1.
   - New test: a stale claim over a completed reply is cleared, returns 1, and is logged. It was red before the fix.
   - Callers: startup (`app.py`) and `chat_sessions.py:193` ignore the return value.
4. **The shutdown drain waits for tasks added during the wait.**
   - `orchestrator.drain_background(timeout)` loops `asyncio.wait(list(_BACKGROUND), timeout=left)` until `_BACKGROUND` is empty or the deadline passes, and returns the number still pending.
   - `app.py`'s shutdown calls it with the same 5 s and the same WARNING. The unused `import asyncio` in `app.py` was removed.
   - Tests: new `server/tests/test_chat_shutdown_drain.py` (3):
     - a write started during the wait is waited for;
     - the deadline stops the wait and reports 1 pending;
     - with nothing pending it returns at once.

     All three were red before the fix (no function).

## Docs
- `docs/CHAT_WITH_PDF.md`:
  - §5 rewritten for the server pipeline: multipart upload → Extracting → Indexing → Indexed, dedup, server-side extraction on the reader's pagination, no client chunks, and a pointer to LIBRARY.md.
  - The re-index note is corrected: it rebuilds from the stored file in one transaction, and needs the sole holder or an admin.
  - The endpoint table is updated. `/chunks` is gone; the list, `/file` and resume/re-index rows are added, with a pointer to LIBRARY.md § API surface.
  - The "tabled" list no longer carries Docling (shipped) or the orphaned-chunks cleanup (content GC exists). The tool-rounds note now names `CHAT_MAX_TOOL_ROUNDS`.
- `README.md`:
  - the "Index this document" feature line;
  - the endpoint table: `/v1/docs` POST/GET, `{id}` GET/PATCH/DELETE, `/file`, `/index`, `/search`, with the `/chunks` row removed and a pointer to LIBRARY.md;
  - the nginx comment (uploads go to `POST /v1/docs`);
  - the project tree: `uploadPdf.js` (deleted earlier) is replaced by the three new lib files, and the `docs.py` line is updated.
- `docs/ARCHITECTURE.md`: one sentence on `GET /v1/docs/{id}/file`.
- `CHANGELOG`: one Fixed line.

## Suites
- backend: 597 passed (591 + 6);
- frontend: 388 passed;
- eslint: clean.

The 2 pytest warnings are authlib's own `AuthlibDeprecationWarning` (httpx → httpx2), from site-packages, and predate this work.
