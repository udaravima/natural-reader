# Session handover

> Working notes (now tracked in git). Latest session on top.

---

## 2026-10-26 — Admin console v2, phase 2a built and review-clean; the in-app walk is pending (START HERE)

**Branch:** `feat/admin-console-v2-phase2a`, cut from `development` at `2fadfa2`. **Not merged, not pushed, no version bump** (CHANGELOG entries sit under `[Unreleased]`). **Deployed anyway:** the live site has run this branch since 2026-10-26 11:39, from an image built at `0f9aec7` (before the final review); the final-review fixes are not deployed yet. Plan: `docs/superpowers/plans/2026-10-26-admin-console-v2-phase2a.md`. Workspace ledger (rulings, every review finding and which ones were deferred): `.superpowers/sdd/2026-10-26-admin-console-v2-phase2a/progress.md`, with each task's brief, report and review diff beside it. Spec: `docs/superpowers/specs/2026-10-25-admin-console-v2-design.md`.

**Commits** (`git log --oneline 2fadfa2..HEAD`, newest first; the docs commit that carries this entry follows them):

- `88484f4` feat(admin): search and filter People on the server
- `658423e` feat(settings): show the context size the server will use for a local model
- `0c0bdfc` fix(admin): a hidden invalid size no longer blocks Save in Models
- `bbe601a` feat(admin): the Models section
- `5d6dcfc` fix(admin): the reader line never fails because the model list does
- `fcfc134` feat(admin): Overview's reader and context lines follow the Models settings
- `dbde911` feat(admin): GET /v1/admin/models, the catalog the Models section chooses from
- `f87b38a` feat(models): the console narrows the chat models; the model list reports each local model's context size
- `7d667c8` fix(reader): a reader on a non-sized chat model sends no context size
- `facbe87` fix(reader): fall back to the chat model, choose it in the console, size it to its model
- `cc8a906` fix(chat): one context size per local Ollama model, and trim history to it
- `6aa3c9e` test(models): an empty host after the trailing-dot strip is cloud
- `c964d80` fix(models): size every self-hosted Ollama model, not only local-looking hosts
- `b1f74af` fix(models): count .local/.lan/.internal/.home.arpa hosts and 100.64/10 as local
- `80a86cb` feat(admin): the models settings section and its resolution rules
- `03acb1d` docs: admin console v2 phase 2a plan (Models, People search)
- `42bdcd6` fix(admin): say which Keycloak role to create when a capability's realm role is missing

**Gates at `88484f4` (the last code commit, 2026-10-26, measured for this entry):** backend `2417 passed, 16 skipped, 2 deselected` (was 2281 at the end of phase 1), run with `TEST_DATABASE_URL` and `TEST_ADMIN_DATABASE_URL` pointing at the scratch Postgres on 5434 (the live 5433 was not listening); frontend `1167 passed` in 118 files (was 1121 in 117); `npx eslint src` clean. Each task was reviewed on its own diff and ended review-clean; the ledger records no whole-branch final review, so run one before merging.

### What a person can do now (and where)

