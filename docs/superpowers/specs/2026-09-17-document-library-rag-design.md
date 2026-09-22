# Document Library & Multi-Doc RAG — projects, sharing, tags, cross-doc chat

**Date:** 2026-09-17
**Status:** Draft — decisions below reflect the 2026-09-17 planning session; supersedes the
[suggestion note](2026-09-14-document-library-rag-suggestion.md) (which remains the pre-read).
**Area:** docs router (`server/routers/docs.py`) · schema (`server/sql/008_*.sql`) · chat tools
(`src/lib/chatTools/`) · chat loop (`src/hooks/useChatEngine.js`) · new Library UI

> Sequencing decision (2026-09-17): **builds after the model-router gateway (E)** — the multi-round
> chat loop this feature needs will call `/v1/inference/chat`, not browser→Ollama directly.

## 1. What changed since the suggestion note

The note's premises were verified against the tree, and two moved:

1. **Ownership exists now.** `005_users_ownership.sql` added `documents.user_id`, uploads stamp
   the authenticated principal ([docs.py:206-221](../../../server/routers/docs.py)), and every
   route is owner-gated. Pre-005 docs were backfilled to the bootstrap admin
   (`00000000-…-0001`) — that's why the library-to-be looks like "all the admin's docs."
   What does **not** exist: sharing of any kind (`authz.py` is strict row-ownership —
   missing and not-owned are both 404), projects, tags, and **even a document list endpoint**
   (`GET /v1/docs` doesn't exist; the SPA never enumerates documents — everything flows from
   upload through in-memory state). The Library is genuinely new surface, not a refactor.
2. **The auth hardening landed** (session/PAT principal, admin role), which the sharing model
   below builds on.

Decisions locked in the planning session: sharing = **both** project membership and per-doc
grants; tags + doc-level search are in scope; users must be able to **select one or many docs
and ask questions**, same interaction shape as today's ask-from-doc.

## 2. Goals / non-goals

**Goals**
- A Library page: list, search (name/tag/project), filter, multi-select → "Ask about these."
- Projects as the grouping *and* primary sharing unit; per-doc grants as the exception path.
- Cross-doc semantic search in one chat, answers citing **which document**.
- Routing stage ("which docs are relevant?") once libraries grow past a shelf.
- Legacy admin-owned docs become reassignable.

**Non-goals (v1)**
- Write-access sharing (grants and memberships are read-only; owner-only writes/deletes).
- Shared tags vocabulary / tag management UI beyond basic add/remove.
- Full-text search over chunk *text* (semantic search already covers it).

## 3. Schema — `008_library.sql`

```sql
projects        (id UUID PK, owner_user_id → users, name, description, created_at)
project_members (project_id, user_id, PK(project_id, user_id))   -- read-only membership
documents       ADD project_id UUID NULL REFERENCES projects ON DELETE SET NULL
doc_grants      (doc_id, grantee_user_id, PK(doc_id, grantee_user_id))  -- per-doc read grant
documents       ADD tags TEXT[] NOT NULL DEFAULT '{}'              -- GIN-indexed
documents       ADD description TEXT                              -- Phase 2 fills this
documents       ADD description_embedding vector(768)              -- Phase 2
```

- **Tags as `TEXT[]` on documents**, not a join-table taxonomy: tags belong to the doc's owner,
  shared viewers see them read-only. A tags *table* buys vocabulary control we don't need yet —
  revisit if tag management grows. (Decision recorded, not cast in stone.)
- **Description embedding** is a separate column from `doc_chunks.embedding` — same 768-dim
  family, but it lives in the documents row and is searched with its own HNSW index, keeping the
  chunk index global and untouched.
- Access-resolution stays one SQL expression, not ORM logic (§5).

## 4. Access model

`can_read(doc)` = **owner** ∨ **project member of doc's project** ∨ **doc_grant for user**.

- New dependency `_require_doc_reader` (read/search/chunks/markdown routes) checks the
  expression; `_require_doc_owner` keeps guarding write/delete — the existing
  [authz.py](../../../server/auth/authz.py) helpers grow `assert_can_read_doc`, keeping
  the missing-vs-forbidden indistinguishable (404) posture.
- Missing membership in an *unshared* project must not leak existence — same 404 rule.
- Search endpoints filter the candidate set through `can_read` in SQL (join), never
  post-filter in Python.

## 5. Library API (docs router + new projects router)

- `GET /v1/docs?q=&project_id=&tag=` — **the list endpoint that doesn't exist yet**; returns
  docs visible to the caller (own + project + granted), with state, tags, project, description.
