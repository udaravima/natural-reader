# Bulk upload into a project (phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Contributor drops files or a folder on a project's Documents tab, sees what each file will become, presses one button, and every file is stored, filed and queued for indexing; a script does the same with one request per file.

**Architecture:** One decision function (`server/services/bulk_upload.py`) answers "what would this file become here" for two routes: a plan route that stores nothing, and an upload route that stages and hashes the file, decides again inside the transaction that locks the project and the matched document, then stores and files it through the existing `_store_and_hold`. The browser collects, filters and hashes files, asks for the plan, lets the person change a row, and sends two files at a time with XHR so each row shows its progress. Indexing is phase 1's queue, unchanged.

**Tech Stack:** FastAPI, psycopg 3 (async), Postgres; React 18, Vitest + Testing Library; Playwright (headless Chromium) for the walk.

**Spec:** `docs/superpowers/specs/2026-11-14-bulk-project-upload-design.md` (revision 3), section "Phase 2: bulk upload". Phase 1 (the queue, plain-word states, Retry index on a project row, the longer watch) is built and merged into `development` (e3f4265, not pushed yet).

## Global Constraints

- Both routes need a Contributor role in the project, admins only through a member role: `require_project_role(conn, principal, project_id, "contributor", admin_ok=False)`, after `deps.require_capability("reader")` (so a `documents`-scoped token passes and a `chat`-scoped one gets 403 `missing_capability`).
- `BULK_UPLOAD_MAX_FILES`, default 200: more files in one plan → 413 `too_many_files`, whole plan refused, the limit in the message.
- `UPLOAD_MIN_FREE_MB`, default 1024: the reserve the storage folder keeps free.
- Size caps are the existing ones: `PDF_UPLOAD_MAX_MB` (50), `TEXT_UPLOAD_MAX_MB` (10), read through `doc_storage`.
- Type rule is the existing one (`doc_storage.type_from_name`): `.md`/`.markdown` Markdown, `.txt` text, every other name PDF, accepted only if it starts with `%PDF-`.
- Names compare as the server already does: `lower(btrim(name))`. A name is the last path segment of what the client sent, trimmed, at most 255 characters, else `document`.
- The content match is made only inside this project; neither route ever says whether the server holds a file elsewhere.
- No library entry is ever added by these routes.
- Lock order stays projects → project_docs → documents (V1 §4.4).
- Every domain or host in docs is `example.com`; no deployment specifics in code.
- Commits only with the owner's word, one at a time (the commit steps below are where to stop and ask).
- Tests run against the scratch database: `TEST_DATABASE_URL=postgresql://natural_reader:natural_reader@127.0.0.1:5434/natural_reader_test TEST_ADMIN_DATABASE_URL=postgresql://natural_reader:natural_reader@127.0.0.1:5434/postgres`. Bare `pytest` hits the live one.

## Departures from the spec (my calls, flagged for the owner)

1. **`ambiguous` only for `on_match=version`.** The spec says several same-named documents answer `ambiguous` "whatever `on_match` says", but its own screen offers **Separate document** on those rows. Only `version` would have to guess, so `separate` creates a document and `skip` skips; `version` without `document_id` answers `ambiguous` and files nothing.
2. **The upload route checks disk too,** not only the plan: after staging, if the storage folder has less than `UPLOAD_MIN_FREE_MB` free, 507 and the staged file is removed. The spec's own script path never calls the plan, so without this a cron loop can fill the disk.
3. **Never-accepted types are gathered into one line** ("23 files aren't PDF, Markdown or text: left out"), as the spec's bullet says, rather than one row each as its mock-up shows for `notes.docx`. A file counts as accepted by its extension (`.pdf`, `.md`, `.markdown`, `.txt`), or with no such extension when its first bytes are `%PDF-`. The plan route still refuses a `.docx` from its first bytes, for scripts.
4. **Hashing stays on the page's thread, one file at a time.** `crypto.subtle.digest` is asynchronous and the browser computes it off the script thread; reading one file at a time keeps memory at one file. A Web Worker would add a build seam for no measured gain. The walk checks the page stays responsive while a large folder is read; if it doesn't, a worker is the fix.
5. **Restore is sent through the upload route,** not the existing restore route, so the server decides again from the project as it is at send time.
6. **Uploads into one project decide one at a time** (a transaction-level advisory lock, Task 2). The spec accepted that two people uploading one new name at once may get two documents; through this route the second becomes version 2. Filing from the library (`PUT …/docs/{doc_id}`) keeps the spec's accepted race.

## Built 2026-11-15: further departures found while building

7. **`document_id` makes `on_match` moot**: a file named for a document is that document's version (or `older_version`
   of it), whatever `on_match` says. The spec says it "overrides the name match"; `on_match` is about name matches.