- **Choose the reader** (Admin → Models → **Reader**): the chat model of each conversation (the default) or any listed model. With none of `READER_MODEL`, `SUMMARIZE_MODEL`, `WEB_SEARCH_SUMMARY_MODEL` set, the reader is the chat model, no longer `llama3.2:3b`.
- **Set the context size of self-hosted Ollama models** (Models → **Context size for local Ollama models**, and **Per-model sizes**). Every request to such a model names a size (the per-model size, else the deployment's, else the person's own value, else 16,384; at most `INFERENCE_NUM_CTX_MAX`), history is trimmed to it, and a reader that is the chat model uses it too, so Ollama stops reloading the model between chat and reader calls. Env fallback: `INFERENCE_LOCAL_NUM_CTX`.
- **Declare a context window** for a model whose provider reports none (Models → **Declared context windows**, or `INFERENCE_<NAME>_CONTEXT_WINDOW` for a provider). Without one, history for that model is not trimmed and the Overview says "Unknown".
- **Mark a provider local or cloud** (Models → **Providers**: Auto / Local / Cloud). Cost flag only (the Overview's quota hint, later usage); it never changes sizing, so a self-hosted Ollama marked Cloud is still sized (final-fix ruling P11).
- **Narrow the chat models offered** (Models → **Chat models offered**). Intersected with the environment's allow-lists, never unioned. The turn route refuses a model outside it (422 `model_not_allowed`), and the picker lists only the ticked models.
- **Search People** (Admin → People: **Search people**, **Status**, **Capability**, 50 at a time with **Show more**, "Showing N of M").
- **Settings → Chat → Context window** shows "Set by your admin: N tokens." (locked) for a self-hosted Ollama model whose size the admin fixed, otherwise **Auto** names the size the server will use.
- Overview's **Reader** line is **OK** by default (it was a warning for the fallback); its **Context windows** line reports the size a request sends. Deployment's read-only list no longer has "Reading model".

**Still not possible without `.env` and a restart:** adding or changing a provider; changing the embedding model (phase 2b); usage by model (phase 3). **No migration** (`models` is one more `app_settings` row).

**Upgrade notes** (also in CHANGELOG and DEPLOYMENT): the reader moves to the chat model (cloud chat model: reader calls count against its quota; pick a local reader in Models); local Ollama models get 16,384 tokens instead of Ollama's 4,096, which reserves more memory when the model loads (size it down in Models or with `INFERENCE_LOCAL_NUM_CTX`), and a model already loaded at another size reloads once; a person's own context size stops applying to those models once the admin sets one.

### Rulings (full text in the ledger and the plan; "cost" = what changing it later touches)

Made while writing the plan (R, `docs/superpowers/plans/2026-10-26-admin-console-v2-phase2a.md`):
- **R1.** The deployment's size is `INFERENCE_LOCAL_NUM_CTX`. Cost if wrong: renaming one env var.
- **R2.** Size order: per-model, deployment (console then env), the person's value, 16,384; clamped to `INFERENCE_NUM_CTX_MAX`. Cost: the owner wanting 16,384 to beat personal values moves step 3 below step 4.
- **R3.** A non-sized model (Ollama cloud, `openai`-kind) keeps the person's value and the reader sends none. Cost: none known.
- **R4.** Locality guess, first match wins: `-cloud`/`:cloud` name, the admin's override, the host, else cloud. Cost: a dotted LAN name reads cloud until overridden (P3 widened the host rule).
- **R5.** The default reader (the chat model) is OK, not a warning. Cost: one state word.
- **R6.** A chat-model selection whose models all vanished offers nothing and the Overview warns. Cost: none known.

Made during the build (P, ledger):
- **P1.** A provider's `detected` flag applies the host rule alone (no model name, no override). Cost: one label.
- **P2.** The turn route reads the `models` row on its own short pool connection, fail-open, not a new `Depends(get_conn)` on the streaming route. Cost: a second read pattern.
- **P3.** `.local`, `.lan`, `.internal`, `.home.arpa` hosts and 100.64.0.0/10 count as local; one trailing dot is ignored. Cost: a wrong Auto label on an exotic public host with such a suffix (the admin overrides).
- **P4.** Sizing applies to every self-hosted Ollama model whatever its host; locality is the cost flag only. `sizes_context` is the predicate. Cost: one predicate swap.
- **P5.** A reader batch never grows past `READER_INPUT_CHARS`; it shrinks to fit the window. Cost: one word if the owner wants window-filling batches.
- **P6.** A batch holds at most 16 passages (`MAX_BATCH_PASSAGES`). Cost: a few extra reader calls on many tiny passages.
- **P7.** A reader on a non-sized chat model sends no size, even if the person set one. Cost: one line.
- **P8.** The model list's `context_size` key stays snake_case beside camelCase `limits`. Cost: one inconsistent payload, one route and one parser to rename.
- **P9.** Deployment's read-only list drops "Reading model" (it reads only the environment and would contradict a console choice). Cost: one row.
- **P10.** The Overview's unset-size copy prints the window actually used ("8,192 tokens unless …" when the ceiling is 8,192). Cost: one string.
- **P11 (Task 7, superseded).** A valid per-model size whose provider is flipped to Cloud stays in the map. Moot since the final-fix P11 below: flipping to Cloud no longer hides the box.

Made in the final fix (the ledger reuses the numbers P11 and P12; these are the later ones):
- **P11.** `sizes_context` ignores the admin's Local/Cloud mark; Cloud is cost-only. Sized = `ollama`-kind, model not `-cloud`/`:cloud`, host not ollama.com. Cost if wrong: a hosted, non-ollama.com Ollama endpoint marked Cloud is sent a `num_ctx` (it loads at our size instead of its own default).
- **P12.** Saving a Reader that is not "chat" and not allowed by the deployer's allow-lists (`is_allowed`, as the chat path) is refused with 422 ("That model isn't in this deployment's model list."); the Overview's Reader line is an error when the reader, from any source, names such a model. Chat turns are unchanged. Cost: one check.
- **P13.** A Context window row locked by an admin size shows that size; the person's stored value is kept and applies again when the admin clears it. Cost: one display line.

### What was NOT verified

- **The in-app walk (plan Task 11) has not run.** Everything above is backend tests with fakes and jsdom component tests. Not seen in a browser: the Models section end to end (save, reload, **Use the server setting**), People search and **Show more** against real data, the Settings lock, the phone layout and the 44 px targets (class-tested only), dark mode, a screen reader. Walk with the backend on port 8100 and a scratch database, never 8000 or 5433.
- **Ollama's reload behaviour was not measured here.** "One size per turn stops the reload" rests on the spec's reading of Ollama's scheduler (`sched.go`); no timing was taken with a real model. The 16,384 default's memory cost on the owner's machine is likewise unmeasured.
- **No cloud eval was run.** Spec §5 requires one before a release, on `ollama:gemma4:31b-cloud` or OpenRouter (never local `llama3.2:3b`); it needs the owner's go-ahead. The one final local `gemma4:e4b` run is still owed too.
- **Walk checks left by reviews:** the locked Context window select shows the person's stored value (e.g. 8192) beside "Set by your admin: 16,384 tokens." (two numbers); "Auto (16,384 on this server)" may clip in the `w-32` select; the Models context-size help says 16,384 while the catalog is still loading, even if the ceiling is lower.
- **Deferred minor review findings** (none blocks; ledger has the rest): `test_inference_router.py` defines the `client` fixture twice and the later one wins, so every test in it runs as the seed admin; a SQL error reading the `models` row on `GET /v1/inference/models` aborts that request's transaction, so the budget is omitted too, and `GET /v1/admin/inference/config` returns 500 if the row is unreadable; the turn route holds one extra pool connection per turn; the client ignores `num_ctx_max` when validating a size (the server refuses; a toast); People: a reload racing **Show more** can drop the appended page, a failed load reads "Showing 0 of 0", a search containing a NUL character is likely a 500 not a 422, past 200 loaded rows a reload after an edit collapses to the first 200; the Overview logs a warning with a traceback per load while a provider hangs.

### Open items

1. **Task 11, the walk** (above), then a whole-branch review.
2. **Merge into `development`** (fast-forward, as phase 1) and **push**: both need the owner's go-ahead. `development` is still unpushed from phase 1; v2.4.0 is not deployed.
3. **Release and deploy** are separate decisions. Phase 1's `TZ` upgrade note still applies, and a deployment that sets no reader gets the chat model as its reader on deploy.
4. **Phase 2b** (embedding engine and model) and **phase 3** (usage by model) are next in the spec.

---

## 2026-10-26 — Admin console v2, phase 1 built and walked

**Branch:** `feat/admin-console-v2`, cut from `development` at `9459201`. **Fast-forward merged into `development` on 2026-10-26** (`git log --oneline 9459201..development` lists the plan's commit first, then the tasks, the final-review fixes and this entry). **`development` is not pushed** (origin/development is still `955231d`). **Nothing deployed.** **The feature branch was pushed:** not by us. Something pushed this branch to origin four times with nobody running `git push` here: `328f68f` at 23:34:59 on 2026-10-25, then `7637c9a` (00:02:31), `469d2d8` (00:07:14) and `292a0ce` (00:07:47) on 2026-10-26 (from `git reflog show origin/feat/admin-console-v2`, all "update by push"). The cause is unknown (no git hook or Claude hook pushes this repo; VS Code's publish/sync is the suspect). Everything after `292a0ce`, including this docs commit, is local only. The owner never granted a push.

**Gates at `9a3f569` (the last code commit, 2026-10-26):** backend `2281 passed, 16 skipped, 2 deselected` (was 2139 passed at `9127981`), frontend `1121 passed` in 117 files (was 1022), `npx eslint src` clean.

### What a person can do now (and where)

Admins open the console with the **shield button in the header** (the profile menu has no Admin entry; the plan text said it had). It opens on **Overview**, with sections Overview, People, Projects, Documents, Chat, Defaults for users, Usage, Deployment (a picker on a phone).

- **See whether the deployment works.** Overview runs six checks (providers, reader, context windows, embeddings, Docling, web search), each OK / Warning / Problem / Off, in at most 5 seconds each, concurrently. **Check again** reruns them.
- **Set the deployment's timezone** (Deployment section). It decides the daily token budget's day (reset at the next local midnight, also on a daylight-saving night), the usage days, the "Today is" line without a browser zone, and People's dates. Console value, then `TZ`, then UTC. `tzdata` is now in `requirements.txt` (Windows).
- **Limit Docling conversion** (Documents section): who may convert, presets, OCR, picture descriptions, a page limit. The convert dialog greys out what is off ("Turned off by your admin"), and the server refuses a request that breaks a rule (403 `convert_not_allowed`).
- **Every setting in those sections has one look**: a source line (Set here. / From the server setting `VAR`. / The default.), **Use the server setting**, one Save, an unsaved-changes prompt. Each saved change writes an audit line.
- **No migration.** Settings are `app_settings` rows `deployment` and `documents`.

**What a user still can't do until a later phase:** choose the reader model or context sizes in the console (phase 2a), change the embedding model in the console (2b), see usage by model (3). The reader/context problems found on 2026-10-25 (a local model silently cut to 4k; the reader reloading the model) are still unfixed: they are 2a.

### Rulings that changed the plan (full text in `.superpowers/sdd/2026-10-25-admin-console-v2-phase1/progress.md`)

- **"Who may convert" is `readers` or `role`**, not "admins". `role` means people with the new **`docling` capability, a Keycloak realm role** (admins can always convert); the default `readers` keeps today's behaviour. `DOCLING_CONVERT_BY=readers|role`. The owner hadn't answered the question when Task 4 came up, so this was built on the recommendation: if wrong, revert the vocabulary entry, the realm-export role and the People checkbox and swap "role" back to "admins" (one task's worth).
- **The `documents` token scope carries `docling`** (conversion is document work), so a role holder's documents-scoped token converts. Other scopes don't.
- **OCR off refuses Accurate too**, because `docling_convert` runs OCR for Accurate whatever the flag.
- **An existing Keycloak realm must have the `docling` realm role created by hand.** The realm export only seeds a fresh database. Until then ticking docling under People fails at the Keycloak write (a 502, capability unchanged) while the Keycloak admin client is configured. A role granted in Keycloak applies at the person's next login; one ticked in People applies at once. Only needed for `role` mode.
- **The Overview reads the database first, then runs the network checks** (a check cancelled mid-query would break the connection).
- **Env fallbacks:** `DOCLING_CONVERT_BY=readers`, `DOCLING_PRESETS=fast,standard,accurate`, `DOCLING_ALLOW_OCR=true`, `DOCLING_ALLOW_PICTURE_DESCRIPTION=true`, `DOCLING_MAX_PAGES=0` (0 = no limit). The env value is the outer bound; the console can only narrow it.
- **The walk backend ran on port 8100**, never 8000: nginx on this host proxies the live site's `/v1` to 8000, and an `AUTH_ENABLED=false` backend there would serve the seed admin to the public.
- **44 px targets** were brought to the moved sections (People, Projects, Chat, Defaults, Usage) with sizing classes only, after the walk measured 13-34 px there (`1100e71`).
- Task 6's profile test asserts `>= n + 900`, not the plan's `n + 1000` (the plan's arithmetic was wrong; the delta is 999 tokens).

### What was walked, and what wasn't

Walked in the running app (headless Chromium, scratch database on 5434, `AUTH_ENABLED=false`, report at `.superpowers/sdd/2026-10-25-admin-console-v2-phase1/task-13a-walk-report.md`): Overview and each state; `/api/ps` really carries an integer `context_length` and a `name` (nomic-embed-text: 2048); Check again; the timezone, the unsaved prompt (Keep editing, Escape, phone variant), Save, reload, People's dates in Colombo, Use the server setting; Documents with OCR off and a limit of 2; the Convert dialog's greyed options and page gate, then a real 2-page conversion; the server's 403s by `curl` (6 pages, Accurate with OCR off, forced OCR); phone picker; keyboard (Arrow, Home, End, wrap); a canary `SESSION_SECRET` found nowhere in the page or the API payload.

**Not walked:**
- **A non-admin refused by "Only people with the Docling role."** It needs a second signed-in user, which `AUTH_ENABLED=false` can't give. Covered by `test_convert_limits.py` only. Walk it with the throwaway Keycloak on `:18081`, never the compose one.
- **The Context windows row of a *loaded* local chat model.** The `/api/ps` shape is confirmed live; the row itself is tested with fakes. Load a local model with one message, then Check again: it should read "Loaded now (Ollama)."
- **The 44 px fix** (`1100e71`) is class-tested (`phoneTargets.test.jsx`) but not re-measured in a browser; the walk predates it.
- Dark mode; the Keycloak write when a `docling` role doesn't exist yet.

### Open items

1. ~~Merge into `development`~~ (done 2026-10-26, fast-forward). Still open: push `development`, and find out what pushed the feature branch.
2. **Deploy** is a separate decision (v2.4.0 itself is also not deployed; see the 2026-10-25 entry). This work needs no migration. For `role` mode, create the `docling` realm role first. **Upgrade note: if the backend's environment sets `TZ` (this host's `.env` does), the daily token budget and the usage days follow it from the deploy, with no console action: budgets reset at local midnight instead of UTC midnight, the switch day gives an early or late reset, and usage days shift. To keep UTC days, choose UTC under Deployment.**
3. **Deferred review findings** (minor, none blocks; the ledger has the rest):
   - `GET /v1/admin/inference/config` (`admin.py`, from before this work) calls `urlsplit(...).hostname` unguarded: a malformed provider URL is a 500.
   - (Fixed after the final review: the embeddings check now makes its own request, so it no longer waits on the indexing semaphore, and a 404 reads "answered HTTP 404".)
   - A scheme-less URL (`OLLAMA_URL=localhost:11434`) shows an empty host in the Overview and Deployment lists.
   - A page limit with a PDF pdfium can't open skips the limit; a range starting past the end counts negative pages.
   - A day boundary in a zone whose local midnight doesn't exist (a DST gap at 00:00, e.g. America/Havana) is about an hour off.
   - Console a11y: `Check again` loses focus while it runs and there is no live region; small amber/green text is about 3:1 contrast; "Use the server setting" unmounts and drops focus; two `<h1>`s while the console is open (pre-existing).
   - An invalid page-limit text alone doesn't count as unsaved; edits made during a save need a second Save with no prompt.
   - Some scoped tokens (`read_aloud`, `chat`) drop `docling` by design; only `documents` carries it.
4. **Phase 2a** (reader falls back to the chat model, one context size per local model) is next in the spec; the §7 decisions are approved.
5. The old plan workspace `.superpowers/sdd/2026-10-25-admin-console-v2-phase1/` can be kept or deleted; the `nr-c21-testpg` scratch container is still running.

### Research note: Docling can already drive DeepSeek-OCR (read from the installed code, not run)

The installed Docling 2.142.1 ships a VLM-convert preset `deepseek_ocr` (`docling/datamodel/stage_model_specs.py` `VLM_CONVERT_DEEPSEEK_OCR`): the VLM pipeline renders each page at scale 2.0 and sends it to Ollama's OpenAI-compatible `/v1/chat/completions` with model `deepseek-ocr:3b` (pulled locally, 6.7 GB), prompt `<|grounding|>Convert the document to markdown.`, `max_tokens` 4096, and parses the reply with `ResponseFormat.DEEPSEEKOCR_MARKDOWN`. Docling 2.142 also has a legacy `ApiVlmOptions` form, `vlm_model_specs.DEEPSEEKOCR_OLLAMA` (timeout 90 s, concurrency 4).

Our code does not use it. `docling_convert._build_converter` gives **Accurate** a bare `VlmPipelineOptions()`, whose default is the `granite_docling` preset run in-process (transformers, on this CPU-only machine). Wiring DeepSeek in as a preset needs four things:

- **`enable_remote_services=True`**: Docling refuses any API VLM without it, even on localhost.
- **The Ollama URL from our own config.** The preset defaults to `localhost:11434`, which inside the backend container is the container itself.
- **A per-page timeout sized for CPU.** No timing has been measured.
- **A place in the console's allow-list.** It reads page images, so the "OCR off" rule should refuse it too.

It also loads a 6.7 GB model into the same Ollama as chat and embeddings, which is the reload problem phase 2a deals with. Backlog item 3 (DeepSeek-OCR vs Docling) starts here.

### Where to look

- `CHANGELOG.md` `## [Unreleased]` (written for users), `docs/USER_GUIDE.md` § Admin console, `docs/ARCHITECTURE.md` (the five new services `console_settings`, `deployment_settings`, `deployment_info`, `docling_limits`, `console_overview`; routes `GET`/`PUT /v1/admin/settings/{section}`, `GET /v1/admin/overview`, `GET /v1/settings/docling`), `docs/IDENTITY_AND_ROLES.md` § The `docling` capability, `docs/TESTING.md` § Admin console v2, phase 1.
- Spec: `docs/superpowers/specs/2026-10-25-admin-console-v2-design.md` (phase 1 is §6 item 1). Plan: `docs/superpowers/plans/2026-10-25-admin-console-v2-phase1.md`.
- The environment and safety rules in the 2026-10-25 entry below still apply unchanged (scratch DB on 5434, never 5433, never the compose Keycloak, never `pkill -f run.py`).

---

## 2026-10-25 — v2.4.0 released; six post-release fixes; eval "drift" diagnosed; admin console v2 designed

**Branch:** `fix/folder-index-docling-progress-doc-refs`, cut from `development` at `955231d`. **Commits on it:** `6ef6ef5`, `9127981`, and the commit that added this entry and the spec. **Not merged into `development`, not pushed.** **Tests at `9127981`:** backend 2139 passed and 16 skipped, frontend 1022 passed, eslint clean. **Releases:** v2.4.0 is on `master` (`ff71f75`), tagged, pushed, and is the GitHub release marked Latest; v2.3.0 also has a GitHub release now. **The live site is NOT redeployed** (see "Deploying 2.4.0" below).

### Starting a fresh session

Paste this as the first message:

> Read `HANDOVER.md` (the 2026-10-25 entry) and `docs/superpowers/specs/2026-10-25-admin-console-v2-design.md`. I approve the spec's §7 decisions [or: change X]. Write the implementation plan for **phase 1** of that spec with superpowers:writing-plans, on a new branch from `development` after merging the fix branch.

Before that session builds anything, the owner decides the first two items under "Waiting on the owner".

### What this session changed (all on the fix branch)

**Commit `6ef6ef5`:**

1. **Index works for a file opened with Open folder.** The reader keeps a workspace file's bytes (`docBytes` in `src/hooks/usePdfEngine.js`). `src/utils/docHash.js` `readOpenDocBytes` / `openDocHash` hash and upload those bytes, never a same-named library book's. Walked in the running app: folder → README.md → **Index** → "Indexed 3 chunks", stored under the file's own hash.
2. **Docling shows progress.** The convert job passes Docling 2.142's `progress_callback` (`services/docling_convert.py` `progress_from_event`). `_ConversionProgress` (`routers/docs.py`) stores the latest report in `documents.conversion_progress` (**migration 022**). It writes only the latest value and has at most one write in flight, posted from Docling's thread back to the event loop. The progress lives in the database, not memory, because of `WORKERS>1`. The button reads Queued → Preparing → Converting n/N → Assembling → Enriching n/N. A conversion Docling fails on offers Retry convert. Walked with a 6-page PDF.
3. **A project chat finds "17-README.md".** `server/chat/tools/scope.py` `resolve_document` takes a document's number and name together (`17-README.md`, `17. "README.md"`) or its quoted name, but only when both name the same document. Error lists now use the map's form: `1. "Alpha.md"`.

**Commit `9127981`:**

4. **`search_documents` names a document that isn't searchable yet** the way the other tools do: `Document 16 ("…") isn't searchable yet.` (`scope.documents_named_in_project`).
5. **Convert shows Queued until Docling starts.** Uploading a new PDF runs its own indexing first, which holds the document's lock. That indexing is redundant once the PDF is converted, but it is cheap and makes the document searchable sooner, so it stays.
6. **Tool specs fit the chat they're in** (`tools.tool_spec`). A project chat gets project wording for `search_documents` and `read_document_pages`; a one-document chat no longer gets the `documents`/`document` fields its code ignored. This is the "I2" change that was reverted before release on noise. It was re-measured side by side with a control, 3 repeats on `gemma4:31b-cloud`: project eval 33/42 against 32/42, document eval 27/27 both. Kept.

### The eval "drift", diagnosed

- **Not our code.** The shipped code `26ace78` scored 38/42 on 2026-10-20 and 34/42 today, the same as HEAD.
- **Not context length for the cloud model.** A 19k-token prompt to `gemma4:31b-cloud` with no `num_ctx` kept a word from its first line; Ollama's docs say cloud models get their maximum context.
- **About 2 passes per run are the eval's own scorer.** The model now writes "The documents **do not list** …", and `_REFUSAL` (`server/evals/doc_qa.py:103`) has no "list". Rescored, today's runs come to 35–36/42.
- **The rest:** sampling noise (the eval sets no temperature; four runs on unchanged code spanned 32–34). Ollama was also upgraded locally on 2026-10-22 (0.40.8); its effect wasn't separated.
- **A real bug found on the way, for local models:**
  1. Ollama gives a local model 4k tokens of context below 24 GiB of VRAM, so always 4k on this machine.
  2. With no `num_ctx` set, the app trims history against the model's *maximum* context (`chat/orchestrator.py:684-689`), so it never trims, and Ollama silently cuts the prompt.
  3. Also, Ollama reloads a model whenever a request's `num_ctx` differs from the one it was loaded with. The reader sends a per-batch size (`chat/reader.py:320-322`), so `READER_MODEL=chat` on a local model reloads the model when chat and reader calls alternate.

  Both are in the spec's phase 2a. Neither is fixed yet.

### Admin console v2: the design (`docs/superpowers/specs/2026-10-25-admin-console-v2-design.md`, revision 2)

The console gets the Settings window's layout: nine sections, one row pattern, and each setting's source shown with a reset. Console row, then env var, then default; allow-lists can only narrow. Revision 2 folds in a review whose four must-fix findings were verified against this repository and Ollama's source:

- the 768-dimension embedding probe must never trust a returned length, because Ollama cuts any vector to `dimensions`;
- changing the embedding model builds first, then switches, with a second `vector(768)` column;
- the rebuild queue moves into the database (`FOR UPDATE SKIP LOCKED`);
- one context size per local Ollama model, with the reader sized to it.

The spec also adds an embedding provider for both provider kinds, which is what removes the dependency on Ollama.

**Phases:**

1. The shell, timezone, and Docling limits.
2. Models, in two parts: **2a** the reader falling back to the chat model, plus the context-size fixes; **2b** the embedding provider and build-then-switch.
3. Usage by model.

The §8 measurements (gemma4:e4b as the reader, reload time) are deferred by the owner. Don't run them unasked.

### Waiting on the owner

1. **Merge** the fix branch into `development` (then push?).
2. **Approve the spec's §7 decisions:**
   - the reader falls back to the chat model;
   - local Ollama models get 16,384 tokens of context, one size per model;
   - Docling stays open to everyone with the reader capability.
3. **The eval scorer:** add "list" to `_REFUSAL`. Recommended.
4. **Deploying 2.4.0 plus these fixes.** Order matters:
   - back up the Postgres volume first: the compose Keycloak goes 26.0 → 26.8.0 and migrates its database irreversibly on first start;
   - run the `embedding_model` query from the release notes;
   - migrations 020–022 apply at backend start.
5. **Still open from earlier:**
   - the one local `gemma4:e4b` eval run (only with the owner's go-ahead);
   - replace the old access token (which one is in Claude's memory, deliberately not in git);
   - remove the `nr-c21-testpg` container when done;
   - delete or keep the old plan workspace under `.superpowers/sdd/`.

### Environment and safety rules (each one was learned the hard way)

- **The real database:** `127.0.0.1:5433`, database `natural_reader`. **Never write to it, and never run tests against it.** Without the variables below, pytest defaults to 5433.
- **The scratch database** is the container `nr-c21-testpg` (`pgvector:pg16`) on `127.0.0.1:5434`. Its data is on **tmpfs**, so stopping the container wipes every database in it. Start it with `podman start nr-c21-testpg`, then run tests with:
  `TEST_ADMIN_DATABASE_URL=postgresql://natural_reader:natural_reader@127.0.0.1:5434/postgres TEST_DATABASE_URL=postgresql://natural_reader:natural_reader@127.0.0.1:5434/natural_reader_test .venv/bin/python -m pytest -q`
  Shell variables don't persist between agent tool calls: put them on every command, or in a wrapper script.
- **Frontend:** `npx vitest run`, then `npx eslint src`.
- **The compose Keycloak is the owner's real identity server** (its `.env` points at the public auth host). Never enroll or test against it.
- **Walking the app** doesn't need Keycloak: `AUTH_ENABLED=false` is accepted on a loopback bind only. Run `run.py` under `env -i` with explicit values: `HOST=127.0.0.1`, a scratch `DATABASE_URL` on 5434, scratch `DOC_STORAGE_DIR` and `LOG_DIR`, `DOCLING_ENABLED=true` when needed, `OLLAMA_URL`, `INFERENCE_MODELS=gemma4:31b-cloud`, `EMBEDDING_MODEL=nomic-embed-text`. Then `npx vite --port 5173`.
  - Headless Chromium: use the Playwright in `~/.npm/_npx/*/node_modules/playwright` with `executablePath` set to `~/.cache/ms-playwright/chromium_headless_shell-1217/...`, because its own browser build isn't downloaded.
  - To open a folder, delete `window.showDirectoryPicker` in an init script, so the app falls back to its `webkitdirectory` input, which `setFiles(dir)` can fill.
- **Evals** (`docs/superpowers/handoff/2026-10-18-project-chat/EVAL.md`):
  - always a scratch database (the script refuses `natural_reader`);
  - always `SUMMARIZE_MODEL=gemma4:31b-cloud`, with `--model ollama:gemma4:31b-cloud --repeat 3`;
  - **never the local `llama3.2:3b`**;
  - comparing two versions needs a same-day control: a `git worktree` at the old commit, run in parallel against a second scratch database;
  - SearXNG isn't running, so the document eval's `live` case logs `ConnectError`, the same in both arms.
- **Processes:** never `pkill -f run.py`; stop a process by its PID, or by the agent's task ID.
- **Secrets:** never print `.env` values; check a key's presence with `grep -c '^KEY=' .env`.
- **Git:** commit or push only with per-action permission from the owner. Stage explicit paths.
- **Open source:** no deployment-specific value in code or docs; docs use example.com.
- **zsh traps:** quote globs (`--include='*.py'`), and don't use `#` as the `sed` delimiter on Markdown.

### Pointers

- `CHANGELOG.md` `## [Unreleased]`: the six fixes, written for users.
- The project chat's full record: `docs/superpowers/handoff/2026-10-18-project-chat/` (`HANDOFF.md`, `EVAL.md`, `WALK.md`).
- The owner's feature wish-list (bulk project upload, auto tags, DeepSeek-OCR vs Docling, embedding trials, MCP, agents, Markdown and PDF side by side, and more) is in Claude's memory as `project_feature_backlog.md`. The admin console v2 covers its timezone, Docling-limits, cloud-stats and embedding items.

---

## 2026-09-22 — Fresh-deploy debugging: founder lockout, login-flow bugs, library bounce, and the docs-404 (ownership guard) + access-model design discussion

**Branch:** `development` · **Commits:** `175ef05`, `df82ef3`, `b20a0cc` (all today) · **Tests:** backend 245, frontend 229 (guard 10/10) · Nothing merged to `master`. Two threads got fixed-and-committed; the last (docs-404) was **diagnosis only — no code**, and opened a design discussion the user is still deciding.

### Deployment topology (verified live this session — keep this, it's not written elsewhere)
- **This box IS the deployment host.** nginx `sites-enabled/`: `chat.example.com` (443, root `/var/www/chat.example.com/dist`), **`chat-ip-fallback`** (`server_name 192.168.11.245`, port 80 — Android/no-DNS access), `auth.example.com`, `default`. Both chat vhosts proxy `/v1/` → `http://127.0.0.1:8000/v1/` and `/api/` → `127.0.0.1:11434` (Ollama).
- **Backend is a bare uvicorn on `:8000` — NOT containerized** (sub-project D still pending). Compose only runs `postgres` (`127.0.0.1:5433`), `searxng`, `keycloak` (`18080`).
- **Postgres:** `127.0.0.1:5433`, db/user `natural_reader`, pw default `natural_reader`. Query: `PGPASSWORD=natural_reader psql -h 127.0.0.1 -p 5433 -U natural_reader -d natural_reader`.
- **`podman ps` on this box needs `env -u XDG_DATA_HOME`** (snap-storage mismatch) — see memory `startup-podman-env-gotchas`.

### Fixed & committed
- **Founder lockout — "account is active but has no access" for the pruned admin** (`175ef05`). Three disarmed safety nets aligned: (1) `admin-user` had **no realm roles** in `realm-export.json`; (2) `BOOTSTRAP_ADMIN_EMAIL` set routes the founder through JIT **branch 2** (email-match, **no capability floor**) instead of **branch 3** (first-user, force-grants `{admin,reader,chat}`); (3) `KC_ADMIN_*` unset ⇒ the callback self-heal ([auth.py](server/routers/auth.py) `if kc is not None`) is dead. Fix: added `"realmRoles": ["admin","reader","chat"]` to admin-user; wired `KC_ADMIN_CLIENT_ID/SECRET` in `.env` (gitignored); documented across DEPLOYMENT/IDENTITY_AND_ROLES/.env.example/deploy README/README/ARCHITECTURE. **`--import-realm` drift trap:** editing the export only seeds a **fresh** DB — the live realm (Postgres `keycloak` schema) is never re-seeded; must `down -v` + re-import or do live-realm surgery.
- **OIDC callback 500** (`df82ef3`). Keycloak returned `error=temporarily_unavailable` (no code) → `authorize_access_token` raised an unhandled `OAuthError` → 500. Fix: error-param short-circuit + `try/except OAuthError` → 303 redirect to login. Test `test_callback_idp_error_redirects_not_500`.
- **Logout "Invalid redirect uri"** (`df82ef3`). Two causes: live-realm drift (post-logout URI not registered) **and** an export bug — `post.logout.redirect.uris` was space-delimited but Keycloak splits on **`##`** (`Constants.CFG_DELIMITER`). Fixed the delimiter in the export; the **live realm still needs the URI registered**.
- **Library view bounce** (`b20a0cc`). `useViewModeGuard` coerced `library` (not a capability) back to reader; Library is intentionally **ungated**. Fix: `CAP_FREE_VIEWS = ['library']`. Answer to the user's question: **no, Library does not need its own capability** — the guard was the bug. Frontend-only ⇒ needs `npm run build` + redeploy to `/var/www/chat.example.com/dist` to take effect live.

### `POST /v1/docs 404` while indexing — diagnosed, NOT a bug, no code change
- **Not a routing 404.** Route is live (unauth `POST` → 401). It's the **anti-hijack ownership guard** in `register_document` ([docs.py:271](server/routers/docs.py#L271)): a `doc_id` that already exists owned by a **different** user → `404 "Document not found"` (deliberately vague to hide existence). `doc_id = sha256(file bytes)` ([docHash.js](src/utils/docHash.js)) ⇒ same file = same id across accounts.
- **DB state:** users = `udaravima@gmail.com` (`f7432eb4`, caps `{reader,projects}`) + `admin@example.com` (`00000000-…0001`, caps `{admin,chat,reader}`). **Both docs (README.md, OpenCode…pdf) owned by udaravima.** So indexing from `admin@example.com` a file udaravima owns → collision → 404.
- **The real knot:** identities are split two ways — the account that **owns** docs (udaravima) lacks `chat`; the account with `chat` (admin) owns nothing and can't re-register. Neither can own-and-chat the same file. Same identity-churn class as the founder lockout.

### Access model reference (verified this session — for whoever builds the design changes)
- **Ownership-gated** (`assert_owns_doc`, non-owner→404): register/index/chunks/convert/**delete**, and grant add/remove (`_require_doc_owner`, **no admin escape hatch** — admin can't self-grant via API; use SQL or act as owner).
- **Read-gated** (`assert_can_read_doc` = owner OR project-member OR grantee, [authz.py:26](server/auth/authz.py#L26)): `GET /{id}`, `/search`, `/markdown`, list — **no admin bypass**.
- **Delete cascade:** `doc_chunks`/`doc_pages`/`doc_grants` are all `ON DELETE CASCADE` on `documents(doc_id)` → no storage orphans. But other users' grants vanish **silently**, chat/pins citing the doc dangle, and per-browser IndexedDB copies are untouched.
- **Projects:** `POST /v1/projects`; members `PUT /v1/projects/{id}/members/{uid}`; file a doc `PATCH /v1/docs/{id} {"project_id":…}` (owner-only; must own/belong to target project). **No create-project UI in the frontend yet** — it only *lists* projects. A doc lives in **≤1 project**.
- **Admin asymmetry:** admin **can reassign** any doc's ownership (admin-only PATCH path, [docs.py:336](server/routers/docs.py#L336)) but **cannot read** it. Give-away without open.

### Design decisions raised — user is deciding, NOTHING built
1. **Same-file collision → auto-grant read instead of 404** (possession of bytes ⇒ read right; tradeoff: loses the existence-hiding the 404 provides).
2. **Delete → pre-delete dependency preflight** (surface other users' grants / chat refs before destroying; storage cascade already safe).
3. **Admin-read-all** — currently no; if added, keep opt-in/audited and **split from delete** (admin read ≠ admin delete).
4. **Build a real "New Project" + doc-assignment UI.**
- **Open question that shapes all four:** single-tenant (few trusted identities → collapse to one shared library) vs. genuinely multi-user (isolation is a feature)?
- **Given to the user as the immediate no-code fix:** create a shared *project* (owner udaravima, member admin, file both docs in) → both identities read+chat. Alternatives: grant udaravima `chat` in Keycloak, or grant admin read on the two docs (SQL, since grant API is owner-only).

### Next
- User decides tenancy model + which of the 4 changes to build → then brainstorm → spec → plan.
- **Deploy still pending:** `npm run build` + redeploy `dist` for the Library fix; **re-import realm (or live surgery)** for founder roles + the logout post-logout URI + granting udaravima/admin the roles they're missing.
- Nothing to `master` (standing rule).

---

## 2026-09-21 — Document library (RAG Phase 0) built end-to-end

**Branch:** `feat/document-library-rag` (off `feat/admin-console`) · **Tests:** backend **195**, frontend **208**, lint clean · Migration **009**. NOT merged, NOT on `master`.

### What shipped
The 10-task document-library plan (`docs/superpowers/plans/2026-09-20-document-library-rag-phase0.md`), executed subagent-driven with a per-task review gate. New owner doc: **`docs/LIBRARY.md`**.

- **Access model** — `can_read` = owner OR project-member OR grantee, resolved entirely in SQL (`server/auth/authz.py`: `assert_can_read_doc`, `readable_docs_where`). **404-indistinguishable**: a doc you can't read is indistinguishable from one that doesn't exist, and a stranger's doc can never surface in a list even on a matching filter (no Python post-filter).
- **Schema (009)** — `projects`, `project_members` (read membership), `doc_grants` (per-doc read), `documents.project_id` (FK, `ON DELETE SET NULL`) + `documents.tags` (`TEXT[]`). Self-registers v9. Independent of auth's 008.
- **API** — read routes (`GET /v1/docs/{id}`, `/search`, `/markdown`) widened owner→`can_read`; `GET /v1/docs?q=&project_id=&tag=` list; `PATCH /v1/docs/{id}` (owner: tags/project with cross-tenant IDOR guard; admin: ownership reassignment); doc grants + a `projects` router (CRUD + members), all owner-only writes, idempotent.
- **Frontend** — a **Library** view (`viewMode:'library'`, `LibraryPage.jsx`): list/search/project-filter, owner-only tag-edit/reassign/delete, read-only "shared" rows. Upload/register gained an optional project+tags picker (`src/lib/docMeta.js` `registerDocument`, per-document `useDocMetaPicker`).

### Review-caught fixes (unit tests passed; review found these)
- **IDOR / cross-tenant assignment** (2 security reviews): a doc owner could file their doc into a stranger's project → it surfaced in that project's members' lists. Fixed: non-admin must own/be-member of the target project (`b2655b7`).
- **Search race** (Task 8): stale in-flight `GET /v1/docs` could overwrite a newer query → request-id guard (`98eecb0`).
- **Keyboard leak** (Task 8): playback/Page-nav keys were live in library/admin, mutating the hidden reader → `inReader` excludes them (`98eecb0`; closed a pre-existing admin leak too).
- **Cross-document metadata leak** (Task 9, Important): the upload picker was session-level, so a project/tag chosen for doc A silently carried onto doc B → per-document `useDocMetaPicker` reset (`6d575d9`).

### Deferred / known
- **Owner-row delete** in the Library was added beyond the literal spec (safe: server owner-gated + confirm) — flagged to the user for a keep/remove call.
- A failed metadata `PATCH` after register is logged, not surfaced (doc still registers; the Library gives owners a recovery path). Could add a toast.
- Read routes gain `require_capability("reader")` only at **integration with the auth branch** (`feat/keycloak-identity-permissions`, migration 008) — deliberately not in this branch.

### Next
- **Final whole-branch review (opus)** of `feat/document-library-rag`, then report.
- Manual browser E2E (library list/search/share/upload-picker) — not yet walked.
- **Merge decisions** for both this branch and the auth branch remain the user's — nothing goes to `master` until the UI and all are complete.

---

## 2026-09-17 (final) — post-1.9.0 documentation completeness pass + admin-console spec

**Branch:** `feat/model-router-gateway` · **Tests:** backend 143, frontend 163, lint clean (re-verified after docs-only changes) · docs only, no code touched.

### What happened
User asked: comprehensive, ordered, complete docs + test cases for **everything after tag v1.9.0** (auth + model routing), plus a proper **separate admin page** design (implementation NOT on this branch). Audit → three real gaps found and fixed:
- **CHANGELOG `[Unreleased]` was missing the entire auth feature set** (only inference items were listed). Added: multi-user OIDC auth (JIT provisioning, sessions, PATs, ownership, admin mgmt, migrations 005/006), the local Keycloak rig (with the persistence semantics), security hardening — plus a **Breaking: every route now requires auth** entry under Changed.
- **ARCHITECTURE.md had zero inference-gateway coverage.** Added: `inference.py` in § Routers (validated envelope, byte-faithful streaming, 429 pre-check + `_UsageTap`, fail-open), `model_router.py` + `inference_budget.py` in § Services, migration 007, the `callChat()`/source-switch rewrite of the `useChatEngine` row, `inferenceSource` persisted setting, ChatSidebar/InferenceSourceSelect/budget-meter components, updated "one chat turn" flow.
- **deploy/README.md was stale**: said Keycloak uses "embedded store" (now the persistent `keycloak` schema in Postgres — with the fresh-volume-only init-script semantics + hand-create-schema command), said admin console password is `admin/admin` (bootstrap creds only apply on first volume init; user has since changed it), had no inference-gateway env vars, no offboarding warning. All fixed; added the live-verified Keycloak-deletion-doesn't-propagate note to the two-user walkthrough.

### New docs
- **`docs/superpowers/specs/2026-09-17-admin-console-design.md`** — the approved-in-principle design for the separate admin page, to be built on a future `feat/admin-console` branch. Covers: `viewMode: 'admin'` + shield entry with boot-time coercion for demoted admins, Users section (incl. **user deletion** with rails: no self/last-admin/seed-row deletes; delete = full wipe via existing cascades + PDF sweep), Inference usage dashboard, read-only deployment config endpoint, 17-item TDD test plan, rollout, open questions (soft-delete, CSV export, session visibility).
- **`docs/TESTING.md`** — the post-1.9.0 test map: every test case in every file and what it asserts (verified against source, not from memory), test conventions (ASGITransport/MockTransport/`db_conn`/`stream_response`), integration seams, the manual/live-verification ledger (gateway scripted pass + Keycloak propagation matrix), and known gaps (no E2E, OIDC flow mocked at claims level, usage/budget UI + user deletion pending admin console).
- **`docs/README.md`** — docs index: all ten docs in reading order with quick-routing pointers ("why can't this user log in?", "what env vars?", "which tests guard this?").

### Next
- **`feat/admin-console`** branch (spec above) after the gateway branch merges — the usage/budget endpoints it consumes are this branch's work.
- The remaining browser click-through (dropdown/meter/toast/local-mode parity) — still open, unit-covered.
- `startup.sh` has an uncommitted user edit (Keycloak wait 30→60 retries) — left alone on purpose.

---

## 2026-09-17 (later still) — Keycloak↔app propagation live-verified; user guide written

**Branch:** `feat/model-router-gateway` · docs + live verification only · **Tests:** unchanged (143/163, lint clean).
### The question
Does creating/deleting a user in Keycloak propagate to the app? **Live-verified end-to-end** against the running rig (Postgres + Keycloak + backend auth-on + Vite), using the Keycloak Admin API + a full scripted OIDC login dance (curl through `/v1/auth/login` → KC form → `/v1/auth/callback` → `nr_session`). Results:
- **Create in KC → nothing happens app-side until first login.** Verified: created `prop-test`, users table unchanged; after the login dance, the row appeared as `pending`/`member` with `oidc_sub` = the KC UUID (JIT provisioning).
- **Delete in KC → NOTHING propagates.** Verified: row stayed `active`, and the user's **existing session still worked** (`/v1/auth/me` 200 after KC deletion — the app never re-checks the IdP).
- **The email trap is real:** a second KC user with the same email got **409 "email already linked to another identity"** at login. And the dev DB already contained a **real orphan of this class**: `aakash.n@…` is `active` in the app but has no Keycloak identity at all (its sub has no `user_entity` row) — this is what happens when KC deletion happens without app-side disable, or a leftover from the H2-era store.
- **Correct offboarding order: disable in the app first (hard-revokes sessions), then delete in Keycloak.** Documented in IDENTITY_AND_ROLES.md + USER_GUIDE.md.

### Also this session
- `docs/USER_GUIDE.md` — end-user guide (sign-in states, PATs & why they exist — "tokens are on the Account panel, not Admin", admin buttons, Server/Local source, budget meter/429/UTC-midnight, FAQ). Linked from README.
- Test artifacts cleaned up: prop-test + prop-test2 removed from both stores (KC natural-reader realm back to just `udara.v`; app back to 2 rows).
- Notes for replaying the dance: Keycloak's login form `action` URL needs `&amp;` → `&` unescaping; header matching must be case-insensitive (`Location:` from KC vs `location:` from uvicorn); form session codes are one-time — the whole login dance must run in one shot; KC Admin API access tokens expire in ~60s; Admin-API-created users hit `VERIFY_PROFILE` unless `firstName`/`lastName` are set. Master-realm admin password is not the bootstrap `admin/admin` (user-changed).

### Next (unchanged from below, plus)
- Admin-side **user deletion** (to free blocked emails / clean orphans) is now a motivated follow-up alongside the `viewMode: 'admin'` panel.

---

## 2026-09-17 (later) — identity deep-dive doc + gateway live verification on `feat/model-router-gateway`

**Branch:** `feat/model-router-gateway` · **Tests:** backend 143, frontend 163, lint clean · **Working tree:** docs only.

### What happened
- User commit `286fa41` ("Keycloak Presistent settings") moved the realm out of Keycloak's embedded H2 into the shared Postgres (schema `keycloak`, seeded by `deploy/postgres/init/` on first volume init). This fixed the recurring 409 "email already linked to another identity" desync: with H2, every container recreation minted new `sub` UUIDs, so returning users stopped matching `users.oidc_sub`.
- Wrote **`docs/IDENTITY_AND_ROLES.md`** — the deep-dive answering "how do Keycloak users map to natural_reader users": two stores (Keycloak schema = credentials, `public.users` = accounts) joined only by the mirrored `(oidc_iss, oidc_sub)` pair; the three `resolve_or_provision_user` branches and their guards; roles live in the app (NOT Keycloak claims) and take effect immediately (Principal rebuilt from the users row per request); credentials (sessions/PATs, sha256-only); special identities (seed admin, dev bypass); and a **proposal** for a third `viewMode: 'admin'` (gated on `role === 'admin'`) to house users + the still-UI-less inference usage/budget endpoints. Cross-linked from ARCHITECTURE.md § auth.
- **Task 13's manual pass (scripted subset) — done and green**, against a live backend (dev bypass) + real Ollama: models endpoint (allowlist filters to `gemma4:latest`; budget key with UTC-midnight `reset_at`), allowlist 422, real NDJSON streaming (thinking chunks byte-faithful), `INFERENCE_DAILY_TOKEN_BUDGET=1` → models shows `remaining_tokens: 0` + chat **429 with structured detail** (accounting had already recorded the earlier stream — recording is unconditional, budget only gates), tool_calls passthrough (gemma4 emitted a proper `current_time_date` call), `GET /v1/admin/inference/usage` (today's 27 tokens for the seed admin — the row's email is the user's real one, i.e. already linked via `BOOTSTRAP_ADMIN_EMAIL`/first-login).
- Not yet human-clicked: the browser-side bits (Server/Local dropdown, budget meter, 429 toast, disabled send in local-mode parity). Unit-tested; final confidence click-through still open.

### Next
- **AdminPanel follow-up** (now designed in IDENTITY_AND_ROLES.md § last): `viewMode: 'admin'` surface with the usage view + per-user budget knob (`PATCH /v1/admin/users/{id}`; `day` from the usage endpoint is an ISO date, not a timestamp; absent field ≠ null in the PATCH semantics).
- Document-library RAG (unblocked by the gateway), then sub-project D.
- `origin/feat/model-router-gateway` is behind local — push only when asked.

---

## 2026-09-17 — Model-router gateway (sub-project E) built on `feat/model-router-gateway`

**Branch:** `feat/model-router-gateway` (branched off `feat/security-hardening` line) · **Tests:** backend 143, frontend 163, lint clean · **Working tree:** clean at last commit.

### What happened
- Implemented the full [gateway spec](docs/superpowers/specs/2026-09-17-model-router-gateway-design.md) (all phases E1–E3) via the 13-task TDD plan ([docs/superpowers/plans/2026-09-17-model-router-gateway.md](docs/superpowers/plans/2026-09-17-model-router-gateway.md)).
- **The pipe (E1):** new `server/routers/inference.py` — `GET /v1/inference/models` + `POST /v1/inference/chat` behind `get_current_user`; strictly validated envelope (`extra="forbid"` down to per-message fields, incl. `images` + `tool_calls`); byte-faithful NDJSON passthrough (uses `client.send(stream=True)` + closes in the generator's `finally` — the StreamingResponse trap); upstream errors forwarded status+body so the SPA's think/tools fallback chain still branches correctly. Frontend: `src/lib/chatTransport.js` seam, `useChatEngine`'s four `/api/chat` sites collapsed into `callChat()`, model list via the gateway, persisted **Inference source: Server | Local Ollama** setting in ChatSidebar.
- **The router (E2):** `server/services/model_router.py` owns everything — `INFERENCE_MODELS` allowlist (422 otherwise), task models (`SUMMARIZE_MODEL` w/ `WEB_SEARCH_SUMMARY_MODEL` fallback, `EMBEDDING_MODEL`), `INFERENCE_TIMEOUT_S`, `INFERENCE_DAILY_TOKEN_BUDGET`. `embeddings.py` / `web_search.py` / `docs.py` (embedding metadata) all read from it now — no service reads Ollama env directly anymore.
- **The budgets (E3):** migration `007_inference_budgets.sql` (`inference_usage` PK (user_id, day) — **day computed in Python, UTC**, no SQL default; `users.inference_daily_token_budget` NULL=default/0=unlimited), `inference_budget.py` service, 429 pre-check + `_UsageTap` stream accounting (only counts completed generations; fresh pooled conn in the generator `finally`; fail-open on DB errors). Admin: `GET /v1/admin/inference/usage` + budget field on the user PATCH (`exclude_unset` semantics: absent=untouched, null=clear). Frontend: budget rides `/v1/inference/models`, meter in ChatSidebar, 429 intercepted in `callChat` BEFORE the retry chains, send disabled at zero.
- **Docs/config:** `.env.example` gateway section; README (new Inference Gateway API table, threat-model `/api/*` rows removed, nginx `/api/` block deleted everywhere + `proxy_buffering off` moved to `/v1/`); CHANGELOG `[Unreleased]`.

### Mid-session incident (resolved)
- The branch was accidentally switched to `master` mid-implementation. **No commits were lost** — all were on `feat/model-router-gateway`; only uncommitted Task-6 scratch files were wiped by the force-checkout and were recreated. Note: `searxng/` is owned by a container uid; checkout errors there are fixed with `git checkout -f` (the files are identical).

### Not verified here / next
- **The live browser walk-through (plan Task 13 step 5) was NOT done** — needs Ollama + a real model: server-mode dropdown/streaming, tool loop through the gateway, image attach, local-mode parity, allowlist, and a budget-exhaustion 429. All of these are covered by unit tests with a mocked upstream; the manual pass is a final confidence check.
- **AdminPanel UI** doesn't surface the usage view / budget knob yet (API only). Small follow-up.
- **Document-library RAG is now unblocked** — its Phase 2 description generation routes through `model_router`'s `summarize` task, and Phase 3's multi-round loop checks budget via the 429 detail.
- Then sub-project **D** (containerization/prod Keycloak/TLS).
- The **admin usage endpoint returns `day` as a date object** — FastAPI serializes it; if the SPA later renders it, remember it's ISO date, not timestamp.

### How to run / test
- Postgres must be up: `env -u XDG_DATA_HOME podman-compose up -d postgres` (container stops between sessions).
- Backend: `.venv/bin/pytest server/tests/` (143). Frontend: `npm run test:run` (163), `npm run lint`.
- Manual: `./startup.sh up` (loopback dev bypass = seed admin) + `npm run dev`; chat defaults to Server mode → `/v1/inference/*`.

---

## 2026-09-17 — SPA auth UI + local OIDC rig built; merged onto `feat/security-hardening`

**Branch:** `feat/spa-auth-ui` (off, and merged back into, `feat/security-hardening`) · **Tests:** backend 92, frontend 150, lint clean · **Working tree:** clean.

### What happened
- Built the **SPA auth UI** — the frontend half of multi-user, so the app is usable through the browser (before this it only ran with `AUTH_ENABLED=false`). Followed the 9-task TDD plan ([docs/superpowers/plans/2026-09-17-spa-auth-ui.md](docs/superpowers/plans/2026-09-17-spa-auth-ui.md)) implementing the [spec](docs/superpowers/specs/2026-09-16-spa-auth-ui-design.md).
- **Pieces:** a `src/utils/apiFetch.js` credential seam (every `/v1` call sends the cookie; a 401 anywhere fires a global handler → back to the login gate); `src/hooks/useAuth.js` state machine (`loading/anonymous/pending/disabled/active/error` from the `/v1/auth/me` probe); `src/components/auth/AuthGate.jsx` + screens; App gated behind it; migrated **every** `/v1` fetch across App/useTtsEngine/lib/MarkdownReader to `apiFetch` (grep-audited); `AccountPanel` (PAT create/list/revoke) and admin-only `AdminPanel` (activate/disable/role) as new collapsible **Sidebar** sections.
- **Backend:** one change — the not-active `403` now carries structured `detail.status` (`pending`/`disabled`) so the SPA branches the gate screens.
- **Local OIDC rig:** `docker-compose.yml` gains a **Keycloak** service (host port **18080**, `start-dev`, imports `deploy/keycloak/realm-export.json`); a **Vite dev proxy** makes `:5173` same-origin with the backend; `deploy/nginx/natural-reader.conf` is a reference site for the prod-like path; `deploy/README.md` has the two-user walkthrough. Verified Keycloak imports the realm and serves correct discovery (`issuer: http://localhost:18080/realms/natural-reader`).

### Project state
- **A + B+C + SPA UI now all on `feat/security-hardening`.** Still **not pushed and not merged to master** (per the standing "keep everything here until the UI and all is done" instruction).
- **The live browser login flow was NOT walked here** — infra (realm import, discovery) and all unit tests are verified, but the human two-user click-through in `deploy/README.md` still wants doing before this goes near master.

### Not done / next
- Wire the **browser extension** to send a PAT (separate repo — the Account panel now produces the token). Until then the shipped extension breaks under `AUTH_ENABLED=true`.
- Sub-project **E** (server-side model-router gateway) and **D** (full containerization / prod Keycloak / TLS / secrets) — still pending. See [memory/project_multiuser_hardening.md].

### How to run
- `deploy/README.md` is the source of truth. Short version: `env -u XDG_DATA_HOME .venv/bin/podman-compose up -d postgres keycloak`; backend with the OIDC env from `.env.example`; `npm run dev`. The pending screen needs a **second** Keycloak user (first login always becomes admin).

---

## 2026-09-16 — Multi-user auth+authz backend done; merged A+B+C onto `feat/security-hardening`

**Branch:** `feat/security-hardening` (integration branch — both sub-projects live here now) · **Tip:** `c9b4949` · **Working tree:** clean · **Tests:** 91 backend passing (needs Postgres up).

### What happened
- **Sub-project B+C (multi-user auth + authz) — backend complete.** 16-task TDD plan ([docs/superpowers/plans/2026-09-14-multiuser-auth-authz.md](docs/superpowers/plans/2026-09-14-multiuser-auth-authz.md)) all green. New `server/auth/` package (config, users+JIT provisioning, DB-backed sessions, personal access tokens, principal-resolution deps, row-ownership `authz`, OIDC RP client), routers `auth`+`admin`, migrations `005` (users + per-user ownership) / `006` (sessions + PATs), ownership guards wired into docs/chat-sessions/tools/TTS, app wiring + startup guard + `bootstrap_admin`.
- **Two rounds of background security-review findings fixed.** Round 1 (4 findings) in `9cacf8f`: TOCTOU seed-claim → atomic UPDATE, `email_verified` gate on email-match provisioning, dev-bypass keyed to the real bind host via `ipaddress`, session hard-revoke on user disable. Round 2 (1 HIGH) in `c9b4949`: the `SessionMiddleware` secret no longer falls back to a hardcoded `"dev-insecure-change-me"` — `startup_guard` refuses to boot a non-loopback bind without a strong (>=32 char) `SESSION_SECRET`; loopback dev degrades to an ephemeral per-process secret.
- **Integrated:** fast-forward-merged `feat/multiuser-auth` (22 commits, `454f2f4..c9b4949`) into `feat/security-hardening`. Sub-project A (vuln hardening, commit `21e6e39`) was already the base of that branch. Both branches now point at `c9b4949`.
- **Cleaned up a stray git state:** an in-progress cherry-pick of `defcf03` ("upgrade vulnerabilities on npm packages: humanfs", from `origin/development`) was stuck on a `package-lock.json` conflict and was aborted. **Loose end:** that npm-audit fix still needs to land on this line — bring it in properly via a `development` → `master` merge, not a cherry-pick onto a backend branch.

### Project state
- **A + B+C are done on `feat/security-hardening`, not pushed and not merged to `master`.** Awaiting the push/PR decision.
- **SPA is NOT auth-wired yet.** The frontend has no login screen / token panel and doesn't send credentials, so it only works with `AUTH_ENABLED=false` (which `startup_guard` permits *only* on a loopback bind). A follow-up plan is needed before the SPA is usable multi-user.
- Sub-projects **E (server-side model-router gateway)** and **D (containerize + proxy + secrets)** are still pending — see [memory/project_multiuser_hardening.md] and the locked design decisions there.

### How to run / test
- Start Postgres (snap-podman env gotcha — the `env -u` is required):
  `env -u XDG_DATA_HOME .venv/bin/podman-compose up -d postgres` (container **stops between sessions** — restart it; `env -u XDG_DATA_HOME podman ps` to check).
- Backend suite: `.venv/bin/python -m pytest server/tests/` (test DB `natural_reader_test`, per-test rollback; router tests use `httpx.AsyncClient`+`ASGITransport`, never `TestClient`).
- New env vars in [.env.example](.env.example): `AUTH_ENABLED`, `OIDC_*`, `SESSION_SECRET` (mandatory for exposed binds), `COOKIE_*`, `BOOTSTRAP_ADMIN_EMAIL`.

### Next step / where to resume
- **Decide integration for `feat/security-hardening`:** push + open a PR to `master`, or keep local. (Push/merge to `master` still needs explicit per-action approval.)
- Then: brainstorm → spec → plan the **SPA login-UI** follow-up (login screen, `credentials:'include'`, PAT management panel) so the frontend actually uses the new auth.
- Then sub-project **E** (model router), then **D** (containerize).

---

## 2026-09-14 — Understood project; captured document-library RAG suggestion

**Branch:** `development` · **Version:** v1.9.0 (`ed83a3f`) · **Working tree:** was clean at start; this session adds two untracked/ignored docs (see below).

### What happened
- Read the project end-to-end to understand the RAG/chat architecture (README, [CHAT_WITH_PDF.md](docs/CHAT_WITH_PDF.md), the `superpowers/specs` + `plans`, and the actual code paths).
- Captured a new feature idea — **ask chat across a library of many indexed docs (multi-project, with a document-routing step and multi-round retrieval)** — as a rich, analyzed suggestion:
  **[docs/superpowers/specs/2026-09-14-document-library-rag-suggestion.md](docs/superpowers/specs/2026-09-14-document-library-rag-suggestion.md)**
- No code changed. No spec written, no plan, nothing approved to build.

### Project state (as understood this session)
- **What it is:** local-first reader — Kokoro ONNX TTS + Ollama chat side panel, fused into "Chat with PDF" (v1.6.0+). FastAPI + Postgres/pgvector backend, React/Vite frontend. Fully local (Ollama does chat *and* embeddings).
- **Retrieval today is single-doc:** index the open doc → `nomic-embed-text` 768-dim chunks in `doc_chunks` → model gets a `search_document` tool → **one** autonomous round-trip.
- **Tool loop is capped at 1 round** on purpose ([useChatEngine.js:694-763](src/hooks/useChatEngine.js#L694-L763)); follow-up call drops `tools`.
- Recent shipped work (git log): file logging + license/contributing + v1.9.0 release; SearXNG-backed `web_search` tool (first backend `/api/generate` path, in `services/web_search.py`); hardcoded doclin support.

### Key file map (for the suggested feature)
| Concern | File |
|---|---|
| Tool registry (add tools here) | [src/lib/chatTools/index.js](src/lib/chatTools/index.js) |
| Existing single-doc search tool + `when` gate | [src/lib/chatTools/searchDocument.js](src/lib/chatTools/searchDocument.js) |
| Chat engine + tool loop + round cap | [src/hooks/useChatEngine.js](src/hooks/useChatEngine.js) |
| Docs + search endpoints (`WHERE doc_id`) | [server/routers/docs.py](server/routers/docs.py) |
| Schema (documents, doc_chunks, HNSW) | [server/sql/001_init.sql](server/sql/001_init.sql) |
| Backend generation pattern to reuse | [server/services/web_search.py](server/services/web_search.py) |

### Next step / where to resume
- Decision made this session: capture as suggestion (done), backlog lives under `docs/superpowers/specs/`.
- When building: start **Phase 1 (corpus search, no routing)** from the suggestion doc, via brainstorming → spec → plan. Phases 2 (descriptions + routing tool) and 3 (budgeted multi-round loop) follow.
- Open decisions still to make (all listed in the suggestion): project = column vs. table; routing = semantic vs. list-and-pick; round budget; descriptions LLM-generated vs. editable.
