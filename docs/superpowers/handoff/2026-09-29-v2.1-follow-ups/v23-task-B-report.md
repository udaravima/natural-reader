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

## Fix round 1 — FIX_BASE 95f5a1d

- **I1: the over-fetch was capped at 10 shown ids.**
  - Now `want = min(k + len(ctx.shown), MAX_ROWS)`, with `MAX_ROWS = 40` (HNSW's default `ef_search`).
  - Test: `test_a_search_asks_for_enough_rows_to_skip_everything_already_shown`, with shown set to 0, 15 or 60, expecting a request of 5, 20 or 40 rows. The 15 and 60 cases failed before the fix.
- **Minor: calibration wording.**
  - The `.env.example`, `ChatConfig` and tool comments now say that noise measured up to 0.513 still passes the 0.45 floor, labelled "weak".
  - The default is unchanged, as the plan mandates. Task E's prefixes shift the scores, so Task G's harness should re-measure.
- **Minor: "0 chunks" for a nothing-new search.**
  - The disclosure now reads "N new passages", or "nothing new", for summaries that have `passages`. Saved chats from before v2.3 keep "N chunks".
  - Two ChatView tests, which failed before the change.
- **Minor: raw scores reach the browser.** Documented in CHAT_WITH_PDF: the saved summary, which the browser also receives.
- **Minor: the mutable set on a frozen dataclass.** It is now `field(default_factory=set, hash=False, compare=False)`.
- **Minor: the `_` convention in ARCHITECTURE.** The tool-contract bullet now names `guidance(ctx)`, the rule that a description never names another tool, `_`-prefixed result keys, and `ToolContext.shown`.
- **Minor: the TDD evidence.** Both tests in this round were run red first.
- **Cannot verify from the diff (carried into the next briefs):**
  - Task C must add "read the page" steering, in its own `guidance()` or its description.
  - Task F must change "by meaning (vector search over its indexed text)" to include exact words.

Suites:
- backend: 667 passed;
- vitest: exit 0;
- eslint: clean.

## After the fix-round re-review (clean)

The re-review found all findings addressed. Two follow-ups are applied here; the Task C review should also check them.

- **Out-of-scope observation, confirmed and fixed: `search_chunks` lost the document's own passages.**
  - The HNSW index covers every document, so a plan that walks it filters `doc_id` only after keeping `ef_search` (40) candidates library-wide.
  - **Reproduced:** 300 closer chunks in another document, with `enable_seqscan=off` and `enable_sort=off` forcing the index walk, returned 1 of the document's 3 passages.
  - With fresh statistics and many small documents the planner picked the `doc_id` btree plus a sort, which is exact. So the bug depends on the plan: a document holding a large share of the chunks, or stale statistics.
  - **Fix:** a `MATERIALIZED` CTE filters to the one document first, then ranks exactly. It is deterministic on pgvector 0.6 (this sandbox) and on 0.8 (the compose image).
  - Test: `test_a_search_finds_the_documents_own_passages_when_other_documents_are_closer`, red before the fix.
- **Minor, applied: already-shown markers.** They collapse into `"already_shown": [{"ref": 1, "pages": [...]}]`, deduplicated by page. The size is bounded by distinct pages, not by the rounds. The description, docs, plan and tests are updated.
- **Minor, applied:** the MAX_ROWS comment now says what is true.

Suites:
- backend: 668 passed.
