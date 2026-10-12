# V1 — document versions: handoff

**Branch:** `feat/v1-document-versions` (from `development` at `c4dc6f5`; not pushed, not merged). **Spec:** `docs/superpowers/specs/2026-10-12-document-versions-design.md`. **Plan:** `docs/superpowers/plans/2026-10-12-document-versions.md`.
**Commits:** `368013b` (spec) … `77e4826` (final-review fix wave), 18 in all. Task 16 adds one walk fix, the `docs/TESTING.md` counts and this handoff, all uncommitted for review.

## Gate (2026-10-12)

| Check | Before the walk (HEAD `77e4826`) | After the walk fix |
|---|---|---|
| Backend `.venv/bin/python -m pytest server/tests -q` | 1231 passed, 16 skipped, 2 deselected, 3 warnings | 1231 passed, 16 skipped, 2 deselected, 3 warnings |
| Frontend `npx vitest run` | 83 files, 573 passed | 83 files, 574 passed |
| `npm run lint` | clean | clean |

The 3 warnings all come from outside this branch, as the final review found: authlib's httpx notice, Starlette's TestClient notice, and the `itertools.product` parametrize in `test_chat_prompt.py`. vitest printed no warnings.

## Running-app walk (2026-10-12)

**Set-up.** The docker-compose Keycloak was not started or contacted; on this machine it is the real identity server.

- **Identity server.** A throwaway Keycloak 26.0 container (`nr-walk-keycloak`, `127.0.0.1:18081`, `start-dev --import-realm`) ran on a scratch copy of `deploy/keycloak/realm-export.json`. The checked-in export already allows `http://localhost:5173/v1/auth/callback` (added after A0), so the copy only gained four walk users with the `reader` and `chat` roles: Olive (project Owner), Cara (Contributor), Max (Maintainer) and Rita (Reader). The realm's `admin-user` was used for J12.
- **Backend.** `run.py` was started under `env -i` with explicit values, so nothing came from `.env`:
  - OIDC pointed at the throwaway realm; `SESSION_SECRET` was a scratch random value and `COOKIE_SECURE=false`;
  - database `natural_reader_walk`, with scratch `DOC_STORAGE_DIR` and `LOG_DIR`, and `DOCLING_ENABLED=false`;
  - local Ollama at `127.0.0.1:11434`, with `INFERENCE_OLLAMA_MODELS=gemma4:31b-cloud` and `SUMMARIZE_MODEL=gemma4:31b-cloud`, and embeddings from local `nomic-embed-text`;
  - no OpenRouter key.
- **Users.** The four walk users were pre-enrolled as `active` rows in the scratch DB. This was setup only; enrolling people through the UI is A0's journey 0.
- **Frontend.** Vite on 5173. Partway through, Vite was restarted with a walk-only config that turned off the dev proxy's upstream keep-alive, to rule that out as a cause of finding 1 below. It wasn't the cause.
- **Browsers.** Playwright drove Cara, Max, Rita and admin-user. A second browser (chrome-devtools, isolated context) drove Olive, so two users could act at once.
- **Clicks.** Playwright's pointer clicks didn't register on this app, as A0 recorded. Every control was clicked with a DOM `click()` on the control itself, text was entered with `fill`, and screenshots were used to judge layout.

Everything was removed afterwards: the container, both scratch databases, and the walk files. No `pg_dump` was taken (J12 used the synthetic route).