8. **The batch lives outside React** (`src/lib/bulkUploadStore.js`, one per project). `ProjectPage` mounts only the
   active tab, and opening a document leaves the page; a batch held in the tab's state would lose its progress while
   its requests went on unseen. The store also asks before the page is left while files are in flight.
9. **A 404 stops the batch only on a row with no `document_id`** (the person was removed from the project). With one,
   the named document may simply be gone, so only that row fails.
10. **Rows say what their current choice will do**, not only the default's ("New document, beside the existing
    setup.pdf"), and the header says "nothing to send" rather than a size when nothing would be sent (the walk found
    "1 KB to send" next to **Upload 0**).
11. **The two error sentences the shared helper got wrong**: a 413 `too_many_files` read "File too large (limit ? MB)"
    and a 507 read "Something went wrong on the server"; both now pass the server's sentence.
12. **Restores are counted as their own part** of the counts line ("1 restored"); the spec's line had no word for them.

## Review Focus

- **Two files that differ only in case or spaces** (`Setup.pdf`, ` setup.pdf`) in one batch: the second is a repeat (Skip by default), exactly as the server would match them. Pinned in Task 3 (`markRepeats`).
- **A dropped folder with more than 100 entries in one directory:** Chrome's `readEntries` returns at most 100 per call, so a walker that calls it once silently loses files. Pinned in Task 3 (`filesFromDataTransfer` loops until an empty batch).
- **The plan is stale at send time** (someone filed the same name or the same bytes in between): the upload answers from the project as it is, and the row shows the server's `result`, not the plan's promise. Pinned in Task 2 (`test_decides_again_at_send_time`) and Task 4 (`row shows the server's result`).
- **Two uploads of the same bytes for the same document at once:** the second must answer `unchanged`, not a 409 `already_current`. Pinned in Task 2 (`test_bulk_upload_concurrency.py`: the decision is made under the project's upload lock).
- **The person loses their role, or their session, halfway:** one message, the batch stops, no flood of failed rows. Pinned in Task 4.

---

### Task 1: The decision function and the plan route

**Files:**
- Create: `server/services/bulk_upload.py`
- Create: `server/routers/project_uploads.py`
- Modify: `server/services/doc_storage.py` (make `_cap_mb` public as `cap_mb`, keep `_cap_mb` as an alias or update its one caller)
- Modify: `server/app.py` (include the router beside `project_documents_router`)
- Test: `server/tests/test_upload_plan.py`
- Test helper: `server/tests/versions_harness.py` (include `project_uploads.router`)

**Interfaces:**
- Produces (in `bulk_upload`):
  - `MAX_FILES_ENV = "BULK_UPLOAD_MAX_FILES"`, `def max_files() -> int` (default 200), `def reserve_mb() -> int` (`UPLOAD_MIN_FREE_MB`, default 1024). An unparsable value falls back to the default with one warning log.
  - `def clean_name(raw: str | None) -> str`: replace `\` with `/`, take the last segment, strip, `[:255]`, else `"document"`.
  - `@dataclass(frozen=True) class Decision: action: str; document: dict | None = None; version: int | None = None; candidates: tuple[dict, ...] = ()` where `action` ∈ `new | version | ambiguous | unchanged | older_version`; `document` is `{id, name, version}` (the current version, from `doc_versions.document_ref`); `version` is the version a `version` would become (`project_docs.last_version + 1`), or the older version's number for `older_version`.
  - `async def decide(conn, project_id, *, name: str, sha256: str | None, document_id=None) -> Decision`, in this order: (1) `sha256` is a document's current version here → `unchanged`; with `document_id` given and that holder being another document → raise `doc_versions` 409 `current_elsewhere` (the existing `_conflict` and message); (2) `document_id` given → `older_version` if `sha256` is an older version of *that* document, else `version` of it (404 `not_found` with `DOCUMENT_NOT_FOUND` unless it is a visible document of this project); (3) `sha256` is an older version of a document here → `older_version` (highest version, as `_older_version_of`); (4) current verified documents with this name: one → `version`, several → `ambiguous` with every candidate; (5) `new`. With `sha256=None`, steps 1 and 3 are skipped. Reads only: no locks, no writes.
  - Candidate: `{id, name, version, added_by: people.person_from(...), added_at}` for the current version, sorted by `added_at, id`.
  - `def refusal_for(name: str, size: int, head: bytes | None) -> dict | None`: `{"code", "message"}` or None. `size == 0` → `empty_file`, "The file is empty."; over `cap_mb(type)` → `too_large`, "Over the 50 MB limit for a PDF." / "Over the 10 MB limit for text and Markdown." (the real cap in the number); a PDF-by-name whose `head` is given and doesn't start with `%PDF-` → `unsupported_type`, "Not a PDF, Markdown or text file."; a text or Markdown `head` containing `\x00` → `unsupported_type` (a UTF-16 file).
  - `def free_mb() -> int` (`shutil.disk_usage(doc_storage.storage_dir())`, the folder created first if missing).
- Produces (route): `POST /v1/projects/{project_id}/uploads/plan`, body `{"files": [{"name": str, "size": int ≥ 0, "sha256": hex64 | null, "head": base64 | null}]}` (pydantic, `extra="forbid"`; at most 10,000 entries in the model so a huge body is a 422 before the 413 rule), answer `{"files": [{"name", "action", "document", "version", "candidates", "code", "message"}]}` in request order, `action = "refused"` with `code`/`message` for a refusal, the other fields null or empty.
- Disk: sum the sizes of every file whose action isn't `refused` or `unchanged`; when that sum > 0 and `free_mb() < ceil(sum / MiB) + reserve_mb()` → 507 `not_enough_space`, "This upload needs {X} MB, and the server has {Y} MB free, of which it keeps {R} MB in reserve."

- [ ] **Step 1: Write the failing tests** in `server/tests/test_upload_plan.py`, using `build_versions_app`, `chat_harness.member`, `seed.make_project`, `seed.seed_doc`, `seed.place_doc` as `test_version_upload.py` does:

```python
async def test_each_action(...):                       # one project, one call, five files
    # new.md (no match) -> "new"; notes.txt same name as a doc at v1 -> "version", version == 2,
    # document["id"] == x; bytes of x's current -> "unchanged"; bytes of x's v1 after a v2 -> "older_version", version == 1
async def test_two_documents_with_the_name_are_ambiguous_with_every_candidate(...):
    # two docs named README.md (keep_both) -> action "ambiguous", len(candidates) == 2, each has id, version, added_by, added_at
async def test_names_compare_case_and_spaces_ignored(...):   # " NOTES.TXT " matches notes.txt -> "version"
async def test_a_path_in_the_name_is_cut_to_its_last_part(...):  # "a/b/notes.txt" and "a\\notes.txt" -> "version"
async def test_docx_and_text_named_pdf_are_refused_from_their_first_bytes(...):
    # head b"PK\x03\x04\x14" name notes.docx -> refused/unsupported_type; head b"hello" name x.pdf -> refused
async def test_utf16_text_is_refused(...):              # head "h\x00e\x00l" name a.txt -> unsupported_type
async def test_the_text_cap_is_10_mb(...):              # size 10*MiB+1 name a.md -> too_large, "10 MB" in message
async def test_empty_file_is_refused(...):
async def test_without_a_hash_only_names_match(...):     # sha256 None, bytes of x's current under another name -> "new"
async def test_the_match_never_reaches_outside_the_project(...):
    # content current in project Q (caller not a member) -> "new"; also when caller IS a member of Q
async def test_not_enough_disk_refuses_the_whole_plan(..., monkeypatch):
    # monkeypatch bulk_upload.free_mb -> 100, UPLOAD_MIN_FREE_MB=1024 -> 507 not_enough_space, "1024" in message
async def test_over_the_limit_is_refused_whole(..., monkeypatch):   # BULK_UPLOAD_MAX_FILES=2, 3 files -> 413 too_many_files, "2" in message
async def test_reader_and_non_member_are_refused(...):  # reader -> 403 insufficient_role; non-member -> 404
async def test_documents_token_works_and_chat_token_does_not(...):
    # principal capabilities = scopes.narrow(frozenset({"reader","chat"}), frozenset({"documents"})) -> 200
    # ... frozenset({"chat"}) -> 403 missing_capability
async def test_nothing_is_stored(...):                   # documents/project_documents row counts unchanged, storage dir empty
```

- [ ] **Step 2: Run them to see them fail** — `.venv/bin/pytest server/tests/test_upload_plan.py -q` (with the scratch-DB env above). Expected: 404s (route missing).

- [ ] **Step 3: Implement `bulk_upload.py` and the plan route** with the interfaces above. The route takes the role check first (before any name is examined), then the file count, then decides each file in one connection (`deps.get_conn`), then the disk check. `decide` reuses `doc_versions` queries where they fit (`_current_holder`, `_older_version_of`, `document_ref`) and adds the all-matches query beside `_same_name` (same SQL with no `LIMIT`, plus `last_version` and the person columns).

- [ ] **Step 4: Run the tests to see them pass**, then the neighbours: `test_version_upload.py test_project_filing.py test_doc_versions_write.py`. Expected: all pass.

- [ ] **Step 5: Commit** (ask first): `feat(upload): one decision for what a file becomes in a project, and the plan route`.

---

### Task 2: The upload route

**Files:**
- Modify: `server/routers/project_uploads.py`
- Modify: `server/routers/docs.py` (`_store_and_hold`: a `prelock` that returns `STORE_NOTHING` commits and stores nothing)
- Modify: `server/services/doc_versions.py` (extract `prove_legacy_current(conn, project_id, doc_id: str, user_id: str) -> str | None` from `file_from_library`, same argument order as `verify_legacy_current`: verify, then the audit and the `document.added` event; `file_from_library` calls it at the same point it records them today)
- Test: `server/tests/test_project_upload.py`

**Interfaces:**
- Consumes: Task 1's `decide`, `Decision`, `clean_name`, `free_mb`, `reserve_mb`.
- Produces: `docs_router.STORE_NOTHING` (a module sentinel object). `_store_and_hold` returns `(False, None, STORE_NOTHING)` when `prelock` returned it.
- Produces (in `bulk_upload`, so the concurrency tests can drive them on real connections):
  - `@dataclass(frozen=True) class Outcome: result: str; decision: Decision; document_id: str | None` (`document_id`: the document a `version`/`restored` files into).
  - `async def decide_and_lock(conn, user_id, project_id, *, name, sha256, document_id, on_match, on_older) -> Outcome`: the advisory lock, the project key, `prove_legacy_current`, `decide`, the outcome table below, and `lock_document` when it files into an existing document. Runs inside the caller's transaction.
  - `async def file_it(conn, user_id, project_id, outcome, *, doc_id, name, note) -> dict`: `add_version` or `create_document`; returns the `document` ref. Only called for an outcome that stores.
- Test: `server/tests/test_bulk_upload_concurrency.py` (the scratch-database, forced-overlap pattern of `test_doc_versions_concurrency.py`)
- Produces (route): `POST /v1/projects/{project_id}/uploads`, multipart: `file`, `on_match` ∈ `version|separate|skip` (default `version`), `on_older` ∈ `leave|restore` (default `leave`), `document_id` (UUID, optional), `note` (≤ 500, `doc_versions.clean_note`). Bad enum → 422. Answer:
  `{"result", "document": {id, name, version} | null, "doc_id", "state", "queued", "candidates": [...]}`; `201` for `created`, `version`, `restored`; `200` for `unchanged`, `skipped`, `left_older` (with `"older_version": k`), `ambiguous` (with `candidates`). `state`/`queued` from `docs_router._state_and_queued` for the filed or matched content; null for `ambiguous` and `skipped`.
- Outcome table (inside the transaction, after the locks):

| Decision | on_match / on_older | Result | Stores |
|---|---|---|---|
| `unchanged` | any | `unchanged` | no |
| `older_version` | `leave` | `left_older` | no |
| `older_version` | `restore` | `restored` (`add_version`, which records the restore under the old version's name) | yes |
| `version` | `version` | `version` (`add_version`) | yes |
| `version` | `separate` | `created` (`create_document`) | yes |
| `version` | `skip` | `skipped` | no |
| `ambiguous` | `version` | `ambiguous` | no |
| `ambiguous` | `separate` | `created` | yes |
| `ambiguous` | `skip` | `skipped` | no |
| `new` | any | `created` | yes |

- Flow: role check (own connection, as `upload_version`), `clean_note`, `stage_upload(file, clean_name(file.filename))` (its 413/415/422 are the refusals), disk check (507 `not_enough_space`, the staged file discarded), then `_store_and_hold(staged, name, hold, prelock=decide_and_lock)` where `decide_and_lock(conn)`: **`SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))` on `'project_upload:' || project_id` first**, then `lock_project_key`, `prove_legacy_current`, `decide`, and when the outcome files into an existing document, `lock_document` on it; returns `STORE_NOTHING` for a no-store outcome (kept in a closure). `hold` calls `add_version` or `create_document`. Then `_after_upload`, `_queue_after_upload(doc_id, job, principal.user_id)`, `_state_and_queued`.
- Why the advisory lock: the document to lock is known only after deciding, so without it two uploads decide from the same snapshot (two identical files would both decide `version`, and the second would hit `add_version`'s 409 `already_current`). The lock serialises only this route's decisions within one project; the transaction it covers is milliseconds (staging, the slow part, happens before it). Lock order becomes advisory → projects → project_docs → documents; no other path takes the advisory lock, so no cycle. Side effect, better than the spec's accepted race: two uploads of one new name through this route become a document and its version 2, not two documents.

- [ ] **Step 1: Write the failing tests** in `server/tests/test_project_upload.py`:

```python
async def test_new_file_is_created_held_by_the_project_only_and_indexed(...):
    # 201, result "created"; no library_entries row; state "indexed" after the harness drains; storage has <sha>.txt
