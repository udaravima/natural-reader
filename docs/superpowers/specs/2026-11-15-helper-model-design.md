# The helper model: tags, descriptions, chat titles, and a profile generator

Status: draft for the owner's approval, 2026-11-15. The shape was agreed in conversation ("yes to all three").

## 1. Intent

Three small writing jobs are done by hand or not at all today:

- A document has no tags of its own and no description. Tags exist only on a person's own library entry
  (`library_entries.tags`); a file bulk-uploaded into a project gets no library entry, so it has nowhere to hold
  a tag.
- A chat's title is the first 60 characters of the first message, cut by the browser (`sessionStore.js`).
- The assistant profile (Admin, up to 8,000 characters) is typed by hand.

The owner wants one small model, named once by the admin, to do all three: about five tags and a description
for each indexed document (with a **Generate** button, and people able to set their own), a chat title written
from the first exchange, and a profile drafted from "what kind of person it has to be".

## 2. Goals, non-goals, anti-goals

Goals

- One setting, **Helper model**, in Admin → Models. Unset means off: nothing generates, no Generate button shows.
- Tags and a description on every indexed document, visible wherever the document is listed.
- Generation never slows indexing or a chat reply.
- A person's own words always win over the model's.

Non-goals

- A fixed, admin-managed tag vocabulary. The model is shown the tags already in use and told to reuse them.
- Titles for chats kept only in the browser (the legacy IndexedDB sessions).
- Backfilling tags for every existing document at upgrade. A project gets a button for it (5.6).

Anti-goals (a failure even if it works)

- Text leaving the machine without the admin having chosen that. See 4.3.
- One project's hand-typed tag or description showing in another project that holds the same file.
- A generated title replacing one a person typed.
- Document text steering the model into writing instructions that the chat later obeys.

## 3. Approaches

**Where generated tags and descriptions live**

1. On each holding: a project's version of a document, or a person's library entry; generated for that holding
   with that holding's tags in use as the vocabulary; people's edits on the project document (chosen, after the
   build: see below).
2. On the content (`documents`), generated once per unique file; people's edits where they have the right to edit
   (the first design, built and then replaced). Rejected after the build's review: the model is shown one
   project's tags in use, hand-typed ones included, and copies some into its answer; stored on the content, that
   answer shows in every other project and library holding the same file. A project's confidential code name
   could reach another project's members. That is the second anti-goal.
3. Only on the content, editable there. Rejected: an edit in one project would show in another.
4. Copied into each holder's personal tags. Rejected: project documents have no holder entry, so the bulk-upload
   journey ends with no tags.

The cost of 1: a file held by two projects is described twice, one call each. Usually a file is held once.

**When generation runs**

1. A queue of its own, worked only while no indexing job waits (chosen). Indexing always goes first.
2. Inside the indexing job. Rejected: it makes every document's indexing as slow as the model.
3. Only on the button. Rejected: fifty uploaded files would need fifty presses.

**Which model**

1. A new Helper model setting, off by default (chosen).
2. Reuse the Reader setting. Rejected: its default is "the chat's own model", and a background job has no chat.

## 4. The helper model

### 4.1 Setting

- `helper` in the `models` settings section; env `HELPER_MODEL`; default empty (off). The value is a model id
  like every other (`provider:name`, or a bare name for the first Ollama provider).
- Admin → Models shows **Helper model** with the same picker as Reader, plus **Off**. The admin's choice wins
  over the env value, as for Reader.
- `HELPER_TIMEOUT_S` (default 120 seconds) per call. `HELPER_INPUT_CHARS` (default 6000 characters, range
  500-50000) of source text per call.

### 4.2 One service

`server/services/helper.py` is the only caller. One function runs a single model call with a fixed brief, no
tools, a low temperature and a capped reply, and returns text or a typed failure (off, timeout, over budget,
bad reply). Every job below uses it. Logs carry lengths and error kinds, never text.

