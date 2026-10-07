### Task 9: Deferred minors and stale docs

**Backend** (from the C1 ledger; each gets a small test):
- `ollama.capabilities()` logs `/api/show` failures at DEBUG.
- `resolve("local:")` (empty model after the colon) is refused as not allowed instead of falling back to a bare name.
- `recover_stale` logs the number of claims it cleared.
- The shutdown drain waits for tasks added during the wait: loop until `_BACKGROUND` is empty or the deadline passes.

**Docs:**
- `docs/CHAT_WITH_PDF.md` and the README endpoint table still describe client-side chunking and `/v1/docs/{id}/chunks` from before A1. Rewrite them to match the server pipeline (`POST /v1/docs` multipart, `/index`, `/search`), or point them at `LIBRARY.md`.