async def test_matching_name_becomes_the_next_version(...):         # 201 "version", document.version == 2
async def test_separate_creates_a_second_document_with_the_name(...):
async def test_skip_stores_nothing(...):                            # 200 "skipped"; no documents row for the sha; storage empty
async def test_unchanged_stores_nothing(...):                       # 200 "unchanged", document.id == x
async def test_older_bytes_are_left_alone_by_default(...):          # 200 "left_older", older_version == 1, no new version row
async def test_restore_makes_the_older_bytes_current_under_the_old_name(...):  # 201 "restored", origin 'restore', origin_version 1
async def test_ambiguous_files_nothing_unless_separate_or_skip(...):
    # on_match=version -> 200 "ambiguous", 2 candidates, no new row; separate -> 201 "created"; skip -> 200 "skipped"
async def test_document_id_overrides_the_name_match(...):           # name matches A, document_id=B -> version of B
async def test_document_id_of_another_project_is_not_found(...):    # 404 not_found, nothing stored
async def test_the_uploader_is_on_the_job_for_a_new_version(..., run_queue=False):
    # doc_index_jobs.requested_by == alice for the new content
async def test_activity_entries_are_written(...):                   # project_events kinds document.added / document.version_added
async def test_decides_again_at_send_time(...):
    # plan says "new" for report.pdf; another member files report.pdf; upload -> "version"
