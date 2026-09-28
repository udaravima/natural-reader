# C1 running-app walk and stage-0 measurement

Date: 2026-09-28. Branch `development` at `5278ce7` (before the walk fixes).

## Setup

- Backend started directly, not through `./startup.sh`: `AUTH_ENABLED=false HOST=127.0.0.1 LOG_LEVEL=DEBUG .venv/bin/python run.py`. The local `.env` points login at a real Keycloak, and the loopback auth bypass avoids touching it. Signed in as the seed admin (`admin@example.com`).
- The SPA ran on the Vite dev server (`:5173`), which proxies `/v1` to `:8000`. Browser: Playwright MCP (Chromium).
- Ollama on localhost with `llama3.2:3b`, `qwen3.5`, `gemma4` and `nomic-embed-text`.
- Hardware: Intel i9-13980HX, CPU only, 30 GB RAM. The machine's NVIDIA GPU is disabled because of a hardware fault, so every timing here is CPU inference, much slower than a GPU deployment.
- First start logged `Applying migration 013_chat_orchestrator.sql` and `Inference providers: ollama (ollama)`.
- Second run used two providers: `INFERENCE_PROVIDERS=local,ollamav1`. `local` is native Ollama. `ollamav1` is `kind=openai` against `http://localhost:11434/v1`, limited to `qwen3.5:latest,llama3.2:3b`. That run started with `Inference providers: local (ollama), ollamav1 (openai)`.
- SearXNG was started for journey 7: `podman-compose up -d searxng`.

## Journeys (spec §2)

| # | Control | Seen | Result |
|---|---|---|---|
| 1 | Composer + Enter (`ollama:llama3.2:3b`; then `ollamav1:qwen3.5`) | Reply streamed. Stats line shown. Session appeared in the sidebar. With `ollamav1:qwen3.5` the "Show thinking" disclosure filled. | ✅ both adapters |
| 2 | Auto read-aloud (default settings) | Kokoro synthesised the first reply (backend log). Playback wasn't audible in the headless browser. | ◐ audio generated; playback not heard |
| 3 | Stop button, mid-reply | Partial text stayed, marked "Stopped". Saved `aborted` in the DB. The next message worked at once (no 409). The stopped step was charged about chars/4 tokens. | ✅ |
| 4 | Reader: select text → "Ask AI" (pins it) → chat | The pin showed above the composer and was saved with the chat (`pins` length 1). "How often is the rain gauge funnel cleaned?" → "every second Tuesday". | ✅ |
| 5 | Attach image (paperclip) to `ollama:qwen3.5`; follow-up; F5; follow-up | Described a red square and a blue circle. Follow-up with no re-attach: "square, red". After reload: "Blue". The bytes are stored in `chat_attachments` (744 B). | ✅ |
| 5b | Same image to `llama3.2:3b` (no vision) | HTTP 422. The draft text and the image went back into the composer. No model call. | ✅ |
| 6 | Opened `zephyr-notes.md` from the Reader, pressed **Index**, then chatted | "Used 1 passage from zephyr-notes.md". Correct answer (Dr. Ilse Varga, 42 knots). Prefetch top score 0.823, kept 1/4, one step. | ✅ (see stage 0 for the misses) |
| 7 | Chat: date; web question | Date: "Monday, September 28, 2026", no tool call. Web: `web_search` ran against SearXNG and the answer came from the results. All three page summaries timed out at 30 s, since one CPU-bound Ollama handles one request at a time, so the answer used the snippets. | ✅ |
| 8 | Admin → Users → budget 1 for the admin → send | One 429, no retries. The notice read "Daily inference budget exhausted — resets at 05:30 AM" and the draft came back. The budget was restored to unlimited afterwards. | ✅ |
| 9 | Picker with two providers; Settings → Chat & Inference | The picker was grouped into `local` (with capability badges) and `ollamav1` (capabilities unknown, so no badges). For an `ollamav1:` model, Settings showed only Thinking and Max reply tokens; Context window and Keep warm were hidden. Admin → Deployment config listed "ollama · ollama · localhost · 4 models" and "Embeddings (Ollama) URL". | ✅ |
| 10 | Sidebar Rename; Delete (confirm dialog); F5 mid-reply | Rename reached the server. Delete removed the chat and its messages. There is no chat **export** control, and there never was one before C1 either. On reload mid-reply, see below. | ✅ rename/delete; export missing |

### Reload mid-reply (journey 10): what actually happens

The spec says the reply "shows still generating… and completes". That isn't what production does.

- Killing the client connection mid-turn (curl, which behaves like nginx dropping the upstream) saved the reply `aborted` within 4 s and released the claim. The deterministic close from ruling 9 works.
- So a same-tab reload or tab close shows the partial reply as **"Stopped"**.
- "Still generating…" appears only when the chat is open somewhere else (another tab or device) while the reply is still streaming.
- Through the Vite dev proxy, one reload let the turn finish, because the proxy relays the disconnect late. Don't read dev-proxy behaviour as production behaviour.
- The user-facing docs (CHANGELOG, USER_GUIDE) state the production behaviour.

### Bugs found and fixed after the walk