| # | Journey | Controls used | What was seen | Result |
|---|---|---|---|---|
| J1 | Contributor uploads v2 from ⋯ | (Cara) Documents row ⋯ → Upload new version → Choose file, "What changed?" note → Upload | One row "report.pdf · indexed · v2 · 2 versions". History shows v2 current with the note, and v1 with Open and Restore, no Delete. A large file (802 chunks) watched by a second member (Olive) went from "README.md · indexing · v4 · 4 versions" to "indexed" in place after 49 s, without leaving the tab, so the I2 poll works. **But see finding 1:** the uploader's own tab showed the old row for up to 60 s. | Pass, with finding 1 |
| J2 | Chat on current, then on v1 | Row Open → Chat "Use this document"; history "Open v1" → banner → Chat, New chat | Current version: "The total revenue … is 5.8 million euros and the head of sales is Diego Ruiz (page 1)". v1: banner "Old version — v1 of report.pdf in Acme X (current is v2) · Open current" in page flow above the toolbar. Chat label "report.pdf · v1 (old version)". Answer: "Please note that this document is version 1 and may be out of date; according to this version, the total revenue … is 4.2 million euros and the head of sales is Alice Brown (page 1)". | Pass |
| J3 | Restore v1 → v3, with an Activity line | History "Restore v1"; Activity tab | Toast "Restored v1 as v3."; history v3 current "restored from v1". Activity: 'Cara Contributor restored v1 of "report.pdf" (now v3)'. Restore on the v1 line (whose bytes are now current) is refused with "This file is already the current version.", and no event is written. | Pass |
| J4 | Name-match dialog: add as new version, keep both | Reader: open a local same-named report.pdf, "Project for this document" → Acme X, Index → dialog. Library: "+ project" chip. Documents tab: "File a document…" | Reader path: "A document called report.pdf is already in Acme X (v3)." → **Add as new version** → v4. Reader path again → **Cancel**: closes, the file stays unfiled. Library chip, a different report.pdf → **Keep both** → a second row "report.pdf · v1". Documents tab, with Olive's old-bytes copy → "This file is an older version (v3) of report.pdf in Acme X." [Restore v3 as current] [Keep both] [Cancel] → **Escape** closes it. All three filing paths and both 409 prompts covered (controller ruling P10). | Pass |
| J5 | Maintainer merges two duplicates | (Max) ⋯ → Mark as newer version of… → pick → Confirm merge | The confirmation reads exactly as spec §6: "budget-final.md becomes v2 of budget.md, and the row will be called budget-final.md. This can't be undone; every file stays in the history." Result: one row "budget-final.md · v2 · 2 versions", with history "merged (was v1)". **The picker listed two "report.pdf" options that couldn't be told apart.** Fixed (finding 2) and walked again, including a merge of two upgraded `minutes.pdf` duplicates in J12's data. | Pass after fix |
| J6 | Maintainer deletes an old version; a Contributor sees no Delete | (Max) history Delete v2 → "Delete v2 for good? Its file goes if nothing else uses it." → Confirm delete | Toast "Deleted v2."; the row reads "v4 · 3 versions" (the number isn't reused). The content row, its chunks and its stored file were GC'd (checked in the scratch DB). Activity: 'Max Maintainer deleted v2 of "README.md"'. No Delete on the current line. Cara's history lines had Open and Restore only. | Pass |
| J7 | Reader sees history and can Open; no ⋯, no Restore | (Rita) Documents tab, history, Open v1, banner Open current | No ⋯, no ×, no "File a document…"; history lines offer Open only. Opening the **old** v1 works (a parked item): "Old version — v1 of README.md in Acme X (current is v4)". Acme Y holds the same bytes as its current version and Rita isn't a member, and "Acme Y" appears nowhere on the page. "Open current" opened v4's hash. | Pass |
| J8 | Owner's personal copy fell behind → Library line → Open current | (Olive) Library → row line "Acme X has a newer version (v4) · Open current" | Opened the board edition (v4), no banner. | Pass (see finding 3a: the Library needed a revisit to refresh) |
| J9 | README in two projects: v2 in X leaves Y unchanged; Library shows two rows | (Cara uploads into X) → (Olive) Library, Acme Y Documents tab | Acme Y: "README.md · v1 · 1 version". Olive's Library: her own README row "Acme Y · v1" with "Acme X has a newer version (v4) · Open current", plus a separate row "README.md · via project · Acme X · v4". | Pass |
| J10 | An old chat's citation opens the exact version, with the banner | (Cara) Chat → saved session → source chip "p. 1" | v1 chat → opened the "first draft" bytes, banner "Old version — v3 of report.pdf in Acme X (current is v4)" (see finding 3b on why it says v3). v2 chat → "Old version — v2 of report.pdf in Acme X (current is v4)". | Pass |
| J11 | Documents tab, history, upload strip and dialog at 390 px | Playwright viewport 390×844 (Cara); chrome-devtools mobile emulation 390×844 (Olive, for the Owner-only controls) | No horizontal scroll on the Library or project tabs (scrollWidth 390). ⋯ and × are 44×44, and the chip × is at least 24 px. The merge picker, the merge and remove confirmations ("Remove README.md and its 3 versions from Acme X? …"), the history and the upload strip all wrap. The dialog fits with 44 px-tall buttons, and focus lands on the first. Observations 3c–3e below. | Pass |
| J12 | A pre-018 database upgrades: documents appear as v1 with unchanged names | Scratch DB `natural_reader_walk_upgrade`; the backend restarted on it ran 018 at startup; admin-user → Library → Legacy P | **Synthetic route** (no real data). `apply_migrations(max_version=17)`, then seeded rows: a normal placement, a filer-renamed entry ("Bob notes.pdf"), a filer who removed their entry, an unverified legacy placement, and two different files both called `minutes.pdf`. The names the pre-018 app showed were recorded with `_placement_name`'s expression. After the upgrade: "2 members · 5 documents"; every row "v1 · 1 version" with its old name. DB check: all six rows `version 1, is_current, origin upload, last_version 1`; names and `verified` identical to before; schema 18 with `project_documents_project_idx`. The `minutes.pdf` pair then merged into "v2 · 2 versions" through the fixed picker. | Pass |

**API-only or env-only:** none of J1–J12. Every step had a UI control.

**Checked on the parked list (controller rulings):**

- the poll cap was never hit;
- the 390 px tap targets are fine;
- the reader-path dialog works;
- a Reader can open an old version.

Not checked: the reader showing `link.error` on a *failed* filing. No filing failed during the walk, and forcing one (a 403 or a network error mid-filing) wasn't attempted. That parked item stays open.

**Privacy check:** the walk's `server.log` and `audit.log` were searched for the walk users' emails, `%40`, document names, project names and the version note. The only hit was the startup line "Bootstrap admin email set to admin@example.com", the deployer's own `BOOTSTRAP_ADMIN_EMAIL`. That line predates this branch.

## Found during the walk

### 1. After an upload, the uploader's whole session freezes until indexing finishes (pre-existing, app-wide; **not fixed**, needs a decision)

**What the user sees.** Cara uploads a large new version. The toast says "README.md is now v3 (indexing…)", but the row keeps saying "v2 · 2 versions" for the full minute the index takes (802 chunks on this CPU-only machine). Every other request she makes, in any tab, also hangs for that minute: a plain `fetch('/v1/projects')` took 49.7 s. Other users are unaffected. Olive watched the same row move from indexing to indexed in real time. With small files the window is under a second, which is why J1 looks fine at first.

**The causal chain** (verified with `pg_stat_activity` during an index):

1. `deps.get_conn` is a yield dependency that holds one pooled connection for the request.
2. `get_current_user` runs `UPDATE sessions SET last_seen_at = now()` on that connection, which opens an implicit transaction and row-locks the caller's session row.
3. The upload route schedules indexing as a FastAPI `BackgroundTask`. In FastAPI 0.142 (unpinned in `requirements.txt`), a yield dependency exits after the response *and after background tasks*. The request's connection therefore sits "idle in transaction", still holding the session-row lock, for the whole index.
4. Every later request from the same user runs the same `UPDATE` on the same row, so it waits on the lock (`Lock / transactionid`, then `tuple`). It is released the instant the index finishes; the stalled requests were all logged in that same second.

**Why it isn't Vite, and isn't V1.**

- Fresh connections to `/v1/health` (which uses no DB) stayed at about 1 ms throughout, direct and through Vite.
- Turning off the Vite proxy's upstream keep-alive changed nothing.
- So this is a DB lock, and should behave the same behind nginx in production. That follows from the mechanism; it wasn't run under nginx.
- The A1 reader upload (`POST /v1/docs`) shows the same stall, so this predates V1. The pattern dates from the auth work (`7747bd7`, `2ddee5a`, 2026-09-14). Whether an older FastAPI ran the dependency exit before background tasks wasn't checked.
- V1 makes it more visible: the Documents tab is the first screen where a user uploads and then watches a row.

**Fix options**, each touching the request-connection lifecycle that every route and the test override use, hence escalated rather than patched here:

- (a) Do the session touch on its own short autocommit connection, or commit right after it.
- (b) Declare the connection dependency with `Depends(get_conn, scope="function")` on routes that schedule background work, so it closes before the response.
- (c) Commit and release the request connection explicitly before returning from those routes.

Option (a) is the smallest and fixes every route at once. It needs a check that `server/tests`' transactional-connection override still isolates tests.

### 2. Merge picker couldn't tell same-named documents apart (**fixed**, TDD)

"Mark as newer version of…" listed each candidate by name only, so two `report.pdf` rows appeared as two identical options. Merging same-named duplicates is the main case J5 exists for: pre-V1, a revised file re-uploaded under the same name became a second document, and spec §8 says these must be merged by hand. Choosing the wrong one can't be undone.

- **Fix:** each option now reads `name · vN · last uploader`, e.g. "report.pdf · v4 · Cara Contributor", "minutes.pdf · v1 · Bob Legacy". This uses the `version` and `updated_by` the listing already returns.
- **Files:** `src/components/projects/ProjectDocRow.jsx` (the option label) and `src/components/projects/ProjectDocsTab.test.jsx` (new test "tells same-named documents apart in the merge picker by version and last uploader").
- **RED:** `npx vitest run src/components/projects/ProjectDocsTab.test.jsx -t "same-named"` failed with Expected `["report.pdf · v4 · Cara", "report.pdf · v1 · Olive"]`, Received `["report.pdf", "report.pdf"]`.
- **GREEN:** `src/components/projects/` 5 files / 68 tests passed, then the full gate above.
- **Re-walked:** J5's picker as Max, and a real merge of the upgraded `minutes.pdf` pair in J12.

### 3. Observations (not fixed; small, mostly pre-existing)

- **a. The Library doesn't refetch on its own.** Olive's Documents list, loaded earlier, still showed "report.pdf · Acme X · v2" and the pre-merge budget rows after other members had acted. Switching Library tabs doesn't reload; going to Reader and back does. This is the same class as A0's finding 4 and predates V1. With versions it matters more, because the chips and the "newer version" line go stale.
- **b. The banner names the highest version number holding the bytes.** A citation made on v1, after v1 was restored as v3, opens with "v3 of report.pdf". That is spec §5.2's rule ("its highest number there"), but a user who remembers chatting with "v1" may be puzzled.
- **c. Same-named rows are hard to tell apart on the tab itself.** Their controls share aria-labels ("History of report.pdf", "More actions for report.pdf"), and at 390 px names truncate to about 8 characters ("report.…"). The deferred "same name" marker (spec §10) would cover this.
- **d. App-wide fixed "Read Selection" / "Ask AI" buttons overlay page content at 390 px.** Over the Documents tab they covered the upload strip's "…or from my library" and a history line's Restore. They are fixed in position, so scrolling uncovers what's beneath. This predates V1.
- **e. The reader header overflows at 390 px** (scrollWidth 442; the "More actions" and export buttons hang off the right edge). The old-version banner itself fits (right edge 375). This predates V1.
- **f. The reader's "Project for this document" picker shows "No project"** for a document already in a project. It chooses where the *next* upload goes, but reads as a statement about the open document.
- **g. After "Add as new version" in the reader,** the toast reads "Filed report.pdf into Acme X." without the new version number. The Documents tab's own upload toast does name it ("… is now v4").
- **h. Restore is offered on a history line whose bytes are already current** (v1 after "restore v1 → v3"). The server refuses cleanly ("This file is already the current version."), and no event is written.
- **i. Console noise:** the reader logs a 404 on `GET /v1/docs/{id}` for a local file not yet on the server (the version-status/metadata probe). Harmless.
- **j. The chat model picker restored an unavailable model from browser storage** (`qwen3.5:latest (unavailable)`) until another was chosen. This predates V1.

## Rulings made during the build

Copied from `.superpowers/sdd/2026-10-12-document-versions/progress.md`, in ledger order. This includes the preflight rulings, which the ledger writes as "Ruling P1:" and so on.

- Ruling P1: T3 also updates test_docs_list.py:128,178,266 chip assertions to {id,name,document_id,version:1,versions_count:1} — plan missed existing tests — cost if wrong: none.
- Ruling P2: T7 changes test_entry_verification.py:213 204→201 — plan wrongly said unchecked — none.
- Ruling P3: T12 adds `/v1/projects/p2/documents` → [] to the LibraryPage "reloads…" mock and runs the full vitest at the end — none.
- Ruling P4: T14 useVersionStatus stores {key, old}, sets state only in the async callback, derives null when key≠docId or !enabled — lint rule react-hooks/set-state-in-effect — none.
- Ruling P5: T4 test count 13, not 14. P6: T3 Step 5 test name is test_project_only_row_shows_the_filers_name_not_the_first_uploaders.
- Ruling P7: drop T3 test_listing_placeholders_match_params (duplicate of T2 coverage) — cost: slightly less coverage.
- Ruling P8: T12 contributor-menu test first asserts the "Upload new version" menuitem exists, then that merge is absent.
- Ruling P9: sync `def` tests under pytestmark asyncio become `async def` — test output must be pristine.
- Ruling P10: T14 adds a ChatView render test for "· v{n} (old version)"; App reader-path name-match wiring is covered by walk J2/J4 in T16 — cost: App wiring only checked by the walk.
- Ruling P11: every READ query touching project_documents carries -- HISTORY or -- CURRENT (incl. document_ref, remove_document/post-merge SELECTs, verify_legacy_current lookup); pure INSERT/UPDATE/DELETE by key are exempt — spec §3.4 classifies reads — cost: a reviewer may want writes tagged too.
- Ruling P12: accept interim UI breakage between T7/T10 and T12/T13 (branch never merged mid-way; full suites must still pass at each task end) — cost: none for users; bisect points mid-branch are UI-broken.
- Ruling P13: the TESTING.md test-count update moves from T15 to T16.
- Ruling P14: FromLibraryIn.note has no pydantic max_length; clean_note is the one rule (422 note_too_long) — consistent with multipart route — none.
- Ruling P15: T7 extends test_no_personal_data_at_info_or_above to exercise filing, a version upload, a restore, a merge and a document removal — Global Constraint names that test as the enforcer — cost: a longer test.
- Ruling P16: the UniqueViolation→409 path falls back to document_ref(conn, document_id) so `document` is never null — §5.3 says every 409 carries it — none.
- Ruling P0: plan Global Constraint "ask before each commit" superseded by the user's 2026-10-12 choice: controller commits each task after a clean review.
- Task 4: Ruling: I2 is plan-mandated but the spec §5.3 404 rule wins — lock/scope check first, then merge_self — cost if wrong: none (a 404 for a cross-project self-merge).
- Task 4: Ruling: ⚠️ add_version verify_legacy_current before current_elsewhere refusal (spec §4.2 order) rolls back with the 409 — accepted; legacy-only path; final review may revisit — cost: the 409 names a doc that stays unverified.
- Task 6: Ruling: ⚠️ a version upload does not run _verify_holdings for the uploader's other legacy rows (only verify_legacy_current for this project) — brief doesn't ask, spec §3.2 ambiguous, user said no additions — cost if wrong: uploader's legacy entries/shares/other-project placements stay unverified until a library upload. Final review may revisit.
- Task 7: Ruling: as_version_of=E taking precedence over step 2 (bytes are an old version of D≠E) is intended — as_version_of is an explicit user choice, like the row upload (Review Focus 2); the UI only sends it after the server names a document — cost if wrong: the same bytes can sit in two histories (keep_both already allows that).
- Task 8: Ruling: fix both despite plan text — spec §4.4 locking is what Task 8 exists to prove; force overlap with a blocker connection + pg_stat_activity wait — cost if wrong: a slower, more complex test file.
- Task 10: Ruling P17: T10 may keep projectDocs/unfileDoc exports until T12/T13 if their removal breaks existing tests — full suites must pass per task — cost: dead exports for two tasks.
- Task 12: Ruling: fix I1 despite plan text — spec §6 history must match the row — key the history fetch on doc.version + doc.versions_count — cost: an extra history fetch after some list reloads.
- Task 14: Ruling: move the banner into normal flow (between toolbar and content, or above the viewer area) for every viewer type it can show over; keep it visible in distraction-free mode — spec §6 says the banner informs, never blocks — cost: content area shifts down by one line while an old version is open.
- Ruling: run the final whole-branch review + its fix wave BEFORE Task 16 (gate, walk, handoff), so the running-app walk exercises the final code — cost if wrong: none (Task 16 stays the last step; skill orders final review after all tasks).
- Ruling: final fix wave = I1 (+ global DeadlockDetected → 503 busy safety net), I2, M1 index, M2 phantom audit, M5 unused imports, M6 busy-guard confirms, M7 Escape guard, M8 tap targets, M10 chat wording; park M3, M4, M9, M11 — reviewer's triage, all small — cost if wrong: a slightly larger fix wave.
- Ruling: delete_user variant of the Task 6 cycle parked (legacy-only, pre-existing in A1) with the 503 mapping as safety net.
- Final fix wave: parked — poll cap not reset after a new action (a row stuck in stored >10 min stops later follow-up) — Ruling: park, walk J1 will show if it matters — cost: refresh needed in that rare case.

## Parked / deferred

None of these blocks merge (final-review triage, upheld by the fix-wave re-review). The final fix wave fixed I1 (lock order `projects` → `project_docs` → `documents`, plus a global `DeadlockDetected` → `503 busy` safety net), I2 (Documents-tab poll), M1 (index), M2 (phantom audit), M5 (unused imports), M6 (busy-guarded confirms), M7 (Escape guard), M8 (tap targets) and M10 (chat wording).

**Final review's triage of the ledger's deferred minors (still parked):**

- **Data and server:**
  - M3: a lost concurrent-filing race answers `409 current_elsewhere` instead of the idempotent 200.
  - M4: a restore whose content is current in an unverified legacy document names itself in the 409.
  - T4: `create_document` 409 may carry `document: null` if the holder vanishes mid-race; the note is stored in `version_restored` details; the `_same_bytes` docstring order; `version_deleted` uses the current name.
  - T3: `delete_project` DISTINCT unguarded.
  - The `delete_user` variant of the Task 6 lock cycle (legacy-only, pre-existing in A1; the 503 mapping is its safety net).
- **Frontend (M9):**
  - the ⋯ menu doesn't close on Escape or an outside click, and several can be open at once;
  - Library "Open current" isn't guarded against a double click;
  - a failed history load has no Retry;
  - the chip lacks `min-w-0`/`break-words` (the walk saw no chip overflow at 390 px);
  - stale file input after switching to a library pick;
  - `maxLength` silently truncates notes;
  - raw "Failed to fetch" toasts;
  - the dialog has no focus trap and isn't re-focused after a second conflict.
- **Docs (M11):** the spec §9 file name `test_doc_versions.py` is now `_read`/`_write`; the LIBRARY intro still says "before A0"; its migration list skips 013–017; the ARCHITECTURE parenthetical omits from-library.
- **Tests:** the missing-assertion lines from Tasks 1, 2, 3, 5, 6, 7, 8, 9, 10 and 14 (the final review checked each code path and found it correct). Also `useVersionStatus`'s fetch-fail test can't fail; fix it or delete it when next in the file.
- **Process:** P12 means `git bisect` across T7..T13 lands on broken UI; say so in the merge commit or squash.

**Parked by the final fix wave (ledger):**

- The poll cap isn't reset after a new action, so a row stuck in `stored` for more than 10 min stops later follow-up. Not hit in the walk.
- A failing `load()` keeps polling and re-alerting for up to about 10 min.
- Two requests per tick (`myDocs`).
- A poll vs. action reload race (it self-corrects).
- A stale lock-order comment at `docs.py:369-371`.
- The 503 docstring overclaims a full rollback for `upload_version`.
- The spec §1 line 12 wording "Search and chat see only the current version".

**From this walk:**

- Finding 1, the upload session freeze, needs a decision.
- Observations 3a–3j.
- The unverified `link.error`-on-failed-filing item.
- The final review's "declined to judge" #3: there is no Retry-indexing control on the Documents tab for a failed version. No index failed in the walk, so it is still untried.

## What the user still can't do

- **Chat across a project's documents** (C2.1). Chat still reads one document per turn, the open one; following "see design.pdf" to the next document is manual.
- **Compare two versions, or review a version before it becomes current** (V2). A new version is current the moment it's uploaded.
- **Rename a document without uploading a new version.** The name is always the current version's file name.
- **Un-merge.** A merge is one-way. Every file stays in the history, and *Keep both* can re-file one as its own document.
- **See the whole project as of a date** (snapshots).
- **Limit storage:** there are no quotas or version-retention limits. Every version keeps its content and index until someone deletes it.
- **Spot same-named documents at a glance:** the "same name as another document" marker is deferred. Since this walk, the merge picker does tell them apart (finding 2).
- **Follow backup guidance:** `docs/DEPLOYMENT.md` has none yet. The CHANGELOG says to take a `pg_dump` before upgrading, because 018 changes a primary key and has no down step.
- **Keep working while a big upload indexes** (finding 1). Until that is fixed, the uploader's other requests wait for the index to finish.
- **Version documents in a personal library.** Not planned: history exists only inside projects (user decision, 2026-10-11).