async def test_refusals_use_the_upload_codes(...):                  # too_large 413, unsupported_type 415, empty_file 422
async def test_not_enough_disk_after_staging_is_507_and_leaves_nothing(..., monkeypatch):
async def test_bad_on_match_is_422(...):
async def test_plan_and_upload_agree(...):
    # for each case (new, version, ambiguous, unchanged, older_version) build the project, ask the plan,
    # then upload with defaults: plan action -> expected result
    # {"new": "created", "version": "version", "ambiguous": "ambiguous", "unchanged": "unchanged",
    #  "older_version": "left_older"}

# test_bulk_upload_concurrency.py — a third connection holds the advisory lock; both uploads
# (decide_and_lock + content insert + file_it, each in its own transaction) start; once
# pg_stat_activity shows both waiting, the holder commits.
async def test_two_uploads_of_one_name_at_once_become_consecutive_versions(scratch):  # versions {2, 3}
async def test_two_uploads_of_one_new_name_at_once_become_a_document_and_its_version_2(scratch):
    # results sorted ["created", "version"], one project_docs row
async def test_two_uploads_of_the_same_bytes_at_once_answer_unchanged_not_409(scratch):
    # results sorted ["unchanged", "version"]
```

- [ ] **Step 2: Run them to see them fail.** Expected: 404/405 (route missing), ImportError for the concurrency file.

- [ ] **Step 3: Implement** the `STORE_NOTHING` path in `_store_and_hold`, `prove_legacy_current`, and the route as above. `upload_version`'s behaviour must not change (its `prelock` returns None).

- [ ] **Step 4: Run the new tests, then the whole server suite** (scratch DB): `.venv/bin/pytest server/tests -q -x`. Expected: all pass (3340 + the new ones).

- [ ] **Step 5: Commit** (ask first): `feat(upload): one route stores and files a file into a project, deciding from the project as it is`.

---

### Task 3: The browser's batch logic (no UI)

**Files:**
- Create: `src/lib/uploadBatch.js` + `src/lib/uploadBatch.test.js`
- Create: `src/lib/folderDrop.js` + `src/lib/folderDrop.test.js`
- Create: `src/lib/fileDigest.js` + `src/lib/fileDigest.test.js`
- Modify: `src/utils/apiFetch.js` (add `apiUpload`), `src/lib/projectsApi.js` (+ its test)

**Interfaces:**
- `folderDrop.js`:
  - `filesFromInput(fileList) -> Array<{file, path}>` (`path` = `file.webkitRelativePath || file.name`).
  - `async filesFromDataTransfer(dataTransfer) -> Array<{file, path}>`: each item's `webkitGetAsEntry()`; directories walked recursively, calling `readEntries` **until it returns an empty array**; falls back to `dataTransfer.files` when entries are unavailable.
- `uploadBatch.js`:
  - `ACCEPTED_EXT = ['.pdf', '.md', '.markdown', '.txt']`
  - `isHiddenPath(path) -> bool`: any `/`-separated part starts with `.` or equals `node_modules`.
  - `nameKey(name) -> string`: last path part, trimmed, lower-cased (the server's `lower(btrim())`).
  - `async collect(items, readHead) -> { files: Array<{file, path, name, head}>, leftOut: number }`: drop hidden paths silently; keep accepted extensions, or no accepted extension with `head` starting `%PDF-`; count the rest as `leftOut`; sort by `path` with `localeCompare(…, 'en', { numeric: true })`.
  - `markRepeats(rows) -> rows`: rows in path order; the second and later rows with one `nameKey` get `repeat: true`.
  - `choicesFor(row) -> Array<{id, label}>` and `defaultChoice(row) -> id`:
    - `version`: `version` "New version of *name*" (default), `separate` "Separate document", `skip` "Skip";
    - `older_version`: `leave` "Leave alone" (default), `restore` "Restore as current";
    - `ambiguous`: one `doc:<id>` per candidate, labelled `v{version} · added by {name} · {date}`, plus `separate`; default `skip`;
    - `repeat`: `skip` (default), `separate`;
    - `new`, `unchanged`, `refused`: no choices.
  - `requestFor(row, choice) -> null | { on_match, on_older, document_id }`: null when nothing is sent (`unchanged`, `refused`, `skip`, `leave`).
  - `summarize(rows, choices) -> { new, versions, unchanged, leftAlone, skipped, refused, toSend, bytesToSend }`.
- `fileDigest.js`: `async digestFile(file) -> { sha256: string | null, head: string }` (`head` = base64 of the first 5 bytes; `sha256` null when `globalThis.crypto?.subtle` is missing, i.e. plain HTTP); `canHash() -> bool`. Reuses `sha256Hex` from `src/utils/docHash.js`.
- `apiFetch.js`: `apiUpload(host, port, path, formData, { onProgress, signal }) -> Promise<{ status, ok, body }>` over `XMLHttpRequest` with `withCredentials = true`; calls the same 401 and `missing_capability` 403 handlers as `apiFetch`; `body` is the parsed JSON or null.
- `projectsApi.js`: `uploadPlan(projectId, files)` → POST JSON `…/uploads/plan`; `uploadFile(projectId, file, { on_match, on_older, document_id }, { onProgress, signal })` → resolves to the answer, or throws an `Error` whose `message` is `noticeFor(status, detail, notice)` and whose `status` and `code` (`detail.error`) are set.

- [ ] **Step 1: Write the failing tests:**

```js
// uploadBatch.test.js
it('leaves out any path with a dot part or node_modules', () => {
  expect(isHiddenPath('repo/.git/objects/ab')).toBe(true);
  expect(isHiddenPath('a/node_modules/x/README.md')).toBe(true);
  expect(isHiddenPath('a/b/.env')).toBe(true);
  expect(isHiddenPath('a/b/notes.md')).toBe(false);
});
it('gathers never-accepted types into a count and keeps an extensionless PDF', async () => { /* .png, .docx -> leftOut 2; 'scan' with head %PDF- kept */ });
it('sorts by path the same way every time', async () => { /* 'b/README.md' after 'a/README.md'; 'x10' after 'x2' */ });
it('marks the later of two names that differ only by case or spaces as a repeat', () => { /* 'Setup.pdf', ' setup.pdf' */ });
it('offers each row its choices with the spec defaults', () => { /* version->version, older->leave, ambiguous->skip, repeat->skip */ });
it('sends nothing for unchanged, refused, skip and leave', () => {});
it('turns a candidate choice into document_id', () => { /* 'doc:abc' -> { on_match: 'version', document_id: 'abc' } */ });
it('counts the summary and the bytes to send', () => {});
// folderDrop.test.js
it('walks a folder whose directory returns 100 entries per readEntries call', async () => { /* fake reader: 250 entries in batches of 100, then [] -> 250 files */ });
it('falls back to dataTransfer.files when entries are missing', async () => {});
// fileDigest.test.js
it('gives the first five bytes in base64 and the sha256 when hashing exists', async () => {});
it('gives no hash without crypto.subtle', async () => { /* vi.stubGlobal('crypto', {}) -> sha256 null, canHash false */ });
// projectsApi.test.js
it('uploadFile sends on_match, on_older and document_id, and reports progress', async () => { /* fake XHR */ });
it('uploadFile throws the server sentence with status and code', async () => {});
```

- [ ] **Step 2: Run** `npx vitest run src/lib/uploadBatch.test.js src/lib/folderDrop.test.js src/lib/fileDigest.test.js src/lib/projectsApi.test.js`. Expected: FAIL (modules missing).

- [ ] **Step 3: Implement** the four modules with the interfaces above.

- [ ] **Step 4: Run them again.** Expected: PASS.

- [ ] **Step 5: Commit** (ask first): `feat(upload): the browser's file collection, hashing and per-file choices`.

