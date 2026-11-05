# Admin console v2, phase 2b: the embedding provider, probe, build-then-switch — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (native: the owner's standing rule is no
> subagents unless asked). Steps use checkbox (`- [ ]`) syntax. Every pytest run sets TEST_DATABASE_URL /
> TEST_ADMIN_DATABASE_URL to the scratch :5434.

**Goal:** an admin changes the embedding model (or its provider) in Admin → Models, and every document stays
searchable while the library is re-embedded in the background, then switches in one step.

**Architecture:** embeddings go through any configured provider (`ollama` `/api/embed`, `openai` `/embeddings`, both
batched). What the stored vectors were made with is a database fact (`embedding_state`), separate from what the admin
chose (the target, a Models setting). When they differ, a background job fills a second column `embedding_next` with
the target model; queries keep using the current one; one transaction swaps the columns. The per-document rebuild
queue (chunker changes, first-use rebuilds) moves from per-process memory to a table claimed with `SKIP LOCKED`.

**Tech stack:** FastAPI, psycopg 3 + pgvector (HNSW), httpx, React (vitest + Testing Library).

**Spec:** `docs/superpowers/specs/2026-10-25-admin-console-v2-design.md` §4.2.3, §4.2.4, §5, phase 2b in §6.

## Global Constraints

- Vectors stay `vector(768)` (`EMBEDDING_DIM`); the probe admits only models that give 768 (§4.2.4).
- `ollama` kind: `POST {url}/api/embed`; `openai` kind: `POST {base}/embeddings`; both with an `input` array, about
  32 texts per call. `dimensions` is sent only when the probe found it needed.
- "No new Ollama-only paths": every embedding call works for both kinds.
- "Per request, never cached per process": settings and job state are read from the database (`WORKERS > 1`).
- Every `/v1/admin/*` route requires the admin capability server-side.
- No value specific to one deployment in code or docs (example.com, example model names).
- Nothing changes for an existing deployment that touches nothing: default provider is the Ollama at `OLLAMA_URL`,
  default model `nomic-embed-text`, and no re-embed starts.
- The Settings window's accessibility rules: labels, 44 px targets on a phone, focus kept.

## Rulings (my calls where the spec is silent; cost if wrong in brackets)

- **R1. What decides that vectors differ** is the model, the `dimensions` sent, and the two prefixes. The provider
  is only where the call goes: moving the same model to another host switches at once, with no re-embed. [A provider
  that serves a different model under the same name would mix vectors; the admin would have to re-embed by hand.]
- **R2. The probe's "declares 768" clause is dropped** for now: no API reliably says a model's trained sizes
  (Ollama truncates anything; only vLLM refuses). A model is admitted when it gives 768 natively or is in the known
  shortenable families. [A Matryoshka model outside the list is refused until added: one line in the table.]
- **R3. An `.env` change of model starts the same migration at startup** as a console change does; it no longer
  makes each document unsearchable until its own rebuild. [Same as the console path; the old per-document
  "reindexing" stays only for documents whose vectors came from another model before this phase.]
- **R4. The swap renames columns** (`embedding` → dropped, `embedding_next` → `embedding`, and its index) under a
  table lock, instead of copying vectors: instant, and the new HNSW index was built as the column filled.
- **R5. One filler at a time** across workers, claimed by a heartbeat in the state row (as chat turn claims do):
  a shared embedding model gains nothing from two, and a dead worker's claim lapses after 2 minutes.
- **R6. Env prefixes (`EMBEDDING_*_PREFIX`) apply only to the env's own `EMBEDDING_MODEL`.** A model chosen in the
  console takes its known prefixes. [An admin wanting custom prefixes for a console model sets them in env with
  that model as `EMBEDDING_MODEL`.]

## Review Focus

1. A document uploaded during the switch: its chunks must not end up with no vector under the new model (the switch
   re-checks for unfilled rows under the lock and goes back to filling if any).
2. A worker killed mid-fill: another resumes after the claim lapses; the switch happens once.
3. A new target chosen mid-migration: the old `embedding_next` is dropped and filling starts over; no column mixes
   two models.
4. Search during a migration compares a current-model query only with current-model vectors.
5. A 1024-dimension model asked for 768 on Ollama returns 768 numbers with no error: the probe refuses it.

---

### Task 1: the embedding client over providers

**Files:** Create `server/services/embedding_models.py`; modify `server/services/embeddings.py`; test
`server/tests/test_embeddings_client.py` (new), adjust `server/tests/test_embeddings*.py` that stub the old endpoint.

