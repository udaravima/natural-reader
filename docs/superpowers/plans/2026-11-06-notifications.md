# Notifications for each person — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. The owner's standing rule is no subagents unless
> they ask, so native execution (executing-plans) is the default here. Steps use checkbox (`- [ ]`) syntax.
> Every pytest run sets `TEST_DATABASE_URL` / `TEST_ADMIN_DATABASE_URL` to the scratch Postgres on :5434
> (container `nr-c21-testpg`; if every DB test errors at once, `podman start nr-c21-testpg`).

**Goal:** each person has a bell in the header with an inbox of notices about their work (a document indexed or
converted, or failing) and, for admins, the library's (a re-embed paused or done, rebuilds failing, passages
shortened), grouped, needs-action first, cleaned up on a schedule.

**Architecture:** one table (`notifications`, migration 026) and one service module (`server/services/notifications.py`)
modelled on `project_events.py`: senders call `send` / `send_admins` / `resolve` where the outcome happens (admin
kinds inside the same transaction; document kinds from a wrapper that reads the job's committed final state). The
inbox is grouped on the server by a pure function, served by four routes under `/v1/notifications`, polled by a
hook every 60 s while the page is visible, and drawn by a `NotificationBell` beside the Settings gear.

**Tech stack:** FastAPI, psycopg 3, Postgres; React 19, vitest + Testing Library; lucide-react icons.

**Spec:** `docs/superpowers/specs/2026-11-06-notifications-design.md` (approved 2026-11-06).

## Global Constraints

- Two levels only: `action` ("needs action") and `info` ("for your information"); a kind fixes its level.
- Inbox order: unread rows before read; among unread, rows needing action (any unresolved member) first; newest
  first within each part (a group by its newest member).
- A group: one person, one kind, all unread or all read, each member within **30 minutes** of the one before it.
  A group of one is a plain row. Details for the newest **50** members; **all** member ids.
- The badge counts unread **rows** (a group once); `99+` above 99.
- Polling: `GET /v1/notifications/unread` every **60 s** while `document.visibilityState === 'visible'`, at once on
  becoming visible, and after the page's own Index / Convert / Retry finishes; never while hidden or signed out.
- Retention: read notices deleted **30 days** after `read_at`, unread **90 days** after `created_at`, at startup
  then every **24 h**; `NOTIFICATIONS_READ_DAYS`, `NOTIFICATIONS_UNREAD_DAYS` override (days; `0` = keep forever;
  a bad value warns and keeps the default).
- `rebuild.failed` only from the **third** failure in a row (`doc_rebuilds.attempts >= 3`).
- No toast, popup, sound or browser notification for a notice, ever.
- A notice write never fails the job it reports on (a savepoint; a failure is logged as a warning).
- No document names in the audit log or in `logger` lines; notices themselves may carry names.
- Another person's ids are ignored by the read route, never reported.
- Phone header: nothing wider than the screen at **320 and 360 px** with the bell added.

## Rulings (my calls where the spec leaves room; cost if wrong in brackets)

- **P1. Document notices come from a wrapper, not from each write.** `run_pipeline`, `run_embed`, `_fail` and
  `_run_convert_job` set `state = 'failed'` in six places. `doc_notices.run_and_notify(job, doc_id, ...)` runs the
  job, then reads the document's **committed** state and sends one notice. It can't miss a path, and still never
  reports a change that didn't commit (the spec's anti-goal). [A second job finishing on the same document between
  the job's end and the read would be reported instead; the per-document lock makes that rare, and it would still
  be a true state.]
- **P2. The re-embed's key is `reembed`**, not `reembed:<model>`: only one re-embed runs at a time, and every way
  it ends (a fill succeeds, switch, cancel, a new start) resolves it. [None: one migration at a time is a rule of
  phase 2b.]
- **P3. Shortened passages are counted through a `ContextVar`** in `embeddings.py` (`counting_shortened()`), not by
  changing `embed_texts`' return type: its callers stay untouched, and `asyncio.gather`'s child tasks share the
  counter object. Counted: one indexing job (run through the wrapper) and one re-embed run (kept in the migration
  state as `shortened`). Not counted: queue rebuilds (they re-embed text the current model already accepted).
