# Document-library RAG — agentic retrieval across many indexed docs (suggestion)

**Date:** 2026-09-14
**Status:** Suggestion — captured & analyzed, **not** designed or approved. No spec, no plan, no code yet.
**Area:** Chat tools (`src/lib/chatTools/`) · chat loop (`src/hooks/useChatEngine.js`) · docs router (`server/routers/docs.py`) · schema (`server/sql/`) · embeddings/generation (`server/services/`)

> Origin: session 2026-09-14. Idea, verbatim intent: *"We have documents indexed.
> Add a feature to ask chat for instructions — index several READMEs and user guides
> of several projects; on a specific question the model first queries **which**
> documents are relevant (descriptions built at index time, docs grouped by project),
> then uses document search to pull the relevant content, possibly over **multiple
> rounds**, then answers."*

## Problem / opportunity

Today's "Chat with PDF" ([CHAT_WITH_PDF.md](../../CHAT_WITH_PDF.md)) is **single-document**.
The model can only search the one doc you currently have open in the reader — the
`search_document` tool is gated on `currentDocId` and drills into exactly that doc's
chunks. There is no notion of a *library* you can question as a whole ("how do I
configure auth in project X?" across a shelf of READMEs and user guides).

The opportunity is that the expensive machinery for a library assistant is **already
built**. What's missing is *scope* (search across docs, grouped by project) and one
genuinely new step (a routing stage that first decides *which* docs to look in). This
note captures the idea, maps it onto the current code, and lists the decisions a
future design must make — so we can pick it up cold later.

## What already exists (do not rebuild)

| Capability | Where | Note for this feature |
|---|---|---|
| Chunk embeddings + cosine search | [`server/sql/001_init.sql`](../../../server/sql/001_init.sql), [`docs.py:413`](../../../server/routers/docs.py) | The HNSW index `doc_chunks_embedding_idx` is **global over all chunks of all docs** — search only *narrows* it with `WHERE doc_id = %s`. Cross-doc search is a WHERE-clause change, not new infra. |
| Autonomous tool calling | [`searchDocument.js`](../../../src/lib/chatTools/searchDocument.js), [`chatTools/index.js`](../../../src/lib/chatTools/index.js) | Registry is built to host more tools — a new routing tool is a drop-in file + one `REGISTRY` line. |
| The frontend tool loop + UI disclosures | [`useChatEngine.js:691`](../../../src/hooks/useChatEngine.js) | Streams tool calls, executes them, re-POSTs, renders the `🔎` disclosure. Reusable as-is except the round cap (below). |
| Backend generative Ollama calls | [`services/web_search.py`](../../../server/services/web_search.py) | The web-search feature already added the first `/api/generate` path + a bounded-semaphore fan-out. **The "generate a description per doc" step can copy this pattern** rather than invent it. |

## What's new (the actual work)

Five pieces, roughly in dependency order:

### 1. Project grouping ("upload documents project-wise")
`documents` ([001_init.sql](../../../server/sql/001_init.sql)) has no project concept.
Cheapest form: a nullable `project` TEXT column (+ index) on `documents`; richer form:
a `projects` table with `project_id` FK. Search/routing then filter by project.
**Decision for later:** free-text tag vs. first-class table. Start with the column
unless projects need their own metadata/description.

### 2. Per-document descriptions (built at index time)
No summary field exists today. Add e.g. `description TEXT` to `documents`, generated
when a doc finishes indexing — one small-model `/api/generate` call over the doc's
first-N chunks (or docling markdown in `doc_pages`), reusing the web-search generation
plumbing. **This is the load-bearing step** (see Trap 2). Also embed the description so
routing can be semantic, not just an LLM reading a list.

### 3. A routing tool — "which documents are relevant?"
New tool, e.g. `find_documents(query, project?)` → returns candidate
`{doc_id, file_name, project, description, score}`. Two implementations:
- **Semantic** — embed the query, cosine-search the *description* embeddings. Scales to
  large libraries; needs description embeddings (step 2).
- **List-and-let-the-model-pick** — return all descriptions, let the chat model choose.
  Dead simple, fine for a handful of docs, blows up the context window past ~dozens.

Recommendation to explore: semantic routing, with the list form as a fallback for tiny
libraries.

### 4. Corpus-scoped `search_document`
Two coupled changes:
- **Endpoint** — either relax `/v1/docs/{doc_id}/search` to accept a set of doc_ids /
  a project, or add `POST /v1/search` that searches across docs (drop/rewrite the
  `WHERE doc_id = %s` at [docs.py:444](../../../server/routers/docs.py) to `doc_id = ANY(...)`
  or a project join). Results must carry `doc_id`/`file_name` so answers can cite *which*
  document, not just which page.
- **The tool gate flips** — see Trap 1.

### 5. Multi-round tool loop (the "multiple rounds")
The loop is **hard-capped at one round-trip**: after tools run, the follow-up `/api/chat`
is sent **without** `tools` ([useChatEngine.js:694-763](../../../src/hooks/useChatEngine.js#L694-L763))
precisely so "a confused model can't spin forever." True route→search→maybe-re-search
needs that cap lifted into a **budgeted loop** (max N rounds, keep `tools` on until the
model stops calling them or the budget is spent). [CHAT_WITH_PDF.md §11](../../CHAT_WITH_PDF.md)
lists this as deliberately unshipped for exactly this reason.

## The three traps (name them before designing)

1. **The tool-gating premise inverts.** `search_document`'s gate is
   `ctx.currentDocId && currentDocIndexState === 'indexed'`
   ([searchDocument.js:48](../../../src/lib/chatTools/searchDocument.js#L48)) — tools appear
   only when you have *one indexed doc open in the reader*. A library assistant answers
   about docs you may not be reading at all. The gate must become "library/project is
   non-empty," and `currentDocId` stops being the anchor. This ripples into `toolCtx`
   ([useChatEngine.js:544](../../../src/hooks/useChatEngine.js#L544)), which today only
   carries the single current doc.

2. **Description quality is the entire ballgame.** Routing is only as good as the
   descriptions it searches. A vague auto-summary sends the model to the wrong document,
   and every downstream step is then confidently wrong with no signal that it happened.
   The cheap-looking "summarize the doc" step is the highest-leverage, highest-risk part —
   budget real attention (and probably eval) here.

3. **Multi-round is a cost/latency/loop risk, not a free toggle.** Each round is a full
   Ollama round-trip; route→search→answer is already 3. Without a hard budget and a
   visible "still searching…" state, a looping model burns tokens and wall-clock with no
   ceiling. The one-round cap exists on purpose — replace it with a *bounded* loop, never
   an open one.

## Suggested phasing (for whoever designs this)

- **Phase 1 — corpus search, no routing.** Add the project column + a cross-doc search
  endpoint; let `search_document` span a project. Ships the "ask across my docs" value
  with the fewest moving parts and no new model calls. Multi-round still capped at 1.
- **Phase 2 — descriptions + routing tool.** Generate/embed descriptions at index time;
  add `find_documents`. Now the model narrows before it drills.
- **Phase 3 — budgeted multi-round loop.** Lift the round cap with a max-N budget + UI.
  Only worth it once routing exists to justify a second hop.

Each phase is independently useful and independently shippable — a strong sign the
decomposition is right.

## Explicitly NOT decided here

Schema shape (column vs. table), routing algorithm, how docs get "uploaded project-wise"
in the UI, the round budget, and whether descriptions are LLM-generated vs.
user-editable are all **open**. This is a captured suggestion, not a design.

## Next step

When ready to build, run this through the brainstorming → spec → plan flow (start
Phase 1). This file is the pre-read; it is not a spec and has not been approved.
