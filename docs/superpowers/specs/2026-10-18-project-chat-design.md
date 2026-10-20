# Project chat (C2.1): ask a whole project — design

**Date:** 2026-10-18 · **Status:** built on the C2.1 branch. Where the build ruled differently, the section carries an "**Amended:**" note with the reason; the rest stands as designed
**Program:** C2 (A0 roles → V1 versions → **C2.1 project chat** → V2 compare/review → C2.2 digests), and the first piece of Phase C.
**Builds on:** chat orchestrator (`2026-09-27-chat-orchestrator-design.md`), v2.4 document context, document versions (`2026-10-12-document-versions-design.md`).
**Research behind it:** `docs/research/2026-10-17-librechat-llmrouter-comparison.md`, plus the Meilisearch and multi-document retrieval reading of 2026-10-17/18 (summarised in §12).

Who said what: "**User:**" marks the user's decisions. "**Our call:**" marks a choice made in this design that the user hasn't ruled on; each one is listed in §11 so it can be checked.

---

## 1. Why

A team keeps a project of 10–50 documents and wants to ask the project, not one file. **User:** the worked example is Natural Reader's own docs as a project (README, DEPLOYMENT, USER_GUIDE, ARCHITECTURE, IDENTITY_AND_ROLES, LIBRARY, CHAT_WITH_PDF, TESTING, docs/README). A newcomer asks, in this order:

1. "What are the features?" Needs breadth: something from every relevant document, not the five closest passages.
2. "How do we deploy and test it quickly, step by step?" Needs one ordered guide stitched from several documents, with the commands.
3. "I'm on RHEL 9, which packages do I need?" The documents are silent. The answer should give what the documents require, fill the gap from general knowledge (clearly marked), and offer a web search.

Today a chat sees one open document. Opening the same tools to a project fails in five specific ways, all read in the code on 2026-10-17/18:

| # | Failure | Where |
|---|---|---|
| F1 | Across documents, passages are sorted by meaning (cosine) alone, so the word ranking is lost and an exact "clause 4.2" loses to a vaguely similar paragraph elsewhere | `server/chat/tools/search_documents.py:109` |
| F2 | No balance: one long document can take all five result slots | same |
| F3 | Citations and source chips are keyed by page number only; "page 3" doesn't say which file | `server/chat/sources.py`, `src/lib/citations.js` |
| F4 | **Fenced code is never indexed.** Markdown code blocks are skipped at extraction, so search and page reading can't see a single command. README has 23 code fences, DEPLOYMENT 6 | `server/services/extract.py` `extract_markdown` (`if is_code: continue`) |
| F5 | The "librarian" rule: step 4 forbids filling a gap "from memory or the web, unless the user asks", and the web is only for "live information". Web results are 2–4 sentence summaries, by a small model, of each page's first 6,000 characters, so exact package names and commands are lost, and each search costs five extra model calls | `server/chat/prompt.py:126-158`, `server/services/web_search.py:194-243` |

## 2. What success looks like

**User:** the assistant goes through the documents and decides where to look next. When the documents run out, it asks whether to search the web. Retrieval has to be spot on, and the assistant has to be able to tell which document to read.

Concretely, for the three questions in §1:
- every fact taken from a document cites that document and page, and the citation opens that page of that file;
- the deploy guide contains the commands from the documents' code blocks, in order;
- the RHEL answer says the documents don't cover RHEL, gives the documents' own requirements (cited), adds general knowledge introduced as not from the documents (no page given for it), and ends with an offer to search the web for a short query;
- no web request leaves the server before the user says yes (default mode).

Measured by the evaluation in §9. **Our call:** targets on a cloud model are ≥ 90% right-document citations on cases with a document answer, the exact command on the code case, 0 unapproved web calls, and 0 invented pages on gap cases. On the local `gemma4:e4b` the numbers are reported but have no target.

## 3. Goals, non-goals, anti-goals

**Goals**
- A chat can be scoped to a project, from the project page or from the chat itself.
- The assistant sees a map of the project (§5.2) and moves through it with tools (§5.4).
- Search across a project ranks the whole project as one list, balanced across documents (§5.5).
- Citations name the document and page (§5.7).
- Code blocks and document outlines are indexed (§5.3).
- Gaps are filled from general knowledge (labelled) or answered with an offer to search the web, which runs only after the user's yes. A reader sub-agent, given a goal by the chat model, goes through documents or web pages and returns verified facts with quotes, so the chat model's context holds findings, not pages (§5.4.1, §5.8).
- A fixed evaluation measures all of the above, before and after each layer (§9).

**Non-goals (each is a later piece of work):**
- **Library-wide scope** ("all my documents"). Later C2 work.
- **Upload in chat and assistant roles.** The next Phase C pieces.
- **Memory.** After Phase C.
- **Document descriptions and digests** (editable one-line descriptions, project overview, section summaries). C2.2.
- **Compare versions.** V2.
- **A reranker and contextual retrieval.** Parked until this eval shows the need; both cost CPU on local deployments (§12).
- **A separate search service** (Meilisearch or similar). **User:** borrow the concepts, don't run one.
- **An "always allow web in this chat" option, and resumable streams.**

**Anti-goals: things that would count as failure even if it "works"**
- **A citation that names the wrong document.** That is worse than no citation.
- **A web request without the user's yes** in the default mode.
- **The map or search reaching documents the asker can't read:** another project, or an old version.
- **Document text leaving the server in a web query or a fetched URL.**
- **A map so large that a chat with a small window runs out of room for the answer.** Design for 8,192 tokens: a user may pick that on a CPU to keep replies fast, even though models like `gemma4:e4b` can take far more.

## 4. Constraints