- **P4. At upload, only `run_pipeline` jobs notify.** `run_legacy_swap` re-extracts content that is already
  `indexed` for its holder; "finished indexing" would be news about nothing.
- **P5. The admin console opens a section on request** through a new `openSection` prop (`{id, n}`; `n` bumps so
  the same section can be asked twice), routed through its own `select()` so the unsaved-changes prompt still
  guards.

## Review Focus

1. **A flood from a stuck step.** The re-embed loop fails every few seconds for an hour: one unread notice with a
   growing `repeats`, never one per attempt. (Task 5, `test_a_pause_that_repeats_is_one_notice`.)
2. **Two workers at the pause transition.** Both fail the same batch: one notice, not two. (Task 5,
   `test_two_workers_failing_together_send_one_pause`.)
3. **A notice write that throws** (a bad kind, a dropped row) inside a job's transaction: the job's own write
   still commits. (Task 1, `test_a_failing_send_does_not_abort_the_callers_transaction`.)
4. **Someone else's ids** sent to `POST /read`: nothing of theirs changes and the count says 0. (Task 4,
   `test_read_ignores_other_peoples_ids`.)
5. **A document the requester no longer holds** when its job ends: the notice still arrives (they asked), and
   clicking it gives the usual not-found toast, leaving it read. (Task 7 for the send; Task 10 for the click,
   `opening a notice for a document you no longer hold shows the not-found toast`.)

---

### Task 1: The table and sending

**Files:**
- Create: `server/sql/026_notifications.sql`
- Create: `server/services/notifications.py`
- Test: `server/tests/test_notifications.py`, `server/tests/test_migration_026.py`

**Interfaces:**
- Produces:
  - `KINDS: dict[str, Kind]`, `Kind = NamedTuple(level: Literal['action','info'], plural: str)` (plural uses `{n}`):
    `reembed.paused` action "Re-embedding paused {n} times"; `reembed.done` info "{n} re-embeds finished";
    `rebuild.failed` action "{n} documents keep failing to rebuild"; `embed.shortened` info "Passages were shortened
    in {n} documents"; `doc.index_failed` action "{n} documents couldn't be indexed"; `doc.indexed` info "{n}
    documents finished indexing"; `doc.convert_failed` action "{n} conversions failed"; `doc.converted` info "{n}
    documents are converted and ready".
  - `async def send(conn, user_ids: Iterable[str], kind: str, *, title: str, body: str | None = None,
    doc_id: str | None = None, target: dict | None = None, key: str | None = None) -> int` (rows written or
    merged; 0 on failure).
  - `async def send_admins(conn, kind: str, **same_keywords) -> int`: every user with `'admin' = ANY(capabilities)`
    and `status = 'active'`.
  - `async def resolve(conn, key: str) -> int`: `resolved_at = now()` on every unresolved notice with that key.
  - `def doc_target(doc_id: str, name: str) -> dict` → `{"doc": doc_id, "name": name}`; `ADMIN_MODELS =
    {"admin": "models"}`, `ADMIN_OVERVIEW = {"admin": "overview"}`.

- [ ] **Step 1: Write the failing tests** in `test_notifications.py` (users via `resolve_or_provision_user` as in
  `test_project_events.py`; an admin by `UPDATE users SET capabilities = '{admin}', status = 'active'`):
  - `test_send_writes_one_row_per_person_with_the_kinds_level`: `send(conn, [a, b], "doc.indexed", title="x")`
    → 2 rows, `level == 'info'`, `read_at`, `resolved_at` NULL, `repeats == 1`.
  - `test_unknown_kind_is_refused`: `send(..., "doc.teleported", ...)` raises `ValueError`.
  - `test_same_key_while_unread_merges`: two sends with `key="index:d1"` → 1 row, `repeats == 2`, the second title,
    `resolved_at` NULL again after a `resolve` in between.
  - `test_same_key_after_read_is_a_new_notice`: mark the first read (`UPDATE ... SET read_at = now()`), send again
    → 2 rows.
  - `test_send_admins_reaches_active_admins_only`: an active admin, a disabled admin, a non-admin → 1 row.
  - `test_resolve_marks_every_recipient`: `send_admins` to two admins with `key="rebuild:d1"`, `resolve(conn,
    "rebuild:d1")` → 2, both `resolved_at` set; a second `resolve` → 0.
  - `test_a_failing_send_does_not_abort_the_callers_transaction`: inside `db_conn`, insert a marker row in a table
    you then read, call `send(conn, ["not-a-uuid"], "doc.indexed", title="x")` → returns 0, logs a warning, and a
    following `SELECT 1` on the same connection succeeds.
  - In `test_migration_026.py` (pattern of `test_migration_024.py`): the table exists with the CHECK on `level`,
    and the partial unique index refuses a second unread row with the same `(user_id, key)`.
