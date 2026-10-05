# Handoff: how document text reaches the model (v2.4 proposal), 2026-10-05

This is for a **local Claude Code session** on the user's machine. It is self-contained: read it, then the files it links. It continues the cloud session that built v2.1 to v2.3 on the branch `claude/task-mwo2ow`.

## Status (updated 2026-10-05, local session): v2.4 built

[Plan](../../plans/2026-10-05-v2.4-document-context.md), branch `feat/v2.4-document-context` (from `claude/task-mwo2ow`; not pushed, not merged).

| Task | Commit | What a person gets |
|---|---|---|
| 0 · The eval checks the strategy | 0eb6e8b | 9 cases (small talk, general, live, follow-up added), seconds per case, `--repeat`, `--think`, `--prefetch` |
| A · One strategy in the rules; truthful tool descriptions; the date in the rules | 20dee8e | The model decides: no tools, the document, the web, or its own knowledge; follow-ups work (the clock line no longer hijacks "that") |
| A2 · Assistant profile | ab90a2f | Admin console → **Assistant**: name, personality, house rules; or `CHAT_ASSISTANT_PROFILE_FILE` |
| B · Prefetch as a tool exchange | 70120ee | Document text never sits in the user's message when the model has tools |
| C · `CHAT_PREFETCH` | fc54181, fixed 77df43d | Default `auto`: no pre-search for a model that searches itself |
| D · Source chips | 9938f7a | "Sources: p. 3 · p. 8" under answers, clickable |
| E · "Use this document" switch | f820e49 | A chip above the chat box, per chat and per user |
| F · Deployment limits | 53cc242 | `INFERENCE_NUM_CTX_MAX` (32768), `INFERENCE_KEEP_ALIVE_MAX` (30m); Settings offers only those |
| G · v2.3 minors, docs walk | bb385a3 | — |

**Measured** (eval, 9 cases): `gemma4:e4b` local CPU 9/9 with the default (`auto`), 5/9 with prefetch forced on; `gemma4:31b-cloud` 27/27 (3 runs). Eval runs use `ollama:gemma4:31b-cloud` and OpenRouter with `SUMMARIZE_MODEL=gemma4:31b-cloud`; the user doesn't target models below `gemma4:e4b`.

**Environment only, no screen:** `CHAT_PREFETCH`, `INFERENCE_NUM_CTX_MAX`, `INFERENCE_KEEP_ALIVE_MAX`.

**Owed before merge:**
- the running-app walk (browser tools were disconnected): admin sets a profile; chips open pages; the switch with two users on one browser;
- **the backend doesn't start on this branch:** `server/logging_config.py` (commits 4896734, 5fa2360) gives `RotatingFileHandler` a `when=` argument; `configure_logging()` raises;
- this machine's `.venv` is Python 3.11.15, while `server/routers/chat_turns.py:238` uses `inspect.getasyncgenstate` (3.12+): 10 `test_chat_turns` tests fail, and the stream-close path would raise in the running app.

**Deferred minors:** in the plan workspace ledger's "Final:" lines, copied to the follow-ups ledger.