- **Open source with many deployers.** Every knob is an env var with a safe default; nothing deployment-specific is hard-coded; docs use example.com.
- **Postgres only, no new service.** The existing hybrid search (meaning + words, reciprocal-rank fusion) is the base.
- **Small local models must work.** Deployers run CPU-only models like `gemma4:e4b`. That means few tools, a small map, and app-run first steps where they help (as the document prefetch does today).
- **The rules stay one strategy.** They are assembled from the offered tools, pinned by the golden test (`server/tests/test_chat_prompt.py`), and stable across a chat's turns so the provider's prefix cache survives (`server/chat/prompt.py` docstring).
- **Document and web text are data, never instructions.** Both are fenced, and the existing data rule applies.
- **Access follows the existing SQL definitions.** That means `require_project_role` for projects and `list_project_documents` for the current versions. No second copy of who-can-read-what.
- **Migrations** are numbered from 021, each recording its `schema_migrations` row.
- **The reader's page layout must not move.** Citations carry the reader's page numbers, and the `server/tests/fixtures/segmentation` fixtures pin that equality.

## 5. Design

### 5.1 What a chat is about (scope)

A chat is about one of three things: **nothing** (general chat), **the open document** (today's "Use this document"), or **a project**.

- **Stored on the chat.** The project scope is stored on the chat: `chat_sessions.scope_project_id UUID NULL REFERENCES projects(id) ON DELETE SET NULL`. A project chat stays a project chat on every device, and the chat list can label it. The document scope stays per turn, as today.
- **Turn request.** The request gains `context.project_id` (UUID). When it is present, the turn is project-scoped and the session's `scope_project_id` is set to it. `context.doc_id` may come too: if that document is a current document of the project, the map marks it "open in the reader", so that "this page" and "this document" resolve. Otherwise it is ignored in project scope. Sending `project_id: null` explicitly clears the session's project scope.
- **Access check.** Every turn runs `require_project_role(conn, principal, project_id, None, admin_ok=False)`, the same check as the project document routes. If the user isn't a member, the turn is refused with 404 `project_not_found` ("This project doesn't exist or you don't have access."), and the session's `scope_project_id` is cleared, so the chat falls back to general chat. **Our call:** a non-member admin can't chat with a project, matching the document routes.
- **Which documents.** The scope's documents are `doc_versions.list_project_documents(project)`: current versions only, which that function already defines for C2.1. Only indexed ones are searched. The others appear in the map with their state (§5.2). Each indexed document goes through `doc_pipeline.ensure_current`, as the open document does today.

### 5.2 The map

The assistant gets a map of the project before it does anything. This is the "two-stage routing" pattern from §12: pick documents from a list, then sections from outlines, then read.

- **Where it goes.** In the system message, after the rules and before the pins (the slot `prompt.py` reserves for C2), as a fenced block `<project_documents>`. Names and headings are other people's text: each is made inert the same way `display_name` does today, and the data rule names this tag.
- **What each line holds.** One line group per document, in `list_project_documents` order (by name), numbered from 1. The number is the document's **ref** within the turn. Each group has:
  - the name and page count;
  - its state if it isn't indexed ("being indexed", "not searchable yet");
  - "open in the reader" when that applies;
  - its outline: headings of levels 1–3, each with its page, written compactly (`Quick start p2 · Configuration p4 · …`);
  - with no outline, its opening line instead: the first 120 characters of page 1.
- **Budget.** `CHAT_PROJECT_MAP_CHARS`, default 6000 (about 1,500 tokens). When the map is over budget it is shortened in this order: drop level-3 headings, then level-2, then cut each outline to its first 8 entries, then names and pages only. Whenever anything was cut, the `document_outline` tool (§5.4) is offered for this turn and the map says so in one line. **Our call:** 6000 leaves room for an answer in an 8,192-token window.
  - **Amended:** the order is: drop level-3 headings; then keep level 2 but cut each outline to its first 8 entries; then drop level 2 as well (level 1 only, still the first 8 entries); then names and pages only. Each step includes the ones before it, and the first that fits is used. *Reason:* a Markdown outline has one level-1 heading (the title) and its sections at level 2, so dropping level 2 before the cap left titles only and gave the cap nothing to cut. On a turn that offers no tools (a model without tool support, or no tool rounds) the one line says only "Outlines are shortened." and never names `document_outline`, because the rules name a tool only when it is offered. A document is never dropped from the map, however many there are.
- **Stability.** The map changes only when the project's documents or their states change, so it doesn't break the prefix cache from turn to turn.

Example (shape only):

```
<project_documents project="Natural Reader docs">
1. "README.md" — 9 pages. Outline: Features p1 · Quick start p2 · Configuration p4 · Troubleshooting p8
2. "DEPLOYMENT.md" — 14 pages — open in the reader. Outline: Requirements p1 · Containers p3 · Bare metal p6 · Upgrades p11
3. "Annual scan.pdf" — 40 pages. No outline; starts: "Annual report 2024. Prepared by …"
4. "Notes.txt" — being indexed, not searchable yet.
</project_documents>
```

### 5.3 Indexing: code blocks and outlines (fixes F4)

- **Code becomes searchable.** Fenced and indented code blocks become chunks with `chunk_type = 'code'`, on the page the block already occupies. Their text is the code verbatim: newlines kept, capped like other chunks. The reader's page layout and narration don't change, because code blocks already take up their page and the reader still skips them when reading aloud. Only the server's chunk list grows. Search and `read_document_pages` include code chunks, and `read_document_pages` shows them fenced, so the model knows they are commands.
- **Amended (ruling R2):** code chunks are interleaved with the other chunks in document order, not appended, so a procedure reads as written. The reader-equality test (`server/tests/fixtures/segmentation`) compares only the non-code chunks, because the reader has no chunk for code; that way the reader's page layout can't move. Only **top-level** code blocks become code chunks: a block inside a list or a quote stays part of that block's text. `read_document_pages` shows code fenced, one fence per block even when the block is stored in parts. *Reason:* in document order a command sits next to the sentence that introduces it, in search results and when a page is read, and the reader's layout stays checkable.
- **Amended:** the cells of a Markdown table are separated by spaces in the chunk text, so their words are searchable (before, `OIDC_ISSUER` and its value ran together as `OIDC_ISSUERhttp://x`, one word nobody would search for). Pages are still counted from the reader's own text, with the cells glued, so no page moves. `CHUNKER_VERSION` is `split1:{max}:{overlap}:code1:cells1`; the `code1` and `cells1` parts make each existing document rebuild on first use, and the rebuild embeds only text with no stored vector.
- **Outlines.** Extraction also records an outline: a list of `(level 1–3, text, page)`.
  - Markdown, and PDFs converted with Docling (their per-page Markdown in `doc_pages`): the headings.
  - Other PDFs: the PDF's own bookmarks (its table of contents, via `pypdfium2`).
  - Plain text: nothing.
- **Where outlines live.** One row per content in a new table, `doc_outlines(doc_id PK REFERENCES documents ON DELETE CASCADE, entries JSONB, opening TEXT, source TEXT CHECK (source IN ('headings','bookmarks','none')), created_at)`. Content is keyed by its hash, so every holder shares it.
- **Existing documents.** A new extraction profile reuses the lazy rebuild (`doc_pipeline.ensure_current` → `run_rebuild`). Each indexed document upgrades in the background the first time it's used, and its old chunks keep answering meanwhile. **Requirement:** text whose embedding already exists under the same embedding model is not embedded again, so the upgrade embeds only the new code chunks. The live site is CPU-bound, and re-embedding every document would take hours. Legacy content with no stored file gets no code chunks and no outline (`source = 'none'`). That's acceptable.

### 5.4 Tools in a project chat

| Tool | When offered | Change |
|---|---|---|
| `search_documents(query, k?, documents?)` | scope has ≥ 1 indexed document | The vector search: meaning plus exact words, fused into one ranking over the whole scope (§5.5). The new `documents` argument is a list of map refs or exact names that narrows the search |
| `read_document_pages(document, first_page, last_page?)` | same | Exact text, as today. The new `document` argument (ref or exact name) is required in project scope and optional in document scope. Output includes code chunks, fenced |
| `extract_from_documents(documents, need, pages?)` | same | New: the reader on documents (§5.4.1). Returns verified facts with quotes, each tagged with document and page |
| `document_outline(document)` | only when the map was shortened | New. Returns the stored outline, all levels, with pages |
| `offer_web_search(query, reason)` | web configured, mode `ask`, turn not approved | New (§5.8) |
| `web_search(query)` and `read_web_pages(results, need)` | approved turn, or mode `auto` | Changed and new; `read_web_pages` is the reader on web pages (§5.4.1, §5.8) |

**Amended:** the last two rows changed in §5.8: `web_search` is offered only in mode `auto`; `read_web_pages` in `auto` and on an approved `ask` turn; `offer_web_search` in mode `ask`, also after a yes (a different search needs its own offer).

Tool results already name documents by ref and name (`scope.documents_listing`). An unknown ref or name returns an error listing the valid ones, so the model can correct itself.

**User (2026-10-18): the chat model chooses.** Each tool is separate, and its description says what it's for:
- `read_document_pages` when the exact wording of a short section matters;
- `extract_from_documents` to go through a lot of text for one goal;
- `search_documents` to find where something is.

Nothing in the app picks a tool by context size. The user also noted that `gemma4:e4b` is a capable model; it can write the goals.

#### 5.4.1 The reader (shared by `extract_from_documents` and `read_web_pages`)

The reader is a sub-agent: a separate model call, with a fresh context and no tools, that is given the chat model's goal (`need`) and a batch of source text, and returns only what serves the goal. Its context is thrown away afterwards, so the chat model's context holds the findings, not the pages.

- **The brief is fixed.** It contains the user's question, the need, and these rules:
  - answer only from the given text;
  - copy exact strings verbatim (commands, package names, versions, numbers, URLs);
  - give each fact the shortest quote that shows it, with the label of the passage it came from;
  - say so when the text doesn't have it;
  - return JSON `{"found": bool, "facts": [{"fact": "…", "quote": "…", "from": "<passage label>"}]}`, at most `READER_OUTPUT_CHARS` (default 1,200) per batch.

  Source text sits in fenced, labelled passages: `[2, page 6]` for a document, `[web 1]` for a page.
- **The app verifies every quote** against the passage the label names, with case and whitespace normalised.
  - If the quote isn't there but is in another passage of the same batch, the label is corrected.
  - Otherwise the fact is dropped and counted in the log.
  - **Amended:** normalising also folds quote and dash kinds (curly quotes, en and em dashes, soft hyphens). A word hyphenated at a line end matches whether the hyphen is kept or dropped. A line break between Han or Kana characters is ignored (those scripts are written without spaces). The fenced form of the passage, which is what the model saw, counts too. A quote needs at least 4 characters (2 if it contains a Han or Kana character) unless it is the whole passage; a fact over 600 characters is dropped. *Reason:* quotes that were faithful to a PDF were being dropped for its line-end hyphens and typography, while a one-letter quote "proves" itself in every passage.

  Verification is what makes the reader accurate rather than just shorter. It is also how a document fact gets its exact page for the citation and the source chips: the evidence is `(document, page, quote)`.
- **Which model reads.**
  - The chain is `READER_MODEL`, then `SUMMARIZE_MODEL`, then `WEB_SEARCH_SUMMARY_MODEL` (the existing names keep working), then the default `llama3.2:3b`.
  - `READER_MODEL=chat` uses the turn's own chat model. No second model gets loaded into RAM, which matters on CPU, where swapping models costs seconds to minutes.
  - Every reader call sets its context size explicitly (`num_ctx` sized from batch + brief + output, clamped by `INFERENCE_NUM_CTX_MAX`). Today's summary call sets none, so the provider's default window applies, and on Ollama that default can be smaller than the page, which then gets cut silently.
- **Batches.** Each batch holds at most `READER_INPUT_CHARS` (default 12,000 characters, about 3,000 tokens). Batches run in parallel up to `READER_MAX_CONCURRENCY` (default 2; a CPU Ollama serves them one by one anyway), each within `READER_TIMEOUT_S` (default 60; `WEB_SEARCH_SUMMARY_TIMEOUT_S` still works as a fallback).
- **Budget.** The reader's input doesn't count against the chat's tool-result budget; its returned facts do.
- **Tests.** The reader's brief is pinned by a golden test, like the chat rules.

**`extract_from_documents(documents, need, pages?)`:**
1. **Arguments.** `documents` takes 1–5 map refs or exact names. `pages` optionally gives page ranges per document.
2. **Which pages.** The given pages; otherwise every page of those documents, up to `CHAT_EXTRACT_MAX_PAGES` (default 12) per call. **Our call:** 12 pages is about three or four batches, so with two at a time and a 60-second limit per batch, one call finishes within about two minutes even on a CPU. Prompt processing is the cost there; measure on the CPU dev machine before raising it. When the documents have more pages than that, the pages are chosen with the vector search (`search_documents`' ranking for the need, best pages first), then read in page order. The result names the pages it did not read, so the chat model can ask for them in another call.
3. **Batching.** Pages are packed in order into batches, with code chunks fenced as in `read_document_pages`. Each batch gets one reader call.
   - **Amended:** a page with no text (a cover, a scan) is skipped: it never takes a place in the page cap and is never listed as not read. A page longer than `READER_INPUT_CHARS` is split into pieces that overlap by 200 characters (a quote across a cut is whole in the next piece), because a batch longer than the limit would otherwise be cut and the reader could only quote the part kept. If ranking the pages for the goal fails, the first pages in order are taken and the result says so.
4. **What comes back.** Facts grouped per document in page order, each with `(ref, page)` and its quote, at most `CHAT_EXTRACT_OUTPUT_CHARS` (default 4,000) in total.
5. **Failure.** A batch that fails or times out is reported as "pages N–M not read", with no raw text, because the chat model can read those pages itself with `read_document_pages`.
   - **Amended:** the result keeps two lists apart: `failed` holds the pages of a batch that failed or timed out, and `not_read` holds pages left out by the page cap (step 2). Neither carries raw text.

**Our call: tool rounds.** `CHAT_PROJECT_MAX_TOOL_ROUNDS`, default 5, applies in project scope; `CHAT_MAX_TOOL_ROUNDS` (default 3) stays for the rest. An overview question needs a round to read sections of several documents, then a round to check. The eval decides whether 5 is right.

### 5.5 Search across a project (fixes F1, F2)

1. **One ranking for the whole project.** One SQL statement covers every searchable scope document. It builds the meaning list (an exact scan inside a MATERIALIZED CTE over the scope's chunks, the same reasoning as today's per-document plan) and the word list, then fuses them **once** with reciprocal-rank fusion (`RRF_K = 60`). For a project the fusion depth is `max(k, CHAT_PROJECT_FUSION_DEPTH)`, default 50. This is the "combined search over many sources" idea from Meilisearch, done with our own ranking.
2. **Balance per document.** Walk the fused ranking. A document contributes at most `CHAT_SEARCH_PER_DOC` passages (default 2) until `k` are chosen. If the walk ends short of `k`, fill from the skipped passages in rank order. This is Meilisearch's "distinct", softened. A document that really holds all the answers still fills the list when nothing else matches.
3. **Partial word matches.** When no chunk contains every word of the query, the word list ranks chunks by how many of the query's distinct words they contain (at least half, rounded up), most words first, then by `ts_rank_cd`. This is Meilisearch's word-dropping strategy. It applies in document scope too, because both scopes use one function.
   - **Amended:** only *informative* words count: a word that some chunk of the scope has but at most half of the chunks do, and at most the 12 rarest of them. So filler words and words nobody has don't decide the match. A partial match also counts a tenth as much as a meaning or all-words match in the fusion. *Reason:* measured on the project eval, at full weight partial rows that were also in the meaning list took the first places from the best chunk by meaning. The tool labels a partial match that clears the cosine floor "some words", and gives it no pass past the floor.
4. **The `documents` filter** narrows the scope for that one call.
5. **Unchanged:** the cosine floor (`CHAT_SEARCH_MIN_SCORE`), the word-match bypass, the skipping of passages already shown, and the per-result caps.
6. **Prefetch.** The app's first search (stage-0 prefetch) runs over the project in project scope, with the same balance. It helps small models, as it does for one document.

**Performance.** The search stays exact. Measure it on a scratch project of 50 documents (about 10,000 chunks) on the CPU dev machine. Target: one search under 300 ms at p95. If that target is missed, the builder may move to an index-based plan that filters within the index, after measuring (§10).

### 5.6 The strategy (fixes F5, the rules half)

The numbered procedure in `prompt.py` keeps its shape. What changes:

- **Step 1 (decide).** Anything that could be in the project's documents goes to step 2. Live information, or anything else outside the documents: answer from general knowledge if that is enough; otherwise offer a web search (step 4).
- **Step 2 (find), project scope.** Use the map to pick documents and sections. Read sections with `read_document_pages` for overviews and procedures. Use `search_documents` for names, labels and numbers, or when the map doesn't say where to look. If the results don't answer, search again with other words, or within the documents named in the map.
- **Step 3 (check):** unchanged.
- **Step 4 (answer).**
  - Each fact from a document cites it: `(Name, page N)` in project scope, `(page N)` in document scope.
  - When the documents don't cover part of the question, say so plainly. General knowledge may fill that part, introduced as not from the project's documents, and never with a page.
  - When the answer needs current or outside facts the model doesn't have, it calls `offer_web_search` with a short query (never document text).
  - **Amended:** on the turn after the user approved a search, the live-information line says the results are above and tells the model to read them (`read_web_pages`) or, for a different search, to call `offer_web_search`. In a chat with no answer step (no document tools and no passages to cite), the sentence that names the site of a web fact, `(example.com)`, goes on the live-information line instead. In a project, a search that finds nothing says "No passages about this in the project's documents." (`NONE_FOUND` is worded for the project, not "the document").

**Our call:** the gap-and-offer change applies to single-document chats too, so the rules stay one strategy. The golden prompt test is updated, so every wording change gets reviewed.

### 5.7 Citations and sources (fixes F3)

- **Format.** In project scope the model cites `(Name, page N)`, with the name exactly as the map gives it. The reply saves a scope note (`docContext.notes` entry `kind: "scope"`, `[{ref, docId, name}]`). The client links a citation only when its name matches an entry in that note, case-insensitively. The match comes from the saved note, never from the reply text alone (the same rule `citations.js` follows today). A name not in the note stays plain text.
- **Bare page numbers.** A bare `(page N)` links only when the reply's evidence comes from exactly one document.
- **Amended:** a citation is counted per part. A part is a name, a comma and a page form (`page 3`, `pages 3-5`, `p. 3`, `pp. 4 and 6`) that sits right after `(` or `;` and right before `;` or `)`, so a joined citation `(A.md, page 3; B.md, pages 1-2)` is two parts and a page list counts every page in it. A range counts every page when it spans up to 10 pages (`RANGE_PAGES`), else only its two ends. The server (`chat/sources.py`) and the client (`src/lib/citations.js`) apply the same rule to each part, and a bare `(page N)` counts for the source chips only when the evidence came from one document. *Reason:* the chips say which pages an answer used, so they must count exactly the citations the reader can click. Web sources are listed only for `http` and `https` addresses, whatever the letter case, and their titles are made inert; a search engine can return `javascript:` and `data:` links.
- **Prefetch note.** The prefetch note (`kind: "prefetch"`) has one `docId` today. In project scope it carries one entry per document that contributed passages; `citationDoc` in `src/lib/citations.js` must not treat a project reply as a single-document one.
- **Source chips.** `answer_sources` works on `(document, page)` instead of page. The `data-sources` event and the saved note carry a list per document: `[{docId, docName, pages, used}]`. The chips are grouped by document ("DEPLOYMENT.md · p.3, 6"). The document-scope shape still renders, so saved chats keep their chips.
- **Web citations.** A fact from the web cites its site, e.g. `(example.com)`. The sources note lists the pages read, with links.

### 5.8 Web: ask first, then raw results (fixes F5, the web half)

**Modes.** `ask` (default) | `auto` | `off`. All of them still require `SEARXNG_URL`.
- `ask`: every web search waits for the user's yes (below).
- `auto` is today's behaviour (web tools offered directly), but with the reader.
- `off` hides the web.

**User (2026-10-18): an admin toggle, under chat options.** The mode is set in a new **Admin → Chat options** section, by a three-way control labelled "Web search": *Ask first* / *Search without asking* / *Off*.
- **Which setting wins.** The first match wins, the same way the assistant profile works (`services/assistant_profile.py`):
  1. the admin console's choice (an `app_settings` row `chat_options`, `{"web_search": "ask"|"auto"|"off"}`);
  2. the `CHAT_WEB_SEARCH` env var, which is the deployer's starting value;
  3. `ask`.
- **When a change applies.** The row is read once per turn (one primary-key read, no cache), so a change applies from the next message on every worker.
- **Without SearXNG.** When `SEARXNG_URL` is unset, the control is shown disabled with the line "Web search isn't set up on this server (SEARXNG_URL)."
  - **Amended:** `SEARXNG_URL` has a default (`http://localhost:18043`), so the variable is never simply unset. The control is disabled when a deployer sets it to an empty value, which is how web search is turned off for a whole server. `GET /v1/admin/chat-options` returns `searxng: false` in that case.
- **API.** `GET`/`PUT /v1/admin/chat-options`, gated by the `admin` capability like the other admin routes. A change writes an audit line: who, the old mode, the new mode.
  - **Amended:** `PUT` takes `{"web_search": "ask"|"auto"|"off"|null}`; `null` deletes the admin's row, so the `CHAT_WEB_SEARCH` value (or `ask`) applies again. The section shows where the current value comes from ("Set here.", "From the server setting CHAT_WEB_SEARCH.", "The default.") and, when it was set here, a **Use the server setting** control that sends `null`. *Reason:* without it, saving a mode once would leave no way back to the deployer's value from the screen.
- **Scope of the section.** Chat options holds only this setting for now. Later chat-wide options (C2.2, roles) belong in the same section and row.

**User (2026-10-18):** ask before searching, and in `ask` mode *every* web search needs the yes, live-information questions included. The rule is enforced in code (the web tools simply aren't offered) rather than hoped for from the prompt. That matters because a small model once sent "the project's codename" to the web every time (see the `web_search.py` note).

**The offer.** In `ask` mode the only web tool offered is `offer_web_search(query, reason)`. A call ends the model's step: the turn finishes with the text written so far plus an offer, saved on the reply (note `kind: "web_offer"`, `{query, reason}`). The call is saved like any tool call, with the result "Offered to the user; they decide", so the next turn's history reads coherently. A user who types "search the web for X" gets the same card, prefilled with X, one click away. That keeps the yes a deliberate act. The chat shows an offer card under the reply with:
- the reason;
- the query in an editable field (≤ 300 characters), so the user sees and controls what leaves the server;
- **Search the web** and **Not now**.

**Amended:** the card removes characters that render as nothing or reorder text from the reason and the query: zero-width and direction-changing characters (marks, embeddings, overrides, isolates), the word joiner and invisible operators, the byte order mark, the Unicode Tag block and the rest of the invisible format characters, and control characters; tabs and line breaks become one space. The server does the same to the approved query (`clean_query` in `chat/web_consent.py`), so the query sent is the query shown even if a request skipped the card. A query with nothing visible left is no approval: the turn runs no search and the log records only that an approval was ignored, never the query. *Reason:* a model can write a query whose visible part is harmless and whose invisible part is not, and the user's yes must cover exactly what leaves the server.

**Approval.** **Search the web** sends a new turn: the visible message "Search the web for: <query>", plus `context.web = {query}`. The server then:
1. runs `web_search(query)` itself as the turn's first tool call, shown like the prefetch;
2. offers `web_search` and `read_web_pages`, plus the document tools, for this turn only.

The approved query skips the copied-from-the-document guard, because the user wrote it. Queries the model writes keep the guard.

**Amended:** consent is narrower than step 2 above says. `web_search` is offered **only in mode `auto`**. On an approved `ask` turn the app runs the user's query itself (as a `web_search` tool exchange with call id `app-web-1`) and offers `read_web_pages` and `offer_web_search`, plus the document tools, and never `web_search`. *Reason:* the user's rule is "every web search asks first". A yes is for the one query they saw; letting the model run a second, model-written query on the same turn would send a search out that nobody approved. A different search is a new offer and needs a new yes. The rule stays enforced by what is offered, not by the prompt. `offer_web_search` is exempt from the turn's tool-result budget and the repeated-call check (`unmetered`): its result is about 150 characters and must always reach the turn, or the offer would be lost.

**`web_search` returns search results only.** Each result is `{n, title, url, site, snippet}`, straight from SearXNG: about 1,500 characters for five results. There is no page fetch and no model call.

**`read_web_pages(results, need)`: a reader sub-agent.** This is the user's design (2026-10-18).

*Why a reader:* raw pages would clog the chat model's context. Three full pages can exceed the window the chat runs with (whatever the user's Settings ask for, capped by `INFERENCE_NUM_CTX_MAX`, default 32,768 tokens). A prompt longer than the window gets cut, so the answer derails or stops midway. Even when the pages fit, on a CPU every extra token in the prompt costs processing time on every step of the turn. Today's summarizer protects the window but loses the detail: it gets only the search query, never the task; it sees the page's first 6,000 characters; and it writes 2–4 generic sentences. The reader keeps the protection and fixes the loss.

1. **Arguments.** `results` is 1–3 result numbers from this chat's web searches, and nothing else. That is the exfiltration guard: a URL found in document text could carry document text out (`https://attacker.example/?q=<secret>`). `need` is the chat model's instruction: what to find and what to bring back, e.g. "the exact dnf package names on RHEL 9 for Python 3.13, ffmpeg and espeak-ng".
   - **Amended (ruling R1):** `read_web_pages` takes only results from the same turn's searches, not the whole chat's. The results are held per turn (`ToolContext.web_results`), and the history sent to the model carries only the text of earlier replies, so a number from an earlier turn would name a result the model was never shown. A number outside this turn's results is refused with the list of valid ones.
2. **The app prepares each page.** It fetches it with the existing guards (public addresses only, 2 MB cap, text types), extracts the whole text with trafilatura (the 6,000-character cut goes), and splits it into passages of about 1,000 characters. It keeps the passages that best match the need and the query, in page order, up to the reader's batch size (`READER_INPUT_CHARS`, §5.4.1).
3. **The reader reads each page** (§5.4.1), one batch per page, with the passages labelled `[web n]`.
   - **Amended:** each page is cut at line breaks, as the extractor writes them (a row of a list or a table stays whole, so a quote of it stays in one passage), then packed into passages of about 1,000 characters; a line longer than that is cut at sentence ends. The passages that share the most words with the need and the search are kept, in page order, up to `READER_INPUT_CHARS`; a page that shares no word with them is read from the top. Each page is its own batch, so one slow or failing page costs only itself.
4. **What the chat model gets.** Per page: the title, the URL, `found`, and the facts with their quotes. Three pages come to about 4,000 characters at most.
5. **Failure.** When a reader fails or times out, that page returns its best-matching passages, capped at 1,500 characters and marked "unread excerpt", so the chat model still gets something bounded. A web page, unlike a document, can't be reread with another tool.

**Web text is untrusted data.** It is fenced in the reader's prompt like document text, and the reader's output is data to the chat model.

### 5.9 Screens and journeys

**Scope chip.** The scope chip replaces the "Use this document" switch in the chat header (`ChatView` `DocUseSwitch`). It shows the scope: "No document", "This document: <name>" or "Project: <name>". Its menu offers:
- no document;
- the open document;
- each project that holds the open document as a current version;
- "Choose a project…": the projects the user can see, from the existing projects list.

The same chip appears in the side panel (compact) and on the phone. **Our call:** one control instead of a switch plus a chip.

**Project page.** The project header gains an **Ask about this project** button. It opens the Chat view with a new chat scoped to the project. It is shown to every member, since reading is all it needs.
- **Amended:** the button is shown when the user has the `chat` capability (the Chat view needs it) and a role in the project (`my_role` on the project the page already loads; no extra request). Without the capability, or as a site admin who is not a member (`my_role` is null, though the page still shows them admin controls), the project page has no such button. It was first gated on the capability alone, and the running-app walk found a non-member admin's first message refused (§5.1: a non-member admin can't chat with a project).
- **Amended:** a chat whose project the reader can no longer see shows **Project unavailable** on the chip and in the chat list (the server sends the project's id but no name). Only the server's own 404 "This project doesn't exist or you don't have access." clears the chat's project, with the toast of that text and the refused message put back in the box; any other 404 (the chat, a proxy) keeps the scope and shows its usual notice. After the clear, the chip falls to *This document* if a document is open and in use, else *No document*.
- **Amended:** the Chat options section shows a source line under the choices ("Set here.", "From the server setting CHAT_WEB_SEARCH." or "The default.") and, when the value was set there, **Use the server setting** (§5.8). Saving a change shows a notice that it applies from the next message and refreshes the assistant-rules preview in the Assistant section, which shows a document chat's rules in the current mode.

**Chat list.** Project chats carry the project's name as a label.

Each journey names the control for every step:

| # | Journey | Controls |
|---|---|---|
| J1 | Overview: Project page → **Ask about this project** → type "What are the features?" → answer cites several documents → click "(README.md, page 1)" → the reader opens README at page 1 | button, composer, citation link |
| J2 | Reader → open a project document → side panel chip → **Project: X** → ask a cross-document question | chip menu |
| J3 | "I'm on RHEL 9, which packages?" → answer with cited requirements plus labelled general knowledge, then an offer card → edit the query → **Search the web** → answer adds web facts citing sites | offer card, query field, button |
| J4 | **Not now** on the offer → nothing is sent; the chat continues | button |
| J5 | Removed from the project → next message in that chat → "This project doesn't exist or you don't have access." → chip shows "No document" | error toast, chip |
| J6 | A project with a document still indexing → the map says so; an answer that needs it says it isn't searchable yet | — |
| J7 | Reopen a project chat from the chat list on another device → chip shows the project | chat list |
| J8 | Phone: same as J1 from the Chat view | chip, composer |
| J9 | Admin → **Chat options** → Web search: *Search without asking* → a user's next live question searches directly; set *Off* → no offer card appears, and the answer says the web isn't available here | three-way control |

### 5.10 Access, versions and safety

- **Access is re-checked every turn** (§5.1), so a removed member's next turn fails, and search only ever covers `list_project_documents`: current versions of that one project.
- **Old transcripts stay.** A removed member keeps their old chat transcripts, as today when a shared document is unshared. **Our call:** consistent with today; nothing is redacted.
- **Text never leaves.** The map and tool results never include documents outside the scope. Document text never goes into a web query (existing guard) or a fetched URL (§5.8).
- **Existing guards hold:** the per-answer tool-result budget (`CHAT_TOOL_RESULT_BUDGET_CHARS`) and the final tools-off step.

### 5.11 Configuration (all env, safe defaults)

| Variable | Default | Meaning |
|---|---|---|
| `CHAT_PROJECT_MAP_CHARS` | 6000 | Character budget of the project map (§5.2) |
| `CHAT_PROJECT_MAX_TOOL_ROUNDS` | 5 | Tool rounds in project scope (§5.4) |
| `CHAT_PROJECT_FUSION_DEPTH` | 50 | Candidates per list before fusing, project scope (§5.5) |
| `CHAT_SEARCH_PER_DOC` | 2 | Passages per document before others get a turn (§5.5) |
| `CHAT_WEB_SEARCH` | `ask` | `ask` / `auto` / `off`: the starting value; Admin → Chat options wins once saved (§5.8) |
| `READER_MODEL` | (falls back to `SUMMARIZE_MODEL`) | Model the reader runs on; `chat` = the turn's own model (§5.4.1) |
| `READER_INPUT_CHARS` | 12000 | Source text per reader batch (§5.4.1) |
| `READER_OUTPUT_CHARS` | 1200 | Most the reader returns per batch (§5.4.1) |
| `READER_MAX_CONCURRENCY` | 2 | Reader batches at once (§5.4.1) |
| `READER_TIMEOUT_S` | 60 | Per-batch time limit; `WEB_SEARCH_SUMMARY_TIMEOUT_S` still works (§5.4.1) |
| `CHAT_EXTRACT_MAX_PAGES` | 12 | Pages one `extract_from_documents` call reads (§5.4.1) |
| `CHAT_EXTRACT_OUTPUT_CHARS` | 4000 | Most one `extract_from_documents` call returns (§5.4.1) |

`WEB_SEARCH_MAX_PAGE_CHARS` stops being used. `.env.example` and `docs/DEPLOYMENT.md` must be updated.

### 5.12 Data

Migration 021 (the `chat_options` row needs no schema change: `app_settings` exists):
- `chat_sessions.scope_project_id`;
- the `doc_outlines` table;
- no constraint change for `chunk_type` (it is free text today; `'code'` is a new value).

The session list and session read endpoints return `scope_project: {id, name} | null`, with the name only when the caller can still see the project.

**Amended:** the field is named `scopeProject`, like the other fields of the session record (`messageCount`, `updatedAt`). When the caller can no longer see the project, `scopeProject` is still present, with `name: null`, so the chat knows it had a project.

## 6. Data flow of one project turn

1. Validate the request → `require_project_role` → `list_project_documents` → `ensure_current` on each indexed document.
2. Build the map, using outlines from `doc_outlines`.
3. Prefetch over the project.
4. Model steps with the project tools, up to `CHAT_PROJECT_MAX_TOOL_ROUNDS`.
5. A final tools-off step if the cap is reached.
6. Source notes per document; a scope note; a web offer note if one was made.
7. Save.

## 7. Errors and edge cases

- **Nothing searchable.** A project with no indexed documents: the map lists them with their states, no document tools are offered, and the assistant says nothing is searchable yet.
- **Wrong document reference.** An unknown `documents` / `document` value: the tool returns an error naming the valid refs and names.
- **Scope changes mid-chat.** Allowed: the chip changes `scope_project_id`, and earlier replies keep their own scope notes, so their citations still open the right files.
- **A project deleted mid-chat.** `ON DELETE SET NULL` clears the scope; old replies' citations open documents the user can still read, and otherwise show the existing "can't open" behaviour.
- **Web problems.** SearXNG down during an approved turn: the app-run search returns an error result, and the model answers from what it has. A page that can't be fetched: `read_web_pages` reports an error for that result, and the model may read another. A reader that fails or times out: the bounded "unread excerpt" fallback (§5.8). A reader that returns JSON that doesn't parse: treated as a failure. A page where every quote fails verification: `found: false`, with the count logged.
- **Offer with no explanation.** The model calls `offer_web_search` without writing anything first: the card's reason line stands alone. That is fine.

## 8. Testing

**Unit tests:**
- Map building and shortening, at the budget edges.
- Ref and name resolution.
- Per-document balance and fill.
- Partial word matching.
- `answer_sources` on `(document, page)`.
- Citation parsing with names (including names with commas and parentheses).
- Offer and approval flow: the web tools are absent before the yes and present after it.
- The `read_web_pages` allow-list (only this chat's result numbers).
- The reader: quote verification drops invented facts and corrects a wrong passage label; the JSON contract; the fallbacks on failure or timeout (web excerpt, document "not read"); `num_ctx` set on every reader call; the `READER_MODEL` fallback chain, including `chat`.
- `extract_from_documents`: the page cap, page choice by the vector search when over the cap, the "pages not read" list, and `(document, page)` evidence feeding the source chips.
- Code chunks keeping the reader's page layout: the segmentation fixtures still pass, and new fixtures pin the code chunks.
- Outline extraction from Markdown, PDF bookmarks and Docling pages.
- Migration recording.
- Chat options: which setting wins (admin row → env → `ask`); the disabled state without `SEARXNG_URL`; the admin-only `PUT`; the audit line; a mode change applying on the next turn.

**Golden prompt test** (`test_chat_prompt.py`): updated deliberately.

**Permission matrix:** the project role checks on the turn route sit beside `test_project_permission_matrix.py`.

**App walk:** J1–J8 on a scratch database with a throwaway Keycloak (never the compose Keycloak), a fake slow OpenAI-compatible model for the stream, and a fake SearXNG for J3.

## 9. Evaluation (build first, then measure each layer)

- **Harness.** A new `server/evals/project_qa.py` beside `doc_qa.py`, running the real turn path (`run_turn`).
- **Fixture.** A frozen snapshot of `docs/` and `README.md` at a fixed commit, copied under `server/evals/fixtures/project_docs/`, so editing the docs never moves the eval. Plus 30 generated padding documents for the 50-document cases.
  - **Amended:** variant 50 is the 9 fixture documents plus 41 generated padding documents (9 + 41 = 50), not 30. *Reason:* "50" is the project size the cases are named for, and the 9 fixture documents count toward it.
- **Cases**, about 14:

| Kind | Example | Scored on |
|---|---|---|
| overview | "What are the features?" | mentions ≥ N listed features; cites ≥ 3 documents; no wrong-document citation |
| procedure | "How do I deploy and test it, step by step?" | contains the start command and the test command from code blocks; cites DEPLOYMENT and TESTING; records whether `extract_from_documents` or `read_document_pages` was used |
| code | "Which command starts the containers?" | exact command present |
| exact label | a named setting or variable | right value, right document and page |
| named document | "What does the user guide say about pins?" | cites only that document |
| balance | an answer in a short document while a long one has many near-matches | the short document is cited |
| gap + general | "Which packages on RHEL 9?" | says the documents don't cover RHEL; no page cited for the RHEL part; offers a web search (mode `ask`) |
| live | "What's the latest Ollama release?" | offers, makes no web call |
| web read | an approved turn against a fake SearXNG and saved fixture pages (one with the answer past the first 6,000 characters, one with a near-miss version number) | the exact strings are kept; no fact without a verified quote; the answer cites the site |
| map shortened | the 50-document variant of overview and code | same scoring; `document_outline` used or not needed |

- **The single-document eval changes too.** `doc_qa.py`'s "absent" cases change with §5.6: an answer passes when it says the document doesn't cover the fact and invents no page. An offer of a web search counts as passing, and its "live" case expects an offer, not a web call.
- **Baseline first.** Run once with only the scope widened (no map, no balance, no code chunks) to get baseline numbers. Then report per layer: code chunks → one ranking + balance → map → citations → strategy → web.
- **Models.** Iterate on cloud models (`ollama:gemma4:31b-cloud` or OpenRouter), then one final local `gemma4:e4b` run. Targets in §2.

## 10. Left to the builder

- The exact SQL for one-ranking search, and whether partial word matching uses `to_tsquery` with OR plus a matched-word count or another formulation, as long as §5.5's behaviour and the 300 ms target hold.
- How the rebuild reuses existing embeddings (§5.3 requirement), and whether `ord` renumbering after inserting code chunks affects anything that reads `ord`. Check `read_document_pages`'s join order, the neighbour logic, and the client's own chunk extraction in `src/hooks/usePdfEngine.js`: confirm it no longer feeds indexing, or keep it in step.
- The passage-ranking method that picks a page's passages for the reader (word overlap or in-memory BM25 over the need and the query; no embeddings needed).
- The reader brief's exact wording, within §5.4.1's rules.
- The offer card's look, within the existing chat styles.
- Whether overview answers are starved by the per-answer tool-result budget (`CHAT_TOOL_RESULT_BUDGET_CHARS`, 24,000 characters) when several documents' sections are read. If the eval shows it, add a project-scope default, kept small enough for an 8,192-token window, rather than raising the global one.
- The order of tasks, except that the eval harness and baseline come first.

## 11. Our calls (for the user to check)

1. (Decided by the user 2026-10-18: every web search needs the yes, live information included.)
2. The gap-and-offer strategy applies to single-document chats too (§5.6).
3. The scope chip replaces the "Use this document" switch (§5.9).
4. A non-member admin can't chat with a project (§5.1).
5. Removed members keep their old transcripts (§5.10).
6. Map budget 6000 characters; 5 tool rounds in project scope; 2 passages per document; 12 pages per extract call; reader batches of 12,000 characters (§5.2, §5.4, §5.4.1, §5.5).
7. The eval fixture is a frozen snapshot of our own docs, plus padding documents (§9).
8. Targets: ≥ 90% right-document citations on a cloud model; local model reported only (§2).

## 12. Research summary (what was borrowed, from where)

- **Map first, two-stage routing.**
  - Route over the file list, then inside the outlines of the files kept; one write-up does this over 63 files and 4,211 pages (Towards Data Science, "Multi-Document RAG: A Folder of Unrelated PDFs Is One Long Document with a Nested Outline"; read via search summaries, the page itself blocked fetching).
  - PageIndex navigates a table of contents the same way.
  - Anthropic's context-engineering guidance: keep pointers, load the text just in time.
- **Retriever quality matters most for weaker models.** BrowseComp-Plus (ACL 2026): with BM25, GPT-5 scored 55.9% and Search-R1 3.86%; a stronger retriever raised GPT-5 to 70.1% with fewer searches. Strong models compensate by searching more, and small ones can't. That's why we invest in the map and the ranking, not a longer loop.
- **Meilisearch (the user's clone, `~/Documents/Github/meilisearch`).**
  - What we borrow: combined multi-source search; "distinct" (one hit per field value); word-dropping matching strategies; a search tool that describes each source; snippet cropping (deferred).
  - What we don't run: the service itself, because a second index of who-can-read-what drifts.
  - What we don't copy: its weighted keyword/meaning score mix, which needs calibrated scores that our cosine values aren't.
- **LibreChat (the user's clone).**
  - Its file search is the naive baseline: one meaning-only query per file, k = 5 each, merged by distance, top 10 (`packages/api/src/files/rag/search.ts`).
  - Its web search scrapes pages and reranks the original text, rather than summarising.
- **Web search for agents.** Search returns snippets, and the agent fetches 2–3 pages in full. Ollama, Exa and Tavily expose search and page-reading as separate tools.
- **Corrective RAG (CRAG)** checks the retrieval and falls back to the web when local results are weak. We put that decision with the person instead.
- **Parked:**
  - Contextual retrieval (a model writes context for each chunk at indexing; reported 49%, or 67% with a reranker, fewer failed retrievals; costs one model call per chunk).
  - Rerankers (Ollama has no rerank endpoint; CPU cross-encoders are reported at tens of milliseconds per pair, which we haven't measured).
  - Grep-only search (a 2026 study found grep strong for agents on exact strings; "how do I deploy" has no exact string, and our hybrid already covers words).
- **Caveat.** The figures above come from abstracts and secondary write-ups, not full papers; the code claims were read in the clones and in this repository.