- [ ] **Step 2: Run them; they fail** — `pytest server/tests/test_notifications.py server/tests/test_migration_026.py -q`
  → errors (`relation "notifications" does not exist` / no module).
- [ ] **Step 3: Write migration 026** exactly as the spec's Data section (columns, `CHECK (level IN ('action',
  'info'))`, `doc_id ... ON DELETE SET NULL`, index `notifications_user_idx (user_id, read_at, created_at DESC)`,
  unique `notifications_open_key_idx (user_id, key) WHERE key IS NOT NULL AND read_at IS NULL`), a manual Down block
  like 025's, and `INSERT INTO schema_migrations(version) VALUES (26) ON CONFLICT DO NOTHING`.
- [ ] **Step 4: Implement `notifications.py`'s sending half.** `send` runs inside `async with conn.transaction():`
  (a savepoint in the caller's transaction) and catches `Exception` → `logger.warning("notification %s not sent",
  kind, exc_info=True)`, return 0. A `ValueError` for an unknown kind is raised **before** the savepoint (a
  programming error, not a runtime one). The merge is `INSERT ... ON CONFLICT (user_id, key) WHERE key IS NOT NULL
  AND read_at IS NULL DO UPDATE SET repeats = notifications.repeats + 1, title = EXCLUDED.title, body =
  EXCLUDED.body, target = EXCLUDED.target, created_at = now(), resolved_at = NULL`. `target` is stored as `Jsonb`.
- [ ] **Step 5: Run them; they pass.**
- [ ] **Step 6: Commit** `feat(notifications): the table and sending (migration 026)`.

### Task 2: The inbox: grouping, order, reading

**Files:**
- Modify: `server/services/notifications.py`
- Test: `server/tests/test_notifications_inbox.py`

**Interfaces:**
- Consumes: Task 1's table, `KINDS`.
- Produces:
  - `def group_rows(rows: list[Notice], *, gap: timedelta = GROUP_GAP) -> list[dict]` (pure; `GROUP_GAP =
    timedelta(minutes=30)`, `GROUP_DETAILS = 50`). `Notice` is a dataclass of the row's columns. Returns rows in inbox
    order, each: `{"kind", "level", "needs_action": bool, "unread": bool, "title", "body", "at", "count",
    "resolved", "ids": list[int], "items": list[dict], "more": int, "target"}` — a single notice has `count == 1`,
    its own title/body/target; a group has the plural title, `body` None, `target` None.
  - `async def inbox(conn, user_id, *, offset: int = 0, limit: int = 30) -> dict` → `{"rows": [...], "total": n}`.
  - `async def unread_counts(conn, user_id) -> dict` → `{"count": rows, "action": rows needing action}` (reads unread
    notices only).
  - `async def mark_read(conn, user_id, ids: list[int]) -> int`; `async def mark_all_read(conn, user_id) -> int`.

- [ ] **Step 1: Write the failing tests.** Pure ones build `Notice`s with explicit times:
  - `test_neighbours_29_minutes_apart_group_31_do_not`: three `doc.indexed` at 0, 29, 60 min → two rows, the newer
    with `count == 2` and title "2 documents finished indexing".
  - `test_a_group_never_mixes_kinds_levels_or_read_state`: interleaved `doc.indexed` / `doc.index_failed`, and one
    read `doc.indexed` among unread ones → separate rows.
  - `test_a_group_of_one_is_a_plain_row`: its own title, body, target; `count == 1`.
  - `test_order_unread_action_then_unread_info_then_read_newest_first`: an older unread failure sorts above a newer
    unread finish; any read row sorts below both; a read failure below an unread finish.
  - `test_a_resolved_failure_sorts_as_information`: one `doc.index_failed` with `resolved_at` set → `needs_action`
    False, ordered among info rows; a group of 3 failures with 1 resolved → `needs_action` True, `resolved == 1`.
  - `test_a_group_of_60_gives_50_details_and_60_ids`: `len(items) == 50`, `more == 10`, `len(ids) == 60`, items
    newest first.
  - DB ones: `test_inbox_pages_by_rows` (`offset`/`limit` over rows, `total` counts rows);
    `test_unread_counts_count_a_group_once` (20 `doc.indexed` a minute apart + 1 `rebuild.failed` → `{"count": 2,
    "action": 1}`); `test_mark_read_touches_only_the_persons_unread_ids`; `test_mark_all_read`.
- [ ] **Step 2: Run them; they fail** (`ImportError: group_rows`).
- [ ] **Step 3: Implement.** Grouping: sort the person's notices by `(kind, unread, created_at)`, start a new group
  when the gap to the previous member exceeds `gap`, then sort groups by `(not unread, not needs_action,
  -newest_at)`. `inbox` loads every notice of the person (retention bounds it) with one SELECT and slices the
  grouped rows; `unread_counts` groups only `WHERE read_at IS NULL`. `mark_read` is `UPDATE ... SET read_at = now()
  WHERE user_id = %s AND id = ANY(%s) AND read_at IS NULL`.
- [ ] **Step 4: Run them; they pass.**
- [ ] **Step 5: Commit** `feat(notifications): the grouped inbox, read and read-all`.

### Task 3: Cleanup on a schedule

**Files:**
- Modify: `server/services/notifications.py`, `server/app.py` (startup beside `project_events.start_retention()`,
  shutdown beside `stop_retention()`), `.env.example`
- Test: `server/tests/test_notifications_retention.py`

**Interfaces:**
- Produces: `def retention(env=os.environ) -> tuple[int, int]` (read days, unread days); `async def purge(conn,
  read_days: int, unread_days: int) -> int`; `def start_retention() -> None`; `async def stop_retention() -> None`.

- [ ] **Step 1: Write the failing tests:** `test_defaults_are_30_and_90`; `test_zero_keeps_forever` (`purge(conn, 0,
  0)` deletes nothing); `test_a_bad_value_warns_and_keeps_the_default` (`"-3"`, `"ten"` → 30 with a warning);
  `test_purge_deletes_read_after_30_days_and_unread_after_90` (rows with `read_at` 31 and 29 days ago, unread
  `created_at` 91 and 89 days ago → 2 deleted, the right 2).
- [ ] **Step 2: Run them; they fail.**
- [ ] **Step 3: Implement** like `project_events` (`PURGE_EVERY_S = 24 * 3600`, a loop task, idempotent `DELETE`),
  but the loop runs when either value is positive. Add both variables to `.env.example` with a one-line comment
  each (days; 0 keeps forever). Wire start/stop in `app.py`.
- [ ] **Step 4: Run them, and `server/tests/test_app_wiring.py`; they pass.**
- [ ] **Step 5: Commit** `feat(notifications): cleanup once a day (30 days read, 90 unread)`.

### Task 4: The routes

**Files:**
- Create: `server/routers/notifications.py`
- Modify: `server/app.py` (import beside the other `from .routers.… import router as …_router` lines, then
  `app.include_router(notifications_router)`)
- Test: `server/tests/test_notifications_routes.py` (the `_client` pattern of `test_project_activity.py`)

**Interfaces:**
- Consumes: Task 2's `inbox`, `unread_counts`, `mark_read`, `mark_all_read`.
- Produces: `GET /v1/notifications/unread` → `{"count", "action"}`; `GET /v1/notifications?offset=&limit=` →
  `{"rows", "total"}` (`offset >= 0`, `1 <= limit <= 100`, default 30); `POST /v1/notifications/read` body
  `{"ids": list[int]}` (at most 1000) → `{"changed": n}`; `POST /v1/notifications/read-all` → `{"changed": n}`.
  All depend on `deps.get_current_user` and `deps.get_conn`.

- [ ] **Step 1: Write the failing tests:** each route's shape; `limit=0` and `limit=101` → 422; 1001 ids → 422;
  `test_read_ignores_other_peoples_ids` (Ben's ids in Ann's request → `{"changed": 0}`, Ben's rows still unread);
  `test_rows_are_only_the_persons_own`; `test_the_four_routes_are_registered` (the app built by `create_app` lists
  the four paths, as `test_app_wiring.py` checks others).
- [ ] **Step 2: Run them; they fail (404).**
- [ ] **Step 3: Implement the router** (`APIRouter(prefix="/v1/notifications", tags=["notifications"])`; a pydantic
  body `ReadIn(ids: list[int] = Field(max_length=1000))`; datetimes serialised as FastAPI does elsewhere).
- [ ] **Step 4: Run them; they pass.**
- [ ] **Step 5: Commit** `feat(notifications): the inbox routes`.

### Task 5: Re-embed notices, and counting shortened passages

**Files:**
- Modify: `server/services/embeddings.py` (`_fit_one`, a new `counting_shortened`),
  `server/services/embedding_migration.py` (`_cleared`, `start`, `fill_step`, `switch`, `cancel`)
- Test: `server/tests/test_embeddings_client.py` (shortening count), `server/tests/test_notifications_reembed.py`

**Interfaces:**
- Consumes: `notifications.send_admins`, `resolve`, `ADMIN_MODELS`.
- Produces: `@contextmanager def counting_shortened() -> Iterator[Tally]` with `Tally.texts: int`; outside such a
  block nothing is counted. The migration state gains `"shortened": int` (reset to 0 by `_cleared`).

- [ ] **Step 1: Write the failing tests.**
  - `test_shortened_texts_are_counted_once_each` (in `test_embeddings_client.py`, its MockTransport refusing any
    text over 300 characters with 400): two texts of 1,200 characters, one of 50 → `tally.texts == 2`, however many
    halvings each took.
  - `test_a_pause_sends_one_notice_to_every_admin`: two admins; `fill_step` with a target whose embed raises
    `EmbeddingError` → each admin has one `reembed.paused` (`level 'action'`, key `reembed`, title "Re-embedding
    paused: <model> stopped answering.", body the error's first 200 characters, target `{"admin": "models"}`).
  - `test_a_pause_that_repeats_is_one_notice`: five failing `fill_step`s → one row per admin, `repeats == 1` (the
    state's `error` was already set, so steps 2–5 send nothing).
  - `test_two_workers_failing_together_send_one_pause`: two connections run the error branch concurrently (the
    pattern of `test_rebuild_queue.py`'s two-connection test) → one row per admin.
  - `test_filling_again_resolves_the_pause`; `test_cancel_resolves_the_pause`.
  - `test_switch_sends_done_and_the_runs_shortened_count`: a run whose fills shortened 12 texts → `reembed.done`
    "The library now uses <model>." and `embed.shortened` "<model> refused 12 passages during the re-embed; they
    were shortened to fit.", both to admins, target Models; with 0 shortened, no `embed.shortened`.
- [ ] **Step 2: Run them; they fail.**
- [ ] **Step 3: Implement.** `_fit_one` adds 1 to the current tally the first time it halves a given text.
  `fill_step` wraps its `embed_texts` call in `counting_shortened()` and adds `tally.texts` to `state["shortened"]`
  in its success write. Its error branch sends `reembed.paused` with `send_admins` **only when `state.get("error")`
  was empty**, inside the same locked transaction. The success write calls `resolve(conn, "reembed")` when the
  error it clears was set. `switch()` reads `shortened` before `_cleared`, then resolves `reembed` and sends
  `reembed.done` (and `embed.shortened` when > 0) in its transaction. `cancel()` and `start()` resolve `reembed`.
  Add `"shortened": 0` to `_cleared`.
- [ ] **Step 4: Run them, and `test_embedding_migration.py`, `test_embeddings_client.py`; they pass.**
- [ ] **Step 5: Commit** `feat(notifications): re-embed paused and done, shortened passages counted`.

### Task 6: Rebuilds that keep failing

**Files:**
- Modify: `server/services/rebuild_queue.py` (`fail`, `finish`)
- Test: `server/tests/test_rebuild_queue.py`

**Interfaces:**
- Consumes: `notifications.send_admins`, `resolve`, `ADMIN_OVERVIEW`. `fail` and `finish` keep their signatures.

- [ ] **Step 1: Write the failing tests:** `test_the_third_failure_in_a_row_tells_the_admins` (claim/fail three
  times, moving `retry_after` back between: no notice after 1 and 2; after 3 one `rebuild.failed` per admin, title
  "<file_name> has failed to rebuild 3 times; its old index is still answering.", body the error, key
  `rebuild:<doc_id>`, target Overview); `test_more_failures_merge_into_it` (a 4th → still one row, `repeats == 2`);
  `test_a_finished_rebuild_resolves_it`; `test_a_late_fail_from_a_lapsed_worker_sends_nothing` (the existing
  ownership rule: `fail` by a worker that no longer owns the row changes nothing and sends nothing).
- [ ] **Step 2: Run them; they fail.**
- [ ] **Step 3: Implement.** `fail`'s UPDATE gains `RETURNING attempts`; when a row came back with `attempts >= 3`,
  read `documents.file_name` and `send_admins`. `finish`'s DELETE gains `RETURNING doc_id`; when one came back,
  `resolve(conn, f"rebuild:{doc_id}")`. The name never goes into a log line.
- [ ] **Step 4: Run them, and `test_doc_rebuild.py`; they pass.**
- [ ] **Step 5: Commit** `feat(notifications): rebuilds that keep failing reach the admins`.

### Task 7: Document notices for the person who asked

**Files:**
- Create: `server/services/doc_notices.py`
- Modify: `server/routers/docs.py` (`register_document`, `start_index_job`, the reindex route at the
  `background.add_task(doc_pipeline.run_pipeline, doc_id)` near the end of the file, `start_convert_job`)
- Test: `server/tests/test_doc_notices.py`, and one assertion each in `test_docs_upload.py` and the convert tests

**Interfaces:**
- Consumes: `notifications.send`, `send_admins`, `resolve`, `doc_target`, `ADMIN_MODELS`;
  `embeddings.counting_shortened`.
- Produces: `async def run_and_notify(job: Callable[..., Awaitable[None]], doc_id: str, *job_args,
  requested_by: str, via: Literal['index', 'convert']) -> None`.

- [ ] **Step 1: Write the failing tests** (jobs replaced by small async stand-ins that set the document's state, as
  `docs_harness.py` does):
  - `test_indexed_tells_the_requester_by_their_own_name_for_it`: Ann's entry names it "Q3.pdf" (the content's
    canonical name differs) → Ann gets `doc.indexed` "Q3.pdf finished indexing.", target `{"doc": id, "name":
    "Q3.pdf"}`; Ben, who holds the same content, gets nothing.
  - `test_failed_indexing_needs_action_and_a_later_success_resolves_it`: state `failed`, `error_message` "No text
    found — try Convert (OCR)." → `doc.index_failed` "Couldn't index Q3.pdf." with that body, key `index:<id>`;
    a second run ending `indexed` resolves it and sends `doc.indexed`.
  - `test_a_conversion_and_its_indexing_send_one_notice`: via `convert`, ending `indexed` with `conversion_state
    'converted'` → only `doc.converted` "Q3.pdf is converted and ready to chat with.", and `convert:<id>` resolved.
  - `test_a_failed_conversion`: `conversion_state 'conversion_failed'` → `doc.convert_failed` "Couldn't convert
    Q3.pdf." with `conversion_error` as body, key `convert:<id>`.
  - `test_a_conversion_whose_indexing_fails_sends_index_failed_only`.
  - `test_a_job_still_running_elsewhere_sends_nothing` (state ends `indexing`).
  - `test_shortened_passages_tell_the_admins_once`: the stand-in job bumps the tally by 12 → one `embed.shortened`
    per admin, "<model> refused 12 passages of Q3.pdf; they were shortened to fit.", target Models.
  - `test_a_requester_who_no_longer_holds_it_still_hears` (their entry removed mid-job: the canonical name is used).
  - `test_a_job_that_raises_still_notifies_from_the_state_it_left` and re-raises nothing new (the wrapper logs and
    returns, as background tasks do today).
  - In `test_docs_upload.py`: a new upload schedules `run_and_notify` with `requested_by` = the uploader and
    `via='index'`; a dedupe schedules nothing; a legacy swap is scheduled bare (ruling P4).
- [ ] **Step 2: Run them; they fail.**
- [ ] **Step 3: Implement `run_and_notify`:** run the job under `counting_shortened()`; then, in one connection
  and transaction, read `state, error_message, conversion_state, conversion_error` and the requester's name for the
  document (`COALESCE(library_entries.file_name, documents.file_name)` for that user, else the canonical name), and
  send per this table (first match wins):

  | `via` | Committed state | Sends | Resolves |
  | --- | --- | --- | --- |
  | convert | `conversion_state = 'conversion_failed'` | `doc.convert_failed` | — |
  | any | `state = 'failed'` | `doc.index_failed` | — |
  | convert | `state = 'indexed'` | `doc.converted` | `convert:<id>`, `index:<id>` |
  | index | `state = 'indexed'` | `doc.indexed` | `index:<id>` |
  | any | anything else | nothing | — |

  Then, when `tally.texts > 0`, `send_admins("embed.shortened", ...)` naming the current embedding model
  (`embedding_state` current). The routes schedule `background.add_task(doc_notices.run_and_notify, <job>, doc_id,
  [options,] requested_by=principal.user_id, via=...)`.
- [ ] **Step 4: Run them, and every `test_docs_*.py`, `test_doc_pipeline.py`, `test_convert_limits.py`,
  `test_docling_progress.py`; they pass.**
- [ ] **Step 5: Commit** `feat(notifications): documents tell the person who asked`.

### Task 8: The browser side of the inbox

**Files:**
- Create: `src/lib/notifications.js`, `src/hooks/useNotifications.js`
- Test: `src/lib/notifications.test.js`, `src/hooks/useNotifications.test.js`

**Interfaces:**
- Produces:
  - `fetchUnread(host, port) -> Promise<{count, action} | null>`, `fetchInbox(host, port, {offset, limit}) ->
    Promise<{rows, total} | null>`, `markRead(host, port, ids) -> Promise<boolean>`, `markAllRead(host, port) ->
    Promise<boolean>`, all through `apiFetch`; any failure → `null` / `false`, never a throw.
  - `useNotifications(apiHost, apiPort, enabled) -> { count, action, refresh }`; `POLL_MS = 60_000`.

- [ ] **Step 1: Write the failing tests** (fake timers, `document.visibilityState` stubbed, `fetch` mocked):
  `polls every 60 s while visible`; `does not poll while hidden, and asks at once on becoming visible`;
  `does nothing while disabled (signed out)`; `refresh() asks at once`; `a failed request keeps the last counts`;
  the four lib functions' paths, methods and bodies (`POST /v1/notifications/read` with `{"ids": [1, 2]}`).
- [ ] **Step 2: Run them; they fail** — `npx vitest run src/lib/notifications.test.js src/hooks/useNotifications.test.js`.
- [ ] **Step 3: Implement.** The hook listens to `visibilitychange` and clears its interval when hidden or
  unmounted.
- [ ] **Step 4: Run them; they pass.**
- [ ] **Step 5: Commit** `feat(notifications): the browser's inbox client and polling`.