**Next (the user's choice):** C2 multi-document projects, or vLLM (provider independence).

## 0. Before anything

- **Branch.** v2.1, v2.2 and v2.3 are on `claude/task-mwo2ow`, and the user merges it into `development`.
  - Start from `development` once it is merged. If it isn't merged yet, start from `claude/task-mwo2ow`.
  - Work on a new branch, e.g. `feat/v2.4-document-context`.
- **Read first:**
  - [`../2026-09-29-v2.1-follow-ups/HANDOFF.md`](../2026-09-29-v2.1-follow-ups/HANDOFF.md): the rules, environment and rulings;
  - its `ledger.md`, last ten lines: the v2.3 final review and the first eval runs;
  - [`docs/CHAT_WITH_PDF.md`](../../../CHAT_WITH_PDF.md) §5 (stage 0 prefetch, the tools) and §6.5 (the eval and the measured scores);
  - [`docs/superpowers/plans/2026-09-29-v2.3-document-qa.md`](../../plans/2026-09-29-v2.3-document-qa.md): how the tools were designed.
- **Rules that bind this work** (from the user, unchanged):
  - **Commits:** commit as you go.
  - **Push and merge:** ask before pushing or merging.
  - **Deployment-specific values:** nothing deployment-specific is hardcoded; new settings are env vars with safe defaults plus a `.env.example` line; docs use example.com.
  - **Secrets:** API keys never appear in logs, events or responses. Never print `.env` values.
  - **User content:** no user content (questions, document text) at WARNING or above.
  - **Before calling anything done:** say plainly what is still API-only.
  - **How to explain:** plain language, cause and effect, what was verified versus assumed.
- **The user's usual chat model is `gemma4:e4b` on Ollama.**
  - It has 7.5B parameters (Q4_K_M) and a 131k context.
  - Its capabilities: tools, thinking (on by default), vision and audio.
  - The user expects it to make retrieval decisions better than `llama3.2:3b` does. **Measure that before designing around it (§3).**

## 1. What the user raised

The user's six points about how a chat turn uses the open document today:

1. **Provenance:** the automatic search's passages arrive inside the user's own message, so the model reads them as something the user wrote. They should come from the system, as a tool result.
2. **Consent:** the user may not want any document context at all.
3. **Cost:** every message carries extra context.
4. **Scale:** with multi-document search (C2), searching every message across documents gets worse.
5. **Agent-first:** the agent should search, see where the information is, and read pages or passages only when it needs them.
6. **Noise:** a message can match the document without being about it.

## 2. What is true today (checked in code, 2026-10-05)

- **Stage 0 prefetch** (`server/chat/context.py: prefetch`, `build_context`):
  - On every message with an indexed document open, the message is embedded and the document searched.
  - When the best passage scores at least `CHAT_PREFETCH_MIN_SCORE` (0.6), up to `CHAT_PREFETCH_K` (4) passages are prepended to the new user message. Each is capped at 1,500 characters and sits in `<document_passages>`.
  - The orchestrator seeds `tool_ctx.shown` and `seen_text` from them, so tools don't repeat them.
- **Point 1 is correct.** Document text sits in the user role, so an instruction planted in a PDF gets the user's authority. The `fence()` escaping stops tag break-out but not that.
- **Point 2 is correct.** The client always sends `context.doc_id = currentDocId` (`src/hooks/useChatEngine.js:479`). There is no switch.
- **Point 3 is half-right.** Passages and tool results are **not** saved into history: `store.load_turn_context` returns what the user typed plus the assistant's text. So nothing piles up. But each turn pays for up to ~1,500 tokens of passages, even "thanks!".
- **Point 4 is correct as a direction.** Prefetch is single-document today. C2's library scope must not prefetch across the library.
- **Point 5 is mostly built.** `search_documents` returns `{ref, page, text}` nearest first. `read_document_pages` reads up to 3 pages. There are 3 tool rounds, and the system rules are built from the tools offered.
- **Point 6 is confirmed by measurement** (below).
- **Tool-role messages already exist.** `server/llm/types.py: Message` has `tool_calls`, `tool_call_id` and `name`, and the orchestrator appends them for real tool rounds. A synthetic tool exchange needs no new types.

## 3. Evidence gathered in the cloud sandbox (2026-10-05)

The sandbox ran Ollama 0.35.1 on CPU, `nomic-embed-text`, and Postgres 16 with pgvector. SearXNG wasn't running, so `web_search` failed with ConnectError.

### 3a. Embedding scores can't decide relevance

The script and its output are in `../2026-09-29-v2.1-follow-ups/score-calibration-2026-10-05.*`. Each cell is the best passage's cosine score, min to max.

| Message | User guide (55 passages) | Sherlock Holmes (631 passages) |
|---|---|---|
| A question it answers | 0.72 – 0.83 | 0.66 – 0.78 |
| A question it doesn't answer | 0.60 – 0.64 | 0.67 – 0.73 |
| Small talk | 0.47 – 0.60 | 0.51 – 0.65 ("thanks!" 0.62) |

- **No absolute threshold separates these.** A z-score or the gap between the first and tenth passage doesn't either.
- **So the relevance label was removed** (commit 5eb36ff). The 0.45 floor stays as a noise floor only.
- **The prefetch gate (0.6) lets small talk through on long documents.**
- **Lesson: a retriever ranks; only a reader decides.** Gate decisions belong to the model, or to the user.

### 3b. The eval (`scripts/eval_doc_qa.py`), prefetch on vs off

Off means `CHAT_PREFETCH_MIN_SCORE=1`. "Right content" means the fact, or a correct refusal.

| Model | Prefetch | Pass (of 5) per run | Right content |
|---|---|---|---|
| `llama3.2:3b` | on | 2, 2, 2, 2 (run 1 before the label change) | 12 / 15 (runs 2–4) |
| `llama3.2:3b` | off | 1, 1, 2 | 7 / 15 |
| `gemma4:e4b` | on | 3, 3 | 6 / 10 |
| `gemma4:e4b` | off | 4 | 5 / 5 |

- **With prefetch off,** `llama3.2:3b` answered "What is the project's codename?" by calling `web_search` every time. It never searched the document.
- **With prefetch on,** most failures were a missing "(page N)", and once a wrong page.
- **Every run used one tool round.** The 3B model never chained search, then read.

### 3c. gemma4:e4b

These are three runs on CPU, at 110–170 s per run. The fourth run (off, run 2) was stopped for time, so **run more locally (§4)**.

- **Prefetch off: 4/5, and every document question went to `search_documents` or `read_document_pages`.** It never called `web_search` for the document. The only failure was a missing "(page 8)" on the two-hop case; the facts were right.
- **Prefetch on: 3/5 twice.** It answered with **zero tool rounds** whenever passages were present, even when they didn't hold the answer. Two-hop and exact-label failed because it trusted the prefetched passages instead of searching (Table 7.3 is on page 6, and the prefetch didn't contain it).
- **Takeaway: the opposite of the 3B result.** For `gemma4:e4b`, prefetch *hurts*: it anchors the model on whatever was found and suppresses its own search. This supports the user's agent-first design for capable models, and makes Task E (a per-model prefetch policy) the core of this plan, not an extra. Like the 3B model, it used at most one round, so the two-hop journey still isn't exercised.