**Interfaces:**
- Produces: `embedding_models.KNOWN: dict[str, Family]`, `Family(prefix_document: str, prefix_query: str,
  shortenable: bool, source: str)` (`source` = where each entry was checked, as the spec asks);
  `embedding_models.family(model: str) -> Family | None` (by name prefix, `:tag` and provider prefix ignored).
- Produces: `@dataclass(frozen=True) Embedder(provider: ProviderConfig, model: str, dimensions: int | None,
  document_prefix: str, query_prefix: str)`; `async embed_texts(e: Embedder, texts: list[str]) -> list[list[float]]`
  (one request per ≤32 texts, `EMBEDDING_MAX_CONCURRENCY` requests in flight, raises `EmbeddingError(message)` on a
  refusal, a wrong length or a count mismatch); `async embed_query(text) / embed_documents(texts)` keep their
  signatures and use the **current** embedder (Task 3).

- [ ] Write tests (httpx `MockTransport`): `test_ollama_kind_posts_a_batch_to_api_embed` (body `{"model", "input"}`,
  no `dimensions` unless set), `test_openai_kind_posts_to_embeddings_with_the_key` (Bearer key, `data[i].embedding`
  in `index` order), `test_forty_texts_go_in_two_calls`, `test_a_wrong_length_or_count_is_an_error_naming_the_model`,
  `test_dimensions_is_sent_only_when_set`, `test_known_families_and_their_prefixes` (nomic, embeddinggemma,
  qwen3-embedding, text-embedding-3-*).
- [ ] Run: `pytest server/tests/test_embeddings_client.py -q`. Expected: FAIL (no module).
- [ ] Implement. Prefixes for the `embeddinggemma` family: query `task: search result | query: `, document
  `title: none | text: `. Checked 2026-11-05 on <https://huggingface.co/google/embeddinggemma-2> (native 768,
  MRL 512/256/128); record that URL in `source`. `embed_batch`'s skip-on-failure contract stays for indexing:
  a failed request marks its texts `None`, as today. Text only in this phase: embeddinggemma-2 also embeds
  pictures (Ollama `/api/embed` accepts `{"image": <base64>}` entries, checked on 0.40.13), which the figures
  phase may use; don't build it here.
- [ ] Run the file, then `pytest server/tests -q -k embed`. Expected: PASS.
- [ ] Commit `feat(embeddings): any provider, batched`.

### Task 2: the probe

**Files:** modify `server/services/embeddings.py`; test `server/tests/test_embeddings_probe.py` (new).

**Interfaces:**
- Produces: `@dataclass ProbeResult(ok: bool, native: int | None, dimensions: int | None, reason: str)`;
  `async probe(provider: ProviderConfig, model: str) -> ProbeResult`.

- [ ] Write tests: `test_a_native_768_model_is_admitted_without_dimensions`;
  `test_a_known_shortenable_family_is_admitted_with_768` (native 1024 `qwen3-embedding`, a second call with
  `dimensions: 768` returns 768 → `dimensions == 768`); `test_a_1024_model_outside_the_families_is_refused_with_its_size`
  (reason names 1024 and why); `test_an_unreachable_or_refusing_provider_says_so` (no key in the reason);
  `test_a_shortened_answer_of_the_wrong_length_is_refused`.
- [ ] Run. Expected: FAIL. Implement (step 1 without `dimensions`, step 2 only for a family). Run. Expected: PASS.
- [ ] Commit `feat(embeddings): probe a model before it is used`.

### Task 3: target, current, and the state row

**Files:** modify `server/services/model_settings.py` (two settings), `server/services/model_router.py`
(`EMBEDDING_PROVIDER`), `server/services/embeddings.py`; create `server/services/embedding_state.py`; modify
`server/app.py` (startup); tests `server/tests/test_embedding_state.py` (new), `test_model_settings.py`.

**Interfaces:**
- Produces: Models settings `embedding_provider` (env `EMBEDDING_PROVIDER`, default: the first `ollama`-kind
  provider, else the first provider) and `embedding_model` (env `EMBEDDING_MODEL`, default `nomic-embed-text`).
- Produces: `embedding_state.target(settings, env) -> Embedder`; `async current(conn) -> Embedder`;
  `async init(conn, env)` (startup: with no `embedding_state` row, write the target as current: today's vectors
  were made with it); `vector_identity(e: Embedder) -> str` (R1); `async needs_migration(conn, env) -> bool`.
- The `embedding_state` app_settings row: `{"current": {...}, "next": {...} | null, "filled": int, "total": int,
  "claimed_by": str | null, "claimed_at": iso | null, "error": str | null}`.
