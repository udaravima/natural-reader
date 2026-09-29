# v2.3 Task B report: `search_documents` says what it is, and filters noise

## Change
- **Rename.** `server/chat/tools/search_document.py` becomes `search_documents.py`; the tool is now named `search_documents`. The orchestrator's DEBUG metric is now `search_documents_called`.
- **Scope-based internals.**
  - `_scope(ctx)` returns the documents searched, in `ref` order. In this slice that is only the open, indexed document.
  - `execute` searches every document in scope, merges the results by score and applies the floor.
  - `available()` means "the scope isn't empty".
- **Result the model reads:**
  - `{query, documents: [{ref, name}], passages: [...], message?}`.
  - A passage is `{ref, page, relevance, text}`, or `{ref, page, already_shown: true}`.
  - `relevance` is strong (0.7 or more), moderate (0.55 or more) or weak. There is no raw score.
  - The name goes through `display_name`.
  - `message`: "No passages about this in the document." when none pass the floor, or "Nothing new: …" when every passage found was already shown.
- **The floor.** `CHAT_SEARCH_MIN_SCORE` (default 0.45) lives in `ChatConfig`, is passed through `ToolContext.search_min_score`, and has a line in `.env.example`.
- **Already shown.**
  - `ToolContext.shown` is the set of chunk ids for this turn.
  - The prefetch passages now carry their chunk `id`; `BuiltContext.shown_chunk_ids` seeds the set.
  - Each search adds the new passages it returns.
  - The tool asks `search_chunks` for `k + min(len(shown), 10)` rows, so a repeated search can still return k new passages.
- **Saved summary.** It keeps `docId` and `docName`, and adds `passages: [{page, score}]` (raw scores, for tuning). `chunk_count` is the number of new passages.
  - Mechanism: `execute` returns a `_saved` key; `run_tool` now strips every key starting with `_` from the model's copy after `summarize` has run. This is a general convention, documented in `run_tool`.
- **Description.** It says it searches the open document by meaning (vector search), returns pages and relevance, leaves out unrelated passages, and that the question was already searched, so the model should use different words. It also says a passage already shown comes back as its page only. It is under 700 characters and names no other tool.
- **Focus 1 fix found along the way.** `web_search`'s description said "use search_document for those", naming a tool that may not be offered (no document open). It is removed; routing lives in the rules.
- **SPA.** `citationDoc` accepts any successful tool call whose summary has a `docId`: `search_documents`, `search_document` in chats saved before v2.3, and Task C's `read_document_pages` without another change.
- **Docs:** CHAT_WITH_PDF (the result shape, the old name in saved chats), ARCHITECTURE and README renamed; CHANGELOG `### Changed`.

## Tests
- `test_chat_tools.py`:
  - offered only for an indexed, readable document;
  - the result shape (ref, page, relevance, no score, cap) and the exact summary;
  - relevance buckets (6 boundaries);
  - the floor, and the "none" message;
  - already shown across three calls (marker, then new, then "Nothing new");
  - prefetched ids count as shown;
  - k is clamped;
  - an unknown tool;
  - **no tool description names another tool**;
  - the description's key phrases and its length.
- `test_chat_orchestrator.py`:
  - end to end, the prefetch chunk comes back `already_shown`, and `CHAT_SEARCH_MIN_SCORE` from the config reaches the tool. **Mutation check:** without the `shown.update` seeding line, this test fails.
  - the environment parsing of `CHAT_SEARCH_MIN_SCORE`.
- `citations.test.js`: both names and a future document tool. Red on the old code, green now.
- **TDD note.** The tool tests were written before the implementation but were not run red separately. Against the old code they fail on the name alone.

## Suites
- backend: 664 passed;
- vitest: 413 passed, exit 0;
- eslint: clean.