## 4. Step 1 for the local session: reproduce, then decide

Run these on the user's machine, which has the real GPU, SearXNG and Keycloak:

```bash
./startup.sh up          # or startup.cmd up on Windows
# each line three times; the eval prints one line per case and a total
python scripts/eval_doc_qa.py --model ollama:gemma4:e4b --show-answers
CHAT_PREFETCH_MIN_SCORE=1 python scripts/eval_doc_qa.py --model ollama:gemma4:e4b --show-answers
python scripts/eval_doc_qa.py --model ollama:llama3.2:3b
CHAT_PREFETCH_MIN_SCORE=1 python scripts/eval_doc_qa.py --model ollama:llama3.2:3b
```

- **Record the results** in the ledger as pass counts, right-content counts, tools and rounds, the same as §3b.
- **Scores on the user's own documents:** run `score-calibration-2026-10-05.py` against one or two of their PDFs (adapt `chunks_of`), or set `LOG_LEVEL=DEBUG` and read the `prefetch doc=… top=…` and `search_documents … best_cosine=…` lines.
- **Decision gate:**
  - If `gemma4:e4b` with prefetch **off** gets at least as much right content as with it **on**, and calls `search_documents` (not `web_search`) for document questions, the user's agent-first design is the default for capable models.
  - If not, prefetch stays on for that model too, delivered as in Task A below.
  - Either way, it becomes a per-model setting (Task E), not one global switch.

## 5. Proposed plan: v2.4, document context the user and the agent control

This is a proposal, so write it up as a plan in `docs/superpowers/plans/` and get the user's approval first.

**Order.** The earlier handoff named provider independence (embeddings over `/v1/embeddings`, vLLM) as v2.4. The cloud session asked the user which comes first, and it has not been answered. Ask again. This plan assumes it goes first and provider independence becomes v2.5.

Run every task through the usual loop: implement, review, fix rounds, then a final review. Run the eval before and after each one.

**Task A: prefetch arrives as a tool exchange, not as user text** (point 1)
- **When the model is offered tools,** `build_context` emits the prefetch as:
  - an assistant message with one `search_documents` tool call, whose arguments are the user's message (`query`) and `k`;
  - a `tool` message whose content is exactly what `search_documents` would return (`result_text`, with refs, pages, texts and `documents`).

  These sit after the user's message, so the model continues from "I searched; here is the result".