- `embeddings.current_profile()` becomes `profile_of(e: Embedder) -> str` (model | prefixes | chunker, as today);
  every caller gets the embedder from `await embedding_state.current(conn)` on a connection it already holds.

- [ ] Tests: `test_a_fresh_install_starts_with_the_target_as_current`; `test_the_same_model_on_another_provider_needs_no_migration`;
  `test_another_model_or_prefix_needs_one`; `test_env_prefixes_apply_only_to_the_env_model` (R6);
  `test_queries_use_the_current_embedder_until_the_switch`.
- [ ] Run, FAIL; implement; run the Task 1–3 files and `pytest server/tests -q -k "embed or model_settings or
  doc_pipeline or search"`. Expected: PASS.
- [ ] Commit `feat(embeddings): what the vectors were made with is kept in the database`.

### Task 4: the rebuild queue in the database

**Files:** create `server/sql/025_rebuild_queue.sql` (with a runnable Down block in its header, as 023 has); modify
`server/services/doc_pipeline.py`, `server/app.py`; test `server/tests/test_rebuild_queue.py` (new); adjust
`test_doc_pipeline*.py` that patch `_rebuilding` / `_failed`.

**Interfaces:**
- Table `doc_rebuilds(doc_id TEXT PRIMARY KEY REFERENCES documents ON DELETE CASCADE, profile TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('queued','running','failed')), attempts INT NOT NULL DEFAULT 0,
  error TEXT, claimed_at TIMESTAMPTZ, retry_after TIMESTAMPTZ, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`.
- `ensure_current(doc)` becomes `async ensure_current(conn, doc) -> ReadableDoc` (it reads the current profile and
  enqueues); its three callers (`routers/docs.py`, `chat/project_scope.py`, `chat/orchestrator.py`) await it.
- Produces: `async enqueue(conn, doc_id, profile)` (idempotent; `ensure_current` calls it instead of
  `_schedule_rebuild`), `async claim_next(conn) -> str | None` (`FOR UPDATE SKIP LOCKED`; a `running` row claimed
  over 10 minutes ago, or a `failed` one past `retry_after`, is claimable), `async run_queue_forever()` (one loop per
  worker, started in the app's lifespan: claim, `run_rebuild`, delete the row or mark it failed with
  `retry_after = now() + 15 min`; sleeps 5 s when empty, woken at once by an in-process enqueue),
  `async queue_counts(conn) -> dict[str, int]`.

- [ ] Tests: `test_two_claimers_never_get_the_same_document`; `test_a_claim_held_too_long_is_taken_over`;
  `test_a_failed_rebuild_waits_before_retrying`; `test_enqueue_twice_is_one_row`; `test_a_deleted_document_leaves_the_queue`;
  `test_a_restart_resumes_the_queue` (rows survive; a fresh runner drains them).
- [ ] Run, FAIL; implement; remove `_rebuilding`, `_failed`, `_rebuild_gate`; run the queue and pipeline tests.
  Expected: PASS.
- [ ] Commit `feat(docs): the rebuild queue lives in the database`.

### Task 5: build, then switch

**Files:** create `server/services/embedding_migration.py`; modify `server/app.py`, `server/services/doc_pipeline.py`
(indexing during a migration needs nothing new: the filler catches new rows); test
`server/tests/test_embedding_migration.py` (new, real scratch database per test like `test_auth_touch.py`).

**Interfaces:**
- Produces: `async start(conn, target: Embedder, *, by: str | None)` (drops any earlier `embedding_next`, adds
  `embedding_next vector(768)` and `doc_chunks_embedding_next_idx` HNSW, sets `next`, counts `total`);
  `async fill_step(conn_factory, worker_id) -> str` (`"claimed-elsewhere" | "filled" | "ready" | "idle"`: claims the
  heartbeat, embeds up to 32 rows `WHERE embedding IS NOT NULL AND embedding_next IS NULL ORDER BY id` with the
  target and the document prefix, writes them by `id` and `text_hash`); `async switch(conn) -> bool` (under
  `LOCK TABLE doc_chunks IN SHARE ROW EXCLUSIVE MODE`: if any row is still unfilled, False; else drop `embedding`
  and its index, rename `embedding_next` and its index, set every vectored chunk's `embedding_model` and every
  indexed document's `embedding_model` and profile to the target's, `current = next`, `next = null`; one
  transaction); `async cancel(conn)`; `async run_forever(worker_id)` (one loop per worker: idle when no `next`).
- Search and indexing read `current` (Task 3), so nothing in `doc_search.py` changes.

