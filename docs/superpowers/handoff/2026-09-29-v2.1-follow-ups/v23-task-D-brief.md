### Task D: Multi-round tool use with guard-rails

**Change** (`server/chat/orchestrator.py`, `server/chat/config.py`):
- **More rounds:** `CHAT_MAX_TOOL_ROUNDS` default goes from 1 to **3** (RAG spec §9), still settable from 0 to 20.
- **"Still searching… (round n)"** shows while a later round runs (RAG spec §9).
- **The daily token budget is checked between rounds.** The orchestrator already stops when the budget runs out between steps; this is tested with 3 rounds.
- **A per-turn result budget:** `CHAT_TOOL_RESULT_BUDGET_CHARS`, default 24,000. When a call would exceed it, the call returns `"Search budget for this answer used up: answer from what you have."`.
- **Repeated calls:** a call identical to an earlier one in the turn returns `"Already searched: see the results above."` without running.
- **The last round:** its tool results carry `"note": "This was the last tool round: now answer from what you found, and say what you could not find."`. The following step offers no tools, and the rules never name one.
- **The web search guard (Review focus 4):**
  - `web_search` queries longer than 300 characters, or sharing a run of 8+ consecutive words with any document text seen this turn, are refused with a message the model can act on: "search the web with the topic, not text from the document".
  - This is the rule the v2.1 plan said was needed before raising rounds.
- **Stats:** existing step counts; the saved `stats.steps` shows rounds used.

**Tests (scripted provider):**
- J2's two-hop trail runs and cites both pages;
- the cap reached means the last results carry the note and the final step has no tools;
- the budget;
- repeated-call short-circuit;
- the web guard refuses a verbatim query and allows a topic query;
- a 4-round turn's saved tool calls.


**Carried from the Task C review:** the web guard keeps its own record of the document text returned (prefetch passages, pins, search passages, pages read including cut ones), not ctx.shown.