### Task 9: The bell and its panel

**Files:**
- Create: `src/components/NotificationBell.jsx`
- Test: `src/components/NotificationBell.test.jsx`

**Interfaces:**
- Consumes: Task 8's lib functions and the hook's `{count, action, refresh}`.
- Produces: `<NotificationBell theme apiHost apiPort count action onRefresh onOpenTarget />`;
  `onOpenTarget(target)` receives `{doc, name}` or `{admin}`.

- [ ] **Step 1: Write the failing tests:**
  - `the bell says how many unread rows and how many need action` (accessible name "Notifications, 3 unread, 1 needs
    action"; with none, "Notifications").
  - `shows 99+ above 99`; `the badge uses the warning colour only when something needs action` (a
    `data-level="action"` attribute on the badge, so the test doesn't read colours).
  - `opening the list fetches it and marks nothing read`.
  - `rows come in the server's order, unread heavier than read, Resolved tagged`.
  - `Mark all as read is disabled with nothing unread, and calls read-all then refreshes`.
  - `a single row marks itself read, opens its target and closes the panel`.
  - `a group marks all its ids read and expands in place` (`aria-expanded` true, members listed, panel open); `a
    member opens its own target`; `clicking the group again collapses it`; `and N more` shown when `more > 0`.
  - `Escape closes the panel and returns focus to the bell`; `Nothing new.` when empty.
- [ ] **Step 2: Run them; they fail.**
- [ ] **Step 3: Implement** with lucide `Bell`, `AlertTriangle` (action), `Info` (info); `useMenuDismiss` for
  Escape and outside clicks; a panel `max-h-[70vh] overflow-y-auto`, full width under the header below `sm`;
  44 px targets below `md`; colours from the theme tokens already in `useTheme`. Relative times with `timeAgo(at, now)`
  from `src/components/projects/ActivityTab.jsx` (move it to `src/utils/timeAgo.js` and import it from both).
- [ ] **Step 4: Run them; they pass.**
- [ ] **Step 5: Commit** `feat(notifications): the bell and its panel`.

### Task 10: In the header, and where a click goes

**Files:**
- Modify: `src/components/Header.jsx` (the bell before the Settings gear), `src/App.jsx` (the hook, `handleOpenNotice`,
  refresh after the page's own Index / Convert / Retry finishes, `openSection` to `AdminConsole`),
  `src/components/admin/AdminConsole.jsx` (`openSection` prop, ruling P5)
- Test: `src/components/Header.settings.test.jsx`, `src/components/admin/AdminConsole.test.jsx`, `src/lib/openDoc.test.js`
  (`openNoticeTarget`; App itself is too heavy to render for this)

**Interfaces:**
- Consumes: Tasks 8–9; `openServerDoc(openDocDeps, docId, name)` from `src/lib/openDoc.js`.
- Produces: `openNoticeTarget(deps, target)` in `src/lib/openDoc.js`: `{doc, name}` → `openServerDoc`; `{admin}` →
  `deps.openAdmin(section)`.

- [ ] **Step 1: Write the failing tests:** `the header shows the bell before the Settings gear for a signed-in
  person`; `a document notice opens the document through openServerDoc`; `opening a notice for a document you no
  longer hold shows the not-found toast` (openServerDoc's existing refusal path); `an admin notice opens that
  console section`; `asking the console for a section with unsaved changes shows the prompt first`.
- [ ] **Step 2: Run them; they fail.**
- [ ] **Step 3: Implement.** The hook runs only while `auth.state === 'active'`. `openAdmin(section)` sets
  `viewMode` to `admin` (through `leaveAdmin` as the header's switch does) and passes `openSection={{id: section,
  n: n + 1}}`. Call `refresh()` where the index and Docling pipeline handlers stop polling (they poll every 2 s).
- [ ] **Step 4: Run the whole frontend suite and lint** — `npx vitest run` and `npx eslint .` → all pass, 0 errors.
- [ ] **Step 5: Measure the header in Chromium** at 320, 360, 768 and 1366 px, every view, as an admin and as a
  reader: `document.documentElement.scrollWidth <= innerWidth`. If the bell pushes a width over, move the
  `ViewMenu` breakpoint rule, not the bell.
- [ ] **Step 6: Commit** `feat(notifications): the bell in the header, and opening what a notice points to`.

### Task 11: Docs, the walk, the gates

**Files:**
- Modify: `docs/USER_GUIDE.md` (the bell: what arrives, levels, groups, read, cleanup), `docs/ARCHITECTURE.md`
  (the module, table, routes, the wrapper; and fix the stale line 99 that says the time tool is gone),
  `docs/DEPLOYMENT.md` (the two variables), `docs/TESTING.md` (the new test files and the walk), `CHANGELOG.md`
  (`[Unreleased]`)

- [ ] **Step 1: Write the docs.**
- [ ] **Step 2: Walk it** on a scratch backend and DB in Chromium at 1366 and 360 px: upload one document and wait
  in the Library for the bell; upload five at once and see one group, click it, see five; force an index failure
  (a text-less PDF) and see it on top; a re-embed to a model that's then stopped (pause, warning badge), started
  again (Resolved), finished (done, and shortened if it refused any); Mark all as read; a reader account sees no
  admin notice. Record what was walked and what wasn't in TESTING and the handover.
- [ ] **Step 3: Run every gate** — pytest (all), `npx vitest run`, `npx eslint .`, `npm run build` → passed with
  no "failed" in pytest's summary, 0 lint errors, build OK.
- [ ] **Step 4: Commit** `docs(notifications): the guide, architecture, deployment and test map`.