---

### Task 4: The batch hook

**Files:**
- Create: `src/hooks/useBulkUpload.js` + `src/hooks/useBulkUpload.test.js`

**Interfaces:**
- Consumes: Task 3's `collect`, `markRepeats`, `defaultChoice`, `requestFor`, `summarize`, `digestFile`, `canHash`, `api.uploadPlan`, `api.uploadFile`.
- Produces: `useBulkUpload({ api, projectId, onLanded }) -> { phase, rows, leftOut, hashed, progress, summary, error, choose(index, choiceId), pick(items), start(), stop(), retry(index), reset() }`
  - `phase` ∈ `idle | reading | planned | sending | done`.
  - `pick(items)` (`items` from `filesFromInput`/`filesFromDataTransfer`): `collect`, then `digestFile` one file at a time with `progress = { done, total }`, then one `uploadPlan`; rows = plan answers + `repeat` marks + the default choice each; `hashed = canHash()`.
  - Each row while sending: `status` ∈ `waiting | sending | done | failed | stopped`, `percent` while sending, `answer` (the server's) when done, `error` (the server's sentence) when failed.
  - `start()` sends rows whose `requestFor` is not null, **two at a time**, in row order; `stop()` lets the two in flight finish and marks the rest `stopped`; `retry(i)` re-sends one failed row.
  - A failure whose `status` is 401, or 403 `insufficient_role`, or 404 `not_found`, stops the batch **once**: `error` = "You were signed out. Sign in, then drop the files again: what was sent is safe." (401) or "You can no longer add documents to this project." (403/404); the remaining rows become `stopped`, not `failed`.
  - `onLanded()` is called after a file lands, at most once a second (trailing), and once when the batch ends.
  - `summary` after `done`: `{ sent, created, versions, restored, unchanged, failed }` from the server's `result`s.

