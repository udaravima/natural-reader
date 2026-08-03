# Chat with PDF — Walkthrough

> **Audience:** anyone running Natural Reader who wants to ask their local LLM about a document they're reading.
> **Status:** ships in `v1.6.0` (PRs 1–5 in the plan archive).
> **Time to first working chat-with-doc:** ~10 minutes on a machine that already has Docker + Ollama.

This document walks the whole feature end-to-end: what it does, why we built it the way we did, how to bring it up locally, and the four different ways you can pull document context into a chat — from a one-click page question to fully autonomous tool calling.

---

## 1. What & why

The reader and the chat used to be two completely separate features. You could open a PDF and have it read aloud, or you could chat with Ollama in the side panel — but the chat had no idea what you were reading. This release links the two.

You can now:

1. **Pin the page you're on** — one toolbar click attaches the current page text; it stays in context across follow-ups.
2. **Highlight a passage and pin *just* that snippet** — selection-aware, and you can stack several pins.
3. **Index the whole document** — pgvector embeddings via Ollama's `nomic-embed-text`, then let the chat semantically retrieve passages from anywhere in the doc.
4. **Let the model decide on its own** — once a doc is indexed, the model gets a `search_document` tool it can invoke autonomously whenever a question warrants it.

Everything is still local: Ollama for the LLM and embeddings, FastAPI + Postgres for the backend, no cloud round-trips.

---

## 2. Architecture

```
┌─────────────────────┐
│       Browser        │
│  (React + Vite)      │
│                      │
│  ┌────────────────┐  │     fetch  ┌──────────────────────────┐
│  │  Reader        │──┼──/v1/*────▶│  FastAPI (port 8000)     │
│  │  Chat          │  │            │  ├── Kokoro TTS          │
│  │  IndexButton   │  │            │  ├── chat_sessions CRUD  │
│  │  Tool registry │  │            │  └── docs + search       │
│  └────────────────┘  │            └────────┬─────────────────┘
│                      │                     │ psycopg
│  IndexedDB           │                     ▼
│  • PDFs              │            ┌──────────────────────────┐
│  • Legacy chats      │            │  Postgres + pgvector     │
│                      │            │  • documents             │
│                      │            │  • doc_chunks (vector)   │
│                      │            │  • chat_sessions         │
│                      │            │  • chat_messages         │
│                      │            └──────────────────────────┘
│                      │     fetch  ┌──────────────────────────┐
│                      │──/api/*───▶│  Ollama (port 11434)     │
│                      │            │  • /api/chat (LLM)       │
│                      │            │  • /api/embeddings (RAG) │
│                      │            └──────────────────────────┘
└──────────────────────┘
```

A few load-bearing decisions worth knowing:

- **Document identity = `sha256` of the file bytes**, computed lazily in the browser the first time you do anything chat-related with a doc. Filenames are metadata only — renaming a file hits the same document; editing it gets a fresh one.
- **PDFs never leave IndexedDB.** Only extracted text chunks (and their embeddings) live in Postgres. No file uploads.
- **Chunks ship from the frontend.** PDF pages, Markdown blocks, and TXT pseudo-pages are extracted client-side and sent as JSON. The backend doesn't need a PDF parser.
- **Backend down ≠ chat broken.** Ollama is independent. If Postgres is unreachable, the chat still streams; only session save fails (with a toast).

---

## 3. Prerequisites

| Component | Minimum | Notes |
|---|---|---|
| Node.js | 18+ | Frontend dev server. |
| Python | 3.10+ | FastAPI uses `\|` union syntax. |
| Docker (or Postgres + pgvector locally) | any recent | The compose file pins `pgvector/pgvector:pg16`. |
| Ollama | latest | Both the chat model and `nomic-embed-text` must be pulled locally. |
| RAM | ~6 GB free | Postgres + Ollama + a small chat model fit comfortably. Larger chat models scale linearly. |

---

## 4. Bringing up the stack

### 4.1. Postgres + pgvector

```bash
# From the repo root
docker-compose up -d postgres
```

This brings up `pgvector/pgvector:pg16` on host port **5433** (chosen to avoid colliding with a system Postgres on 5432). Data lives in a named `pgdata` volume so it survives container restarts.

Verify:

```bash
psql postgresql://natural_reader:natural_reader@localhost:5433/natural_reader -c "SELECT 1;"
```

If you don't have Docker, you can use a host Postgres — set the `DATABASE_URL` env var when starting `run.py`:

```bash
export DATABASE_URL=postgresql://USER:PASS@HOST:PORT/DBNAME
```

### 4.2. Python dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

New deps from `v1.6.0`: `psycopg[binary,pool]`, `pgvector`, `httpx`. Existing TTS deps (Kokoro, soundfile, FastAPI, uvicorn) are unchanged.

