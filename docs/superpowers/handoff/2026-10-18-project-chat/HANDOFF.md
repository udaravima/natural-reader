# C2.1 — project chat: handoff

**Branch:** `feat/c2.1-project-chat` (from `development` at `5bff936`). **Not merged** when this was written. **Spec:** `docs/superpowers/specs/2026-10-18-project-chat-design.md`. **Plan:** `docs/superpowers/plans/2026-10-18-project-chat.md`. **Companions in this folder:** `EVAL.md` (every measurement), `WALK.md` (the running-app walk).
**Commits:** 51 on top of `5bff936`, from the spec `d2ba147` to `6a29499` (the re-walk). Everything is committed except this file. The ledger is `.superpowers/sdd/2026-10-18-project-chat/progress.md`; line numbers like `L174` below point into it as it stood when this was written.

## Summary

A person can now ask one chat about every current document in a project: Library → Projects → open a project → **Ask about this project**, or the chat's scope chip → **Choose a project…**. The assistant gets a map of the project, searches all its documents as one ranked list, hands bulk reading to a separate model call whose quotes the app verifies, and cites each fact as **(Guide.md, page 7)**, a link that opens that file at that page. The web is searched only after the user clicks **Search the web** on an offer card (default mode `ask`); an admin can switch the mode to `auto` or `off` under Admin → Chat options. On the cloud model it still misses two of the spec's four §2 targets: right-document citations 87.9% on the 9-document project and 82.1% on the 50-document one against 90%, and one invented page in 6 gap turns (the code case, 18/18, and unapproved web calls, 0 of 87 turns, are met). Two answer-quality gaps are open on purpose: "What are the features?" cites README.md alone (1 turn of 9 passes), and the RHEL question gets its text-plus-card answer in 4 of 6 eval runs but 1 of 4 chats in the running app; Task 22 is done, the final whole-branch review is pending, and the local `gemma4:e4b` run waits for the user's go-ahead.

## Gate

Final gates ran at `cd0e956` (ledger, "Final gates"). HEAD has moved since only by docs commits, `b8bc2df` and `6a29499` (`git diff --stat cd0e956..HEAD` touches `docs/CHAT_WITH_PDF.md` and `WALK.md` and nothing else), so the gates hold for HEAD.