Everything a document or a person wrote goes into the call fenced as data (`chat/prompt.py`'s `fence`), and the
brief says to treat it as text to describe, never as instructions.

### 4.3 Privacy and cost

- The Models page states, beside the setting, whether the chosen model is local or cloud (the existing
  `provider_locality` judgement), and for a cloud model: "Document text, chat messages and profile descriptions
  are sent to this provider."
- Chat titles: when the helper is a cloud model and the conversation's own model is local, no title is
  generated. A person who chose a local model for a chat must not have its text sent to a cloud by a side job.
- Usage is recorded like any model call (`inference_budget.record_usage`), against the person who caused it:
  the uploader or the person who pressed Generate, the chat's owner, the admin. A person over their daily
  budget gets no automatic generation, and a pressed Generate answers with the budget sentence.
- Before release, the real outgoing request is logged once and read (the project's standing rule for privacy
  promises).

## 5. Tags and descriptions

### 5.1 Data (migration 028)

- `gen_tags TEXT[]`, `gen_description TEXT`, `gen_model TEXT`, `gen_at TIMESTAMPTZ` on `project_documents` (one
  project's version of a document) and on `library_entries` (one person's library entry). Written only by the
  helper, for that holding alone. NULL `gen_at` = never generated. Nothing is stored on the content: what the
  helper wrote for one holding never shows in another.
- `project_docs.tags TEXT[]`, `project_docs.description TEXT`. NULL = "show the generated ones of the current
  version". Set by project Contributors and above.
- Personal library tags stay as they are. No personal description.
- `doc_describe_jobs`: one row per holding waiting: the content to read (`doc_id`), and either the project
  document (`project_doc_id`; the version holding that content) or the person (`user_id`; their entry for it).
  Also `requested_by`, `attempts`, claim columns like `doc_index_jobs`. A job goes with its project document or
  library entry.

### 5.2 What the model is given and returns

Given: the file name, the outline's headings if there is one, a sample of the document's indexed text, and up to 50 of the most used tags where the request came from (the project's, or the
person's own for a library upload).

The model never reads the whole document. The sample is at most `HELPER_INPUT_CHARS` characters (default 6000,
about 1,500 tokens) whatever the document's size: the opening (half the allowance), then passages taken at even
steps through the rest, each cut at a passage boundary. The text comes from the passages indexing already
stored, so nothing is extracted or embedded again, and a 5-page and a 500-page document cost the same one call.

Returns JSON: `{"tags": [...], "description": "..."}`. The app, not the model, enforces: at most 5 tags, each
lower-cased, trimmed, 1-32 characters, no control characters, no duplicates; the description one line, at most
300 characters. A reply that doesn't parse counts as a failed attempt.

The result belongs to the holding it was written for, as does its vocabulary: a project's tags in use for a
project document, the person's own library tags for their entry. A second project holding the same file gets its
own call, with its own tags in use.

### 5.3 When it runs

- When a file is placed and the helper is on, a describe job is queued for the new holding: a new project
  document or version (upload, bulk upload, filing from the Library, a new version from the Library), or a Library
  upload. The job waits until the content is indexed, so it is queued whether or not indexing has finished (an
  already-indexed file is described without indexing again). Restoring an older version in the same project
  reuses what the helper wrote for that version there, with no call. A Library entry that came by sharing gets its
  tags from its row's **Generate**.
- The bulk upload panel has a tick, **Write tags and descriptions**, shown when the helper is on and ticked by
  default. Unticked, the files of that batch are indexed without a describe job (the upload route takes
  `describe=false`); they can still get tags later from the row's **Generate** or the project's button (5.6).
- A worker claims a describe job only when no indexing or conversion job is queued or running, one at a time.
- Two attempts. A failure leaves the document without tags and logs the kind; it is not a document error and
  shows no red state.
- Measured on the build walk (2026-11-15, CPU-only Ollama, `gemma4:e4b`, context 16,384): 28 to 33 s per document,
  the same for a 31 KB and a 121 KB file, since the model reads a fixed-size sample.

### 5.4 What people see

Project → Documents, each row, under the name:

    Annual report 2025.pdf            v3 · Indexed
    Yearly results and outlook for the hardware division.
    [finance] [annual report] [hardware]            ✎  Generate

- Readers see the description and tags. Contributors and above also get the pencil (edit both in place) and
  **Generate**.
- **Generate** re-runs the model for the current version and clears the project's own values, after a confirm
  when the project has hand-typed ones. While it waits or runs, the row says "Writing tags…".
- A tag chip filters the list to that tag; the filter box above the list matches names, tags and descriptions.

Library → Documents, each row:

- The description written for the person's entry under the name; else, for a document they see through a project,
  that project's (the first by name when several hold it). Only projects the person is a member of are read.
- The person's own tags as today. Generated tags the person doesn't have show as lighter chips; one tap keeps
  one as their own tag. Search matches both, and the description shown.
- **Generate** for a row the person holds, when it has never been generated for their entry.

### 5.5 Versions

Each version of a project document gets its own generated values. A project document with no hand-typed values
follows its current version. Hand-typed values stay across versions. A merge moves versions with their values,
and a waiting job with them.

### 5.6 Existing documents

The project's Documents tab shows, to Contributors, "Write tags for N documents" when indexed documents there
have none. It queues them all. Nothing is queued by the upgrade itself.

### 5.7 The project chat

The project map (`chat/project_map.py`) adds a document's description to its line, through `display_name` like
every other document-derived string. When the map is over budget, descriptions stay while the outlines are cut
(level 3, then each outline's first 8 entries, then level 2) and go just before the last step, names and pages
only. (Changed during the build: the first rule, "descriptions are dropped first", was walked with eight real
documents on the default 6,000-character budget, and it kept every outline whole and no description at all, the
very case descriptions are for.)
Only generated or Contributor-typed text reaches it, and the map's data rule already tells the model that the
block is data.

### 5.8 API

- `GET` project documents and library documents gain `tags`, `description`, `generated` (bool), `describing`.
- `PATCH /v1/projects/{id}/documents/{document_id}`: `tags`, `description` (Contributor). `null` returns to
  the generated ones.
- `POST /v1/projects/{id}/documents/{document_id}/describe` and `POST /v1/docs/{doc_id}/describe`: queue a
  job. 409 when the helper is off, 429 with the budget sentence when over budget.
- `POST /v1/projects/{id}/documents/describe-missing`: queue every indexed document without generated values.

## 6. Chat titles

- `chat_sessions.title_source`: `default`, `cut`, `model` or `person` (migration 028; existing rows with a
  title other than "New chat" become `person`, so nothing already named is ever replaced).
- The browser keeps writing the 60-character cut at the first message and marks it `cut`. A rename marks
  `person`.
- When the first reply of a conversation finishes, and the helper is on, and 4.3 allows it, the server asks for
  a title from the first message and the first reply (each cut to 2,000 characters), off the reply's path. The
  result (one line, at most 60 characters, no quotes) is written only if the source is still `default` or `cut`.
- The sidebar shows the new title without a reload.
- The chat's menu gains **Generate title**, which does the same on request from the conversation so far and
  marks it `model`.
- On any failure the cut title stays. Nothing is shown.

Built as planned in `docs/superpowers/plans/2026-11-16-chat-titles.md`, whose top lists the departures: migration 029 (not 028); the server writes the cut title; Generate title sits beside Rename (there is no chat menu); a `title_pending_at` mark and a 4-second re-read show the new title without a reload; a reply held from other models (real values) is left out of what the helper reads.

## 7. The profile generator

- Admin → Assistant profile gains a box, "Describe the assistant", and **Generate**.
- `POST /v1/admin/assistant-profile/draft` with `{description}` (at most 2,000 characters) returns `{text}`.
  Admins only.
- The brief asks for a profile in the second person covering name, personality, tone and house rules; tells
  the model to name no tools (the app describes tools itself) and to stay under the 8,000-character limit.
- The draft is placed in the editor, after a confirm when the editor holds unsaved text. Nothing is saved until
  the admin presses Save, and the existing checks on a saved profile apply unchanged.
- With the helper off, the button is replaced by one line: "Choose a helper model in Models to generate this."

## 8. Order of work

Each phase ships on its own.

1. Helper setting, the service, tags and descriptions (5).
2. Chat titles (6).
3. The profile generator (7).

Until phase 1 ships, none of this is visible. Until a deployer names a helper model, none of it runs.

## 9. Left to the builder

File layout beyond `services/helper.py`; the exact briefs (pinned by tests, like the reader's); the worker's
polling; screen markup. The plan lists every departure from this document at its top.

## 10. Calls made by the author, for the owner to check

- No fixed vocabulary; reuse is by showing the model the tags in use (2).
- Generated values live on each holding (a project's version, a person's library entry); edits live on the project
  document (3). Changed after the build's review, by the owner's choice: they first lived on the content, which let
  one project's vocabulary reach another holding the same file.
- A project-only Library row shows the description of the first project (by name) the person is a member of that
  holds the file; a Library entry that came by sharing gets tags only from its row's Generate (5.3, 5.4).
- A cloud helper never titles a chat that runs on a local model (4.3).
- Two attempts, then silence, for a failed generation (5.3).
- A bulk upload writes tags unless its tick is cleared; the model reads a fixed-size sample, never the whole
  document (5.2, 5.3).
- Existing documents get tags only when a Contributor presses the project's button (5.6).
- Descriptions feed the project chat's document list (5.7).
- Titles already set at upgrade count as typed by a person (6).