### 4.3. Ollama models

You need **two** models pulled:

```bash
# Any chat-capable model. For autonomous tool calling (Section 6.4)
# pick one with tools support — qwen2.5, llama3.1, llama3.2, mistral, gemma2.
ollama pull qwen2.5

# Embeddings — hard-coded as `nomic-embed-text` (768 dim) in v1.6.0.
ollama pull nomic-embed-text
```

> **Why is the embedding model hard-coded?** pgvector requires a fixed dimension on the column. Swapping models = dropping the `embedding` column and re-indexing every doc. To override anyway, set `EMBEDDING_MODEL` + `EMBEDDING_DIM` env vars before starting the FastAPI server.

### 4.4. Start everything

```bash
# Terminal 1 — FastAPI (TTS + chat sessions + docs + search)
python run.py

# Terminal 2 — Vite dev server
npm run dev
```

Open **http://localhost:5173**.

You should see:
- The reader's normal welcome screen.
- A green "✓ Connected" indicator in the chat sidebar (Kokoro reachable).
- Old chat sessions (if any) show a small amber **LOCAL** badge — they live in IndexedDB and are read-only now. New chats write to Postgres.

---

## 5. Indexing a document

1. Drop a PDF (or `.txt` / `.md`) onto the reader.
2. The toolbar now shows an **Index** button between the zoom controls and the existing "Ask page" button.
3. Click **Index**.
4. The button cycles through three states:
   - **Uploading** — chunks are POSTed to the backend in batches of 50.
   - **Indexing N/M** — the embedding job runs in the background; the count polls every 2 s.
   - **Indexed** — green checkmark; the doc is now searchable.

Behind the scenes:

