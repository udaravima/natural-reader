# Uploading many files to a project: design

Date: 2026-11-14, revised 2026-11-15. Status: revision 3, approved by the owner ("go ahead", then "check and
continue" on a review of revision 2). Phase 1 (the queue) built and walked
2026-11-15 on `feat/index-queue`; phase 2 not started.

Revision 1 kept the server almost unchanged and listed what that left out.
The owner asked for the design that is right for everyone who uses the app:
comfortable for someone who just drops a folder, and complete for a technical
user or a script. Revision 2 closes the gaps revision 1 only named.

Revision 3 folds in a review of revision 2 that was checked against the code:
a file that crashes a worker must not crash it for ever; Docling conversion
joins the queue; start-up recovery must not reset another worker's live job;
documents stuck before the upgrade are queued; and two documents with the same
name can't be told apart by name alone.

## Why

A project holds 10 to 50 documents. Today each one takes two screens: open the
file in the reader with **Choose File** (that uploads and indexes it), then go
to the project's **Documents** tab and pick it from **File a document…**. The
Documents tab has no upload control of its own. Filling a project, or bringing
it up to date after a round of edits, is that journey repeated per file.

The owner wants to pick many files at once on the project's page and have them
uploaded, filed and indexed. A file whose name matches a document already in
the project becomes **a new version of that document** (the owner's decision,
2026-11-14), so uploading the same folder again updates the project.

## Who it is for

| Person | What they need |
|---|---|
| Someone filling a project | Drop a folder, press one button, see it finish. No questions per file |
| Someone keeping a project up to date | Drop the same folder again; only what changed is sent, and they see what will change **before** it does |
| A technical user | Decide per file (new version, separate document, skip); know why a file was refused; trust that a restart loses nothing |
| A script or a hobbyist's cron job | One request per file with a personal access token, the same rules as the screen, answers a program can read |

## Goals

- **One place on the project's Documents tab** takes many files or a whole
  folder: two buttons, and dropping files onto the tab.
- **A plan before anything is sent.** The person sees what each file will
  become, and can change it per file. The defaults need no touching.
- **A matching name is a new version,** by default, without a question per
  file.
- **Unchanged files are never sent.** Uploading the same set again costs a
  hash per file, not an upload.
- **Indexing survives a restart** and never runs more than the machine can
  take. This holds for every upload, not only bulk ones.
- **The same thing from a script:** one documented route, one request per
  file.
- **The person sees what happened to every file,** and can tell a document
  that is waiting its turn from one that is stuck.

## Non-goals

- **A zip upload, or one request carrying the whole batch.** See
  "Approaches".
- **Folders as structure.** A folder is a way to pick files. Subfolder names
  are not kept: a project stays a flat list of documents. (Paths inside names
  would touch citations, downloads and name matching everywhere.)
- **Bulk upload into a personal library.** The Library page is not changed.
- **Converting with Docling as part of the batch.** Conversion stays a
  per-document choice.
- **Removing documents that are no longer in the folder.** A bulk upload only
  adds and updates. A mirror that deletes is a different, riskier feature.
- **Continuing after the tab is closed.** A browser can't. What was sent is
  safe, and opening the tab and dropping the folder again finishes the rest.

## Anti-goals (failure even if it works)

- A batch makes chat, search or another person's upload stall.
- A file becomes a version of the wrong document and the person wasn't shown
  it beforehand.
- The list says "done" for a file that was refused, or hides a refusal.
- The person has to answer a question per file to get the default.
- A restart, a closed tab or a second worker leaves documents that never get
  indexed and say nothing.
- The screen and the script follow different rules.
- A person's library fills with files they only meant to put in a project.

## Decisions

**The owner's:**

1. A file whose name matches a document in the project becomes that document's
   new version.
2. Design it for comfort and for technical users alike; don't leave known
   traps for later (2026-11-14).

**My calls (the owner can overturn any):**

- **Who:** anyone who can file today (Contributor and above). No new
  permission. A personal access token needs the `documents` scope.
- **Straight into the project.** A bulk-uploaded file is held by the project,
  not added to the uploader's library, the way **Upload new version** already
  works. Someone who wants it in their library can open it from the project.
- **A file identical to the current version** is "unchanged" and not sent.
- **A file identical to an *older* version** is left alone by default and
  shown as such, with **Restore** as that file's other choice. Restoring
  silently would undo someone's newer version because of a stale file.
- **Two files with the same name in one batch:** the first follows the normal
  rule; the later ones default to **Skip**, with **Separate document** as the
  other choice. Otherwise the second would become version 2 of the first.
- **Limits are settings:** `BULK_UPLOAD_MAX_FILES` (default 200) per batch;
  the existing size caps per file.
- **Indexing runs one document at a time per worker,** from a queue in the
  database. More workers (`WORKERS`) means more at a time.
- **Two phases, each shippable:** the indexing queue first, then bulk upload
  on top of it.
- **Docling conversion goes on the queue too,** in a lane of its own (revision
  3). Revision 2 left it as a background task. That kept the trap open (two
  workers writing one document's passages) and left the heaviest job in the
  app unlimited.
- **A job is tried at most 3 times.** After that the document is marked failed
  and whoever asked is told.
- **Two documents with the same name are never guessed between.** That row
  defaults to Skip and asks which one.

## Approaches

1. **Revision 1: the browser loops over the two existing routes** (upload to
   my library, then file into the project), plus an in-memory limit on
   indexing.
   - For: the smallest change.
   - Against: every file lands in the uploader's library; a waiting document
     is forgotten on a restart; unchanged files are uploaded just to learn
     they are unchanged; a script has to chain two requests and handle the
     "name exists" answer itself; no preview. These are the gaps the owner
     asked not to leave.
2. **A plan route, an upload route, and a durable indexing queue** (chosen).
   The browser hashes the files and asks the server for a plan; then sends
   only what the plan says, one request per file, to a route that stores the
   file and files it in one step. Indexing goes through a queue in the
   database that every worker drains.
   - For: the server is still the only judge of every rule, and the screen
     and a script use the same route. Nothing is sent or stored twice. The
     queue also fixes today's single-upload weakness (an interrupted index
     waits for someone to notice).
   - Against: more to build: one migration, two routes, a queue. The pieces
     it stands on exist, though: the rebuild queue's claim rules
     (`server/services/rebuild_queue.py`) and the "store the content and add
     a holding" step that **Upload new version** uses
     (`docs_router._store_and_hold`).
3. **One request with every file, or a zip, run as a server job.**
   - For: survives a closed tab.
   - Against: a multi-gigabyte request against a 50 MB per-file cap; a zip
     brings path and size attacks; one bad file can sink the request; progress
     per file needs a job table anyway. Approach 2 gets the same safety (what
     was sent is safe, the rest resumes) without it.

## Phase 1: the indexing queue

### Today

Every upload starts its own background job the moment it arrives
(`background.add_task` in `server/routers/docs.py`). Nothing limits how many
run together, so thirty uploads mean thirty PDF extractions at once. The job
lives in the worker's memory: a restart forgets it, and the document stays in
the state `stored` until someone presses **Retry index** in the reader.

### After

- **A table of indexing jobs** (migration 027, with a runnable Down in its
  header as 023 and 025 have): the document, who asked (for the notice), when
  it was queued, and the claim (`claimed_by`, `claimed_at`).
- **Every start of the pipeline enqueues** instead of running in the request's
  background: a new upload, **Index**, **Retry index**, re-extract, a new
  version, and **Convert**. A document has at most one job (the document is
  the table's key), so an indexing job and a conversion of the same document
  can't both exist.
- **Convert and a waiting index job.** Pressing **Convert** on a document
  whose indexing job is still queued replaces that job (a conversion ends by
  indexing). If the job is already running, Convert is refused with "This
  document is being indexed. Try again when it has finished." (409).
- **Each worker's existing runner** (`run_queue_forever`, which already drains
  rebuilds one at a time) also claims indexing jobs, with the rebuild queue's
  rules: `FOR UPDATE SKIP LOCKED`, a heartbeat, and a claim not heard from in
  10 minutes is taken over. One document at a time per worker, across rebuilds
  and indexing, because the embedding model is shared.
- **Rebuilds go first, but not for ever.** Someone is waiting on a rebuild in
  a chat, so it is claimed before an indexing job. After 3 rebuilds in a row
  the runner takes one indexing job if any waits, so a long re-embed can't
  starve uploads.
- **Conversions run in a second lane:** one at a time per worker, beside the
  indexing lane, so a 20-minute Docling run doesn't hold up every upload
  behind it, and five conversions no longer mean five Docling runs at once.
- **A job that kills its worker stops after 3 tries.** A damaged PDF that
  crashes the PDF library, or a scan that runs the machine out of memory,
  takes the worker down holding the claim. Ten minutes later another worker
  would take it over and die the same way, and with one worker the whole
  backend restarts each time. So a claim that would be a job's 4th is not
  run: the document is marked failed ("The server stopped while reading this
  file, 3 times. The file may be damaged."), whoever asked is told, and the
  job is removed. The rebuild queue gets the same cap, counted the same way:
  a rebuild whose worker died while running it 3 times stops being retried
  until a newer profile asks for it, and the admins are told; the old index
  keeps answering. (Built so, 2026-11-15: an earlier draft said "after 5
  claims", which would also have parked a rebuild that merely failed 5 times
  while the embedding server was down, and those retry by themselves.)
- **A job ends once.** Success removes it. A failure records the reason on the
  document (`state = 'failed'`, as today), tells the person who asked (as
  today), and removes the job: no automatic retry, because most failures are
  the file's own. **Retry index** enqueues it again. Only a worker that died
  mid-job is retried by itself, through the lapsed claim.
- **The document lists say when a document is queued** (a `queued` flag beside
  `state`, and how many are ahead), so the screen can say "Waiting to index
  (3 ahead)" and tell it from a document nothing will index. "Ahead" counts
  the jobs before it in its lane, plus the rebuilds waiting, since those go
  first.
- **Every job records who asked,** new versions included, so a failure always
  reaches someone. (Today a new version's indexing tells nobody.)
- **Start-up recovery respects live claims.** Today every worker, when it
  starts, resets every `extracting` document to `stored`, every `indexing`
  one to `extracted`, and stamps every `converting` one "Interrupted by server
  restart" (`server/db.py`, `doc_content.recover_states`). uvicorn restarts a
  dead worker by itself, so with two workers one worker's crash resets its
  sibling's live jobs. Recovery now touches only documents with no live claim
  (no job, or a claim older than the lapse).
- **Documents stuck before this release are queued.** On every start, after
  recovery, any document in `stored`, `extracting`, `extracted` or `indexing`
  that has no job gets one. Without that, "a restart loses nothing" would be
  false on the day this ships. It runs on every start because it is cheap and
  heals any job lost some other way.
- A single upload behaves as before from the person's side: the runner is
  woken at once, so it starts immediately when nothing else is running.

### A known trap this removes

`server/services/doc_pipeline.py` and the conversion job guard a document with
a lock that lives in one process. With several workers, two could run the
same document: two pipelines, or a conversion on one worker and the pipeline
on another, both writing the same passages. One job per document, claimed in
the database, is the guard that holds across workers. That is why conversion
has to be on the queue as well: a guard that covers only indexing leaves the
conversion case open.

## Phase 2: bulk upload

### What the person does and sees

On a project's **Documents** tab, for a Contributor or above:

```
[ File a document… v ]  [ Upload files ]  [ Upload folder ]
        …or drop files or a folder anywhere on this tab
```

**1. Pick.** Files (multiple selection), a folder (with its subfolders), or a
drop.

**2. The plan.** The browser hashes the files (a progress line for a large
set), asks the server, and shows:

```
Upload to "Network handbook"                       18 files · 41 MB to send

  intro.md            New document
  setup.pdf           New version (v3) of setup.pdf            [ v ]
  glossary.md         Unchanged: not sent
  old-setup.pdf       Same as v2 of setup.pdf: left alone      [ v ]
  b/README.md         Another file here is named README.md: skipped   [ v ]
  huge-scan.pdf       Over the 50 MB limit: can't be sent
  notes.docx          Not a PDF, Markdown or text file: can't be sent

  9 new · 3 new versions · 2 unchanged · 1 left alone · 1 skipped · 2 can't be sent

                                                    [ Cancel ]  [ Upload 12 ]
```

- The defaults follow the rules above; pressing **Upload** without touching
  anything is the comfortable path.
- `[ v ]` is a small menu on rows that have a real choice:
  - a name match: **New version of *name*** (default), **Separate document**,
    **Skip**;
  - an older version: **Leave alone** (default), **Restore as current**;
  - a repeated name: **Skip** (default), **Separate document**.
- **Two documents here share the name.** The server can't know which one a
  file belongs to, so it doesn't guess: the row reads "2 documents here are
  named README.md: skipped", and its menu lists each one with what tells them
  apart (its version, who added it, when), plus **Separate document**. This is
  the one question the screen asks, and only when names really are ambiguous.
- Rows that can't be sent say why and have no menu. Files of a type that is
  never accepted are gathered into one line ("23 files aren't PDF, Markdown
  or text: left out") and don't count toward the limit.
- On a phone the row stacks: name, then what it becomes, then the menu.

**3. Sending.** The same list, now with each row's state, two files at a
time:

```
Uploading · 7 of 12                                              [ Stop ]
✓ intro.md            New document
✓ setup.pdf           New version (v3)
… routing.pdf         Sending (64%)
  5 waiting
```

**Stop** ends after the files in flight. A file that fails keeps the server's
reason and a **Try again** on its row; the rest carry on.

**4. Afterwards.** One line stays above the document list until dismissed:
"12 sent: 9 new, 3 new versions. 1 failed." with **Show details**. The
documents appear in the list as they are filed, each with its state in plain
words: **Waiting to index (3 ahead)**, **Reading**, **Indexing**, **Indexed**,
**Failed** (the reason on the badge). A waiting or failed row has **Retry
index**. Today the row shows the raw word (`stored`) and has no retry.

### The two routes

Both need a Contributor role in the project (admins only through a member
role, as for filing today), and accept a session or a token with the
`documents` scope.

**`POST /v1/projects/{id}/uploads/plan`**: what would happen. Nothing is
stored.

```json
{ "files": [ { "name": "setup.pdf", "size": 482113, "sha256": "…", "head": "JVBERi0=" } ] }
```

`sha256` is optional (see "Plain HTTP" below). `head` is the file's first 5
bytes in base64: the server treats every name that isn't `.md`, `.markdown`
or `.txt` as a PDF and accepts it only if it starts with `%PDF-`
(`doc_storage.type_from_name`, `_check_type`), so without the first bytes the
plan would call `notes.docx` a new document and the send would refuse it.

Answers, per file, in the order given:

| `action` | Meaning | Also returns |
|---|---|---|
| `new` | No document here has this name or content | |
| `version` | Exactly one document here has this name | the document, the version it would become |
| `ambiguous` | Several documents here have this name | each of them: id, current version, who added it, when |
| `unchanged` | This exact content is a document's current version here | the document |
| `older_version` | This exact content is an older version here | the document, that version |
| `refused` | Can't be sent | `code` and a sentence: too large (50 MB for a PDF, 10 MB for text and Markdown, or the deployment's own caps), not a supported type, empty |

- The content match is made **only inside this project**. The route never
  says whether the server holds a file somewhere else.
- **A repeated name inside one batch is the client's to handle,** on the
  screen and in a script alike: the route judges each file against the
  project only, and it can't see a batch. The script documentation says so
  and shows the two safe ways (ask for the plan first, or pass
  `document_id`).
- Names compare the way the server already compares them: letter case
  ignored, surrounding spaces trimmed.
- **Disk space.** The route adds up the sizes it would accept and refuses the
  whole plan (507, with a sentence naming both numbers) when the storage
  folder's free space is less than that plus a reserve
  (`UPLOAD_MIN_FREE_MB`, default 1024).
- More than `BULK_UPLOAD_MAX_FILES` files is refused whole (413), with the
  limit in the message.
- The hash is a claim, used only to plan. The upload route hashes the bytes
  itself, as every upload does.

**`POST /v1/projects/{id}/uploads`**: one file (multipart), stored and filed
in one step.

| Field | Values | Default |
|---|---|---|
| `file` | the file | |
| `on_match` | `version`, `separate`, `skip`: what to do when a document here has this name | `version` |
| `on_older` | `leave`, `restore`: what to do when the content is an older version here | `leave` |
| `document_id` | the document this file is a version of, when the caller already knows (the plan's answer, or the person's choice between same-named documents). Overrides the name match, as `as_version_of` does for filing today | none |
| `note` | up to 500 characters, kept on a new version | none |

Answers `201` when something was filed and `200` when nothing changed, with
`result` (`created`, `version`, `restored`, `unchanged`, `skipped`,
`left_older`, `ambiguous`), the document, its version, and the content's indexing state.
Refusals use the upload's existing codes (`too_large`, `unsupported_type`,
`empty_file`).

- It uses the same steps as **Upload new version**: stage and hash the file,
  decide, then store the content with the project document as its holding
  (`_store_and_hold`), the document locked first. No library entry is added.
- When the answer is `unchanged`, `skipped`, `left_older` or `ambiguous`,
  nothing is stored. The decision is made after the bytes are hashed and
  before the content is written.
- **One decision function for both routes.** The plan and the upload decide
  from the same code, in the order `file_from_library` already uses (current
  here, an explicit document, an older version, a name match, new). A test
  runs every case through both and compares the answers, so the plan can't
  promise what the upload won't do.
- Without `document_id`, several documents with the name answer `ambiguous`
  and nothing is filed, whatever `on_match` says. A script sees that in the
  answer and can pass `document_id`.
- The uploader is recorded on the indexing job, for a new document and a new
  version alike.
- Each filing writes the project's activity entry, as filing does today.
- New content is queued for indexing (phase 1).

**From a script**, the whole feature is one loop, and uploading again is safe:

```bash
shopt -s nullglob              # a pattern that matches nothing is dropped, not sent as a file name
for f in ./handbook/*.pdf ./handbook/*.md; do
  curl -sS --fail-with-body -H "Authorization: Bearer $NR_TOKEN" -F "file=@$f" \
    "https://reader.example.com/v1/projects/$PROJECT_ID/uploads" || echo "refused: $f" >&2
done
```

`--fail-with-body` makes a refusal (a 413, a 409) a non-zero exit with the
server's reason printed; without it a cron job never notices. The loop is
right for a folder whose file names are all different. For a tree where two
files share a name, the script has to say which document each belongs to
(`document_id`), exactly as the screen asks the person.

### What the browser does

A new hook owns the batch; the tab renders it.

- **Collect:** files from the picker, the folder picker, or a drop (a dropped
  folder is walked with its subfolders). A file is left out without a row when
  **any part of its path** starts with a dot or is `node_modules`: a folder
  under git holds thousands of files in `.git/`, none of whose names start
  with a dot, and they would trip the limit and refuse the whole plan. Files
  are then **sorted by path**, so "the first file with a name" is the same in
  every browser.
- **Hash** each file (SHA-256, off the main thread for large files; the
  existing `src/utils/docHash.js` is the starting point) and keep its first 5
  bytes, then one plan request.
- **Plain HTTP.** The browser's hashing (`crypto.subtle`) exists only on
  HTTPS and on localhost, and a deployment on plain HTTP is supported
  (`COOKIE_SECURE` can be off). There the hash is left out: the plan matches
  by name only and says so in one line ("Unchanged files can't be detected
  before sending on this connection"), every matching file is sent, and the
  upload route still answers `unchanged` and stores nothing.
- **Repeated names** (compared as the server compares them) are marked from
  the plan's point of view: the first file by path keeps the server's answer,
  later ones default to Skip.
- **A file created as a Separate document** is sent with nothing to tie it to
  a later upload. The next time, its name is ambiguous and the row asks which
  document it is: that is the price of two documents sharing a name, and the
  screen says so once when Separate document is chosen.
- **Send** only rows whose action sends something, two at a time, each with
  its `on_match` and `on_older`. A row's result is the server's answer.
- **A 401, or the person losing their role mid-batch,** stops the batch once
  and says why.
- **The document list** is re-read as files land, at most once a second, and
  once at the end.
- **The watch on indexing** must outlast a batch. Today the tab re-reads every
  3 seconds and gives up after about 10 minutes, and a batch on a machine with
  no GPU takes longer (about 2.5 minutes per 1,000 passages were measured for
  embedding here). Keep reading while anything is queued or running, slowing
  to once every 15 seconds after the first 10 minutes; stop when nothing is.

### What happens when…

| Case | Result |
|---|---|
| The same folder again, nothing changed | Every row "Unchanged". Nothing is sent |
| One file was edited | One "New version"; the rest unchanged |
| A file was reverted to older content | "Same as vN: left alone", with Restore as its other choice |
| Two different files both named `README.md` | The first (by path) follows the rule; the second is skipped unless the person picks Separate document |
| The project already has two documents named `README.md` | Neither file is matched by guess: each row asks which document, and defaults to Skip |
| A name matches by coincidence | The plan shows "New version (v2) of *name*" before anything happens; Separate document is one tap away |
| The project changes between the plan and the send (someone else files the same name) | The upload route decides again from the project as it is; the row shows what actually happened |
| The tab is closed mid-batch | What was sent is filed and will be indexed. Dropping the folder again shows the rest as new and the sent ones as unchanged |
| The backend restarts mid-batch | The files in flight fail and can be tried again. Queued documents are indexed after the restart, by themselves |
| The embedding server is down | Each document fails with that reason and a notice; **Retry index** when it is back |
| Two people upload the same new name at once | Both may create a document with that name (the name match is advisory today, `docs/LIBRARY.md`). A Maintainer merges them |
| A Reader opens the tab | No buttons, no drop target |
| The person is removed from the project mid-batch | The batch stops once, saying so |

### Notifications

Each document already tells whoever asked how its indexing ended, and the bell
groups notices of one kind within 30 minutes, so a batch is one group. The
queue keeps that: the job records who asked.

## Testing and proof

**Automated, phase 1:**

- Enqueue is idempotent; two workers never claim one job; a lapsed claim is
  taken over and a late finish from the dead worker is ignored; a failure
  ends the job, sets the document failed with its reason and sends one
  notice; success removes the job; a rebuild is claimed before an indexing
  job; a restart (a new runner on the same database) finishes what was
  queued.
- Every way the pipeline starts goes through the queue (a guard test over the
  routers, like the one that walks every router for `get_conn`'s scope).
- The lists report `queued` and the number ahead, rebuilds included.
- A claim that would be a job's 4th marks the document failed, tells whoever
  asked, and removes the job; a rebuild's 6th claim parks it and tells the
  admins.
- After 3 rebuilds in a row, a waiting indexing job is taken.
- Conversion: one at a time per worker; Convert replaces a queued indexing
  job and is refused while one runs; a conversion and an indexing job for one
  document can't both exist.
- Start-up recovery leaves a document with a live claim alone, resets one
  whose claim lapsed, and queues a stuck document that has no job.
- A new version's failure reaches its uploader.
- Migration 027 up and down on a scratch database.

**Automated, phase 2:**

- The plan route: each action, `ambiguous` with every candidate; a `.docx`
  and a text file with a PDF's name are refused from their first bytes; the
  10 MB cap for text; no hash given; not enough disk; the match never reaches outside the project
  (a file another project holds reads `new`); over the limit; a Reader and a
  non-member are refused; a `documents`-scoped token works and a `chat`-scoped
  one doesn't.
- The plan and the upload agree: every case through the shared decision, both
  ways.
- The upload route: each `result`; `document_id` overrides the name match and
  is refused for a document of another project; `ambiguous` files nothing;
  the uploader is on the job for a new version; nothing stored for `unchanged`, `skipped`,
  `left_older`; no library entry is added; the activity entry; two uploads of
  one name at once become consecutive versions; the hash is the server's,
  whatever the plan claimed.
- The hook, with a faked API: the defaults; each per-file choice reaches the
  request; repeated names; two in flight at most; Stop; Try again; a lost
  role stops the batch once.
- The tab: buttons and drop target only for someone who can file; the plan's
  rows and counts; states in plain words; **Retry index** on a waiting or
  failed row; the watch continuing past 10 minutes; 360 px.

**Walked in the running app** (scratch database, headless Chromium, the local
embedding model, then two workers):

1. A folder of about 20 mixed files (PDF, Markdown, text, a `.docx`, one over
   the cap, two `README.md` in different subfolders) dropped on an empty
   project: the plan is right, **Upload** needs no other input, every row
   ends right, and every document reaches **Indexed**.
2. The same folder again: all unchanged, nothing sent (checked in the network
   log).
3. Edit two files, revert one: two new versions; the reverted one left alone,
   then restored from its menu.
4. Restart the backend while documents wait: they finish by themselves.
5. With `WORKERS=2`: no document is indexed twice (the log shows one claim
   each).
6. While a batch indexes, a chat question in another tab answers without
   stalling.
7. Close the tab mid-send, reopen, drop again: the project ends complete.
8. The script loop above with a `documents` token: the same results, and a
   second run changes nothing.
9. As a Reader: nothing to press, nothing to drop on.

## Docs to change

- `docs/USER_GUIDE.md`, "Projects and people": uploading many files, the
  plan, each choice, what each state means.
- `docs/LIBRARY.md`: the two routes, the queue, the script loop.
- `docs/ARCHITECTURE.md`: the indexing queue beside the rebuild queue.
- `docs/DEPLOYMENT.md` and `.env.example`: `BULK_UPLOAD_MAX_FILES`; that
  indexing is one document at a time per worker.
- `docs/TESTING.md`, `CHANGELOG.md`, release notes (migration 027: back up
  first).

## Left to the builder

- The table's and hook's names, and whether indexing jobs share the rebuild
  table or get their own (their own is cleaner: the end-of-job rules differ).
- How the lists compute "ahead" cheaply.
- How a dropped folder is walked in each browser, and how hashing stays off
  the main thread.
- The exact wording of each row.
- Whether the old route for filing from the library (`PUT
  …/docs/{doc_id}`) and its dialog stay as they are (they should: that
  journey is unchanged).

## Open risks

- **No quotas and no upload rate limits exist anywhere in the app.** The disk
  check stops one batch from filling the disk; it doesn't stop many. A
  per-person or per-project quota is its own piece of work.

- **Hashing a large folder in the browser** takes time (a 50 MB PDF is a
  second or two); the plan step needs its own progress line.
- **Moving every pipeline start onto the queue** touches the most-used path
  in the app. Phase 1 ships and is walked by itself before phase 2 builds on
  it.
- **Not measured:** how long a 50-document batch takes to index on a machine
  with no GPU. The walk records it, and the docs give the number.