- [ ] **Step 1: Write the failing tests** with a faked `api` (resolvable promises so the test controls timing) and stubbed `digestFile`:

```js
it('reads, plans and gives every row its default with nothing touched', async () => {});
it('sends each per-file choice in the request', async () => { /* choose(1,'separate') -> uploadFile(..., { on_match: 'separate' }) */ });
it('skips the later repeated name by default', async () => {});
it('has at most two files in flight', async () => { /* 5 sendable rows; count concurrent unresolved calls === 2 max */ });
it('Stop lets the two in flight finish and starts no more', async () => {});
it('Try again re-sends only that row', async () => {});
it('a lost role stops the batch once and leaves the rest stopped', async () => { /* 2nd upload rejects {status:403, code:'insufficient_role'} -> error set once, 1 call after it at most (the other in flight) */ });
it('a 401 stops the batch with the signed-out sentence', async () => {});
it("row shows the server's result, not the plan's", async () => { /* plan 'new', answer result 'version' -> row.answer.result 'version' */ });
it('re-reads the list at most once a second while files land, and once at the end', async () => { /* fake timers */ });
it('without hashing, says so and still sends every matching file', async () => { /* canHash false -> hashed false; sha256 null in plan request */ });
```

- [ ] **Step 2: Run** `npx vitest run src/hooks/useBulkUpload.test.js`. Expected: FAIL.

