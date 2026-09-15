# Session handover

> Private, git-ignored working notes (`.gitignore` → `HANDOVER.md`). Latest session on top.

---

## 2026-09-14 — Understood project; captured document-library RAG suggestion

**Branch:** `development` · **Version:** v1.9.0 (`ed83a3f`) · **Working tree:** was clean at start; this session adds two untracked/ignored docs (see below).

### What happened
- Read the project end-to-end to understand the RAG/chat architecture (README, [CHAT_WITH_PDF.md](docs/CHAT_WITH_PDF.md), the `superpowers/specs` + `plans`, and the actual code paths).
- Captured a new feature idea — **ask chat across a library of many indexed docs (multi-project, with a document-routing step and multi-round retrieval)** — as a rich, analyzed suggestion:
  **[docs/superpowers/specs/2026-09-14-document-library-rag-suggestion.md](docs/superpowers/specs/2026-09-14-document-library-rag-suggestion.md)**
- No code changed. No spec written, no plan, nothing approved to build.

### Project state (as understood this session)
- **What it is:** local-first reader — Kokoro ONNX TTS + Ollama chat side panel, fused into "Chat with PDF" (v1.6.0+). FastAPI + Postgres/pgvector backend, React/Vite frontend. Fully local (Ollama does chat *and* embeddings).
- **Retrieval today is single-doc:** index the open doc → `nomic-embed-text` 768-dim chunks in `doc_chunks` → model gets a `search_document` tool → **one** autonomous round-trip.
- **Tool loop is capped at 1 round** on purpose ([useChatEngine.js:694-763](src/hooks/useChatEngine.js#L694-L763)); follow-up call drops `tools`.
- Recent shipped work (git log): file logging + license/contributing + v1.9.0 release; SearXNG-backed `web_search` tool (first backend `/api/generate` path, in `services/web_search.py`); hardcoded doclin support.

### Key file map (for the suggested feature)
| Concern | File |
|---|---|
| Tool registry (add tools here) | [src/lib/chatTools/index.js](src/lib/chatTools/index.js) |
| Existing single-doc search tool + `when` gate | [src/lib/chatTools/searchDocument.js](src/lib/chatTools/searchDocument.js) |
| Chat engine + tool loop + round cap | [src/hooks/useChatEngine.js](src/hooks/useChatEngine.js) |
| Docs + search endpoints (`WHERE doc_id`) | [server/routers/docs.py](server/routers/docs.py) |
| Schema (documents, doc_chunks, HNSW) | [server/sql/001_init.sql](server/sql/001_init.sql) |
| Backend generation pattern to reuse | [server/services/web_search.py](server/services/web_search.py) |

### Next step / where to resume
- Decision made this session: capture as suggestion (done), backlog lives under `docs/superpowers/specs/`.
- When building: start **Phase 1 (corpus search, no routing)** from the suggestion doc, via brainstorming → spec → plan. Phases 2 (descriptions + routing tool) and 3 (budgeted multi-round loop) follow.
- Open decisions still to make (all listed in the suggestion): project = column vs. table; routing = semantic vs. list-and-pick; round budget; descriptions LLM-generated vs. editable.
