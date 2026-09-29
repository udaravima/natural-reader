# v2.3 Task D report: multi-round tool use with guard-rails

## Change
- **Config.**
  - `CHAT_MAX_TOOL_ROUNDS` defaults to **3** (range 0-20, unchanged).
  - `CHAT_TOOL_RESULT_BUDGET_CHARS` is new: default 24,000, range 1,000-1,000,000.
  - Both have `.env.example` lines.
- **`TurnTools`** (`server/chat/tools/__init__.py`) runs one turn's calls:
  - **Repeated call.** The same `(name, sorted-JSON args)` as a call that already ran returns `{"message": "Already searched: see the results above."}` without running.
  - **Budget.**
    - With the budget used up, a call returns `{"message": "Search budget for this answer used up: answer from what you have."}` without running.
    - A call whose result, sized as `result_text`, the exact tool-message text the model reads, would exceed the budget is run, then **discarded**. The `ctx.shown` and `ctx.seen_text` entries it added are rolled back, because the model never saw that text.
    - A discarded or refused call isn't recorded as done, so it can be tried again.
  - A note result is saved and streamed as an ok summary, with the note as `summary_text`.
- **Web search guard** (`web_search.py`, Review focus 4):
  - A query over 300 characters is refused.
  - A query sharing a run of 8 words with any `ctx.seen_text` is refused. The comparison is lowercase over `\w+` words, so punctuation doesn't matter.
  - Both refusals are errors telling the model to "Search the web with the topic in a few words, not text from the document."
  - `seen_text` is fed by:
    - the prefetch passages (`BuiltContext.shown_texts`);
    - the pins' text (orchestrator);
    - `search_documents` passages;
    - `read_document_pages` pages, including a cut page, per the Task C contract.
- **Orchestrator.**
  - Uses `TurnTools`.
  - `tool-input-available` carries `round`.
  - The tool message is built with `result_text`.
  - The daily budget check before every step is unchanged; it is now tested across 3 rounds.
- **SPA.**
  - `toolStatusFor(name, round)`: "Searching the document…", "Reading the document…", "Searching the web…", or "Running a tool…" for any other tool. From round 2 on it reads "Still searching… (round n)".
  - The generic "executing tool…" text is gone.
- **Fixture.** `tool_round.jsonl` was regenerated; `tool-input-available` gains `"round": 1`.
- **Docs.** CHAT_WITH_PDF (the loop diagram and a guard-rails section replacing the "multi-iteration" to-do), ARCHITECTURE, `.env.example`, and a CHANGELOG line under Changed.

## Tests
New `server/tests/test_chat_multiround.py`, 15 tests. They were written first and failed at import (`TurnTools`):
- defaults and environment parsing;
- **J2's two-hop trail** (rounds 1 and 2, both pages cited, 3 steps saved);
- the capped round carries the note, and the final step has no tools;
- a 4-round turn saves every call in order, with rounds 1-4;
- the daily budget is checked between rounds (it stops after 3 steps);
- a repeated call isn't run: one search, and its message is streamed;
- the result budget refuses a result that would exceed it, and its text never reaches the model;
- a refused result leaves `shown` and `seen_text` untouched;
- the web guard: 5 cases (copy, case and punctuation, topic, 6 words, over 300 characters);
- every kind of document text is guarded end to end (prefetch, pins; a topic query goes through);
- passages from an earlier round are guarded.

**Mutation checks:** removing the prefetch seeding or the pin seeding of `seen_text` each fails the end-to-end guard test.

Existing tests that meant "a cap of one round" now pin `ChatConfig(max_tool_rounds=1)`. The SPA's `chatEvents.test.js` covers the status per tool and per round.

## Suites
- backend: 707 passed;
- vitest: exit 0;
- eslint: clean.