- [ ] **Step 3: Implement** the hook. The plan request carries `{ name, size, sha256, head }` per file (name = `file.name`).

- [ ] **Step 4: Run again.** Expected: PASS.

- [ ] **Step 5: Commit** (ask first): `feat(upload): the batch, two files at a time, stoppable, one message when access is lost`.

---

### Task 5: The screen

**Files:**
- Create: `src/components/projects/BulkUploadPanel.jsx` + `.test.jsx`
- Modify: `src/components/projects/ProjectDocsTab.jsx` (+ `.test.jsx`)

**Interfaces:**
- Consumes: `useBulkUpload`, `filesFromInput`, `filesFromDataTransfer`, `choicesFor`, `summarize`.
- `BulkUploadPanel({ theme, project, batch })` renders the plan and the sending list; `ProjectDocsTab` owns the hook, the two buttons, the hidden inputs (`multiple`; `webkitdirectory` on the folder one, set through a ref because React drops unknown boolean attributes), the drop target, and the summary line.

Copy (exact):
- Buttons: **Upload files**, **Upload folder**; under them, muted: "…or drop files or a folder anywhere on this tab".
- While reading: "Reading files · {done} of {total}".
- Header: `Upload to "{project.name}"` and `{n} files · {MB} MB to send` (one decimal under 10 MB).
- Rows: `New document` · `New version (v{version}) of {name}` · `Unchanged: not sent` · `Same as v{version} of {name}: left alone` · `Another file here is named {name}: skipped` · `{k} documents here are named {name}: skipped` · the refusal's `message`. A row with choices has a small menu (`<select>`, `aria-label="What to do with {path}"`).
- Left-out line: "{k} files aren't PDF, Markdown or text: left out".
- No hash: "Unchanged files can't be detected before sending on this connection."
- Separate document chosen (once per batch): "A separate document with the same name will be asked about by name next time."
- Counts: `{new} new · {versions} new versions · {unchanged} unchanged · {left} left alone · {skipped} skipped · {refused} can't be sent` (zero parts left out).
- Buttons: **Cancel**, **Upload {toSend}** (disabled at 0).
- Sending: `Uploading · {done} of {total}` with **Stop**; rows `✓` done, `…` sending `Sending ({percent}%)`, failed with the server's sentence and **Try again**; `{k} waiting`.
- After: `{sent} sent: {created} new, {versions} new versions. {failed} failed.` (zero parts left out), **Show details**, **Dismiss**.
- Drop overlay while dragging files over the tab: `Drop to upload to "{project.name}"`. Drags that carry no files (`dataTransfer.types` without `Files`) are ignored.
- Rows stack (name, then what it becomes, then the menu) below 640 px (`sm:` breakpoint).