- [ ] Tests: `test_a_migration_fills_every_vectored_chunk_with_the_target`;
  `test_search_meanwhile_uses_the_current_vectors` (query embedded with current; results identical to before);
  `test_the_switch_waits_for_a_chunk_added_during_it` (Review Focus 1); `test_a_lapsed_claim_is_taken_over_and_switches_once`
  (Focus 2); `test_a_new_target_mid_way_starts_over` (Focus 3); `test_cancel_drops_the_column_and_changes_nothing`;
  `test_after_the_switch_documents_need_no_rebuild` (their profiles match current).
- [ ] Run, FAIL; implement; run. Expected: PASS. Then the whole backend suite. Expected: PASS.
- [ ] Commit `feat(embeddings): re-embed in the background, then switch at once`.

### Task 6: admin routes and Overview

**Files:** modify `server/routers/admin.py`, `server/services/console_overview.py`; tests
`server/tests/test_embeddings_admin.py` (new), `test_console_overview.py`.

**Interfaces:**
- `GET /v1/admin/embeddings` → `{current: {provider, model, dimensions}, target: {...}, source: {provider, model},
  migration: {state: "none"|"building"|"switching"|"failed", filled, total, error} , providers: [{name, kind}]}`.
- `POST /v1/admin/embeddings/probe` `{provider, model}` → `ProbeResult` as JSON (422 for an unknown provider; the
  provider must be one configured: no URL from the browser).
- `PUT /v1/admin/embeddings` `{provider, model}` → probes again server-side and refuses (422, the probe's reason)
  unless admitted; saves the target (audited); starts a migration when the identity differs, else switches at once
  (R1); returns the GET view. `{provider: null, model: null}` goes back to the environment.
- `DELETE /v1/admin/embeddings/migration` → cancels; the target goes back to current.
- Overview's embeddings check uses the current embedder through Task 1's client and adds
  "Re-embedding for <model>: 1,234 of 5,000 passages" while one runs, and "N documents waiting to be rebuilt"
  from `queue_counts` (replacing today's per-process `stale` count).

- [ ] Tests: admin only (403 for a member); probe refuses an unknown provider; PUT refuses a refused model with its
  reason; PUT with the same model on another provider switches with no migration; DELETE cancels; Overview reports
  progress.
- [ ] Run, FAIL; implement; run. Expected: PASS.
- [ ] Commit `feat(admin): choose the embedding model in the console`.

### Task 7: the console's Embeddings controls

**Files:** create `src/components/admin/EmbeddingPicker.jsx` (+ test); modify `src/components/admin/ModelsSection.jsx`;
Overview needs no change (it renders checks).

- [ ] Tests (`EmbeddingPicker.test.jsx`): shows current model and provider; picking a model and **Check** shows the
  verdict (native size, "shortened to 768", or the refusal); **Use this model** is enabled only after an admitted
  check and sends the PUT; while re-embedding shows "Re-embedding: 1,234 of 5,000 passages" (polled every 5 s while
  open) and **Cancel**; a refusal from the server is shown in its words; **Back to the server's setting**.
- [ ] Run, FAIL; implement (provider select; model as a `SearchableSelect` of that provider's listed models with a
  free-text fallback for a provider that lists none); run. Expected: PASS. Lint.
- [ ] Commit `feat(admin): Embeddings in Admin → Models`.

### Task 8: docs, the walk, the measurement

- [ ] Docs: USER_GUIDE (Admin → Models → Embeddings), DEPLOYMENT (`EMBEDDING_PROVIDER`, the probe's rule, the
  migration, its disk and time cost), ARCHITECTURE (embeddings, the queue, the migration), `.env.example`,
  CHANGELOG `[Unreleased]`, TESTING rows and counts.
- [ ] Walk (scratch backend :8100, scratch DB, preview :5174, Chromium 1366 and 360 px): index three documents with
  `nomic-embed-text`; in the console Check and Use `embeddinggemma-2:740m`; while it re-embeds, ask the chat about a
  document (answers, with sources); after the switch, the same question; Cancel a second migration; kill the backend
  mid-fill and restart (it resumes). Record the seconds per 1,000 passages on this CPU (spec §8).
- [ ] The owner's trial: the document eval (`server/evals/doc_qa.py`, cloud chat model) once with `nomic-embed-text`
  and once with `embeddinggemma-2:740m`, same cases; report facts found and citations for each. Measurement only:
  the default stays `nomic-embed-text` unless the owner says otherwise.
- [ ] Commit `docs: the embedding provider and model, in the console`.
