# Indexing Queue (bulk upload, phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every start of document indexing and conversion goes through a queue in the database, so the work is limited per worker, survives a restart, can't run one document on two workers, and can't crash a worker for ever.

**Architecture:** A new table `doc_index_jobs` (migration 027), one row per document, and a module `server/services/index_queue.py` with the rebuild queue's claim rules. Each worker runs two lanes: the existing runner claims rebuilds and indexing jobs, and a second runner claims conversions. The routes that start indexing or conversion enqueue instead of using `BackgroundTasks`. Start-up recovery respects live claims and queues stuck documents. Lists and the status route report `queued` and `queue_ahead`, and the two screens that show a document's state use them.

**Tech Stack:** Python 3.13, FastAPI, psycopg 3 (async), Postgres 16; React 19, Vitest.

**Spec:** `docs/superpowers/specs/2026-11-14-bulk-project-upload-design.md` (revision 3), "Phase 1: the indexing queue". Phase 2 gets its own plan after this ships.

**Revised 2026-11-15** after a review checked against the code: an attempts cap, conversion on the queue, recovery that respects live claims, a backfill, a bound on rebuild priority, and the uploader recorded on every job.

**Built 2026-11-15** (all eight tasks, walked). Where the build departs from this plan, and why:

- **Rebuild cap (Task 5):** counts the times a rebuild's worker died (`doc_rebuilds.stops`, added in migration 027), parked at 3, not "5 claims": claims also count ordinary failures, which retry by themselves (an embedding server down for an hour would have parked every rebuild).
- **A full pool on upload (Review Focus 5):** the upload answers as usual with `queued: false` instead of a 503. The file is already stored, so asking for it again would only re-send it; the screen offers Retry index and the next start queues it.
- **New uploads are inserted `stored`, not `extracting`:** truthful while they wait; one job per document now does what the early `extracting` guarded.
- **Re-extract is refused while a job runs** (409 `busy_document`), the same guard as Convert: it would race the running job's writes.
- **A project Contributor may resume indexing** (Retry index on the project tab; bulk-uploaded files are in nobody's library). A project Reader still may not.
- **The Library list shows plain-word states too:** the walk found it printing the raw state.
- **The test client drains the queue after each response** (`docs_harness`, `run_queue=True`) instead of adding `drain()` to 30 tests: it stands in for the worker's runner, which is woken at once.
- **Shutdown waits up to 5 s for the runners** so a running job is handed back before the pool closes.

## Global Constraints

- Branch `feat/index-queue` from `development`.
- **No commit without the owner's word.** Each task's last step stages the files and asks; it never runs `git commit` by itself.
- Backend tests run only against the scratch Postgres: `TEST_DATABASE_URL=postgresql://natural_reader:natural_reader@127.0.0.1:5434/natural_reader_test TEST_ADMIN_DATABASE_URL=postgresql://natural_reader:natural_reader@127.0.0.1:5434/postgres .venv/bin/python -m pytest`. Bare `pytest` targets the app's own database.
- Migration 027 has a runnable Down in its header, like 025.
- One job per document (the document is the table's key).
- Lanes per worker: **index** (rebuilds and the jobs `pipeline`, `legacy_swap`), one document at a time; **convert** (the job `convert`), one at a time.
- A rebuild is claimed before an indexing job, but after 3 rebuilds in a row one waiting indexing job is taken.
- A claim not heard from in 10 minutes is taken over (`rebuild_queue.CLAIM_LAPSE`, reused, not copied).
- `MAX_ATTEMPTS = 3` for an indexing or conversion job; `rebuild_queue.MAX_CLAIMS = 5` for a rebuild.
- A job ends once: success or failure removes the row. No automatic retry of a failure. Only a dead worker's job is retried, up to the cap.
- `doc_pipeline.run_pipeline` and `run_legacy_swap` keep their signatures: the evals call `run_pipeline` directly.
- Exact copy:
  - gave up: "The server stopped while reading this file, 3 times. The file may be damaged."
  - Convert refused: "This document is being indexed. Try again when it has finished."
  - states: "Waiting to index", "Waiting to index (N ahead)", "Not indexed", "Reading", "Indexing", "Indexed", "Failed"; the button "Retry index".

## Review Focus

1. **A file that crashes the worker.** Expected: tried 3 times at most, then the document is failed with the gave-up sentence, the requester is told once, and the job is gone. (Tasks 1, 2.)
2. **A worker dies while its sibling is mid-job.** Expected: the restarted worker's recovery leaves the sibling's document and claim alone. (Task 4.)
3. **Convert pressed while the document's indexing job is queued, and while it runs.** Expected: queued → the job becomes a conversion, one row; running → 409 with the refusal sentence, nothing changed. (Tasks 1, 3.)
4. **A document deleted, or its requester deleted, mid-job.** Expected: the row goes with the document; a deleted requester leaves the job running and no notice. No error in the log. (Tasks 1, 2.)
5. **The pool is full when a route enqueues.** Expected: 503 with the existing database-unavailable refusal; the document is left `stored`, which the next start's backfill queues. (Tasks 3, 4.)

---

### Task 1: The table and the queue module

**Files:**
- Create: `server/sql/027_index_queue.sql`
- Create: `server/services/index_queue.py`
- Test: `server/tests/test_index_queue.py`, `server/tests/test_migration_027.py`

**Interfaces (produced, in `server/services/index_queue.py`):**
- `INDEX_JOBS = ("pipeline", "legacy_swap")`, `CONVERT_JOBS = ("convert",)`, `MAX_ATTEMPTS = 3`
- `@dataclass(frozen=True) class Claim: doc_id: str; job: str; requested_by: str | None; attempts: int; options: dict | None`
- `async def enqueue(conn, doc_id: str, *, job: str = "pipeline", requested_by: str | None = None, options: dict | None = None) -> bool` (True when it wrote a row; an existing row is left as it is)
- `async def enqueue_convert(conn, doc_id: str, *, requested_by: str | None, options: dict) -> bool`: no row → insert; a **queued** `pipeline`/`legacy_swap` row → becomes `convert` with these options and this requester, keeping its `queued_at`; a queued `convert` row → options replaced; a **running** row → returns False and changes nothing
- `async def claim_next(conn, worker: str, jobs: tuple[str, ...]) -> Claim | None`
- `async def heartbeat(conn, doc_id: str, worker: str) -> None`
- `async def finish(conn, doc_id: str, worker: str) -> bool` (True when `worker` still owned the row)
- `async def live_claim(conn, doc_id: str) -> bool` (a running row whose claim hasn't lapsed)
- `async def positions(conn) -> dict[str, int]` (doc_id → jobs ahead of it in its lane, a running job 0; for the index lane, plus the rebuilds queued)
- `async def queue_counts(conn) -> dict[str, int]` (`{"queued": n, "running": n}`)

- [ ] **Step 1: Write `server/sql/027_index_queue.sql`**

Header in the style of 025 (what it is for, the claim rules, one row per document), with the Down, then:

```sql
--   BEGIN;
--   DROP TABLE IF EXISTS doc_index_jobs;
--   DELETE FROM schema_migrations WHERE version = 27;
--   COMMIT;

CREATE TABLE IF NOT EXISTS doc_index_jobs (
    doc_id        TEXT PRIMARY KEY REFERENCES documents(doc_id) ON DELETE CASCADE,
    job           TEXT NOT NULL DEFAULT 'pipeline' CHECK (job IN ('pipeline', 'legacy_swap', 'convert')),
    options       JSONB,
    requested_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    state         TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'running')),
    attempts      INT NOT NULL DEFAULT 0,
    claimed_by    TEXT,
    claimed_at    TIMESTAMPTZ,
    queued_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS doc_index_jobs_order_idx ON doc_index_jobs (queued_at, doc_id);

INSERT INTO schema_migrations(version) VALUES (27) ON CONFLICT DO NOTHING;
```

- [ ] **Step 2: Write the failing tests in `server/tests/test_index_queue.py`** (fixtures and helpers as in `test_rebuild_queue.py`)

```python
async def test_enqueue_twice_is_one_row_and_keeps_the_first_requester(db_conn):
    assert await index_queue.enqueue(db_conn, A, requested_by=ann) is True
    assert await index_queue.enqueue(db_conn, A, requested_by=bob) is False
    # one row: (A, "pipeline", ann, "queued", 0)

async def test_claims_go_oldest_first_and_never_twice(db_conn):
    first = await index_queue.claim_next(db_conn, "w1", index_queue.INDEX_JOBS)
    second = await index_queue.claim_next(db_conn, "w2", index_queue.INDEX_JOBS)
    assert (first.doc_id, second.doc_id) == (A, B)
    assert await index_queue.claim_next(db_conn, "w3", index_queue.INDEX_JOBS) is None
    assert first == index_queue.Claim(A, "pipeline", None, 1, None)

async def test_each_lane_claims_only_its_jobs(db_conn):
    # A is "pipeline", B is "convert" with options {"preset": "fast"}
    assert (await index_queue.claim_next(db_conn, "w", index_queue.CONVERT_JOBS)).doc_id == B
    assert (await index_queue.claim_next(db_conn, "w", index_queue.CONVERT_JOBS)) is None
    assert (await index_queue.claim_next(db_conn, "w", index_queue.INDEX_JOBS)).doc_id == A

async def test_a_lapsed_claim_is_taken_over_and_counts_an_attempt(db_conn):
    # claim A as w1; set claimed_at = now() - interval '11 minutes'
    taken = await index_queue.claim_next(db_conn, "w2", index_queue.INDEX_JOBS)
    assert (taken.doc_id, taken.attempts) == (A, 2)
    assert await index_queue.finish(db_conn, A, "w1") is False   # the dead worker's late finish
    assert await index_queue.finish(db_conn, A, "w2") is True

async def test_a_fresh_claim_is_not_taken_over(db_conn): ...
async def test_heartbeat_keeps_a_claim(db_conn): ...
async def test_live_claim_is_true_only_for_a_running_unlapsed_row(db_conn): ...   # queued → False, running → True, lapsed → False, no row → False

async def test_convert_replaces_a_queued_indexing_job(db_conn):                   # Review Focus 3
    await index_queue.enqueue(db_conn, A, requested_by=ann)
    assert await index_queue.enqueue_convert(db_conn, A, requested_by=bob, options={"preset": "fast"}) is True
    # one row: job "convert", requested_by bob, options {"preset": "fast"}, queued_at unchanged

async def test_convert_is_refused_while_the_document_runs(db_conn):               # Review Focus 3
    # A enqueued and claimed → enqueue_convert(...) is False; the row is still "pipeline", "running"

async def test_a_deleted_document_leaves_the_queue(db_conn): ...                  # Review Focus 4
async def test_a_deleted_requester_keeps_the_job(db_conn): ...                    # requested_by becomes NULL

async def test_positions_count_the_lane_and_the_waiting_rebuilds(db_conn):
    # index lane: A running, B and C queued; convert lane: D queued; two rows queued in doc_rebuilds
    assert await index_queue.positions(db_conn) == {A: 0, B: 3, C: 4, D: 0}
    assert await index_queue.queue_counts(db_conn) == {"queued": 3, "running": 1}

async def test_two_connections_never_claim_one_job(scratch): ...                  # as the rebuild queue's test
```

`server/tests/test_migration_027.py`: the table exists; the `job` and `state` CHECKs refuse other values; running the header's Down statements and then the file again restores the table and version 27.

- [ ] **Step 3: Run; expect FAIL** (`ModuleNotFoundError: server.services.index_queue`).

- [ ] **Step 4: Implement `server/services/index_queue.py`**, following `rebuild_queue.py`'s statements. Every function runs in the caller's transaction and doesn't commit. `positions` for the index lane: a running job is 0; a queued job is (queued rebuilds) + (running jobs in its lane) + (queued jobs before it). For the convert lane: jobs before it in that lane.

- [ ] **Step 5: Run the two files, then the whole backend suite; expect PASS and no new failures.**

- [ ] **Step 6: Stage and ask the owner before committing**

```bash
git add server/sql/027_index_queue.sql server/services/index_queue.py server/tests/test_index_queue.py server/tests/test_migration_027.py
# message: feat(index): a queue of indexing and conversion jobs in the database (migration 027)
```

---

### Task 2: The runners

**Files:**
- Modify: `server/services/doc_pipeline.py` (`run_queue_once` near line 416, `run_queue_forever`; new functions beside them)
- Modify: `server/routers/docs.py` (move `_run_convert_job`'s body where the runner can call it without importing a router: `server/services/doc_convert_job.py`, re-exported under its old name)
- Modify: `server/app.py:135` (start the convert lane beside the existing runner)
- Test: `server/tests/test_index_runner.py`

**Interfaces:**
- Consumes: Task 1.
- Produces, in `server/services/doc_pipeline.py`:
  - `async def enqueue_index(doc_id: str, *, job: str = "pipeline", requested_by: str | None = None) -> bool` and `async def enqueue_conversion(doc_id: str, *, requested_by: str | None, options: dict) -> bool`: each on a connection of its own, committed at once, then wakes this process's runners. Unlike `_enqueue_soon`, they **raise** when no connection can be had.
  - `async def run_index_job(claim: index_queue.Claim) -> None`
  - `async def give_up(claim: index_queue.Claim) -> None`: marks the document failed with the gave-up sentence (for a conversion: also clears `conversion_state` and sets `conversion_error` to it) and notifies `claim.requested_by`.
  - `run_queue_once(worker) -> str | None` (unchanged signature): the index lane's one step.
  - `async def run_convert_once(worker) -> str | None`, `async def run_convert_forever(worker) -> None`: the convert lane.
  - `REBUILDS_IN_A_ROW = 3`

- [ ] **Step 1: Write the failing tests in `server/tests/test_index_runner.py`** (use `shim_pool` and the extraction and embedding fakes of `test_rebuild_queue.py` / `test_doc_pipeline.py`)

```python
async def test_a_queued_upload_is_indexed_and_its_job_removed(): ...
async def test_a_rebuild_is_claimed_before_an_indexing_job(): ...
async def test_after_three_rebuilds_in_a_row_a_waiting_indexing_job_is_taken():
    # 5 rebuild rows and 1 index job → order of run_queue_once results: R, R, R, the index job, R, R
async def test_a_taken_over_job_finishes_a_document_left_indexing():
    # state "indexing", chunks without vectors, a lapsed claim → "indexed", every chunk embedded, job gone
async def test_a_taken_over_job_restarts_a_document_left_extracting(): ...
async def test_the_fourth_claim_gives_up_instead_of_running():                # Review Focus 1
    # a row with attempts = 3 and a lapsed claim, requested_by=ann; the pipeline fake raises if called
    # → documents.state == "failed", error_message == "The server stopped while reading this file, 3 times. The file may be damaged."
    # → ann has exactly one notification; the job is gone; the fake was never called
async def test_a_failure_ends_the_job_and_tells_the_requester_once(): ...
    # then run_queue_once returns None: no automatic retry
async def test_a_job_with_no_requester_sends_no_notice(): ...                 # Review Focus 4
async def test_a_document_deleted_mid_job_ends_quietly(caplog): ...           # Review Focus 4
async def test_a_legacy_swap_job_runs_the_swap(): ...
async def test_a_new_runner_finishes_what_an_old_one_left_queued(): ...
async def test_the_convert_lane_runs_a_conversion_with_its_options():
    # a "convert" row with options {"preset": "fast"}; the conversion function is monkeypatched to record
    # → called once with (doc_id, {"preset": "fast"}); the job is gone; run_queue_once never took it
async def test_a_conversion_that_gave_up_clears_converting():
    # attempts = 3, lapsed, conversion_state "converting" → conversion_state NULL, conversion_error is the sentence
async def test_enqueue_index_raises_when_no_connection_can_be_had(): ...
```

- [ ] **Step 2: Run; expect FAIL.**

- [ ] **Step 3: Implement**

`run_index_job(claim)`: `legacy_swap` → `run_legacy_swap`; `pipeline` → read the state: `indexing` → `run_embed(doc_id)`, anything else → `run_pipeline(doc_id)`. With a requester and job `pipeline`, run through `doc_notices.run_and_notify(..., via="index")`; otherwise directly, inside a `try` that logs and goes on.

`run_queue_once`: keep a per-runner count of rebuilds run in a row. Claim a rebuild unless the count is `REBUILDS_IN_A_ROW` and an indexing job is claimable; else claim an indexing job; reset the count when an indexing job runs or no rebuild was claimable. A claim with `attempts > MAX_ATTEMPTS` goes to `give_up` and is finished without running. Share one heartbeat wrapper between the rebuild, index and convert paths.

Conversion: the runner calls the moved conversion function through `doc_notices.run_and_notify(..., via="convert")`, as the route does today, with `claim.options`. Its inline embed at the end stays.

Check for an import cycle among `doc_pipeline`, `doc_notices` and the moved conversion module; import inside the function where there is one.

- [ ] **Step 4: Run the new tests and `test_rebuild_queue.py`, `test_doc_pipeline.py`, `test_doc_notices.py`, the conversion tests; expect PASS.**

- [ ] **Step 5: Stage and ask the owner before committing**

```bash
git add server/services/ server/routers/docs.py server/app.py server/tests/test_index_runner.py
# message: feat(index): each worker drains indexing jobs one at a time, conversions in a lane of their own
```

---

### Task 3: The routes enqueue

**Files:**
- Modify: `server/routers/docs.py` (`register_document` near 362, `start_index_job` near 750, the convert route near 1004, the re-extract route near 1092; `_after_upload`'s return values)
- Modify: `server/routers/project_documents.py:89-90` (`upload_version`)
- Create: `server/tests/queue_helpers.py`
- Test: `server/tests/test_index_routes_enqueue.py`; adapt existing tests that relied on the background task

**Interfaces:**
- Consumes: `doc_pipeline.enqueue_index`, `enqueue_conversion`, `run_queue_once`, `run_convert_once`.
- Produces: `server/tests/queue_helpers.py`: `async def drain() -> list[str]` (runs both lanes until neither has work; returns the doc ids it ran). The routes' answers gain `"queued": true` when they enqueued; `start_index_job` and re-extract answer the true `"state": "stored"`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_an_upload_enqueues_and_runs_nothing_in_the_request():
    # 202, {"state": "stored", "queued": True}; a row (doc, "pipeline", the uploader, "queued")
    # still "stored"; after queue_helpers.drain() it is "indexed"
async def test_known_indexed_bytes_enqueue_nothing(): ...
async def test_retry_index_on_a_queued_document_is_one_row(): ...
async def test_a_new_version_records_its_uploader():
    # POST …/versions with new bytes → the row's requested_by is the uploader; a failing pipeline → they get a notice
async def test_reextract_enqueues(): ...
async def test_a_legacy_upload_enqueues_a_legacy_swap(): ...
async def test_convert_enqueues_with_its_options():
    # POST /v1/docs/{id}/convert → 202; a "convert" row whose options are the request's; nothing ran in the request
async def test_convert_replaces_a_queued_indexing_job(): ...                          # Review Focus 3
async def test_convert_is_refused_while_the_document_is_being_indexed():              # Review Focus 3
    # the row claimed → 409, detail "This document is being indexed. Try again when it has finished."; conversion_state unchanged
async def test_a_full_pool_answers_503_and_leaves_the_document_stored(): ...          # Review Focus 5
def test_no_router_starts_indexing_or_conversion_in_a_background_task():
    # every server/routers/*.py as text: no line has "add_task" together with
    # run_pipeline, run_legacy_swap, run_embed or _run_convert_job
```

- [ ] **Step 2: Run; expect FAIL.**

- [ ] **Step 3: Change the routes.** `_after_upload` returns the job's name (`"pipeline"`, `"legacy_swap"`) or `None`. Each route awaits the enqueue where it called `background.add_task`, with `requested_by=principal.user_id` everywhere except a legacy swap (`None`: it is no news, as today). The convert route sets `conversion_state` as it does today only after `enqueue_conversion` returned True; False is the 409. A raised enqueue becomes the routers' existing 503 `db_unavailable` refusal. Remove `BackgroundTasks` parameters no longer used.

- [ ] **Step 4: Run the whole backend suite. Fix the tests that assumed the request ran the work** by calling `await queue_helpers.drain()` after the request, and nothing else. Put the number of tests that needed it in the commit message.

- [ ] **Step 5: Stage and ask the owner before committing**

```bash
git add server/routers/ server/tests/
# message: feat(index): uploads, Index, re-extract, new versions and Convert enqueue instead of running in the request
```

---

### Task 4: Recovery that respects live claims, and the backfill

**Files:**
- Modify: `server/services/doc_content.py:188` (`recover_states`), `server/db.py:69` (`_after_migrations`)
- Test: `server/tests/test_index_recovery.py`

**Interfaces:**
- Consumes: Task 1's table.
- Produces: `async def recover_states(conn) -> None` (same name, new rule) and `async def enqueue_stuck(conn) -> int` in `doc_content.py` (how many it queued).

- [ ] **Step 1: Write the failing tests**

```python
async def test_recovery_leaves_a_document_with_a_live_claim_alone(db_conn):       # Review Focus 2
    # A "indexing" with a running row claimed 1 minute ago; B "extracting", same → both unchanged
async def test_recovery_resets_a_document_whose_claim_lapsed_or_has_no_job(db_conn):
    # "extracting" → "stored", "indexing" → "extracted", for a lapsed claim and for no row
async def test_a_live_conversion_is_not_stamped_interrupted(db_conn):
    # conversion_state "converting" with a live "convert" claim → unchanged
async def test_a_conversion_with_no_job_is_interrupted_as_before(db_conn):
    # → conversion_state NULL, conversion_error "Interrupted by server restart"
async def test_a_conversion_whose_claim_lapsed_is_left_for_the_takeover(db_conn):
    # the row stays; conversion_state stays "converting" (the convert lane re-runs it)
async def test_stuck_documents_get_a_job(db_conn):
    # states stored, extracted with no row → enqueue_stuck returns 2, rows "pipeline", requested_by NULL
    # indexed, failed, registered → no row; a document that already has a row → still one row
async def test_enqueue_stuck_twice_adds_nothing(db_conn): ...
```

- [ ] **Step 2: Run; expect FAIL.**

- [ ] **Step 3: Implement.** A live claim is a `running` row with `claimed_at` newer than `rebuild_queue.CLAIM_LAPSE`. `_after_migrations` runs `recover_states`, the conversion reset (now limited to `converting` documents with no job row), `sweep_orphans`, then `enqueue_stuck`.

- [ ] **Step 4: Run; expect PASS. Whole suite; no new failures.**

- [ ] **Step 5: Stage and ask the owner before committing**

```bash
git add server/services/doc_content.py server/db.py server/tests/test_index_recovery.py
# message: fix(index): a starting worker no longer resets another worker's live job, and stuck documents are queued
```

---

### Task 5: A rebuild that kills its worker stops being retried

**Files:**
- Modify: `server/services/rebuild_queue.py` (`claim_next` near line 48)
- Test: `server/tests/test_rebuild_queue.py`

**Interfaces:**
- Produces: `rebuild_queue.MAX_CLAIMS = 5`. `claim_next` keeps its signature.

- [ ] **Step 1: Write the failing tests**

```python
async def test_a_rebuild_claimed_five_times_is_parked_and_the_admins_told(db_conn):
    # a row with attempts = 5 and a lapsed claim → claim_next returns None;
    # the row is state "failed", retry_after 'infinity', error "Stopped the server 5 times; not retried."
    # one "rebuild.failed" notice to the admins, keyed rebuild:<doc>
async def test_a_newer_profile_revives_a_parked_rebuild(db_conn):
    # enqueue(A, "p2") → queued, attempts 0 (today's reset, still true for a parked row)
async def test_the_same_profile_does_not_revive_it(db_conn): ...
```

- [ ] **Step 2: Run; expect FAIL.**

- [ ] **Step 3: Implement** in `claim_next`: before claiming, park any claimable row whose `attempts >= MAX_CLAIMS` (one `UPDATE … RETURNING doc_id`, then the notice per row), then claim as today.

- [ ] **Step 4: Run `test_rebuild_queue.py` and the suite; expect PASS.**

- [ ] **Step 5: Stage and ask the owner before committing**

```bash
git add server/services/rebuild_queue.py server/tests/test_rebuild_queue.py
# message: fix(rebuild): a rebuild that stops the server 5 times is parked, not retried for ever
```

---

### Task 6: The lists say what is queued

**Files:**
- Modify: `server/routers/docs.py` (`GET /v1/docs` near 268, `_fetch_doc_status`)
- Modify: `server/services/doc_versions.py` (the listings near lines 108 and 147 that select `d.state`)
- Test: `server/tests/test_index_queue_in_lists.py`

**Interfaces:**
- Consumes: `index_queue.positions(conn)`.
- Produces: every document record those places return gains `queued: bool` and, when queued, `queue_ahead: int`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_the_library_list_marks_queued_documents_with_what_is_ahead():
    # three uploads, none drained → queue_ahead 0, 1, 2 in upload order, queued True; an indexed one → queued False, no "queue_ahead"
async def test_waiting_rebuilds_count_as_ahead():
    # one upload queued and two rebuild rows queued → queue_ahead 2
async def test_the_status_route_reports_the_queue(): ...
async def test_a_projects_documents_and_a_documents_versions_report_the_queue(): ...
async def test_a_stored_document_with_no_job_is_not_queued(): ...
```

- [ ] **Step 2: Run; expect FAIL** (`KeyError: 'queued'`).

- [ ] **Step 3: Implement** with one `positions` query per request, merged in Python by one helper used in all three places.

- [ ] **Step 4: Run; expect PASS. Whole suite; no new failures.**

- [ ] **Step 5: Stage and ask the owner before committing**

```bash
git add server/routers/docs.py server/services/doc_versions.py server/tests/test_index_queue_in_lists.py
# message: feat(index): lists and the status route say what is queued, and how many are ahead
```

---

### Task 7: The two screens that show a document's state

**Files:**
- Create: `src/lib/docStateLabel.js`, `src/lib/docStateLabel.test.js`
- Modify: `src/components/IndexButton.jsx` (the `stored` / `extracted` branch near line 66)
- Modify: `src/components/projects/ProjectDocRow.jsx:51` (the raw `{doc.state}` badge), `src/components/projects/ProjectDocsTab.jsx` (the watch, lines 11-12 and 47-62)
- Modify: `src/App.jsx` (the index state near line 199) and `src/lib/docStatusPoller.js`, so `queued` and `queue_ahead` reach `IndexButton`
- Modify: `src/lib/projectsApi.js` (add `reindex: (docId) => POST /v1/docs/{docId}/index`)
- Test: `src/components/IndexButton.test.jsx`, `src/components/projects/ProjectDocsTab.test.jsx`

**Interfaces:**
- Produces: `docStateLabel({ state, queued, queue_ahead }) -> string`.

- [ ] **Step 1: Write the failing tests**

```js
expect(docStateLabel({ state: 'stored', queued: true, queue_ahead: 3 })).toBe('Waiting to index (3 ahead)');
expect(docStateLabel({ state: 'stored', queued: true, queue_ahead: 0 })).toBe('Waiting to index');
expect(docStateLabel({ state: 'stored', queued: false })).toBe('Not indexed');
expect(docStateLabel({ state: 'extracting' })).toBe('Reading');
expect(docStateLabel({ state: 'extracted' })).toBe('Indexing');
expect(docStateLabel({ state: 'indexing' })).toBe('Indexing');
expect(docStateLabel({ state: 'indexed' })).toBe('Indexed');
expect(docStateLabel({ state: 'failed' })).toBe('Failed');
expect(docStateLabel({ state: 'something-new' })).toBe('something-new');
```

`IndexButton`: a queued `stored` document shows "Waiting to index (2 ahead)" as text, not a **Resume** button, and its title doesn't say "interrupted"; a `stored` document that isn't queued still shows **Resume**.

`ProjectDocsTab`: the plain-word label, the server's `error_message` as the badge's title when failed; **Retry index** on a `failed` row and on a `stored` row that isn't queued, for someone who can file (it calls `api.reindex(doc.doc_id)` and re-reads the list), none for a Reader and none on a queued row; with fake timers, the watch goes on past 200 reads while a row is queued or running, every 15 seconds after the first 10 minutes, and stops when none is.

- [ ] **Step 2: Run; expect FAIL.**

- [ ] **Step 3: Implement.** In `ProjectDocsTab`, replace `MAX_POLLS`: 3 s between reads for the first 200, then 15 s, for as long as a row is queued or in `extracting` / `extracted` / `indexing`. A `stored` row that isn't queued no longer keeps the watch alive.

- [ ] **Step 4: Run the three files, then `npx vitest run`, `npx eslint src`, `npm run build`. Expect PASS, 0 errors, a build.**

- [ ] **Step 5: Stage and ask the owner before committing**

```bash
git add src/
# message: feat(index): "Waiting to index (N ahead)" in the reader and the project tab, and Retry index on a project row
```

---

### Task 8: Walk it, then the docs

**Files:**
- Modify: `docs/LIBRARY.md`, `docs/ARCHITECTURE.md`, `docs/DEPLOYMENT.md`, `docs/USER_GUIDE.md`, `docs/TESTING.md`, `CHANGELOG.md`

- [ ] **Step 1: Walk in the running app.** Scratch database on :5434, a scratch backend on :8011 with `AUTH_ENABLED=false`, a scratch Vite on :5174 proxying to it (a config under `tmp/`), headless Chromium, the local embedding model. Record each result:

1. One upload from the reader starts at once and reaches **Indexed**.
2. Five uploads in a row: the log shows one pipeline at a time; the reader's button and the project tab count down "Waiting to index (N ahead)".
3. Kill the backend while two wait; start it again: both are indexed with nobody pressing anything.
4. A document left `stored` with no job (delete its row by hand): the next start queues it.
5. `WORKERS=2`, six uploads: each document is claimed once; kill one worker mid-job: the other's document is untouched, and the dead one's is taken over after the lapse (shorten `CLAIM_LAPSE` for the walk and say so in the record).
6. A job made to kill its worker (a test hook in the scratch run, not in the code): after 3 tries the document is **Failed** with the gave-up sentence and one notice; the backend stops restarting.
7. A scanned PDF with no text: **Failed** with its reason, one notice, **Retry index** on its project row queues it again.
8. Two conversions pressed together (the Docling build, if the disk allows; otherwise say it was not walked): one runs, one waits; an upload made meanwhile is indexed without waiting for them.
9. During the five uploads, a chat question in another tab answers.

- [ ] **Step 2: Write the docs from what the walk showed**, numbers included. `CHANGELOG.md` under Unreleased, with a deploy note (migration 027: back up first; documents that were stuck are indexed by themselves after the upgrade).

- [ ] **Step 3: Run the full gates** (backend suite on :5434, vitest, eslint, build) and record the counts in `docs/TESTING.md`.

- [ ] **Step 4: Stage and ask the owner before committing**

```bash
git add docs/ CHANGELOG.md
# message: docs(index): the indexing queue, walked
```