- `POST/PATCH/DELETE /v1/projects`, `PUT/DELETE /v1/projects/{id}/members/{user_id}` (owner-only).
- `PUT/DELETE /v1/docs/{doc_id}/grants/{user_id}` (owner-only). Admin panel lists grants later.
- `PATCH /v1/docs/{doc_id}` — tags, project, (re)assign owner *(admin, or owner-of-nothing —
  the legacy-backfill escape hatch)*.

## 6. Library UI

- New **LibraryPage** (nav alongside Reader/Chat): document table with name, state, project,
  tags, owner; search-as-you-type over name/tag; project filter chips; multi-select rows →
  **"Ask about these"** button → opens Chat with a `docScope` (the selected doc ids) attached.
- Upload flow gains an optional project + tags step (App.jsx's existing upload pipeline just
  carries two extra fields).
- Admin gets a small "reassign documents" action for the seed-admin backfill (Phase 0 hygiene).

## 7. Phase 1 — corpus-scoped search

- Backend: `POST /v1/docs/search` — `{ query, doc_ids: [...] | project_id }` → cosine search
  over the global HNSW index narrowed by `doc_id = ANY(%s)` **and** `can_read` in SQL. Results
  carry `{doc_id, file_name, page, score, text}` so answers cite the document, not just the page.
- Frontend tool: `search_document`'s gate flips from
  `ctx.currentDocId && state === 'indexed'` ([searchDocument.js:48](../../../src/lib/chatTools/searchDocument.js))
  to **`ctx.docScope` non-empty** — where `docScope` = the reader's current doc (today,
  unchanged behavior) *or* the Library multi-selection. `toolCtx`
  ([useChatEngine.js:544](../../../src/hooks/useChatEngine.js)) grows `docScope`; `currentDocId`
  remains for pinned-context preamble, which is unchanged.

## 8. Phase 2 — descriptions + routing tool

- At index completion, one summarize call over the doc's first-N chunks/markdown fills
  `description`, and its embedding fills `description_embedding`. Generation goes through the
  **model-router gateway's task table** (`summarize` route) — the RAG feature is the first
  customer of sub-project E's centralized inference path (no new env reads, one audit point).
- New tool `find_documents(query, project?)` → top-k by description-embedding cosine over docs
  the caller `can_read`. Fallback (tiny libraries): return all titles+descriptions and let the
  model pick — the suggestion note's list-and-pick, kept as a code path, not a promise.
- **Trap, inherited and still true:** description quality is the entire ballgame. A vague summary
  routes confidently wrong. Budget review attention here; make description user-editable
  (`PATCH /v1/docs/{doc_id}`) so a human can fix a bad one.

## 9. Phase 3 — budgeted multi-round loop

- The one-round cap ([useChatEngine.js:694-763](../../../src/hooks/useChatEngine.js) — follow-up
  sent without `tools` so a confused model can't spin) lifts into a **max-N-rounds loop**
  (default 3, setting-capped): keep `tools` on while the model still calls them and budget
  remains; a visible "still searching…" state per round; hard stop regardless.
- Each round is a gateway round-trip and spends budget tokens (E's accounting) — the loop
  checks remaining budget between rounds and stops with a clear message, not a silent 429.

## 10. Phasing summary

| Phase | Ships | Independently useful? |
|---|---|---|
| 0 | Library schema, list API, projects/grants/tags, LibraryPage, backfill reassignment | Yes — it's the library feature by itself |
| 1 | `docScope` + cross-doc search + gate flip | Yes — "ask across my selected docs" |
| 2 | Descriptions + `find_documents` routing | Yes — answers span shelves, not selections |
| 3 | Budgeted multi-round loop | Only meaningful after 2 |

Each phase is independently shippable — the decomposition survives contact with the new
requirements.

## 11. Testing strategy

- Access matrix (owner / member / granted / stranger × read/write) as table-driven pytest —
  the 404-indistinguishability rule must hold in every cell.
- SQL-filtered search: stranger's docs must never appear in cross-doc results even when
  semantically closest.
- Tool gate: no scope → no `search_document` in `tools`; scope → tool present and querying the
  right doc set.
- Phase 3: loop respects max rounds and budget; "still searching…" state machine.

## 12. Risks / open questions

- **The three traps stand** as written in the suggestion note: gate inversion, description
  quality, multi-round cost. Nothing in the new requirements defuses them; §7-§9 address each.
- **Shared-doc chunk search + session context:** asking about docs you can read but whose chunks
  were embedded under another owner's model — fine (embedding model is per-doc column), verify.
- **Open:** `docScope` persistence in chat sessions — should a saved session remember its doc
  set (schema `chat_sessions.doc_scope JSONB`)? Leaning yes in Phase 1; decided at plan time.
- **Open:** project descriptions used for routing too (Phase 2 extension) — defer.