| File type | Chunking strategy |
|---|---|
| **PDF** | One chunk per page (PDF.js `getTextContent()` joined). |
| **Markdown** | One chunk per top-level block (paragraph / heading / list / table / blockquote). Code blocks are skipped. |
| **Text** | One chunk per pseudo-page (~40 sentences, matching the reader's pagination). |

You can poke around the stored state with psql:

```bash
psql postgresql://natural_reader:natural_reader@localhost:5433/natural_reader \
  -c "SELECT doc_id, file_name, state, page_count FROM documents;"
psql postgresql://natural_reader:natural_reader@localhost:5433/natural_reader \
  -c "SELECT count(*) AS total, count(embedding) AS embedded FROM doc_chunks;"
```

> **Re-indexing is idempotent.** Chunks are upserted on `(doc_id, text_hash)` so clicking Index again on the same doc doesn't duplicate rows — and existing embeddings are kept.

---

## 6. Asking questions

There are a few ways to give the model document context. The first two create
**pins** — persistent excerpts that stay attached to the conversation (Section 6.3);
the last is fully autonomous retrieval.

### 6.1. Ask page (current page → a pin)

The fastest path. Cap is `~8000 chars` per page (truncated tail marker added if over).

1. While reading a page, click **Ask page** in the toolbar.
2. The app jumps to chat mode and **pins** the page: a purple **pin chip** appears above the input reading `Page N · filename.pdf` + a preview. The pin stays attached to the conversation.
3. Type your question (or just hit Send to let the model decide what to say about the page).
4. The model gets a system preamble with the page text — re-sent on **every** turn (positioned right before your latest question), so follow-ups keep the context without re-attaching.

**No indexing required.** Works the moment a doc loads. Remove a pin anytime via the ✕ on its chip.

### 6.2. Ask AI on a text selection (→ a pin)

Same idea, scoped to whatever you highlighted — and you can stack several.

1. Select text on the rendered page.
2. Two floating buttons appear bottom-right: **Read Selection** (existing TTS feature) and the purple **Ask AI**.
3. Click **Ask AI** → jumps to chat and pins a `Selection · filename.pdf` chip.
4. Cap is `~8000 chars` per pin; cross-page selections are concatenated as one snippet (no per-page tagging). **Multiple pins accumulate** — see 6.3.

### 6.3. How pins behave (persistent context)

A pin is **not** a one-shot. Once created (Ask page or Ask AI) it stays attached to
the conversation and is **re-sent to the model on every turn**, injected as a system
note **immediately before your latest question** — so it never gets buried as the
chat grows, and follow-ups "just work" without re-attaching.

This is close to how a normal ChatGPT/Gemini session keeps context in view, with one
deliberate improvement: pasting text into a single message freezes it at that spot in
the transcript, where it recedes turn after turn; a pin instead **floats to just
before the current question every turn**, staying maximally relevant. (The
conversation's user/assistant turns are still re-sent in full each turn, exactly like
a normal chat — Ollama's `/api/chat` is stateless.)

- **Multiple pins** accumulate as separate chips; remove any via its ✕.
- **Dedupe** by `(doc_id, kind, text)` — re-pinning the same passage is a no-op.
- **Bounded**: max **6 pins** and **~12 000 chars** total (each excerpt still caps at
  ~8000); over that, adding is refused with a toast rather than silently ballooning.
- **Saved with the chat session** (Section 7) — reopen the chat and the pins are back.
- **Whole-document breadth is not a pin.** Pinning an entire document would blow the
  budget; instead, index the doc and let **autonomous retrieval** (Section 6.4) pull
  relevant passages on demand. Pins and retrieval compose: pins are the exact excerpts
  you always want in view, retrieval fills in everything else.

> **Note:** the older per-chip "Use whole document" checkbox (manual `k=3` retrieval
> folded into the preamble) was **retired** with this feature — its job is now done by
> the autonomous `search_document` tool below.

### 6.4. Autonomous tool calling

The new flagship feature in `v1.6.0`. **No chip, no toggle, no manual setup.** Once a doc is indexed and you have a tools-capable chat model selected, the model gets a `search_document` tool in every chat request and decides on its own whether to invoke it.

What it looks like:

1. Open an indexed doc.
2. In chat (with no chip attached), ask: *"What does this document say about X?"*
3. A small cyan pill appears under the assistant's avatar: **🔄 Searching document…**.
4. The pill disappears and the actual answer streams in, citing the retrieved passages.
5. A small `🔎 search_document` disclosure appears on the assistant bubble — click to see the exact query the model used and how many chunks came back.

**How the loop works** (no backend changes from PR 4):

```
1. Browser → POST /api/chat with tools=[search_document]
2. Ollama → streams `{message: {tool_calls: [...]}}`  (no content)
3. Browser → POST /v1/docs/{doc_id}/search (executes the tool)
4. Browser → POST /api/chat again, this time WITHOUT tools
              (history now includes the tool_call + tool result)
5. Ollama → streams the final answer
```

Capped at a single round-trip — no recursive tool calls in v1.

**Compatibility notes:**

- **Models without tool support** silently ignore the `tools` field and respond normally. No breakage.
- **Thinking + tools** (`think: true`) sometimes 400s on some models. We fall back to a tools-less retry (keeping thinking on) and surface a one-time toast.
- **No doc loaded** or **doc not indexed** → tools aren't sent at all. Identical behavior to pre-`v1.6.0`.

---

## 7. Sessions & persistence

| Source | Where it lives | Badge | Editable? |
|---|---|---|---|
| New chats (post `v1.6.0`) | Postgres `chat_sessions` + `chat_messages` | none | yes |
| Pre-`v1.6.0` chats | IndexedDB `chat_sessions` store | amber **LOCAL** pill | read-only |

Old IDB sessions stay readable forever; if you edit one and send a message, the chat engine forks it to a fresh Postgres session — the IDB original is left untouched.

The sidebar merges both lists, newest-first, deduped by id.

**What gets persisted per message:**

| Field | Notes |
|---|---|
| `content` / `thinking` | The model's reply text + reasoning trace. |
| `attachments` | Image metadata (binary `dataUrl`/`base64` stripped on save). |
| `docContext` | Legacy per-message context (pre-pins). No longer written for new messages; retained so old sessions still re-render their chip. |
| `stats` | Ollama's per-turn token + latency numbers (the `⚡` disclosure). |
| `toolCalls` | Compact summary of any autonomous tool calls — `{name, arguments, result_summary}`. Used to re-render the 🔎 disclosure on reload. |

**Pins are persisted at the *session* level** (not per message): a `pins` JSONB
column on `chat_sessions` (migration `004_chat_pins.sql`). They're written **instantly**
on add/remove via `PATCH /v1/chat/sessions/{id}` and again in the full `PUT` upsert, and
restored into the pin-chip row when you reopen the session.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| **IndexButton flashes "Failed"** after upload phase | Ollama can't reach `nomic-embed-text`. | `ollama pull nomic-embed-text`, then click Retry. |
| **`/v1/docs/...` returns 503** | Postgres not reachable from FastAPI. | Check `docker-compose ps` and `DATABASE_URL` env var. |
| **"Backend offline — chat sessions won't be saved" toast** | FastAPI is down (or `DATABASE_URL` is wrong). | Restart `python run.py`. Chat itself still works against Ollama. |
| **Embedding dim mismatch** error in the FastAPI log | You set `EMBEDDING_MODEL` to a model whose dimension ≠ 768. | Either set `EMBEDDING_DIM` to match the new model AND drop+recreate the column, or revert to `nomic-embed-text`. |
| **Toast: "This model rejected tools"** | The selected chat model 400s on `tools+think` together. | Either disable thinking, or switch to a model with stronger tool support (qwen2.5, llama3.1+). |
| **Autonomous search never fires** | The doc isn't indexed yet (state ≠ `indexed`), OR the model doesn't support tools. | Click Index; or switch to a tools-capable model. |
| **Old IDB session won't accept new messages** | Read-only by design. | Just type — the next save forks the session to Postgres. The IDB original is preserved. |

---

## 9. API reference

All under `http://localhost:8000` by default. Same FastAPI app as the existing Kokoro TTS endpoints.

### Chat sessions

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/chat/sessions` | List session metadata (newest first). |
| `GET` | `/v1/chat/sessions/{id}` | Full record: messages + events. |
| `PUT` | `/v1/chat/sessions/{id}` | Transactional upsert of the whole session — messages, events, and `pins`. |
| `PATCH` | `/v1/chat/sessions/{id}` | Partial update — any of `title`, `model`, `pins` (the last powers instant pin save). |
| `DELETE` | `/v1/chat/sessions/{id}` | Cascade-deletes messages + events. |

### Documents + retrieval

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/docs` | Register a document (idempotent on `doc_id` sha256). |
| `GET` | `/v1/docs/{doc_id}` | Status: `state`, `chunk_count`, `embedded_count`, model + dim. |
| `POST` | `/v1/docs/{doc_id}/chunks` | Bulk insert/upsert chunks. |
| `POST` | `/v1/docs/{doc_id}/index` | Kick off the embedding job (202 + state set to `indexing`). |
| `POST` | `/v1/docs/{doc_id}/search` | `{query, k}` → top-k chunks by cosine similarity. |
| `DELETE` | `/v1/docs/{doc_id}` | Cascade-deletes chunks. |

---

## 10. Performance — audiobook export & multi-worker

The audiobook export (header **Library** icon, v1.7.0+) synthesises one page per `/v1/batch_synthesize` call and stitches the WAVs client-side. The frontend loop pipelines up to **3 page-synths in parallel** by default. With the default single-worker server those three requests still queue at the worker, so the parallelism is mostly a small startup-overlap win.

To actually fan synthesis across CPU cores, run the server with multiple uvicorn workers — each one loads its own Kokoro ONNX session, so an audiobook job with N workers truly runs N pages at a time:

```bash
WORKERS=4 python run.py        # four kokoro models in RAM, four pages in flight
```

Trade-offs:

- **RAM**: each worker holds a full Kokoro model. Budget ~300–500 MB per worker on the ONNX-CPU build; more if you switch to the GPU build.
- **First-call latency** is unchanged (the worker still has to do the first inference); it's the *batch* time that drops roughly linearly with worker count up to your CPU-core ceiling.
- **GPU**: if you've installed `onnxruntime-gpu` and have an NVIDIA card, one worker on the GPU is faster than four on CPU. Multi-worker on a single GPU isn't useful — they fight over VRAM.
- **Postgres pool** is per-process, so N workers means N × `max_size=10` connections to Postgres. Default Postgres `max_connections` is 100 — fine up to ~9 workers, retune above that.

Same trick helps any synth-heavy workflow (the reader's per-page TTS, "Read selection", chat read-aloud), not just audiobook export.

A standard `docker-compose.yml` for the FastAPI backend doesn't ship yet — until it does, run the server directly or wrap `WORKERS=4 python run.py` in your favorite process manager (systemd / supervisor / pm2-with-python-interpreter).

## 11. What's next

The PR 5 tool registry (`src/lib/chatTools/`) is built to host more tools. Concretely:

- **`web_search`** — Brave/Tavily/SearXNG behind a small backend proxy. One frontend file under `src/lib/chatTools/`, one backend endpoint under `server/routers/tools/`.
- **`read_url`** — fetch + readability so the model can ingest a URL the user mentions.
- **`code_interpreter`** — sandboxed Python execution. The biggest jump in scope (process isolation).

The `src/lib/chatTools/_example.js` stub documents the shape; adding a new tool is a four-step recipe (commented at the top of that file).

Also tabled for future work:

- **Docling** for layout-aware PDF chunking (reading order, table reconstruction, heading hierarchy). Would require shipping PDFs to the backend on Index click — a meaningful UX shift, so deliberately held off.
- **Multi-iteration tool calls.** PR 5 caps at one round-trip; multi-step agents need budget/loop controls before they're safe.
- **No-chip retrieval toggle** for users who want manual whole-doc context without going through Ask page / Ask AI first.
- **Cleanup job** for orphaned `doc_chunks` rows when a file's bytes change (new `doc_id` leaves old chunks behind).