- [ ] **Step 1: Write the failing tests:**

```js
// ProjectDocsTab.test.jsx
it('shows Upload files, Upload folder and a drop target only to someone who can file', async () => {
  /* reader project: no buttons, dropping files does nothing (api.uploadPlan not called) */
});
it('dropping files shows the plan, and Upload with nothing touched sends the defaults', async () => {});
it('re-reads the documents as files land', async () => {});
// BulkUploadPanel.test.jsx
it('shows each row in plain words and the counts', () => { /* one row per action incl. ambiguous with 2 candidates */ });
it('gathers left-out files into one line', () => {});
it('says when unchanged files cannot be detected', () => {});
it('a failed row keeps the server sentence and offers Try again', () => {});
it('the summary line counts what happened and can be dismissed', () => {});
```

- [ ] **Step 2: Run** `npx vitest run src/components/projects`. Expected: the new tests FAIL, the old ones pass.

- [ ] **Step 3: Implement** the panel and the tab changes. The existing **File a document…** select and its dialog stay as they are.

- [ ] **Step 4: Run the whole frontend gate:** `npx vitest run`, `npx eslint src`, `npm run build`. Expected: all pass, 0 lint errors.

- [ ] **Step 5: Commit** (ask first): `feat(upload): Upload files, Upload folder and drop on a project's Documents tab`.

---

### Task 6: Docs and the walk

**Files:**
- Modify: `docs/USER_GUIDE.md` ("Projects and people": uploading many files, the plan, each choice, what each state means)
- Modify: `docs/LIBRARY.md` (the two routes with their fields and answers, the script loop with `shopt -s nullglob` and `--fail-with-body`, the repeated-name rule for scripts)
- Modify: `docs/ARCHITECTURE.md` (the decision function shared by the plan and the upload)
- Modify: `docs/DEPLOYMENT.md`, `.env.example` (`BULK_UPLOAD_MAX_FILES`, `UPLOAD_MIN_FREE_MB`, the 507)
- Modify: `docs/TESTING.md` (the walk record), `CHANGELOG.md` (Unreleased: Added), the spec's status line
- Modify: this plan (a "Built" list of departures found while building)

- [ ] **Step 1: Walk it** on a scratch backend (`walk.sh` pattern from phase 1: `:8011`, a scratch database on `:5434`, `AUTH_ENABLED=false`, the local embedding model) with headless Chromium, the spec's nine steps. The folder is picked with `setInputFiles(<dir>)` on the folder input (Playwright sends a directory to a `webkitdirectory` input); the drop is checked with a synthetic `drop` carrying files. Record: the plan is right with no input; the network log shows nothing sent on the second run; the edited/reverted files; a restart while documents wait; `WORKERS=2` claims once each; a chat answer while a batch indexes; tab closed mid-send then dropped again; the script loop twice with a `documents` token (needs auth on: a throwaway Keycloak on `:18081`, never the compose one, or a token minted on the scratch database); a Reader sees nothing to press; 360 px. Also time a 50-document batch on this CPU-only machine for the docs.
- [ ] **Step 2: Write the docs** from what the walk showed, with the measured times.
- [ ] **Step 3: Final gates:** server suite on the scratch DB, `npx vitest run`, `npx eslint src`, `npm run build`.
- [ ] **Step 4: Commit** (ask first): `docs(upload): uploading many files into a project, walked`.