- **When it isn't offered tools,** keep today's fenced block. Some strict templates (vLLM Gemma/Mistral) reject tool messages without tools.
- **Keep:** prefix-cache order (rules and history unchanged), `shown` and `seen_text` seeding, the `data-context` note for the browser, and the saved message (the user's own text only).
- **Watch for:**
  - the system fold for strict templates (`ruling R9`, final review I3);
  - an Ollama tool message needs `name`;
  - providers that reject a tool call id they didn't issue: test on Ollama and OpenRouter (the OpenAI-compatible adapter).
- **The guidance text** in `search_documents.guidance` that says "If the user's message has a <document_passages> block" must change with it.
- **Tests:** the message shape per provider; a planted "ignore your instructions" in a PDF still sits in tool content.

**Task B: "Use this document" switch** (points 2, 3, 6)
- **The composer** gets a chip or toggle showing the open document's name, on by default.
- **When it's off,** the turn is sent with `doc_id: null`: no prefetch, no document tools, and no document line in the rules.
- **The choice** is per chat session and per user, stored like the other v2.2 per-user browser state (see `useUserDraft`), with no cross-user leaks.
- **Edge cases:** pins still work when it's off, because pins are the user's explicit choice. Citations from earlier replies still open.

**Task C: document first, then the web** (the measured `web_search` failure)
- **One rule line**, built only when both a document tool and `web_search` are offered: questions that could be about the open document ("the project", "this paper", "chapter 3") are searched in the document first; `web_search` is for things outside it.
- **Add eval cases:**
  - "thanks!" on a document: no tool call, no citation;
  - an unrelated general question: no document citation;
  - a follow-up, "explain that more simply": answered from the previous answer, no search needed.

**Task D: the app attaches page references** (the biggest failure class in §3b)
- The app knows which passages and pages each answer received: `shown`, and the `_saved` pages in tool summaries.
- **Show them as source chips** under the reply, as clickable links like today's "(page N)" links, so a correct answer isn't lost to a missing citation.
- **Keep the model's inline "(page N)"** where it writes one. Whether a chip shows only pages the answer's text matches, or every page retrieved, is a user decision: offer both, and recommend "pages the answer used, else all retrieved, marked as retrieved".
- **The eval reports** both inline citations and chip pages.

**Task E: prefetch policy per model** (points 4, 5)
- `CHAT_PREFETCH` is `auto`, `on` or `off`, with a per-model override (e.g. `CHAT_PREFETCH_MODELS=ollama:gemma4:e4b=off`). Follow how `INFERENCE_MODELS` and the router parse per-model settings.
- **`auto`** means on when the model has no tools, otherwise the deployment default. The eval in §4 sets the documented default for `gemma4:e4b` and `llama3.2:3b`.
- **Prefetch stays single-document,** forever. Library or project scope (C2) is reached only by the agent's tools.

**Out of scope here, next:** C2 library scope. That is `list_library` and `find_documents`, then search over several documents returning short location snippets first, with `read_document_pages` for depth (point 5 at library scale). §6 below covers the document descriptions that routing needs.

## 6. The user's second question: summarising a 1,000-page PDF with a very small, fast model

**Short answer:** yes, but never in one call. Use map-reduce over the document's own structure, with the small model doing the many local steps and a better model doing the one final step.

**Why one call fails.**
- 1,000 pages is about 500k–700k tokens, far past what a small model attends to well, even when its nominal context is 128k.
- **The silent trap:** Ollama truncates input to `num_ctx`, often 2k–8k by default, without an error. The model then "summarises" the first few pages and sounds confident.
- **The C2 suggestion note's "first-N chunks" description** has the same failure on a book: it describes the preface.

**How it should work.** This is a background job, resumable, stored per step. Steps 1 to 3 use the small model or no model; step 4 uses the better one.

1. **Structure, free.** Docling's Markdown (`doc_pages`) has headings. Build a table of contents with page ranges. The title, TOC, abstract or preface and chapter titles already give most of a description, with zero model calls.
2. **Map: the small model.** Make one call per section, or per window of ~6–10 pages that fits well inside an explicitly set `num_ctx`.
   - The output is structured JSON (Ollama `format` with a schema): 3–5 points, each with its pages.
   - Use temperature 0, and an "extract, don't add" instruction.
   - 1,000 pages is ~100–170 calls: minutes on a GPU.
3. **Check: cheap code, no model.**
   - Drop any point whose names or numbers don't appear in its source pages, using a string match like `has_term` in the eval.
   - Deduplicate across sections, using the existing embeddings.
4. **Reduce: a tree.**
   - Section points become a chapter summary, and chapter summaries become a ~150-word description plus 5–10 key points.
   - The depth is about log_k(sections). The final call can go to `gemma4:e4b`: the summarise route of the model router (`SUMMARIZE_MODEL`) for the map, and a second route for the reduce.
5. **Store and use:**
   - `documents.description` and `description_embedding`, per the RAG design spec §8;
   - per-section summaries with their embeddings, as a second retrieval level for broad questions ("what is chapter 4 about?"), which chunk search answers poorly (RAPTOR-style).
   - **Make the description user-editable** (the spec's trap: a vague description routes confidently wrong).

**A cheaper variant: coverage by sampling.**
- Cluster the chunk embeddings the document already has (k-means, ~20–30 clusters).
- Summarise only the chunk nearest each centroid, plus the TOC.
- That is ~30 small calls instead of ~150. It gives a good description, but not good per-section summaries.

**How to know it works.** Add an eval with:
- planted facts in chapters 1, 5 and 9 of a long fixture: the description must mention each chapter's theme;
- no names that aren't in the text, checked by string match;
- a timing budget.

Run it with the small model on the map step alone, then with `gemma4:e4b` on the reduce step.

## 7. Open user decisions (carry forward)

- **The order:** this plan or provider independence first (§5).
- **The source chips:** "pages used" vs "all retrieved" (Task D).
- **From v2.1:** the Task 10 403 wording; ruling R2 revised (no "local" owner).
- **Local checks still owed:**
  - the running-app walk (two users on one browser, Open as a share recipient, read-aloud with real Kokoro);
  - `startup.cmd` on real Windows;
  - count indexed documents with a NULL `embedding_model`.
- **Deferred v2.3 final-review minors** (the ledger's last line): fold them into this plan's first task or a cleanup task.
