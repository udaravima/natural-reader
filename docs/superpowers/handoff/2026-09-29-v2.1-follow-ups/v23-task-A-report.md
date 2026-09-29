# v2.3 Task A report — rules in a system message; document text fenced as data

BASE 37cebd5.

## Change
- **New `server/chat/prompt.py`:**
  - `system_rules(doc, tools, tool_ctx, has_pins)`:
    - a base line;
    - the document line (indexed, with the page count, or "isn't indexed yet — click Index");
    - the grounding and citation block (only when an indexed doc is open or pins exist);
    - "Tools you can call:" with one `Tool.guidance(ctx)` line per **offered** tool, plus the rule "when a tool result says the tool rounds are over, answer…".
  - `fence(text)` neutralizes our tags inside document text: `<document_passages` / `</…` become `‹…`.
- **Tools:**
  - `Tool.guidance` is added to the protocol.
  - `search_document`'s line keeps the C1 spec's load-bearing steering: "Answer from those when they are enough".
  - `web_search`'s line says never to copy document text into queries.
- **`server/chat/context.py`:**
  - `PREFETCH_PREAMBLE` and `OPEN_DOC_LINE` are removed.
  - Pre-fetched passages become `<document_passages source="…">…</document_passages>` data in the user turn.
  - Pins become fenced `<pinned_excerpt source where>` blocks. That fixes the "(page, page 4)" label and drops "use the document search tool if available".
  - **One** system message holds the rules, then the pins. It is always present, so every prompt now starts with a system message.
  - `TurnInput` gains `tools` and `tool_ctx`.
- **`server/services/doc_search.py`:** `ReadableDoc` gains `page_count`, read from `documents.page_count`.
- **`server/chat/orchestrator.py`:**
  - Tools are chosen **before** the context is built, so the rules describe exactly the tools on step 0 (`[]` when `CHAT_MAX_TOOL_ROUNDS=0` or the model has no tools).
  - `LAST_ROUND_NOTE` is appended to the results of the last allowed tool round, so the tools-off final step is announced where the model reads it last. The system message stays identical across steps (prefix cache).
- **Docs:**
  - the C1 spec's "steering" note records where the steering now lives;
  - a CHANGELOG Fixed line.

## Tests
- **New `server/tests/test_chat_prompt.py` (23):**
  - the full matrix of document state (none / not indexed / indexed) × tools (none / web / all) × pre-fetch (hit / miss): a tool is named iff offered; no tool name, "cite" or "call" in the user turn; passages only inside the block and exactly once; "(page 12)" iff citable; the document line is right;
  - the rules are stable across steps (no time or passages in them);
  - document text can't close its block;
  - pins are fenced after the rules;
  - a pin without an indexed doc still asks for citations;
  - the steering line is kept only with the tool.
- **`test_chat_orchestrator.py` (+2):**
  - the last round's results carry the note, the final step has `tools == []`, and the system message is identical on both steps;
  - no note while rounds remain.
- **Updated for the new contract:**
  - `test_chat_context.py`: the pin format, the passage block, the always-present system message with index shifts, and the pre-fetch-miss document line now in the rules;
  - the 2 orchestrator tests that read `messages[0]`.

## Suites
- backend: 635 passed;
- frontend: unchanged (server-only);
- eslint: clean.

## Not verified here
- Behaviour with real models. That is the local walk and Task G's evaluation harness.