1. **Stale model id after a provider rename.**
   - With the saved selection `ollama:qwen3.5:latest`, the admin set `INFERENCE_PROVIDERS=local,…`. The picker then *showed* `local:llama3.2:3b` while the app *sent* the stale id.
   - The server treated the unknown prefix as a bare name, and Ollama answered 400 "invalid model name".
   - The reply read "The model provider returned an error: invalid model name" under a picker showing a different model.
   - Fix: the SPA remaps by model name, and shows an "(unavailable)" option when nothing matches. The turns route refuses unlisted models with 422 `model_not_allowed`.
2. **A provider 400 before generation was charged an estimated 18 tokens.** Now an `error` step with no output and no usage isn't charged.
3. **The embedding model (`nomic-embed-text`) was offered as a chat model.** It's now omitted from the model list.
4. **`CHAT_PREFETCH_MIN_SCORE` default changed from 0.75 to 0.60** (see stage 0).
5. **Dead `/api` → Ollama proxy removed from `vite.config.js`.**

### Observed, not fixed (not C1 regressions)

- Web-search page fetches have no total time or size cap. One 111 MB PDF held a turn for 6.5 minutes.
- `llama3.2:3b` sometimes writes a tool call as JSON text, and that text is saved as the reply.
- With a prefetch hit, `llama3.2:3b` still called `search_document` in 3 of 5 runs. The steering preamble is weak for small models.

## Stage 0 measurement (spec §11)

Setup: document `zephyr-notes.md` (4 chunks), embedding model `nomic-embed-text`, chat model `local:llama3.2:3b` (Q4_K_M, CPU). Each run was a fresh chat created with `curl` against the turns endpoint.

**Top prefetch score per question**, computed with the production `embed_one` and `search_chunks`:

| Question kind | Scores | ≥ 0.75 | ≥ 0.60 |
|---|---|---|---|
| Answerable from the document (6) | 0.563, 0.635, 0.681, 0.745, 0.807, 0.823 | 2 | 5 |
| Unrelated (6) | 0.428 – 0.513 | 0 | 0 |

**Same question, "Who calibrated the anemometer…?" (top score 0.823), 5 runs each:**

| Prefetch | Median duration | Steps | Median tokens | Correct | Tool used |
|---|---|---|---|---|---|
| On (0.75) | 7.5 s | 1,1,2,2,2 | ~974 + 59 | 3/5 (2 were JSON-text tool calls) | `search_document` 3/5, redundant |
| Off (`MIN_SCORE=1`) | 63 s (8.6 s to 408 s) | 2 each | ~1,199 + 99 | 0/5 | `web_search` 5/5, the wrong tool |

**A question below 0.75** ("backup radio channel", 0.681), prefetch on: 5 of 5 runs called `web_search`, and 0 of 5 were correct.

**Conclusion, for this model and hardware only:**
- Prefetch is what makes document questions answerable. Without a hit, `llama3.2:3b` never picked `search_document`.
- 0.75 missed two thirds of the answerable questions. 0.60 would have caught 5 of 6 and let in none of the unrelated ones.
- The default is now 0.60. Deployers with other embedding models should tune it from the DEBUG `prefetch … top=` log lines.

### Why the model didn't use `search_document`, and whether the prompt could fix it

**What the model saw on a prefetch miss.**
- The history, a `Current time: …` line and the question.
- Nothing named the open document. Before C1, only a pin ever said "The user is reading …".
- `web_search`'s description claims "any factual question requiring live information".

**The change.** Two edits were made:
- The volatile system message now says `The user has "<name>" open. Questions about names, terms or facts you don't recognise are probably about it: use search_document before web_search.` It does this whenever an indexed document is attached and nothing was prefetched.
- `web_search`'s description now excludes questions about the open document.

**The test.** A/B run with prefetch forced off (`CHAT_PREFETCH_MIN_SCORE=1`), asking "Which radio channel does the Zephyr station's backup radio use?":

| Model | Old prompt | With the open-document line |
|---|---|---|
| `llama3.2:3b` | `web_search` 5/5, correct 0/5 | `web_search` 5/5, correct 0/5 |
| `qwen3.5` | `search_document` 3/3, correct 3/3 | `search_document` 3/3, correct 3/3 |

**Conclusion.**
- On this question the prompt wording made no measurable difference.
- Tool choice was decided by the model. The 3B model ignores both the tool descriptions and the instruction; `qwen3.5` gets it right without help.
- The line is kept because it states a fact the model otherwise lacks, at about 30 tokens per turn. It isn't claimed as a fix.
- What actually helps small models is prefetch hitting, hence the 0.60 default, or recommending a larger chat model for document questions.

## Can the user do what they asked for, start to finish, without curl?

Yes, for journeys 1 and 3–9. Journey 10 is covered except export. Journey 2's audio was generated but not heard in the headless browser.

What a user still can't do after C1:

- **Configure model providers in the UI.** Providers are set only in `.env`. The admin console shows them but can't edit them.
- **Attach audio.** Audio attachments are disabled in the UI (ruling R7).
- **See an old image after a reload.** It appears as a chip, though the model still sees it.
- **Export a chat.** No control exists, and none existed before C1.
- **Keep a reply going across a reload.** A reply the user reloads away from is saved as "Stopped". It only finishes if the chat is open elsewhere.