| Check | Result at `cd0e956` |
|---|---|
| Backend `.venv/bin/python -m pytest server/tests -q` | 2106 passed, 16 skipped, 2 deselected (container-marked, excluded by `pytest.ini`), 2 warnings, both third-party deprecation notices (Authlib, Starlette's httpx) |
| Frontend `npx vitest run` | 1010 passed, 106 files |
| `npx eslint .` | clean |

For scale, the plan started at 1308 backend and 658 frontend tests (`04e9633`).

The final whole-branch review is **pending**: <!-- FINAL REVIEW: filled by the controller -->

## What was built

Everything in this section is committed and passed its task review. The first walk (J1–J9) and the re-walk after Task 22 (W1–W5) exercised the user-facing pieces in a running app; the server-side pieces (search, reader, consent rules, extraction) are verified by unit tests and by the eval, not by hand.

### Project scope and the map

**Controls:** which documents a chat can see, and what the model knows about them before it does anything.

- A chat can be scoped to a project. The scope is stored on the chat (`chat_sessions.scope_project_id`, migration `021`, `ON DELETE SET NULL`, plus a small partial index so a project delete doesn't scan every chat). It is re-checked on every turn with `require_project_role(..., admin_ok=False)`: a non-member, even a site admin, is refused with "This project doesn't exist or you don't have access." and the chat falls back to no project.
- The documents are `list_project_documents`: current versions only. Each goes through `ensure_current`. Documents that are still indexing or failed are listed in the map with their state and are not searched.
- The map is a fenced `<project_documents>` block in the system message, between the rules and the pins. One line group per document: ref number, name, pages, state, "open in the reader", and an outline (headings of levels 1–3 with pages) or, with no outline, the opening line. Names and headings are made inert. When it is over `CHAT_PROJECT_MAP_CHARS` it shortens in this order: drop level 3 → keep level 2 but cut each outline to 8 entries → level 1 only, 8 entries → names and pages only. A document is never dropped. Once shortened, `document_outline` is offered.
- Session meta carries `scopeProject: {id, name}`; `name` is `null` when the caller can no longer see the project.
- Files: `server/chat/project_scope.py` (`load_project_scope`, unique names `Name (2)`), `server/chat/project_map.py` (`build_map`), `server/chat/tools/document_outline.py`, `server/chat/tools/scope.py`, `server/services/doc_outlines.py`, `server/sql/021_project_chat.sql`, `server/routers/chat_turns.py` (`context.project_id`), `server/routers/chat_sessions.py`, `server/chat/store.py`.

### Search across documents

**Controls:** which passages reach the model, and how evenly they come from different documents.

- `search_scope` (`server/services/doc_search.py`) is one SQL statement over the whole scope: a meaning list (an exact scan inside a materialised CTE) and a word list, fused once with reciprocal-rank fusion (`RRF_K = 60`), then balanced in Python so one document can't take every slot. `search_chunks` for one document is now a thin wrapper over it.
- Partial word matches: when no chunk has every query word, the word list ranks chunks that have at least half of the query's informative words (words some chunk has, but at most half of all chunks do; the 12 rarest). In fusion a partial match counts a tenth as much (`PARTIAL_WEIGHT = 0.1`). Rows carry `word_match` = `all` / `partial`; the tool labels a partial match "some words".
- Tool: `search_documents(query, k?, documents?)`; `documents` narrows one call. The first search (prefetch) runs over the project too, with the same balance.
- Files: `server/services/doc_search.py`, `server/chat/tools/search_documents.py`, `server/chat/context.py`, `scripts/bench_project_search.py`.

### The reader sub-agent and `extract_from_documents`

**Controls:** how many pages one question can be read from, and whether a quoted fact can be trusted.

- The reader is a separate model call with no tools and a fresh context. It gets the chat model's goal (`need`) and a batch of fenced, labelled passages (`[2, page 6]`, `[web 1]`) and returns JSON facts with quotes. The app checks every quote against the passage it names, after normalising case, whitespace, quote and dash kinds, line-end hyphens and spaces between Han/Kana characters. A fact whose quote isn't found is dropped; a quote found in another passage of the batch is relabelled. A quote needs at least 4 characters (2 with Han/Kana) unless it is the whole passage. The model never sees an unverified fact. Every fact whose quote verifies is kept, whatever the reader's own `found` says, and `found` is true exactly when one verified (Task 22a, fix round 1; L496). The brief says: "Return every fact that serves any part of the goal. Only if nothing in the passages serves any part of it, return found false and no facts."
- `extract_from_documents(documents, need, pages?)` reads up to `CHAT_EXTRACT_MAX_PAGES` pages a call (when a document has more, pages are chosen by the same search ranking for the need). It returns facts with `(ref, page, quote)`, plus two lists kept apart: `not_read` (left out by the page cap) and `failed` (a batch that failed or timed out). Blank pages are skipped; a page longer than `READER_INPUT_CHARS` is split into passages overlapping by 200 characters.
- `read_document_pages` takes a `document` in project scope and shows code chunks fenced, one fence per block. A call to it, `document_outline` or `extract_from_documents` that names no document means the document open in the reader, when that is one of the project's searchable documents; otherwise the old "which document?" error (Task 22a, item o; unit tested, not exercised by the eval, which opens no document).
- Files: `server/chat/reader.py`, `server/chat/tools/extract_from_documents.py`, `server/chat/tools/read_document_pages.py` (`page_chunks`, `page_texts`).

### Web search and consent

**Controls:** whether anything leaves the server, and who decides.

- Modes `ask` (default) / `auto` / `off`. The first match wins: the admin's saved choice (an `app_settings` row `chat_options`), then `CHAT_WEB_SEARCH`, then `ask`. Read once per turn, so a change applies from the next message. The admin control is **Admin → Chat options → Web search** (*Ask first* / *Search without asking* / *Off*), with a source line and **Use the server setting**; `GET`/`PUT /v1/admin/chat-options`, audited. It is disabled when `SEARXNG_URL` is set empty, which is how a deployer turns web search off for the server.
- Consent is enforced by which tools are offered, not by the prompt. In `ask` the only web tool is `offer_web_search(query, reason)`; calling it ends the model's tool rounds and the turn finishes with an offer saved as a `web_offer` note. **Search the web** on the card sends a new turn with `context.web = {query}`: the app itself runs that one search (a tool exchange with id `app-web-1`) and offers `read_web_pages` and `offer_web_search`, never `web_search`. A different search needs a new offer. `web_search` is offered only in `auto`.
- **An offer comes with an answer** (Task 22a, item a). If the model wrote no text in the round that offered, the turn takes one more step with no tools (the existing final step, counted like any model step), so the card arrives under an answer. The offer's result carries a fixed note telling the model what to write; it has three forms, chosen by `prompt.document_tools_offered` (the condition the rules use for "search the documents first"): a project with a searchable document (what the documents say, with their pages, and what they don't cover; general knowledge labelled, never with a page), a single searchable document (the same, with "the document"), and otherwise (no document, or a project with none searchable) an answer from general knowledge, said to be not from a search and possibly out of date. The events run: text deltas, `data-web-offer`, `data-sources`, `finish`. A provider failure or a used-up daily budget (a `budget_exhausted_no_answer` notice) still ends the turn with the card. **Stop during the answer step loses the card** (the reply is marked Stopped); asking again, or typing "search the web for X", brings it back (L495). No web tool runs in the extra step, so consent is unchanged.
- `web_search` returns results only (`{n, title, url, site, snippet}`, no page fetch and no model call). `read_web_pages(results, need)` takes result numbers from the same turn's searches only (the exfiltration guard), fetches with the existing SSRF guards, picks the best ~1,000-character passages of the page and runs the reader. A reader failure returns a capped "unread excerpt". The tool line counts only pages that gave facts ("Found facts on 1 of 2 page(s): docs.example.com.") and lists the others as "Nothing found".
- The approved query is cleaned on the client and again on the server (`clean_query`): invisible and direction-changing characters are stripped, so the query sent is the query shown.
- `POST /v1/tools/web_search` still works but now returns results, not summaries (a public-API change on an open-source server).
- Files: `server/services/chat_options.py`, `server/routers/admin.py`, `server/chat/web_consent.py`, `server/chat/tools/offer_web_search.py`, `web_search.py`, `read_web_pages.py`, `server/services/web_search.py`, `src/components/chat/WebOfferCard.jsx`, `src/components/admin/ChatOptionsSection.jsx`.

### Citations and sources

**Controls:** whether a citation opens the right file, and which pages the source chips claim.

- In project scope the model cites `(Name, page N)`. The reply saves a `scope` note (`{ref, docId, name}` per document) and the client links a citation only when its name is in that note, so a citation in an old reply still opens the document it meant after the chat's scope changed. A bare `(page N)` links only when the evidence came from one document. A name not in the note stays plain text.
- The server (`cited_pages` in `server/chat/sources.py`) and the client (`src/lib/citations.js`) read the same grammar per part: names with commas and parentheses, joined `(A.md, page 3; B.md, pages 1-2)`, `page`/`pages`/`p.`/`pp.`, lists, ranges up to 10 pages. A 300,000-string differential fuzz found no disagreement outside the ruled range difference (L382).
- Chips group by document (**Guide.md · p. 3 · p. 6**) from a `sources` note `[{docId, docName, pages, used}]`; `used: false` shows as "Searched (not cited)". Web pages the reader got facts from become `web_sources` links (`http`/`https` only). Document-scope replies keep their old note shapes byte for byte.
- Files: `server/chat/sources.py`, `server/chat/orchestrator.py`, `src/lib/citations.js`, `src/lib/chatEvents.js`, `src/components/ChatView.jsx` (`AssistantMarkdown`, `SourceChips`, `ContextNotes`).

### The rules the model reads

**Controls:** how the model decides between documents, general knowledge, an offer and an answer.

`server/chat/prompt.py` is still one strategy, assembled from the offered tools and pinned by goldens (`GOLDEN_ALL`, `GOLDEN_PROJECT`, `GOLDEN_APPROVED` in `test_chat_prompt.py`). New: a project first line, a project step 2 that sends the model to the map by number, step 4 citing `(Name, page N)`, and the gap rule: say plainly what the documents don't cover, optionally fill it from general knowledge introduced as not from the documents and never with a page. The step-1 offer line says to search the document(s) first when they may cover any part of the question (Task 22a, item b2), and it and the approved turn's line end "Don't ask in words whether to search: offer_web_search asks the user." (item b). The gap-and-offer rule applies to single-document chats too. Project chats get `CHAT_PROJECT_MAX_TOOL_ROUNDS` tool rounds; other chats keep `CHAT_MAX_TOOL_ROUNDS` (3).

### The client

**Controls:** how a person picks and sees what a chat is about.

- **Scope chip** (`src/components/chat/ScopeChip.jsx`) replaces the "Use this document" switch, in the chat header and the side panel: *No document* / *This document: name* / *Project: name*. Its menu offers the open document's projects and **Choose a project…**. Keyboard operable; it keeps Space/Enter/Escape away from the reader's shortcuts.
- **Project chats:** `src/hooks/useChatEngine.js` and `useChatScope.js` hold the scope; every turn sends `project_id` (null when none). The chat list labels project chats. A project the reader can no longer see reads **Project unavailable**; only the server's own "doesn't exist or no access" 404 clears it.
- **Ask about this project** (`ProjectPage.jsx`): shown to project members who have the `chat` capability (Task 22b: it used to show to any chat user, and a non-member site admin's first message was refused).
- **Offer card** (`WebOfferCard.jsx`): the reason, an editable query (≤ 300 characters), **Search the web** and **Not now**. Only the last reply's card has buttons; it survives a reload because it is drawn from the saved note.
- **Admin → Chat options** (`ChatOptionsSection.jsx`), described above.
- **Task 22b client fixes** (committed `7c7fb75`, `b841d16`): see the Task 22 table.

### The chunker and the rebuild

**Controls:** what the index contains, and what an upgrade costs.

- `server/services/extract.py`: fenced and indented code blocks become `code` chunks, interleaved in document order (R2), top-level blocks only; a block inside a list or quote stays part of that block's prose. Markdown table cells are separated by spaces so env-var names are findable. Outlines are recorded: Markdown headings, PDF bookmarks, or Docling's per-page Markdown. `CHUNKER_VERSION = split1:{max}:{overlap}:code1:cells1`.
- The reader's page layout does not move: `src/utils/segmentation.fixtures.test.js` and `server/tests/fixtures/segmentation/*.expected.json` are untouched, and the server-side equality test compares only the non-code projection plus `ord` running 0..n-1.
- `server/services/doc_pipeline.py`: every indexing path saves the outline in the same transaction as the chunk swap; the lazy rebuild re-uses a stored vector for any text that already has one under the same embedding model, so an upgrade embeds only new text (`_reusable_vectors`).

### Evaluation harness

- `server/evals/project_qa.py`, `scripts/eval_project_qa.py` (variants `9` and `50`, `--repeat`, `--web-fixture`, `--show-answers`), `server/evals/web_fixture.py`, and a frozen copy of the project's own docs in `server/evals/fixtures/project_docs/` (`docs/README.md` is stored as `DOCS_INDEX.md`; `SOURCE.txt` names the commit). 13 cases; variant 50 adds 41 generated padding documents (9 + 41 = 50) and 2 map-shortened cases; `--web-fixture` adds the web-read case. Citations are counted with the product's own parser, `server.chat.sources.cited_pages` (Task 22a, item d). Run with `SUMMARIZE_MODEL` (or `READER_MODEL`) set to put the reader on a cloud model; with neither set the reader is `llama3.2:3b` (see the correction under Measurements).
- Both eval scripts refuse a database named `natural_reader` and write their documents into their own temporary storage folder (L96, L101).

### Configuration (all env vars; a non-number or out-of-range value warns and uses the default)

| Variable | Default | Unit and meaning | Range |
|---|---|---|---|
| `CHAT_PROJECT_MAP_CHARS` | 6000 | characters of the project map (about 1,500 tokens) | 500–100000 |
| `CHAT_PROJECT_MAX_TOOL_ROUNDS` | 5 | tool rounds per turn in a project chat, before the tools-off final step | 0–20 |
| `CHAT_PROJECT_FUSION_DEPTH` | 50 | candidates per list before fusing, project search | 1–500 |
| `CHAT_SEARCH_PER_DOC` | 2 | passages one document may contribute before others get a turn | 1–10 |
| `CHAT_EXTRACT_MAX_PAGES` | 12 | pages one `extract_from_documents` call reads | 1–200 |
| `CHAT_EXTRACT_OUTPUT_CHARS` | 4000 | characters one `extract_from_documents` call returns (the model reads it pretty-printed, so about 15–25% more) | 500–50000 |
| `CHAT_WEB_SEARCH` | `ask` | `ask` / `auto` / `off`: the deployer's starting value; the admin's saved choice wins; an invalid value warns and is ignored | — |
| `READER_MODEL` | falls back to `SUMMARIZE_MODEL`, then `WEB_SEARCH_SUMMARY_MODEL`, then `llama3.2:3b` | model id the reader runs on; the value `chat` uses the turn's own model (no second model to load into RAM on a CPU) | — |
| `READER_INPUT_CHARS` | 12000 | characters of source text per reader batch | 500–200000 |
| `READER_OUTPUT_CHARS` | 1200 | most the reader returns per batch, in characters | 100–20000 |
| `READER_MAX_CONCURRENCY` | 2 | reader batches at once (a CPU Ollama serves them one by one anyway) | 1–32 |
| `READER_TIMEOUT_S` | 60 | seconds per batch (the model call only, not the queue); `WEB_SEARCH_SUMMARY_TIMEOUT_S` is read as a fallback | 1–3600 |
| `WEB_SEARCH_RESULT_COUNT` | 5 | results one search lists | 1–10 |
| `WEB_SEARCH_MAX_CONCURRENCY` | 4 | page fetches at once | 1–32 |
| `WEB_SEARCH_FETCH_TIMEOUT_S` | 8 | seconds: the SearXNG query, and each hop of a page fetch | 1–120 |
| `WEB_SEARCH_FETCH_TOTAL_S` | 15 | seconds: one page, redirects and body included | 1–300 |
| `WEB_SEARCH_MAX_RESPONSE_BYTES` | 2000000 | bytes: the largest page body read | 10000–50000000 |

The five `WEB_SEARCH_*` rows existed before this branch; Task 22a (g) made them warn and default like the rest (before, a typo in one stopped the server at import). `WEB_SEARCH_MAX_PAGE_CHARS` is no longer read.

### Docs

`CHANGELOG.md` `[Unreleased]`, `README.md`, `docs/USER_GUIDE.md` ("Asking a whole project", "When the assistant offers a web search"), `ARCHITECTURE.md`, `DEPLOYMENT.md`, `TESTING.md`, `CHAT_WITH_PDF.md` and `.env.example` were updated in `08410b4` and `cc5dfac` and check out against the code (Task 20's reviews). Later touch-ups: `8d65886` (the button's membership rule, offers, web search settings), `26ace78` (USER_GUIDE: a stopped answer comes without the card) and `b8bc2df`, which corrected `CHAT_WITH_PDF.md` §3: it said Node 18+ and Python 3.10+, and the chat quoted that to a newcomer; it now matches what `startup.sh` checks (Node ^20.19 or >=22.12, Python 3.12–3.13). The frozen eval fixture copy of that file is deliberately left with the old numbers.

### Commit map

| Task | What | Commits |
|---|---|---|
| spec, plan | design, implementation plan | `d2ba147`, `04e9633` |
| 1 | eval fixture, cases, scoring | `97ed236`, `0628db4`, `bb62f60` |
| 2 | migration 021 | `0c74337` |
| 3 | project scope through the turn | `2ad554d` |
| 4 | eval runner and baseline | `41a3bff` |
| 5, 6 | code chunks, outlines; pipeline, reuse of embeddings | `ddd9fc4`; `87e079e` |
| 7 | one balanced ranking, partial words | `0be9067`, `87eaa93` |
| 8, 8b | project search tool and prefetch; table cells apart | `3d17fba`, `9ff6d17`; `f5cf7a2` |
| 9 | project map, `document_outline` | `1857ae7`, `b1f3215`, `03a54d0` |
| 10 | reader | `f0999ed`, `6d12ee6` |
| 11 | `extract_from_documents` | `8ac1d0a`, `ab59630` |
| 12 | web search results; `read_web_pages` | `98c287c`, `e49e3cc` |
| 13 | web consent, chat options | `ecd364f`, `5b3fb6c` |
| 14 | strategy rewrite | `e0fd70e`, `c66f648` |
| 15, 16 | citations and sources, server; client | `d3bdab8`, `1c73547`; `4d8dd4b`, `51092b6` |
| 17 | scope chip, project chats | `24ed740`, `4b3fe68` |
| 18 | web offer card | `c6f2832`, `323c686`, `ec297f4` |
| 19 | Admin → Chat options | `b282cec`, `86051f1` |
| 20 | documentation | `08410b4`, `cc5dfac` |
| 21 | cloud eval, rebuild timing, walk | `bf88b94` |
| 22b | client gaps from the walk | `7c7fb75`, `b841d16` |
| 22a | server and eval gaps: code, tests, docs; EVAL.md after Task 22; review fix round 1; USER_GUIDE Stop caveat | `8d65886`, `61a0463`, `9333cd7`, `26ace78` |
| 21 follow-ups | the shipped cloud-reader measurement; the prerequisites fix; the re-walk | `cd0e956`, `b8bc2df`, `6a29499` |

## Measurements

All numbers are in `EVAL.md`. Every run used `ollama:gemma4:31b-cloud` as the chat model, thinking off, chat web mode `ask`. Noise is large: two attempts at variant 50 on identical code differed by 3 passes, and `EVAL.md` puts run-to-run noise at no less than two passes, so single-digit differences between sets are not findings.

### A correction: which model the reader ran on

Every eval in this plan before the "Final (cloud, cloud reader)" section ran the reader sub-agent on the local `llama3.2:3b` default, because no command set `READER_MODEL` or `SUMMARIZE_MODEL`. The walks used `READER_MODEL=chat` (the cloud model), so the walk findings are not affected. Reader-dependent figures before that section (facts found, source chips, `web-read`) were measured on the 3b. The shipped measurement below re-ran both variants with `SUMMARIZE_MODEL=gemma4:31b-cloud`; the earlier "after Task 22" section stays as Task 22's like-for-like before/after, labelled as a 3b-reader run (L490).

### The shipped measurement: "Final (cloud, cloud reader)"

Code `26ace78` (includes Task 22a's review fix round `9333cd7`), 2026-10-20, scratch database, fake web only in variant 9. The reader was confirmed as `gemma4:31b-cloud` (a pre-flight call, a log of every reader call, and `ollama ps` sampled every 15 s: only the embedding model was ever loaded, never `llama3.2`). Variant 9, `--repeat 3 --web-fixture`: **38/42** passed. Variant 50, `--repeat 3`: **34/45**. No provider error, so no re-run.

| §2 target | Before Task 22 ("Final (cloud)", 3b reader) | After Task 22, 3b reader | **Shipped, cloud reader** | Met |
|---|---|---|---|---|
| ≥ 90% right-document citations on cases with a document answer | v9 29/33 = 87.9% (re-scored with the product's parser 30/33 = 90.9%); v50 28/38 = 73.7% and 28/39 = 71.8% (re-scored 32/38 = 84.2% and 31/39 = 79.5%) | v9 30/33 = 90.9%; v50 31/39 = 79.5% | **v9 29/33 = 87.9%; v50 32/39 = 82.1%** | **Missed** (v9 by one turn: a `next-work` turn that never cites DOCS_INDEX.md) |
| The exact command on the code case | 26 of 27 turns | v9 9/9; v50 9/9 | **18/18** (v9 9/9, v50 9/9; `start-command-50` 3/3) | Met |
| 0 unapproved web calls | 0 of 132 turns | 0 of 87 | **0 of 87** (42 + 45; the 3 approved turns made 6 calls, a search and a read each) | Met |
| 0 invented pages on gap cases | 2 of 9 gap turns | 1 of 6 | **1 of 6** (v50 run 1: a page cited for the part the documents don't cover; v9's three: none) | **Missed** |

**Missed: the citation rate (87.9% and 82.1%) and the invented pages (1 of 6).** The v9 gain between the first and second column is the measurement change (Task 22 d counts a joined citation as the product does), not the model.

| Kind | v9: 38/42 | v50: 34/45 |
|---|---|---|
| overview (`features`) | 0/3 | 0/3 |
| procedure (`deploy-and-test`) | 3/3 | 3/3 |
| code | 9/9 | 9/9 |
| exact-label | 6/6 | 6/6 |
| named-doc | 6/6 | 6/6 |
| balance | 5/6 | 3/6 |
| gap-general (`rhel-packages`) | 3/3 | 0/3 |
| live (`ollama-release`) | 3/3 | 3/3 |
| web-read | 3/3 | — |
| map-shortened | — | 4/6 |

- **The RHEL answer shape (§2): text and card in 4 of 6 runs** (v9 3/3; v50 1/3). In the other two v50 runs the model read the documents, wrote an answer and ended with no card and no offer, in words or by the tool. All six said the documents don't cover it and cited the documents' own requirements; general knowledge was introduced as such in 2/3 (v9) and 1/3 (v50), by `EVAL.md`'s reading rather than a score; one v50 run cited an invented page. The official `gap-general` score (v9 3/3, v50 0/3) fails the v50 answers because the eval's refusal wording lacks "list" ("The documents do not list…"); with "list" accepted, a score computed from the saved answers with the scorer unchanged, it is 3/3 and 2/3. Changing a scorer after seeing the answers needs the user's eye, so it was not changed (L491).
- **"What are the features?": 1 turn of 9 passes** (v9 0/3; v50 `features` 0/3, `features-50` 1/3). The terms are found, but each turn cites one document (two in v50) where three are needed. Task 22's item (c), a prompt sentence for whole-project questions, was measured with the 3b reader and **removed**: `features` stayed 0/3 and `deploy-and-test` fell from 3/3 to 0/3 (the model read pages and skipped the search). Untried, per `EVAL.md`: an overview the app builds from each document's opening, one search per area name, or following the README's own "see also" links.
- **Source chips:** the expected document was named in 30/30 (v9) and 33/33 (v50) cases, the expected page in 15/15 and 18/18. `web-read` showed a web source chip in 3/3 turns (the fake `docs.marrowdeck.example`), each with 2 web calls. With the 3b reader the same chip showed in 10 of 12 turns, against 9 of 9 for the old brief; with the cloud reader it is 3 of 3, which says the cloud reader keeps the chip, not why the 3b sometimes didn't.
- **Still failing at 50 documents:** `next-work` 0/3 here (0/6 before Task 22, 0/3 on the shipped 3b-reader tree, 1/3 with item (c)); with 41 padding documents the "What's next" sections win the ranking and the pointer to `HANDOVER.md` in DOCS_INDEX.md is never relayed. There is no reranker (see deferrals).
- **Not measured with the cloud reader:** the reader keeping verified facts on `found: false` (unit tested only), and `extract_from_documents` (called in 0 of 87 turns).

### What Task 22 moved (3b reader, like-for-like; `EVAL.md` "Final (cloud, after Task 22)")

Variant 9 36/42 and variant 50 35/45 on the shipped tree (33/42 and 35/45 with item (c)).

- **(a)** The RHEL question in variant 9 went from the card alone (3 of 3, no text) to text and card, 3 of 3 (5 of 6 in a diagnostic); `ollama-release` went from the card alone to one sentence plus the card, 6 of 6, with 0 web calls.
- **(b)** Variant 50: 0 of 6 RHEL turns offered in words (before: 2 of 3 in one attempt). They offered no card either, so the consent dead end is gone and the offer is still missing.
- **(b2)** Every RHEL turn that offered searched the documents first, 11 of 11, against the offer as the only call in 3 of 3 before.
- **(d)** `session-lifetimes` 2/3 → 3/3 (v9) and 0/3, 1/3 → 3/3 (v50), plus one `deploy-and-test` and one `features` turn: a measurement change, not the model.
- **(h)** Unit tested; `web-read` did not move (2/3 shipped, 3/3 with (c); diagnostic 5/6 for both briefs).
- **(o), (g)** Unit tested only; the eval exercises neither (it opens no document and sets no web-search variable).

### How it got here (layer by layer, from the baseline)

Variant 9 is 3 runs unless noted; "right-doc" is right-document citations. Until the strategy layer the pass count mostly measures citation form (a bare `(page 3)` against `(Name, page N)`), so read the facts tables in `EVAL.md`, which every layer carries. Every row but the last used the 3b reader.

| Layer | v9 passed (right-doc) | What moved |
|---|---|---|
| Baseline, scope widened only (`2ad554d`) | 6/39 (6/33); v50 1/15 (1/13) | Rules still said "the open document"; most failures were citation form. |
| Code chunks | 6/39 (6/33) | Code cases found their fact 9/9 (baseline 3/9). |
| One ranking + balance | 2/39 (3/33) | `next-work` recovered to 3/3 once partial matches counted a tenth. |
| Table cells | 1/39 (1/33); same-day control with the old extraction 4/39 (4/33) | `oidc-issuer` 3/3 in one round; `next-work` 3/3 → 0/3 (one big table's chunks moved). Accepted trade (L165). |
| Map | 4/39 (4/33); v50 1/15 (1/13) | `document_outline` was offered and not called. |
| Web (one run) | 3/14 (2/11) | `web-read` passed: `4.18.3` found past 6,000 characters, the near-miss `4.17.3` avoided. |
| Strategy | 36/42 (28/33); v50 10/15 (10/13) | The rules now ask for `(Name, page N)`. Single-document eval 24/27 → 27/27: only `live` moved (it now offers). No non-live case dropped. |
| Citations (one run each) | v9 11/14 (9/11); v50 12/15 (10/13) | The model's text was unchanged; the difference is noise. Chips named the right document 10/10 and 11/11. |
| Final (cloud) | 37/42 (29/33); v50 29/45 and 32/45 (28/38, 28/39) | Joined citations scored as none; see the targets table. |
| After Task 22 | 36/42 (30/33); v50 35/45 (31/39) | See above. |
| **Shipped, cloud reader** | **38/42 (29/33); v50 34/45 (32/39)** | See above. |

### Search timing (Task 7, post-fix, 50 documents × 200 chunks, random vectors, query embedding not included)

p50 33.4 ms, **p95 122.5 ms** overall against the 300 ms target; slowest shape (a 32-word query) p95 126.6 ms. A 250-document library (50,000 chunks): p95 165.9 ms. The target held for every query shape in every run. The first implementation read p50 48.1 / p95 106.8 ms on 4-word queries but took 360–650 ms on 32-word ones; the review fix round fixed that (L129, L137).

### Rebuild timing (what an existing library pays on its first project chat)

Set up with the real old code (the v2.3.0 extractor and pipeline), not by editing a version string: the string-only control embeds nothing (0.5 s).

- 9 fixture documents, 793 old chunks → 846 new. **15.2 s** and **15.0 s** in two runs, in the background; the turn waited **5 ms** and **2 ms**.
- **136 of 843 distinct new texts were embedded (16%)**: 52 code chunks and 84 table chunks (the table text changed because cells are now spaced). 707 (84%) kept their vector. No prose chunk was re-embedded. For scale, indexing the same nine documents from nothing with the old code took 51.9 s and 51.7 s: the upgrade costs about 29% of the time and 17% of the embeddings.
- CPU only (the GPU on this machine is disabled), local `nomic-embed-text`, machine busy (load 5.6–7.1), so read the seconds as an upper estimate. Not measured: a project of many documents (the rebuild is serial; time scales roughly 0.1 s per changed chunk here), several users at once, a GPU or hosted embedder, the quality of a turn that lands mid-rebuild. During the rebuild the old chunks keep answering (read from `ensure_current`, not tested).
- **Before upgrading a live database** (L125): run `SELECT embedding_model, count(*) FROM doc_chunks WHERE embedding IS NOT NULL GROUP BY 1`. Rows with a NULL `embedding_model` are re-embedded during the upgrade. The revert route (`routers/docs.py:974`) deletes chunks but leaves `doc_outlines`; the map ignores outlines of documents that aren't `indexed`.

<!-- LOCAL RUN: filled by the controller from EVAL.md when the gemma4:e4b run is made. It is owed by plan Task 21 step 1. It waits for the user's go-ahead (L492); when it runs it must set READER_MODEL=chat so only one local model is in RAM. -->

**Not measured yet:** the local-model run (the plan's final `gemma4:e4b` variant-9 run), a real SearXNG (both walks used a fake; the re-walk's snippets were copied from each page's own opening text, the first walk's were hand-written and more specific than the pages), and `extract_from_documents` and the keep-verified-facts reader change with a cloud reader.

## Running-app walks

Two walks, both against a running app: the first of the finished feature (J1–J9), and a targeted re-walk after Task 22 (W1–W5).

### First walk (J1–J9)

Walked 2026-10-19/20 against `ec297f4`; HEAD moved to `cc5dfac` during the walk with docs-only changes, so the walked code equals HEAD at the time. Set-up: a throwaway Keycloak on `127.0.0.1:18081` (never the compose one), scratch database `natural_reader_walk`, the backend under `env -i` with explicit values, a fake SearXNG returning three real public documentation pages, `gemma4:31b-cloud` through local Ollama (reader `READER_MODEL=chat`), local `nomic-embed-text`, headless Chromium through Playwright (DOM `click()` on controls, real key presses). Project "Natural Reader docs" was built through the UI (nine fixture files, one added member). Set-up-only edits: two pre-enrolled users and TESTING.md's state set to `indexing` for J6. Everything was removed afterwards; the privacy check of the walk's server and audit logs found only the deployer's own `BOOTSTRAP_ADMIN_EMAIL` line.

**Every J1–J9 step had a UI control; none needed the API.** Seven journeys pass, two are Partial, both on answer quality.

| # | Journey | Controls used | Result |
|---|---|---|---|
| J1 | Overview question | Project page **Ask about this project**; composer; citation link; source chip | **Partial.** Every control works (the citation opened README.md at 1/10; the "Source: page 5 of USER_GUIDE.md" chip opened it at 5/6). The answer cited README.md only, 3 of 3 tries (finding 7). |
| J2 | Cross-document question from the reader | Documents tab **Open DEPLOYMENT.md**; **Chat beside the page**; **New chat**; scope chip → "Natural Reader docs" | **Pass.** A two-phase step list citing DEPLOYMENT.md pp. 1, 2, 5 and TESTING.md p. 1; the joined citation became two links; the TESTING.md link swapped the reader to that page. |
| J3 | RHEL 9 question, offer, edit, search | **Ask about this project**; card's **Web search query** field; **Search the web** | **Partial.** Card, edit and search work, and SearXNG received exactly the edited query. But the offer came with no text and no document search first (finding 2), and the approved answer cited sites with no web source chip (finding 1). |
| J4 | Not now | Card **Not now** | **Pass.** 0 `/v1/` requests in the next 4 s, stored message count unchanged, no SearXNG query, focus back to the composer; the next message was answered normally. |
| J5 | Removed from the project | Members tab **Remove** (Olive); composer (Max) | **Pass.** Toast "This project doesn't exist or you don't have access."; chip → "No document"; the text stayed in the composer; old transcript kept; `scope_project_id` NULL. The removal itself had no confirmation (observation 6b). |
| J6 | A document still indexing | Documents tab; **Ask about this project**; composer | **Pass.** Tab showed "TESTING.md · indexing"; the answer said it isn't searchable yet; the tool error named document 8. |
| J7 | Another device | Chat view → session row | **Pass.** Every project chat carried the project label; the chip read "Project: Natural Reader docs"; the transcript, offer line and web answer loaded. |
| J8 | Phone, J1 from the Chat view | Chat mode; **Show Sidebar**; **Model**; chip → **Choose a project…**; composer; citation; card | **Pass.** No horizontal scroll (390 px); chip and menu items 44 px. The card's query field was 39 px (observation 6e). Once a document had been opened, the floating **Read Selection** covered Send (finding 3, pre-existing). |
| J9 | Admin chat options | Admin console → **Chat options** radios; **Use the server setting**; rules preview | **Pass.** *Search without asking*: no card, searches directly; *Off*: no card, "I cannot look up live information"; reset restores the original, byte-identical preview; `CHAT_WEB_SEARCH=off` at restart shows "From the server setting CHAT_WEB_SEARCH." |

Extra checks from task reviews, all **Pass**: in the reader side panel, Space and Escape on the scope chip and its items, and on the offer card's field and **Not now**, never reach the narration shortcuts. Space activates an item in all four other menus once focus is inside; Escape from the opener of the profile, header and PDF-toolbar menus stopped narration (finding 4), and the project row's ⋯ menu doesn't close on Escape (finding 5).

### Findings 1–7 of the first walk, and where they stand

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | The web reader drops a page that answers only part of its goal, so approved web answers get no web chips. Cause: the reader brief's "If the passages don't have it, return found false" is read as the whole goal; a three-part need returned `found: false` 5 of 5 times, the same page with a one-part need was found 3 of 3. The tool line then says "Read 2 page(s)" when nothing came back. | Important | **Fixed, Task 22a** (item h, `8d65886`; `9333cd7` keeps every verified fact). Re-walked: the approved search showed a **docs.redhat.com** chip, and the Python fact was followed by its site (W1). The walk's reader was the cloud model, so this finding is not an artefact of the 3b. |
| 2 | In `ask` mode the RHEL question goes to an offer without a document search (2 of 2); in `auto` it answered from README pp. 3–5. The ask line has no "find it in the document first". | Important | **Partly fixed, Task 22a** (items b2 and a, `8d65886`). Re-walked: the one reply that offered searched the documents first and wrote text before the card. But 3 of 4 chats offered nothing (re-walk finding 1). |
| 3 | The floating **Read Selection** button covers Send once a document has been opened (pre-existing). | Important | **Fixed in the Chat view, Task 22b** (item j, `7c7fb75`). Re-walked with real mouse clicks (W5): absent in Chat at 1366 and 390 px, Send reached. Still shown over the Library view. |
| 4 | Escape on an open menu's opener closes the menu and stops narration (profile, header overflow, PDF toolbar menus). | Minor | **Fixed, Task 22b** (item k, `7c7fb75`; `b841d16` for the case where focus is on the page). Re-walked (W5): narration kept playing. |
| 5 | The project row's ⋯ menu can't be closed with Escape or an outside click (pre-existing). | Minor | **Fixed, Task 22b** (item l, `7c7fb75`). Re-walked (W5). |
| 6a | No control to upload a brand-new file on the project Documents tab (pre-existing). | Observation | **Not fixed.** |
| 6b | Removing a member is one click, no confirmation (pre-existing). | Observation | **Not fixed.** |
| 6c | After **Use the server setting**, focus falls to `<body>`. | Observation | **Fixed, Task 22b** (item n). The 22b implementer found a real mouse click already landed on the checked radio; only a DOM `click()` or Safari fell to `<body>`, so the walk's report was partly a measurement artifact. Re-walked with a real click (W5): focus on the checked radio. |
| 6d | The model twice called `read_document_pages` without `document` in project scope; each cost a tool round. | Observation | **Fixed, Task 22a** (item o, `8d65886`). Unit tested only: neither the eval nor the re-walk opened a document in a project chat. |
| 6e | The offer card's query field is 39 px at 390 px. | Observation | **Fixed, Task 22b** (item m). Re-walked (W5): 278×44. |
| 6f | Pre-existing, seen again: the reader's "Project for this document" shows "No project"; the reader header is 442 px wide at 390 px; status stays "Reading..." while paused. | Observation | **Not fixed.** |
| 7 | "What are the features?" cites one document (README.md) in 3 of 3 tries. | Minor | **Not fixed.** Item (c) was measured and removed (`features` stayed 0/3, `deploy-and-test` fell 3/3 → 0/3). Re-walked (W2): README.md page 1 only, again. |

### Re-walk after Task 22 (W1–W5, 2026-10-20)

Walked 13:03–13:33 against `26ace78`; `cd0e956` (EVAL.md only) was HEAD when the backend started, so the walked code equals HEAD. Same rules as the first walk (throwaway Keycloak, scratch `natural_reader_walk`, backend under `env -i`, cloud model only, fake SearXNG, headless Chromium), with two differences: controls were operated with **real mouse clicks and locator clicks**, which fail when another element covers the control (the first walk used DOM `click()`, which skips hit-testing), and the fake SearXNG's snippets were copied from each page's own opening text. The nine documents went into the project through the API from Olive's signed-in context (set-up only); the project and the member were made through the UI. Everything was removed afterwards. Mode was `ask` throughout.

| Step | Controls used | What was seen | Result |
|---|---|---|---|
| W1. RHEL question: documents first, text then card, reload, approve | **Ask about this project**; composer; card **Web search query** field → **Search the web**; **Sessions** row after a reload | In the one reply that offered: `search_documents`, then `offer_web_search`, text citing README.md page 3 that says what the documents lack, a section headed "General Guidance (Not from documents)" with no pages, and the card under it (stream: text deltas → `data-web-offer` → `data-sources` → `finish`); the text doesn't ask whether to search. Reopening the session row after a reload showed the same text and card. Approval sent exactly the edited query one second after the click and showed a **docs.redhat.com** source chip; "`sudo dnf install python3.12` (docs.redhat.com)" carried its site. The Node.js and Podman lines carry no site and no label (re-walk finding 3). | **Partial**: every step that happened works; the offer happened in 1 of 4 chats (below) |
| W2. Features | **Ask about this project**; composer | Long, accurate answer; every citation "(README.md, page 1)". Searched, not cited: ARCHITECTURE.md, CHAT_WITH_PDF.md, USER_GUIDE.md. | **Partial**, as in the first walk |
| W3. No document, ask mode | Chat view → **New chat** → chip *No document* → "What is the latest stable Python version right now?" | The offer came first, then the answer step: "Based on my general knowledge … this information may be out of date", no page citations, the card under it. | **Pass** |
| W4. Stop, and a typed request | **Stop generation** during the answer step; composer "search the web for RHEL 9 python packages" | Stop: the reply kept its text, marked **Stopped**, no card and no "Offered a web search" line, also after a reload (saved `status: aborted`, no `web_offer` note); the saved text ran a few words past what the screen showed. Typed request: a card appeared, prefilled "RHEL 9 python 3.13 packages" (not the typed text) in the chat that held the stopped reply, and exactly "RHEL 9 python packages" in a fresh chat. | **Partial**: Stop as documented; the paraphrase happened once in 2 tries |
| W5. Task 22b fixes | walkadmin: Admin console → **Open Natural Reader docs**; Max (reader): Library → Projects; Chat view and reader at 1366 and 390 px; the three reader menus by mouse; the row ⋯ menu; Admin → Chat options → **Use the server setting** | (e) the non-member admin has no **Ask about this project**, a Reader member has it and it opens a chat with the project chip. (j) Read Selection absent in Chat at both sizes, Send is the element at its own centre (1185,846 and 330,720); in the reader with the side panel and a text selection it sits clear of the panel's Send. (k) in the three menus Escape closed the menu, focus stayed on the opener, narration kept playing (counter 3 → 11 of 37), Space on the opener opened the menu without toggling play. (l) `aria-expanded` follows the ⋯ menu; Escape and an outside click close it. (m) query field 278×44, no horizontal scroll. (n) after reset, focus on the checked "Ask first" radio; the audit has `old=ask new=auto`, then `old=auto new=None`. | **Pass** (e, j, k, l, m, n) |

**W1 count.** The same first question, "I'm a newcomer on RHEL 9. Which packages do I need to install to run this?", asked in four project chats: **text plus card 1, card only 0, text only 3**. In the three text-only chats the model searched twice and read pages, said the documents lack a RHEL 9 package list, and gave no card; none asked in words whether to search. The card-only reply that both J3 runs got in the first walk never appeared.

**Set next to the eval:** the eval's RHEL case had text and card in 4 of 6 runs (v9 3/3, v50 1/3; 3 of 6 with the 3b reader). The walk's 1 of 4 is lower, and four chats can't separate the two. The walker's guess, **untested in the app**: the walk's wording ends "to run this", which reads as a question the documents answer, so the model sees no gap and makes no offer, while the eval asks "I'm on RHEL 9, which packages do I need?". Nobody asked the eval's wording in the app. The controller ruled (L530) that no further prompt iteration happens in this plan for this case or for `features`, because each wording change needs a full eval cycle and has moved these cases within noise twice; the user's working path is typing "search the web for X".

**Privacy count.** The fake SearXNG logged **1 query** in the whole re-walk, one second after the only **Search the web** click (the edited card query). **Unapproved: 0.** The other five offers (W3, the stopped W4 offer, the two W4 typed-request cards and the fresh chat's card before approval) sent nothing. The server and audit logs were searched for the walk users' emails, the queries' words, the fetched sites and the project name; the only hits were the deployer's own bootstrap-admin line and a `site-packages` path.

**Re-walk findings** (numbered separately from the first walk's):

1. **Important, open: in a project chat the RHEL question usually gets no offer** (3 of 4 chats; the W1 count above).
2. **Important (answer quality): 2 of 4 RHEL answers gave stale prerequisites.** The first chat and fresh chat 3 read `CHAT_WITH_PDF.md` page 1 and answered "Node.js 18+", "Python 3.10+" and "Docker", with the page cited correctly, while README.md says Node 20.19+ or 22.12+ ("older Node will fail to start") and Python 3.12–3.13. A newcomer who followed it would install a Node and a Python the app refuses. **Fixed at the source in `b8bc2df`** (the doc now matches `startup.sh`); the frozen eval fixture copy still has the old numbers. Not fixed, and the walker's reading rather than something verified: nothing in the map or the rules tells the model which of two disagreeing documents is current.
3. **Minor, open: in the approved web answer, lines not taken from a page carry neither a site nor a general-knowledge label** (the Node.js and Podman dnf commands). Only result 1 was read; reading result 2 would have given a site. Not checked: whether the approved-turn rules ask for a label.
4. **Minor, open: "search the web for X" brings the card, but X can change** (the prefill is the model's own `offer_web_search` call; no code copies X into it). Consent holds: the user can edit the query, and nothing was sent before the click. The answer under that card in the paraphrased case also said "Since I am currently waiting for the search results", though nothing runs until the click.
5. **Minor, open: after Stop, the saved text runs a few words past what the screen showed** (inferred: the server kept deltas produced before the abort reached it).
6. **Minor, pre-existing, open: a browser reload opens a new empty chat**, not the one you were in. It is one click away in **Sessions**; the J7 path is the same.
7. **Minor, pre-existing, open: on the phone, the open sidebar covers its own Hide Sidebar button** (a mouse click can't reach it; tapping the dimmed backdrop closes the sidebar). The first walk used DOM `click()` there, which can't see this.
8. A walk note, not a product finding: one wrong locator of the walker's collapsed a section and flipped a chat's chip; nothing was sent from that state.

**Not checked in the re-walk:** the exact text of the offer note as sent to the model (the stream doesn't carry it; its third form is inferred from W3's wording and `offer_note()` in `server/chat/web_consent.py`); the single-document offer note; budget-exhausted, provider-failure and Stop-on-an-approved-turn paths; the chat side panel at phone width; the PDF toolbar menu with a real PDF (tested with README.md at 700 px); the RHEL count with the eval's wording; **Not now** (the first walk's J4 covers it).

### Task 22 (added after the final eval and the first walk)

Brief: `.superpowers/sdd/2026-10-18-project-chat/task-22-brief.md`; ruling at L442.

| Item | What | Closes | Status |
|---|---|---|---|
| a | An offer round that wrote no text gets one tools-off final step, so the model writes what the documents say and don't before the card (the note text is fixed; no web tool runs; a provider failure there still ends with the offer) | RHEL answer shape | Task 22a, done (`8d65886`) |
| b | The step-1 offer line and the approved-turn line end "Don't ask in words whether to search: offer_web_search asks the user." | v50 offer in words, a consent dead end | Task 22a, done (`8d65886`) |
| b2 | The step-1 offer line says to search the document(s) first when they may cover any part of it | finding 2 | Task 22a, done (`8d65886`) |
| c | A project step-2 sentence for whole-project questions. Kept only if v9 `features` passed in 2 of 3 runs and nothing else lost a pass beyond noise | finding 7 | Task 22a: **measured and removed** (code, golden lines and test; `features` 0/3, `deploy-and-test` 3/3 → 0/3) |
| d | The eval counts citations with the product's `cited_pages` parser; "Final (cloud)" is re-scored under it | joined citations scored as none | Task 22a, done (`8d65886`, `61a0463`) |
| g | `WEB_SEARCH_*` numbers warn and default (see the configuration table); the "not checked" caveats dropped from DEPLOYMENT.md and `.env.example`; three doc nits from Task 20 | Task 20 review | Task 22a, done (`8d65886`) |
| h | Reader brief reworded to keep partial answers; `read_web_pages` summary counts only pages that gave facts | finding 1 | Task 22a, done (`8d65886`, `9333cd7`) |
| o | A missing `document(s)` in project scope means the open document, when it is one of the project's searchable documents | observation 6d | Task 22a, done (`8d65886`) |
| f | Re-run cloud v9 ×3 `--web-fixture` and v50 ×3 | — | Task 22a, done: "Final (cloud, after Task 22)" (`61a0463`, 3b reader) and "Final (cloud, cloud reader)" (`cd0e956`, the shipped one) |
| review fixes | From the 22a review (opus), fix round 1: a third offer note for chats with no document (and a project with no searchable one); a notice when the daily budget is used up before the answer step; the reader keeps every verified fact; a USER_GUIDE line that Stop loses the card | review Important 1, Minors 1–4, 6, 7 | Task 22a, done (`9333cd7`, `26ace78`); Minors 5 and 8 parked |
| e | **Ask about this project** only for members with `chat` (uses the project detail's `my_role`; no new request) | Task 20 review | Task 22b, done (`7c7fb75`); re-walked (W5) |
| j | **Read Selection** hidden in the Chat view; a selection being read keeps a Stop pill at the top; beside the side panel it never overlaps (chosen because the composer's height varies, so any "above it" spot is a guess) | finding 3 | Task 22b, done (`7c7fb75`); re-walked |
| k | Menu openers keep their keys (`data-own-keys` on each menu's wrapper; a shared `useMenuDismiss` hook; Escape returns focus to the opener and no longer reaches the narration shortcuts) | finding 4 | Task 22b, done (`7c7fb75`, `b841d16`); re-walked |
| l | `ProjectDocRow` ⋯ menu: `aria-expanded`, Escape, outside click | finding 5 | Task 22b, done (`7c7fb75`); re-walked |
| m | Offer-card query field 44 px under 768 px | 6e | Task 22b, done (`7c7fb75`); re-walked |
| n | Focus returns to the checked radio after **Use the server setting** | 6c | Task 22b, done (`7c7fb75`); re-walked |

How the items were checked: 22b by unit tests (vitest 1010 passed, 106 files) and one-off mocked-backend browser runs, then by the re-walk with real clicks against the full app; 22a by unit tests, the eval and the re-walk (items a, b2 and h in W1; item o and the third offer note's exact text were not seen in the app). The jsdom tests can only assert class names and conditions, and no test renders the real `App`.

## Rulings

Decisions the user made are marked "User:" in the spec (§1–§3, §5.4, §5.8, §5.9) and aren't repeated here; the spec's §11 lists the "Our call" choices still open to check. The five rulings made while planning are in the plan (not the spec); the spec only carries R1 and R2 inline, as "Amended" notes.

### R1–R5 (plan, "Rulings made while planning")

- **R1.** `read_web_pages` accepts result numbers from web searches in the *same turn*, not "this chat" as the spec first said. Reason: in `ask` mode only an approved turn has web tools, and the history sent to the model carries only text, so an earlier turn's number would name a result the model was never shown; in `auto` a later turn searches again. Spec §5.8 amended.
- **R2.** Code chunks are interleaved in document order (`ord` follows the document), not appended. Reason: a procedure reads in order, with each command beside the sentence that introduces it. The reader-equality test compares the non-code projection and checks `ord` runs 0..n-1, so the segmentation fixtures stay untouched. Spec §5.3 amended.
- **R3.** `ToolContext.evidence` becomes `(doc_id, page, text)` triples in Task 3. Document-scope source chips filter to the open document until Task 15 generalises them. The plan states no reason; the triples are what Task 15's per-document chips group on.
- **R4.** Session meta uses camelCase like its other keys: `scopeProject: {id, name} | null`. Reason: consistency with `messageCount`, `updatedAt`. Spec §5.12 amended (and `name` is `null`, with the key still present, when the project is no longer visible).
- **R5.** A turn request *without* `context.project_id` keeps the session's stored scope (re-checked); an explicit `null` clears it. The plan states no reason; the client always sends the key (null when none), so the "keeps" case is for other API callers.

### Rulings made during execution (the ledger's "Ruling:" lines, in ledger order)

Each entry: what was decided, why, and the cost if it was wrong. "Cost not stated" means the ledger line gave none. `L###` is the ledger line.

**Pre-flight (L46–L49)**

1. (L46, F1) Task 9 adopts Task 14's exact data-rule sentence (three tags) and updates `GOLDEN_ALL` in the same commit. Why: the "golden updated in the same commit" constraint can't hold otherwise. Cost: one extra golden edit.
2. (L47, F2) Task 13 updates existing tests that assume `web_search` is offered by default (set `CHAT_WEB_SEARCH=auto` there, or assert the offer tool), in its own diff. Why: the default must be `ask`, per the user. Cost: test churn.
3. (L48, F3) Task 11's `page_texts` fences a run of consecutive `code`/`code+cont` chunks as one block. Why: a split command must not show as two fences. Cost: cosmetic. (As built, L231: one fence per code block, a chunk plus its `code+cont` parts; two source blocks stay two fences.)
4. (L49, F5) Evals run on the throwaway Postgres (`natural_reader_eval` on :5434) with `ollama:gemma4:31b-cloud`; the final local run uses the local gemma4 tag (this line writes `ollama:gemma4:latest`); a failed cloud call makes the task report the eval as not run. Why: no code task waits on the network. Cost: a layer's number is missing until re-run.

**Task 1: eval fixture and scoring (L54–L57, L63)**

5. (L54) `gap-general` must require `offered` (mode `ask`). Why: spec §9 says it "offers a web search". Cost: a stricter gap score.
6. (L55) `overview` passes on spec terms: at least 3 distinct fixture documents cited, no wrong-document citation, and at least ⌈2/3⌉ of its expect terms. Why: spec §9 "mentions ≥ N listed features; cites ≥ 3 documents". Cost: N = 2/3 is the implementer's number.
7. (L56) `Case` gains `pages` (expected `(doc, page)` citations) for exact-label and code cases, computed in tests from the frozen fixture. Why: spec §9 "right document and page". Cost: one more field.
8. (L57) `padding_docs` defaults to n = 41, so variant "50" has 50 documents (9 + 41). Why: spec §9 said 30 padding but names a 50-document variant, and the map must be shortened there. Cost: a slower 50 variant. Spec §9 amended in Task 20.
9. (L63) For `named-doc`, "cites only that document" wins over the plan's letter: wrong = cited − docs. Why: spec §9. Cost: a stricter case.

**Task 2: migration (L74)**

10. (L74) Keep the unrequested partial index `chat_sessions_scope_project_idx`. Why: `ON DELETE SET NULL` would otherwise scan `chat_sessions` on every project delete. Cost: one small index.

**Task 3: project scope (L86–L87)**

11. (L86) Carry review minor 1 into Task 8: in document scope `read_document_pages` ignores the `document` value (returns scope[0]). Why: small models shouldn't waste a round on a near-name, and single-document chats must not regress. Cost: none.
12. (L87) Carry review minor 3 into Task 8: search results list only the documents their passages (or already-shown notes) refer to, not every searchable document. Why: about 3k characters a call at 50 documents would eat the 24k turn budget; the map (Task 9) lists them all. Cost: the model sees fewer names per result.

**Task 4: runner and baseline (L96, L97, L101)**

13. (L96) The eval script always uses its own temporary `DOC_STORAGE_DIR`, removed afterwards, unless `--storage-dir` is given; `.env`'s value is never used. Why: the first baseline run wrote 50 document files into the app's real `./data/pdfs` (the 41 synthetic ones were moved to a quarantine folder; the 9 public-doc copies were left, and can't be proven to have been absent before). Cost: none.
14. (L97) The controller commits Task 4 after the fix. Why: the user granted per-task commits for this plan (2026-10-18); the implementer had declined to commit under the per-action rule. Cost: not stated.
15. (L101) Before Task 14 runs `scripts/eval_doc_qa.py`, Task 14 adds the same two guards to it (refuse a `DATABASE_URL` naming `natural_reader` or unset; own temporary `DOC_STORAGE_DIR`). Why: the old script writes fixtures into `./data/pdfs` and has no DB guard. Cost: none.

**Task 5: extraction (L108, L110)**

16. (L108) Only top-level code blocks become code chunks; a fence inside a list item or blockquote stays in that block's prose. Why: chunking it too would duplicate text, removing it would move the reader's layout. Cost: a list-nested command loses its line breaks.
17. (L110) From now on the controller commits each task. Why: subagents decline under the user's per-action rule, and the user granted per-task commits to the controller. Cost: not stated.

**Task 6: pipeline (L118, L119, L124)**

18. (L118) An outline's `source` is `none` whenever its entries are empty, for any file type. Why: so `source` is truthful. Cost: none.
19. (L119) `_reusable_vectors` also filters rows by `embedding_model` = the current model. Why: belt and braces over the document profile. Cost: none.
20. (L124) Carry into Task 9: the map reads outlines only for documents in state `indexed`. Why: the revert route deletes chunks but leaves `doc_outlines`, so a stale outline can outlive its chunks. Cost: an unindexed document shows no outline, which it couldn't use anyway.

**Task 7: one ranking (L133–L135, L138, L141)**

21. (L133, "Important 2, plan-mandated") Partial word matching counts only *informative* words: found in at most half the scope's chunks, at most the 12 rarest; none informative means no partial list; threshold ⌈m/2⌉ of the m informative words. Why: the spec says "distinct words", but counting "what is the" lets unrelated chunks in (Meilisearch's frequency strategy, which spec §12 cites). Cost: a query made only of common words gets no partial match.
22. (L134, "Important 1") `matched` is computed once per candidate, and the benchmark gains a 20+ word shape that must also meet p95 < 300 ms. Why: it was computed twice per chunk and long queries were slow. Cost: none.
23. (L135, "Important 2/3") Rows carry `word_match` = `all` / `partial` / NULL (`by_words` stays true for both); Task 8 applies the cosine-floor bypass only to `all`, labels `partial` "some words", and adds a tool test through the real search path. Why: partial matching is already live in single-document chats via `search_chunks`. Cost: none.
24. (L138) A word that no chunk contains is not informative. Why: otherwise unknown names in a long question fill the 12 slots and raise the bar. Cost: a typo'd rare word never counts.
25. (L141) Carry into Task 8: the partial pool orders ties by `hashint8(id)`, not `id`, and the query's lexemes are capped at 64 before the frequency lookups. Why: id order silently favours the oldest documents; 200 or more distinct words exceeds 300 ms. Cost: none.

**Task 8: search tool and prefetch (L146, L150, L155, L157)**

26. (L146) The partial word list enters fusion at half weight (0.5/(K+r)). Why: a partial match is weaker evidence than meaning for sentence-like queries, while all-word label matches stay strong. Cost: a label buried in a long question ranks a little lower. (Superseded by the next one; it still left `next-work` at 0/3.)
27. (L150) `PARTIAL_WEIGHT = 0.1`. Why: a probe on the eval DB brought DOCS_INDEX back to rank 4 for "next piece of work" and the OIDC chunk to ranks 1–3; partial matches become a light tie-breaker without outranking meaning. Cost: a label inside a long question gains little from words; the eval decides.
28. (L155) Add Task 8b: extraction keeps table cells apart with whitespace in the server's chunk text, while page packing keeps using the glued reader text; bump the chunker version. Why: cells were glued (`OIDC_ISSUERhttp://…`) so word search couldn't find env-var names, and the user's example questions are about settings in tables. Cost: one small extraction change.
29. (L157) `search_documents`' `documents` is ignored in document scope. Why: same reasoning as `read_document_pages`. Cost: none.

**Task 8b: table cells (L165)**

30. (L165) Accept the trade: exact names in tables become word-findable (systematic), while `next-work`'s loss (3/3 → 0/3) is chunk-boundary sensitivity of one large table's meaning rank (incidental); re-check at the Task 9 eval. Cost: one case stays lost until row-aware table chunking.

**Task 9: map (L174)**

31. (L174) Map shortening steps become L3 → L2 → L2 first 8 → L1 first 8 → names and pages (the spec said L3 → L2 → first 8 → names). Why: a Markdown outline has one level-1 heading, so dropping level 2 before capping made "first 8" a no-op. Cost: revert one tuple. Spec §5.2 amended. Correction at L178: neither the 9- nor the 50-document fixture reaches that step at 6000 characters (v9 stops at "drop level 3", v50 at "level 1 capped"); kept on principle.

**Task 10: reader (L188–L190, L196, L209, L213)**

32. (L188) The reader's timeout covers the model call only (the semaphore is taken first). Why: a batch queued behind slow ones mustn't time out unstarted. Cost: a slow provider holds a turn up to batches × timeout ÷ concurrency.
33. (L189) `ReaderResult.found` = any verified fact; errors give `found` false and no facts. Why: the chat model must never see an unverified fact. Cost: none. **Superseded in part by entry 123 (L496):** this ruling also said a model's own `found: false` makes its facts be ignored; Task 22a's fix round 1 reversed that, so every fact whose quote verifies is kept and `found` is true exactly when one verified.
34. (L190) The timeout defaults to 60 s when `READER_TIMEOUT_S` and `WEB_SEARCH_SUMMARY_TIMEOUT_S` are unset (web search's own default of 30 doesn't apply). Why: spec §5.4.1. Cost: none.
35. (L196) Verification also accepts a quote that keeps the hyphen of a line-end break (`well-⏎known` matches `well-known`) and ignores whitespace between two Han/Hiragana/Katakana characters. Why: the brief's join rule drops correct compound quotes, and CJK line breaks become spaces. Cost: a quote differing only by such a hyphen or space is accepted.
36. (L209) A verified quote must be at least 4 normalised characters, or the whole normalised passage. Why: a one-character quote ("e") verified against any passage. Cost: a 3-character quote's fact is dropped (fails safe).
37. (L213, "fix round 2") The minimum is 2 when the quote contains Han/Hiragana/Katakana (4 otherwise), and the CJK whitespace rule also covers U+3001–U+303F punctuation. Why: a 4-character minimum dropped every short CJK quote. Cost: a 2-character CJK quote can match by chance (the fact text is still model-written).

**Task 11: `extract_from_documents` (L200–L207, L217, L240, L241)**

38. (L200) `ToolContext` gains `model: str = ""`, the turn's canonical model id. Why: `READER_MODEL=chat` needs it and no tool could see it. Cost: one field.
39. (L201) The move out of `read_document_pages` is `page_chunks(conn, doc, first, last)` returning `(page, text, chunk ids)`, with `page_texts` a thin wrapper. Why: the brief's `page_texts` alone would lose the `ctx.shown` bookkeeping. Cost: one extra function.
40. (L202) A run of consecutive code chunks is joined, then fenced once, with a backtick fence longer than any backtick run inside (minimum 3). Why: a fence inside code must not close ours. Cost: cosmetic. (Built as one fence per code block; see 3.)
41. (L203) Candidate pages = per named document, its `pages` ranges if any entry names it, else all its pages; an entry naming a document not in `documents` is an error; ranges clamp to the page count and a `first_page` past it is an error; over the cap, ranked rows filtered to candidates, best first, filled in (ref, page) order if short, then sorted; empty pages are never sent and never "not read". Why and cost: not stated.
42. (L204) A page longer than `READER_INPUT_CHARS` is split into several passages with the same label before batching. Why: `batches()` would cut it silently. Cost: more reader calls on huge pages.
43. (L205) `seen_text` gets each fact's text as well as its quote. Why: facts can carry verbatim document strings and the model sees both. Cost: a web query sharing an 8-word run with a fact is refused.
44. (L206) In document scope `documents` resolves to the open document whatever it says (as `read_document_pages`). Cost: none.
45. (L207) No rules wording for `extract_from_documents` in this task (Task 14 owns it); if registering it changes `GOLDEN_ALL`, update the golden in the same change. Cost not stated.
46. (L217) A failed embed or search for page ranking falls back to the first cap candidates in (ref, page) order, with a message. Why: ranking is an optimisation, and losing the whole call to it isn't acceptable. Cost: less relevant pages on a provider hiccup.
47. (L240) Task 11's fix round 1 runs in parallel with the Task 12 implementer; the files are disjoint, the fix runs only its focused tests, and the controller runs the full suite after both commit. Cost: one re-run if a cross-file break shows up.
48. (L241) Pieces of a split page overlap by 200 characters (step `input_chars − 200`). Why: a quote spanning a cut was dropped. Cost: a few repeated characters per huge page.

**Task 12: web results and `read_web_pages` (L222–L228, L237, L238, L268)**

49. (L222) `POST /v1/tools/web_search` keeps working and returns the new shape (results, no summaries); Task 20 notes the change. Why: it is public API on an open-source server. Cost: an outside caller loses summaries.
50. (L223) `site` = the URL's hostname, lowercased, leading `www.` dropped; titles go through `display_name`, snippets through `fence()` capped at 300 characters. Why: web text is data. Cost: cosmetic.
51. (L224) Result numbering is turn-wide; a URL already in `ctx.web_results` keeps its first number. Cost: none.
52. (L225) Each page is one reader batch (its best passages, within `READER_INPUT_CHARS`), labelled `[web {n}]`. Why: keeps per-page attribution exact. Cost: none.
53. (L226) A page that can't be fetched returns `{"n","title","url","error":"Couldn't read this page."}`; `found` false stays `found` false with no facts; a reader error returns an `unread_excerpt` (fenced, ≤ 1500 characters). Why and cost: not stated.
54. (L227) Web facts go neither to `ctx.seen_text` (the copy guard protects *document* text from leaving) nor to `ctx.evidence` (document pages only); a read page is marked `ctx.web_results[n]["read"] = True` for Task 15's web sources. Cost: Task 15 adapts.
55. (L228) Remove `SUMMARY_TIMEOUT_S` along with `summarize_one` (the reader reads `WEB_SEARCH_SUMMARY_TIMEOUT_S` itself); `fetch_and_extract` is renamed `fetch_page_text`. Why and cost: not stated.
56. (L237) `page_passages` falls back to the top of the page when no passage shares a word with need + query. Cost: none.
57. (L238) An unknown result number's error lists this turn's numbers as compact ranges, or "none yet. Search first." Cost: none.
58. (L268, "replaces ruling 8's splitting") `page_passages` splits at `"\n"`, drops blank lines, packs adjacent lines into passages of about 1,000 characters and splits only a line over 1,000 (at sentence ends, then spaces, then hard); the fallback is contiguous from the top. Why: trafilatura separates paragraphs and list items with single newlines (0 blank lines measured), so the blank-line split never fired and list pages were hard-split mid-row. Cost: one function.

**Task 13: web consent (L259–L266, L274, L291, L292)**

59. (L259) `PUT /v1/admin/chat-options` also accepts `{"web_search": null}`, which deletes the row (back to env/default). Why: without it `source` can never return to `env` once an admin saves, as with the assistant profile's empty text. Cost: one branch.
60. (L260) Web modes apply to every chat, document and project scope alike. Why: the user's words, "every web search asks first, live questions included". Cost: none.
61. (L261) Approval lasts one turn; the next turn asks again. Cost not stated.
62. (L262) The offer turn keeps any text the model wrote and adds none; the card is Task 18's, and a reload shows it from the `web_offer` note. Why: server-written copy can't match the chat's language. Cost: an empty bubble until Task 18. (Superseded in effect by Task 22 item a, L442.)
63. (L263) The eval's web fixture patches `searxng_search` and `fetch_page_text` in-process to serve `server/evals/fixtures/web/`. Why: no local HTTP server and no SSRF-guard bypass in product code. Cost: none.
64. (L264) The `web-read` eval case runs as a user would: turn 1 should end with an offer (recorded yes/no), turn 2 sends `context.web` with the offered query (or the question if none) and is scored on facts. Cost: eval only.
65. (L265) `offer_web_search` has source `web` and `reads_documents` false, and no rules wording until Task 14; in `ask` mode the "Live information" sentence disappears with `web_search` meanwhile. Cost not stated.
66. (L266) `.env.example` documents `CHAT_WEB_SEARCH` (ask|auto|off, admin setting wins) and drops `WEB_SEARCH_MAX_PAGE_CHARS`, unread since Task 12. Cost not stated.
67. (L274) Options are read once per turn; a DB error reading them falls back to env, then default, with a warning (a turn never fails on the options row); `off` mode ignores `context.web` (INFO log, no query text); an app-run search failure becomes an `{"error"}` in the `app-web-1` exchange and the turn continues. Cost not stated. (One ledger line holding four related decisions; kept as one entry.)
68. (L291) Consent is narrowed below the brief and spec: `web_search` only in `auto`; an approved `ask` turn offers `read_web_pages` (the approved search's results) and `offer_web_search`. Why: "every web search asks first"; as briefed, one yes let the model run further searches on queries the user never saw. Cost: one more click when the first query was poor. Spec §5.8 amended.
69. (L292) `offer_web_search` is exempt from the tool-result budget and repeat notes (about 150 characters). Why: it must reach the user (the review reproduced a crash when an offer came after the budget was spent). Cost not stated.

**Task 14: strategy rewrite (L279–L282, L304–L309, L322, L323)**

70. (L279) `system_rules` gains `project: ProjectScope | None` and keeps `has_map`. Why: the map can be absent in a project only if building it failed; `has_map` stays the data-rule trigger and `project` drives wording. Cost: one redundant flag.
71. (L280) `NONE_FOUND` in project scope reads "No passages about this in the project's documents."; document scope is unchanged. Carried from Task 8. Cost not stated.
72. (L281) Task 14 adds the DB guard and temporary `DOC_STORAGE_DIR` to `scripts/eval_doc_qa.py` before running it (same as 15). Cost not stated.
73. (L282) A drop in the single-document eval makes the implementer report DONE_WITH_CONCERNS with the per-case diff, with no self-tuning beyond the brief's sentences; the controller rules. Cost not stated.
74. (L304) The approved `ask` turn's step-1 line is fixed text ("the results of the web search the user approved are above; call read_web_pages on the one to three best results …"), with precedence `web_search` > `read_web_pages` > `offer_web_search` > "can't look it up". Why: the narrowed consent left that turn with no web line. Cost: one sentence.
75. (L305) The site-citation sentence follows `read_web_pages` being offered; the offer sentence follows `offer_web_search`, approved turn included. Cost not stated.
76. (L306) `read_web_pages` in an approved turn with no results answers "Read only results of this turn's web searches: the approved search found nothing to read." Why: the old "Search first." pointed at a tool the model no longer has (L296). Cost not stated.
77. (L307) Three goldens: `GOLDEN_ALL` (document, auto web, pins), `GOLDEN_PROJECT` (project, all document tools and outline, ask), `GOLDEN_APPROVED` (document, approved ask turn). Cost not stated.
78. (L308) The single-document eval baseline is run in this task before the prompt change, same model, `CHAT_WEB_SEARCH=ask`, then after. Why: a same-day before/after is the only fair comparison. Cost: one extra eval run.
79. (L309) If the baseline wasn't taken before `prompt.py` changed, take it from a temporary worktree of `5b3fb6c` with only the guard and scoring files copied in; never revert `prompt.py` in the main tree. Cost not stated. (Not needed: the baseline was taken in the main tree first.)
80. (L322) With `read_web_pages` offered and no step 4 (a plain chat, or an unindexed document), the site-citation sentence is appended to the step-1 live line. Why: spec §5.7 says web facts cite their site, and the plainest web journey had no such instruction. Cost: one sentence.
81. (L323) The single-document eval's `absent` case passes on an offer with no invented page or figure. Why: the two evals disagreed, and Task 21's local run could false-fail. Cost not stated.

**Task 15: citations, server (L317–L319, L330, L331, L361)**

82. (L317) The `scope` note lists `project.docs` (the map's list, refs as the map numbers them) with their inert names; citations are matched against those names. Why: that is what the model saw. Cost: none.
83. (L318) Web sources come from `ctx.web_results` entries marked read, titles through `display_name`, and only `http`/`https` URLs. Why: a link the client renders must never be `javascript:` or `data:`. Cost: none.
84. (L319) The eval's "citations" layer reports, per case, whether the sources note names the expected document and page; pass/fail scoring is unchanged. Cost not stated.
85. (L330) `cited_pages` reads joined citations `(A, page 3; B, page 1)` and page ranges and lists (`pages 4-5`, `pages 4 and 6`, `pp.`, ranges ≤ 10 pages). Why: the strategy eval showed joined citations failing. Cost: a chip for a loosely cited page.
86. (L331) A citation naming a document outside the scope note makes no chip. Cost not stated.
87. (L361) The server counts exactly what the client links: the all-or-nothing `;` group regex is replaced by the client's per-part rule and page grammar (`page3` and lists containing 0 rejected on both sides). Why: a chip row missing a page the text links is a visible mismatch, and it removes the regex that needed a ReDoS fix. Cost: one function.

**Task 16: citations, client (L336–L338, L344, L345)**

88. (L336) The client links the same forms the server counts (joined, ranges, lists), against the reply's own scope note, longest name first. Cost not stated.
89. (L337) Web source links only for `http`/`https` (checked again on the client); label = host without `www.`; title as text only. Cost not stated.
90. (L338) Tool labels: `extract_from_documents` "Reading pages…", `document_outline` "Reading an outline…", `read_web_pages` "Reading web pages…", `offer_web_search` "Offering a web search…". Why: the SPA showed "Running a tool…" for them (L255). Cost not stated.
91. (L344) A bare page preceded by a known scope name and a comma stays text in a reply that has scope names. Why: a wrong-document link is worse than none. Cost: a prose citation doesn't link.
92. (L345) Web sources on the same host get " · n" suffixes, with the title in `aria-label`. Cost not stated.

**Task 17: scope chip (L352–L354, L377, L378)**

93. (L352) `scopeProject {id, name: null}` (project no longer visible) shows "Project unavailable" and still sends `project_id`, letting the server's 404 clear it with the toast. Why: the client can't know access. Cost: one failed turn before the scope clears.
94. (L353) A fork takes its parent's scope (as `docUse.adopt`); a new chat gets the document if one is open, else none; `startProjectChat` overrides. Cost not stated.
95. (L354) **Choose a project…** loads on choice with loading, "Couldn't load projects." and "You're not in any project yet." states, and the chip is keyboard operable. Cost not stated.
96. (L377) `ScopeChip` stops propagation of Space/Enter/Escape *and* the global handler skips targets inside `[role="menu"]`; buttons in general are not exempted (Space as play/pause with a toolbar button focused is existing reader behaviour). Why: the review reproduced the reader's shortcuts eating Space and Escape on the chip. Cost: a key handled twice or not at all elsewhere.
97. (L378) "Project gone" only on a 404 whose message equals `PROJECT_NOT_FOUND`; other 404s keep the scope. Why and cost: not stated (a chat or proxy 404 mustn't clear a valid scope).

**Task 18: offer card (L369–L371, L401, L402, L414)**

98. (L369) The card is titled "Search the web?", then the model's reason as text, then the query input and the two buttons; with no reply text the card is the reply, with no server- or client-written filler. Cost: a copy tweak.
99. (L370) Reason and query are raw model text: rendered as text only, control characters stripped, the reason capped at 300 characters for display; the card is a region labelled "Web search offer". Cost not stated.
100. (L371) The card is drawn from the reply's saved `web_offer` note (so it survives reload); only the last assistant reply's offer has controls. Cost not stated.
101. (L401) Strip a list of invisible characters (U+200B, U+200E/F, U+202A–202E, U+2060–2064, U+2066–2069, U+FEFF, U+E0000–E007F) from query and reason; keep ZWJ/ZWNJ; the sent query equals the visible, trimmed value. Why: what you approve is what is searched. Cost: a query loses an invisible character. (Superseded by 103.)
102. (L402) A generic `data-own-keys` attribute that `useKeyboardShortcuts` skips (on the card region), keeping the dialog and menu exemptions. Cost: none.
103. (L414, "fix round 2") Replace the hand list with a class: strip every `\p{Cf}` except U+200C/U+200D, plus U+034F, U+FE00–FE0E (keep FE0F), U+E0100–E01EF and the Hangul fillers; U+2028/2029 become a space; apply the same cleaning on the server to `context.web.query` before the app-run search. Why: the approved query skips the document-text guard because "the user saw it", so the guarantee must not depend on a hand-made list or on the client alone. Cost: an invisible character is lost from a query.

**Task 19: Admin → Chat options (L391, L392, L405)**

104. (L391) The section shows the mode's source ("Set here." / "From the server setting CHAT_WEB_SEARCH." / "The default.") and, when set here, **Use the server setting**, which PUTs null. Why: without it an admin could never return to the deployer's setting. Cost: one button.
105. (L392) The admin rules preview is built from a real document-scope turn's tools in the effective web mode (document tools minus `document_outline`; `auto` shows `web_search` + `read_web_pages`, `ask` shows `offer_web_search`, `off` none; web only with SearXNG), and refreshes after a mode save. Why: the preview had been built from the whole registry and showed mutually exclusive web lines at once (carried from Task 14). Cost not stated.
106. (L405) Refocus the radios only when focus is null, `body` or inside the section at the end of a save. Why: the review reproduced the effect pulling focus back after the admin had moved on, so the next arrow key changed a deployment-wide mode. Cost not stated.

**Task 20: documentation (L437)**

107. (L437) **Ask about this project** shows for non-member admins (gated on the chat capability), and their first message is refused; fix it *after* the walk. Why: a source edit would hot-reload the app under the walker. Fix: show it only when the viewer has a project role. Cost: one condition. (Done in Task 22b, item e.)

**Task 21: final measurements and walk (L420–L424, L430)**

108. (L420) Split into 21a (cloud evals, rebuild timing, gates; then the local `gemma4:e4b` run *after* the walk, because a CPU-only machine can't hold both) and 21b (the walk, on the most capable model). Why: the walk has the most moving parts. Cost: two dispatches.
109. (L421) Both start after Task 18's fix round 2 lands. Why: they must run the final code, and a half-edited server file must not be imported mid-eval. Cost not stated.
110. (L422) J3's fake SearXNG returns real public documentation URLs, so no query reaches a real search engine while the real fetch path, SSRF guard intact, runs; record whether the fetch succeeded. Cost: J3's facts depend on a public page.
111. (L423) Walk answers use `gemma4:31b-cloud` via local Ollama; embeddings are local `nomic-embed-text`; set-up-only database edits are allowed (pre-enrolled users, a document set to `indexing` for J6), never through product code. Cost not stated.
112. (L424) The walk also checks the Task 17/18 keyboard fixes in the reader side panel and Space in the other menus. Cost not stated.
113. (L430) The rebuild timing expects code *and* table chunks re-embedded (`cells1` changed table text too), not code only as the brief said. Cost: none.

**Task 22: close the measured gaps (L442–L530)**

114. (L442) Add Task 22 after the walk: (a) an offer round with no text gets a final tools-off step; (b) "Don't ask in words…" on the offer line; (c) a whole-project step-2 sentence, kept only if it helps; (d) the eval uses the product's citation parser; (e) the button only for members; (f) re-run cloud v9 ×3 and v50 ×3, then the local `gemma4:e4b` run. Why: three §2 targets missed, two on the user's own motivating questions. Cost: one extra model call on offer turns, a rules sentence or two. (Items g, h, b2, o, j, k, l, m, n were added after the walk from its findings.)
115. (L461) Item (a)'s final step runs even after the last allowed round, obeys the existing limits, and a provider failure there still ends with the offer. Cost not stated.
116. (L468, Task 22b review) Fix round 1 for review minors 2 and 3; park 4 and 5. Why: minor 3 is walk finding 4's own symptom on a real browser path (Safari doesn't focus buttons on click), and minor 2 is one prop that silently restores Send's overlap if dropped. Cost: one small src-only round.
117. (L471) The missing-`inChat` throw is development-only; production logs `console.error` and renders as `inChat=false`. Why: a dropped positioning prop must not white-screen users but must fail loudly for a developer. Cost: one reachable overlap in production, logged.
118. (L490) Every eval in this plan before the cloud-reader section ran the reader on the local `llama3.2:3b` default, because no command set `READER_MODEL` or `SUMMARIZE_MODEL`. After the 22a review fixes, re-run v9 ×3 `--web-fixture` and v50 ×3 with `SUMMARIZE_MODEL=gemma4:31b-cloud` as a new `EVAL.md` section, "Final (cloud, cloud reader)"; that section is the shipped measurement, and the 3b before/after stays as Task 22's labelled like-for-like comparison. Why: the reader's quality drives facts, chips, partial answers and extract results, and the user's standing preference is a cloud summarizer, not the local 3b. Cost: about an hour of cloud eval.
119. (L491) `doc_qa._REFUSAL` is unchanged (it is shared with the single-document eval); `EVAL.md` states the alternative score with "list" accepted (`gap-general` v9 3/3, v50 2/3). Why: a scorer change made after seeing the answers needs the user's eye, not a quiet edit. Cost: a pass under-reported, with the alternative shown next to it.
120. (L492) The local `gemma4:e4b` run waits for the user's go-ahead, and when it runs `READER_MODEL=chat` keeps one local model in RAM. Why: it is the one RAM-heavy local action the user gates. Cost: one question at the end.
121. (L494) A third offer note, for chats with no document: "The user now sees a card asking whether to search the web. Write your answer from what you know in general, saying it is not from a search and may be out of date. Don't ask whether to search; the card does." It is chosen by the same condition as b2's "document tools are offered". Why: a reply about a document that isn't there invites invented pages, against the §2 target. Cost: one sentence of wording.
122. (L495) Stop during the answer step loses the card; document it, no code change. Why: the user chose to stop, and asking again or typing "search the web for X" brings the card back. Cost: one extra message for that user.
123. (L496) The reader keeps every verified fact whatever "found" says, and `found` is true when and only when a fact verified (review Minor 7). Why: item (h) exists to keep partial answers, and dropping facts on a contradictory `found: false` undoes it; quote verification still guards against invention. Cost: a fact the model itself doubted, but whose quote is verbatim, gets shown. (Reverses part of entry 33.)
124. (L505) The project offer note needs document tools offered too: a project with no searchable documents gets the no-document note. Why: it is the review's Important 1 defect again (no documents to report pages from). Cost: nothing; the condition is strictly narrower.
125. (L530) No more prompt iteration in this plan for the re-walk's missing RHEL card or for `features`; both are recorded as the top open answer-quality gaps, with the walker's untested guess about the wording. Why: each wording change needs a full eval cycle and has moved these cases within noise twice (Task 14, Task 22 item c); the user keeps a working path ("search the web for X" gives the card) and consent holds. Cost: the one-click card stays unreliable until a follow-up.

**Count.** Against the ledger as it stands (534 lines):

- `grep -c 'Ruling' progress.md` gives **129**; `grep -c 'Ruling:'` gives **120**.
- I listed **125** numbered entries, each tagged with its ledger line.
- The 120 colon lines are 119 rulings plus one note that is not a ruling: L476, the controller's entry about the first draft of this handoff, which quotes the strings "Ruling:" and "Ruling (qualifier):". So 119 colon rulings are listed (the original 111 plus L490, L491, L492, L494, L495, L496, L505 and L530).
- 6 more rulings are written with a qualifier in place of the colon right after the word, which `grep 'Ruling:'` does not match: L133 "Ruling (Important 2, plan-mandated):", L134 "Ruling (Important 1):", L135 "Ruling (Important 2/3):", L213 and L414 "Ruling (fix round 2):", and L268 "Ruling (replaces ruling 8's splitting):" (entries 21, 22, 23, 37, 103, 58). 119 + 6 = 125.
- The other 4 lines that `grep 'Ruling'` finds make no ruling: L45 (the heading "Rulings (pre-flight)"), L145 and L149 (they mention "Ruling 3" in passing) and L476. 125 + 4 = 129.
- A script check confirmed that the ledger lines of my 125 entries are exactly the lines matching `Ruling:` or `Ruling (`, less L476: nothing missing, nothing extra, no duplicates.
- Entries 41, 67 and 114 are single ledger lines that bundle several related decisions; I kept each as one entry. The ledger also records decisions without the word "Ruling" (for example "Accepted:" notes and the Task 22 brief's items); those are in the sections below, not here.

## Parked and deferred

None of these blocks the merge on the ledger's own triage. The **final whole-branch review is pending** (see the Gate section); the items the ledger tags "for the final review" are marked below and are what it should judge. Controller doc edits made outside the review loop, which the final review should also see (L531): `8d65886` (`CHAT_WITH_PDF.md:432` button wording, the spec's §5.9 source sentence for the non-member-admin refusal, the spec's §8 "J1–J8" now J1–J9), `26ace78` (USER_GUIDE Stop line) and `b8bc2df` (`CHAT_WITH_PDF.md` §3 prerequisites).

### Parked by review, by area

**Search and ranking**
- T7: dead quote-escaping code and a test that checks nothing; hyphenated words split into several lexemes; no behavioural test for `depth`; `search_scope`'s docstring should say callers pass only readable documents; `PARTIAL_POOL = 4` and `COMMON_SAMPLE = 1000` are the implementer's values; the bench script imports `server.tests.dbutil`.
- T8: the cosine floor and threshold apply after the balance, so a prefetch can end with fewer passages and a document's allowance can be spent on a passage the floor then drops; the `documents` argument's description says "as results list them" though refs are discoverable only via the map; prefetch uses a `len(docs) > 1` heuristic to choose the project shape; `depth` isn't hard-capped (fetch = k + len(shown)); a follow-up search returns exactly k new rows before the floor, with no slack.

**Extraction, outlines, rebuild**
- T5: the pypdfium2 4.x bookmark branch is unverified (the 5.x API was adapted); one bad bookmark title drops the whole outline; the PDF is opened twice; a trailing space after the 120-character cut; heading text keeps raw HTML or alt text; setext headings differ between the two Markdown paths; leading blank lines kept in code; `_page_of` has a dead branch and a stale docstring; the PDF builder is duplicated in `test_extract.py` (extend `pdfgen.make_pdf`). Consequence of ruling 16: a command nested in a list loses its line breaks.
- T6: the outline in `run_legacy_swap` is untested; no test that a failing `save_outline` rolls back the chunk swap; `_reusable_vectors` reads config itself instead of `run_rebuild`'s captured values; the revert route leaves `doc_outlines`; `_embedding_part` splits on `|` (`rsplit` is safer).
- T8b: the `extract.py` comment shows the old glued form as `OIDC_ISSUER|http://x`; a table that follows other content in the same block keeps its first header cell glued (unpinned); the version string is asserted twice; the plan still quotes `:code1` (amended in Task 20).
- Deferred follow-up from T8b: row-aware table chunking (split big tables at row boundaries, repeat the header).
- T12: passage scoring uses `\w+`, which can't match inside unspaced scripts (CJK, Thai), so those pages fall back to the top of the page.

**Map and `document_outline`**
- T9: a duplicated `INDEXED` check in `project_map._body`/`_line` and its own constant beside bare literals elsewhere; `test_project_map` imports private helpers from another test module; the shortening ladder isn't monotone in length (harmless, the first fit wins); a stale comment on `ProjectMap.shortened`; a ~135-character docstring line; the `tools_on` condition is written twice in `run_turn`. Accepted: `shortened` is set only when the chosen body differs from the full one.
- T9, parked design issue, **for the final review**: rules and map are built at step 1, so if a provider rejects tools mid-turn (`FeatureDropped("tools")`) a shortened map still names `document_outline`, as the rules still name `search_documents`.
- T9, parked: a sectioned 50-document map would need a budget of about 9,400 characters or a per-document share, not a different shortening order. Revisit if variant-50 answers look starved; the final eval didn't isolate this.
- Measured use: `document_outline` was called 0 times in the map layer's 78 variant-9 and 15 variant-50 turns, and once in the strategy layer's variant-50 run (that turn then skipped search and missed `pytest`).

**Reader and extract**
- T10: `_number` duplicates `load_chat_config`'s helper; `read_many`'s `batches` parameter shadows the function; timing margins of 0.15 s in the timeout test. False drops (safe direction): a standalone dash at a line end (`foo -⏎bar`) matches neither hyphen form; a quote spanning two line-end hyphens that need different choices verifies in neither; 2-character kana pairs (です) verify almost anywhere (a ruled trade-off). Design note: only `quote` is verified; `fact` is model text (≤ 600 characters), so tool results keep both as data.
- T11: `extract_from_documents.py` is 353 lines (helpers could move to a sibling); `execute`'s tail mixes evidence recording with summary bookkeeping; the result is pretty-printed, so its real size is about 15–25% over `CHAT_EXTRACT_OUTPUT_CHARS`; one verified quote over the output limit would empty the facts (unreachable: the reader's reply is ≤ about 2.4k characters); the project summary carries the plural `documents`; no test names another project's document (structurally covered); the de-dup key includes fact text, so two wordings of one overlapped quote show as two facts; `_pieces` with a limit under 4 skips a character (unreachable, `READER_INPUT_CHARS` ≥ 500); `SELECT DISTINCT … BETWEEN 1 AND 0` runs for an empty document (harmless).
- **For the final review (L508):** there is no tool-level test of `found: false` plus a verified fact in `extract_from_documents` or `read_web_pages` (the reader-level behaviour is unit tested).
- T12: packed multi-line passages are scored by distinct-word overlap, so a 15-row passage can outscore the single row holding the answer; trafilatura runs synchronously on the event loop for pages up to 2 MB (pre-existing); `services/web_search.py` was at 299 lines.

**Web consent and the offer**
- T13: an approved search is silently skipped (INFO log) with no tool support, zero rounds, or `SEARXNG_URL` empty (a data notice would help); the app-run search sits outside the tool-result budget and runs before `_over_budget`; a model repeat of the approved query runs again; the audit line's `old` is the effective mode, not the stored row; `POST /v1/tools/web_search` ignores the mode, including `off` (pre-existing; nothing in `src/` calls it; **final review to judge**); the offer's query and reason are raw model text; `unmetered` is read via `getattr`, not declared on the `Tool` protocol; several offers in one round all run and only the first is shown (stored summaries only, no loop, untested); on an offer turn `stats.doneReason` stays `tool_calls` though `finish_reason` is `stop`; `orchestrator.py` grew to 697 lines.
- T18: the saved and streamed `web_offer` note isn't cleaned server-side (the card cleans on display; one `clean_query` call would do it; **final review to judge**); `\p{Cf}` also strips visible Arabic signs U+0600–0605 and U+06DD (follows the ruling); the Tag-character strip turns England/Scotland/Wales flag emoji into a plain black flag; a search turn that fails mid-stream leaves the offer as a dead line (fails closed: ask again); card state is lost on unmount (**Not now**, then a refused normal send, brings the card back; a refused search turn brings it back with the original query, not the edit); Safari IME `keyCode` 229 on Enter; a 301-character reason is shown cut.

**Prompt and strategy**
- T14: the offer line lacked the document-first clause (item b2, since done); the v50 RHEL answer refused in prose ("do not list") and the eval's refusal pattern didn't accept it (not widened); `prompt.py` is about 304 lines; an `absent` answer that offers plus a confident non-figure claim passes (figure and page checks stop concrete inventions); `check_database_url` and `eval_storage` are duplicated in both eval scripts; the `.env`-only case of `test_main_refuses_the_real_database_before_anything_runs` asserts only "Refusing to run", which the unset case also says.

**Citations and sources**
- T15: searched-but-unused documents appear with `used: false`, bounded by the passages shown, not by project size; the `scope` note lists every document on every project reply (about 250 bytes each, uncapped); `test_chat_orchestrator` imports private helpers from other test modules; prose such as "on page 7" in a one-document project isn't counted (by design); Unicode `\d`/`\s` differ between Python and JS (Arabic-Indic digits count on the server only; use `[0-9]`); the server reads raw Markdown while the client skips inline code and links (a citation in backticks counts but doesn't link); `EVAL.md`'s citations section shows the older total format.
- T16: "(Section 2, page 7)" stays text in a project reply; comma lists are accepted in prose; names with Markdown characters that split the text node don't link; `afterName` is unanchored on the left ("MyGuide.md, page 7" also suppresses, which fails safe); "Guide.md, (page 7)" still links the sole-evidence document (pre-existing narrow edge).

**Client**
- T17: `App.jsx`'s `handleAskProject` wiring is untested apart from the journey test's copy (the walk's J1 did exercise it); **Choose a project…** for a role that has chat but not reader access (`GET /v1/projects` needs reader; unverified that such a role exists); the "This document" label has no name while `pdfFileName` loads; `role="status"` mounts pre-filled; a project in both the open document's list and the chosen list shows two checked radios (cosmetic).
- T19: `text-amber-600` is about 3:1 at 12 px (repo convention); after a failed reset, focus lands on the radio, not the reset button; a slow `refreshKey` GET could show a preview missing just-saved profile text; `ChatOptionsSection.test.jsx` failed once in a full run (not reproduced in 6 isolated, 8 loaded, 3 full runs; timing-fragile assertions were listed).
- T22b: placement and height rest on class names in jsdom (the real check was a one-off browser run, and later the re-walk); a harmless duplicate `ProjectPage` test.
- **For the final review (L473):** an open header menu stays open when Escape is pressed inside `ScopeChip`'s menu or `SettingsDialog`, because their React `stopPropagation` swallows it (pre-existing); and no test renders the real `App`, so the `inChat` guard on `ReadSelectionButton` relies on its development-only throw at first load.

**Task 22a review (opus) leftovers**
- Minor 5, parked: the settings helper (`num(...)`, the warn-and-default pattern) is copied across services and chat rather than shared; a layering choice.
- Minor 8, parked: the offer note is a second app instruction to the model that the rules don't name, and changing its text changes every golden.
- Confirmed but out of scope: `doc_qa._REFUSAL` lacks "list", so answers that open "The documents do not list…" fail the official `gap-general` score (v50 0/3, 2/3 with "list" accepted); a scorer change needs the user's eye (entry 119).
- Accepted by the review: the "agreement rewording" the implementer raised (the ledger's words); the chip-count difference (10 of 12 against 9 of 9 with the 3b reader) is noise.

**Data and access**
- T2: the test doesn't pin the partial index; no explicit FK-violation test for `doc_outlines.doc_id`; migration test helpers are duplicated.
- T3: races: membership lost between the route and the loader gives `internal_error` with the scope not cleared; a project deleted between the check and `begin_turn` gives a 500 with nothing written; a refused project on a chat scoped to another valid project clears that scope (untested; matches the spec's wording); the UUID-422 test was never seen failing for the right reason; a `test_chat_prompt` parametrize deprecation warning (pre-existing).

**Evals and measurement**
- T1: digit terms can match citation page numbers (strip citations before matching); the gap-sentence heuristic has holes both ways (a standalone citation sentence after an RHEL claim passes); the `balance` case premise is untested (HANDOVER appears in other documents); `user-guide-pins` accepts only "each message" or "every message"; `start-command` names README in the question (less discovery in the 50 variant); frozen-ness is checked by names and a commit string, not hashes; `gap-general` reads `expect[0]` (IndexError if empty); the web-calls line sums skipped cases too; the verdict column is narrow.
- T4: `run_eval`, `_ask` and `_turn` have no automated test; two tests are named for behaviour they don't test (dotenv override order); `bytes_path` dangles for the other variant's documents in the scratch DB; the variant-50 project name differs; `EVAL.md`'s commit line should name `41a3bff`; `run_eval` doesn't refuse default storage for programmatic callers; `seed_project` reads `doc_storage._EXT`; long lines.
- T5, T6, T7, T13, T15: stale `_page_of` docstring in `test_eval_project_qa.py`; `EVAL.md` header and several historical sections still say "uncommitted"; `EVAL.md` overstates "the document filter keeps cost on the project's chunks" (+35% with a 40k-chunk library) and leaves a wrong first sentence above its correction; its T13 Change line calls the app-run search a model call.

### Product deferrals (not built, on purpose)

- **C2.2 descriptions and digests.** Editable one-line document descriptions, a project overview, section summaries. Until then the map is built from outlines and opening lines, and `SUMMARIZE_MODEL` stays in `model_router` for it.
- **A reranker, and contextual retrieval.** Parked until the eval shows the need; both cost CPU on local deployments, and Ollama has no rerank endpoint. Variant 50's `next-work` (0/3 in the shipped set, 0/6 before Task 22) is where one would be tried.
- **Library-wide scope** ("all my documents"). Later C2 work.
- **Two answer-quality gaps left open on purpose** (entry 125): the RHEL question's one-click card in a project chat, and "What are the features?" citing README.md alone. `EVAL.md` lists untried ideas for the second: an overview the app builds from each document's opening (the project map already carries it for small projects), one search per area name, or following the README's own "see also" links. The walker's untested guess for the first is the question's wording ("to run this").
- Also deferred by the spec: upload in chat and assistant roles (the next Phase C pieces), memory (after Phase C), compare versions (V2), a separate search service (the user's call: borrow the concepts, don't run one), an "always allow web in this chat" option, resumable streams, snippet cropping, and row-aware table chunking.

### From the walk, not fixed

- **No upload-new-file control on the project Documents tab** (pre-existing, A1/V1): "File a document…" lists only files already in your library. A first upload goes Reader → Choose File → "Project for this document" → Index.
- **Member removal has no confirmation** (pre-existing, A0): documents get one, members don't. Whether that is intended wasn't checked.
- **Snippet-only web facts can carry a site citation without a chip.** Web chips are drawn only for pages the reader returned verified facts from, by design; when the model answers from search snippets it can still write `(docs.example.com)`. Task 22a (h) removes the multi-part-need cause but not this.
- **The idle Read Selection button still shows over the Library view** (the 2026-10-12 walk's 3d). 22b hid it only in the Chat view; the Admin view wasn't run in a browser. Side effect of 22b: a user can no longer select text in a chat reply and read that selection aloud (per-message read-aloud still works).
- **From the re-walk** (the Findings list above): the approved web answer's lines not taken from a page carry no site and no label; a typed "search the web for X" may come back with a paraphrase of X in the card (the user can edit it before approving); after Stop the saved text runs a few words past the screen; a browser reload opens a new empty chat (pre-existing; the chat is one click away in Sessions); on the phone, the open sidebar covers its own Hide Sidebar button (pre-existing; the backdrop tap closes it); nothing tells the model which of two disagreeing documents is current.
- The reader header is 442 px wide at 390 px, the reader's "Project for this document" reads "No project" for a project document, and the status text stays "Reading..." while paused (all pre-existing, observation 6f).

## What the user still can't do

Judged from the walks, the parked items and the measurements: what a person cannot do start to finish in the app today. No step of J1–J9 or W1–W5 needed curl; the environment-only items are marked.

- **Ask across their whole library.** The scope chip offers *No document*, *This document*, or one project; there is no *All my documents* option, and the turn API takes a single `context.project_id`. To get a cross-document answer today, put the documents in a project first (Library → New project; then Reader → "Project for this document" → Index, or **File a document…** on the project's Documents tab for files already in the library).
- **Upload a brand-new file from the project page.** The Documents tab has no upload control; the path is through the Reader (above).
- **Get a broad overview that draws on several documents.** "What are the features?" cites README.md alone: 1 turn of 9 passes in the shipped eval, and README-only in the first walk (3 of 3) and again in the re-walk. No control changes that; Task 22's one attempt, item (c), was measured and removed.
- **Rely on a one-click web card for a question like the RHEL one in a project chat.** The card came with an answer in 1 of 4 walk chats (the other three gave text only, saying the documents lack a package list) and in 1 of 3 runs of the 50-document eval (3 of 3 at 9 documents). In `ask` mode the card's **Search the web** button is the only way to approve a search, and a typed "yes" carries no approval. What works: type "search the web for X" and the card arrives prefilled; the model may paraphrase X (once in 2 tries), but the user can edit the query before approving, and nothing leaves the server until the click. **Stop** during the answer step also loses the card; asking again or typing that request brings it back.
- **Open the page behind every web fact.** After Task 22 an approved search shows a source chip for each page the reader got facts from (docs.redhat.com in the re-walk). Lines the model adds from its own knowledge carry no site and no label, and an answer written from search snippets alone can cite a site with no chip.
- **Narrow a project chat to some of its documents.** The model can (`documents` on `search_documents` and `extract_from_documents`); the person can't. The only control is which documents are in the project.
- **Chat with a project as a site admin who isn't a member.** The button is hidden for non-members (re-walk W5: hidden for the admin, shown for a Reader member) and the server refuses the turn. An admin must add themselves first (Members tab → **+ Add people**); the server allows it and logs a warning, but that path was not walked in a browser.
- **Tune project chat from the UI.** Admin → Chat options holds only the web-search mode. The map size, tool rounds, passages per document, extract page cap, reader model and timeout, and `SEARXNG_URL` are environment variables, edited on the server.
- **Allow the web for one chat.** There is no "always allow in this chat"; in `ask` mode each search needs its own click, and only an admin can change the mode, for everyone.
- **Describe a document to help the assistant route, or see a project overview.** C2.2; the map uses outlines and opening lines only.
- **Ask a project about an old version, or compare versions.** Project chat searches current versions only; compare is V2.
- **Trust a 50-document project as much as a 9-document one.** Measured: citations 82.1% against 87.9%, `next-work` 0/3 against 2/3, and the RHEL card 1 of 3 against 3 of 3. No reranker exists to help.
- **Tell the assistant which of two disagreeing documents is current.** In 2 of 4 re-walk chats it quoted the older `CHAT_WITH_PDF.md` prerequisites to a newcomer. The document was corrected (`b8bc2df`); nothing in the map or the rules says which of two documents wins (the walker's reading, not verified).
- **Keep a chat across a browser reload.** A reload opens a new, empty chat (pre-existing); the previous one is one click away in **Sessions**.
- **Close the phone sidebar with its own Hide Sidebar button.** The open sidebar covers the button (pre-existing); tapping the dimmed backdrop closes it.
- **Know how a small local model behaves.** The plan's final `gemma4:e4b` run has not been made; it waits for the user's go-ahead and must run with `READER_MODEL=chat` so only one local model is in RAM.
- **Be asked before a member is removed** (the Members tab's remove control acts at once), **or keep the idle Read Selection button off the Library view** (it still floats over it).

Before deploying to a live database, run the `embedding_model` query under "Rebuild timing". The first project chat after the upgrade starts a background rebuild; the turn doesn't wait for it, and the old chunks keep answering meanwhile.
