# v2.2.0 — Projects for teams, document versions, and a backend in a container

Projects are now **team spaces with roles**: Reader, Contributor, Maintainer
and Owner. You manage members, shares and activity in the app; before, this
was only possible through the API. A revised file becomes **a new version of
the same document** instead of a second copy. Every version stays in the
history and can be opened or restored, while chat and search see only the
current version. The backend can run **in a container**, which takes a fresh
clone to a running app with one command. Document chat finds more and makes
up less.

This release has **breaking API changes** and **five migrations**. One of them,
`018`, has no down step. **Take a `pg_dump` before upgrading**, and read
[Upgrade notes](#upgrade-notes) first.

## Added

### 👥 Projects for teams (A0)

- **Roles.** Each member is a **Reader** (reads the documents), a
  **Contributor** (also files their own documents), a **Maintainer** (also
  removes documents, renames the project and manages members up to
  Maintainer) or an **Owner** (everything, including other Owners and
  deleting the project). A project always keeps at least one Owner.
- **Library → Projects** lists your projects. Each project page has
  **Documents**, **Members** and **Activity** tabs.
- **Find people by name or email** to add to a project or share with.
  The deployment decides who can be found (`USER_DIRECTORY_MODE`:
  `exact` | `domain` | `open`). By default, people are found only by their
  full email address.
- **Share a document from the Library**, then see and stop existing shares.
- **Activity history:** who added or removed whom, role changes, renames, and
  documents filed, removed or versioned.
- **Admin → Projects** lists every project with its Owners. *Ownerless only*
  and *Add me as Owner* recover a project whose last Owner was deleted.
  Admins also get per-user project limits (`PROJECT_LIMIT_PER_USER`,
  default 20) and `PROJECT_CREATION` (`readers` | `admins`).

### 🗂️ Document versions in projects (V1)

- **Upload a revised file as a new version.** Use *Upload new version* in a
  document's ⋯ menu. Or file a same-named file: the app asks first whether to
  **Add as new version**, **Keep both** or **Cancel**, in the reader, the
  Library and the Documents tab.
- **History.** Open any version. Restore one: it comes back on top as a new
  version, and nothing is lost. Delete an old one.
- **Merge duplicates.** *Mark as newer version of…* folds two documents that
  are really one into a single history. Each option names the document's
  version and last uploader, so same-named files can be told apart.
- **Only current versions count.** The Library, project listings, chat and
  search see current versions only, so a revised file no longer doubles the
  search results.
- **Old versions are labelled.** An old version opens with an *Old version*
  strip and a link to the current one, and chat answering from it says so.
  An old chat's citation opens the version it quoted.
- **Your own copy falling behind.** The Library shows each project's version
  on its chips, plus a line when your own copy is behind the project's.

### 📦 The backend in a container

- **A `Containerfile`** (Python 3.13, with the Kokoro voice model built in and
  Docling optional) and a `backend` service in `docker-compose.yml`. A host OS
  upgrade no longer changes the Python the app runs on.
- **`./startup.sh quickstart [--single-user]`** takes a fresh clone to a
  running app with no host Python. Running it again picks up where a failed
  run stopped.
- **Live mode:** `./startup.sh start | stop | rebuild | logs | enable-autostart`
  (`enable-autostart` brings the stack back after a reboot on podman).
  `BACKEND_RUNTIME=venv` keeps the bare-metal backend.
- **Windows:** `startup.cmd` / `startup.ps1` provide `init`, `up`,
  `up-with-dev-auth` and `down` on stock PowerShell.

### 💬 Document chat that finds more and invents less

- **The whole of every page is searchable.** Long pages are split into
  overlapping parts of about 1,200 characters. Before, the bottom half of
  most PDF pages was never searched.
- **Exact words match too.** "Table 4.2", "MIMIC-IV" and "§3.1" are now
  found even when meaning-based search ranks them low.
- **Up to 3 rounds of tool calls per answer**, so the model can follow a
  trail through a document. It can also read up to 3 whole pages at once.
- **Source chips under answers** show the pages a reply used; click one to
  open that page. **Citations are links** that open the document at that page.
- **Open a Library document in the reader**, including one that was shared
  with you or that you see through a project.
- **"Use this document" switch:** chat without the open document, per chat.
- **An assistant profile.** In Admin → Assistant, an admin sets the
  assistant's name, tone and house rules, with a preview of what the model
  receives.
- **`scripts/eval_doc_qa.py`** checks your own model against a document with
  planted facts.

## Changed

- **Breaking (API), projects:**
  - `PUT /v1/projects/{id}/members/{user_id}` needs `{"role": …}`.
  - Project objects swap `is_owner` / `owner_user_id` for `my_role`,
    `member_count`, `doc_count`, `created_by` and `can`.
- **Breaking (API), filing:**
  - `PUT /v1/projects/{id}/docs/{doc_id}` returns `200`/`201
    {document_id, version}` and takes `as_version_of` / `keep_both`. Until
    one is passed, a clash answers `409 name_exists` or `409 older_version`.
  - `DELETE /v1/projects/{id}/docs/{doc_id}` is removed: use
    `DELETE /v1/projects/{id}/documents/{document_id}`.
  - New routes under `/v1/projects/{id}/documents` cover listing, versions,
    upload, restore, merge and delete.
- **API, Library:**
  - `GET /v1/docs` lists current versions only.
  - Its rows gain `newer_in`, and project chips gain `document_id`, `version`
    and `versions_count`.
  - `doc_count` counts documents, not versions.
- **Deleting a user** keeps the projects they created; only their memberships
  go.
- **Python 3.12 or 3.13 is required** (was 3.10–3.13).
- **Kokoro uses the CPU build of ONNX Runtime.** The OpenVINO build never
  actually loaded. The image is about 500 MB smaller.
- **Logs rotate at midnight** (`LOG_FILE_ROLL_OVER_TIME`, `LOG_FILE_BACKUPS`).
  `LOG_FILE_MAX_MB` is gone.
- **Context-size and keep-alive caps for shared servers.**
  `INFERENCE_NUM_CTX_MAX` defaults to 32,768 tokens and
  `INFERENCE_KEEP_ALIVE_MAX` to 30 minutes.
- **`CHAT_PREFETCH=auto`** is the default: the pre-search runs only for models
  that can't search for themselves.
- Access-log lines no longer include query strings.

## Fixed

- **A large upload no longer freezes your other requests.** Every request from
  the same login used to hang until indexing finished, for about a minute on a
  big file. The cause was a "last seen" stamp whose row lock lived as long as
  the upload's background indexing. Measured after the fix during a
  90-second index: 2–8 ms per request, against 49.7 s before.
- **Log out ends the Keycloak session too.**
- **The browser remembers per user.** "Your Library", browser-only chats, the
  workspace folder, reading positions and the unsent chat draft now belong to
  whoever signed in, not to everyone on the machine.
- **Search within one document is exact**, whatever else is in the library.
- **A tool call that a small model writes as text now runs**, instead of
  becoming the answer.
- **Clear provider refusals:**
  - `429`: busy or rate-limited;
  - `401`: the API key needs checking;
  - `402`: out of credit;
  - `403`: refused by the provider, with its reason.
- **`web_search` page fetches are capped** by size, time and content type.
- **Windows bare metal:** fixed the console encoding, the event loop and
  `localhost` resolving to IPv6.
- **Phone width:** the admin console and the Library's project filter fit
  the screen.
- **Migrations 014–016** no longer re-run on every start.
- Read-aloud, reader and Index-button fixes are listed in the changelog.

## Upgrade notes

1. **Back up first.** For example:

   ```bash
   podman exec natural-reader-postgres pg_dump -U natural_reader -Fc natural_reader > natural_reader-pre-2.2.dump
   ```

   Migration `018` changes the primary key of `project_documents` and has no
   down step, so the dump is the only way back. The dump covers the database
   only; uploaded files under `data/` aren't changed by any migration.
2. **Migrations `014`–`018` run on the first start:**
   - `014` and `016` add columns and a settings table.
   - `015` rewrites `doc_chunks` once to add full-text search, so a large
     library makes the first start slower.
   - `017` makes each project's owner an Owner member. Other existing members
     become Contributors.
   - `018` turns every project document into version 1 of itself, with the
     name it shows today. Existing duplicates are not merged: use *Mark as
     newer version of…*.
3. **Every indexed document is rebuilt once, in the background,** the first
   time it is searched. Its old index keeps answering meanwhile.
4. **Moving to the backend container:** run `./startup.sh stop` (or `down`),
   then `./startup.sh start`. The first build downloads about 1.3 GB. Your
   existing `data/`, `logs/` and `.env` are used as they are. Only `.env`
   reaches the container. The container runs on Linux x86_64; Windows and
   macOS keep the bare-metal flow.
5. **Custom scripts** that call `PUT` or `DELETE /v1/projects/{id}/docs/{doc_id}`
   will break (see Changed). Nothing in `extension/`, `scripts/` or `deploy/`
   calls them. Deploy the SPA and the backend together.

## What you can't do yet

- **Chat across a whole project.** Chat reads one document per turn: the one
  that's open.
- **Compare two versions, or review a version before it becomes current.** A
  new version is current as soon as it's uploaded.
- **Rename a document without uploading a new version**, **undo a merge**,
  **see a project as of a date**, or **set storage quotas**. Every version keeps
  its content and index until someone deletes it.
- **Spot same-named documents at a glance** on the Documents tab. Only the
  merge picker tells them apart.
- **Version documents in a personal library.** History exists only inside
  projects.
- **Configure model providers in the app.** They're set in `.env` only.
- **GPU or NPU acceleration in the container.** The image is CPU-only.
- **Many big uploads indexing at once.** Each running index holds one of the
  backend's 10 database connections until it finishes, so about ten at once
  slow everyone down.

## Full changelog

[CHANGELOG.md](../../CHANGELOG.md#220---2026-10-12) · diff:
[v2.1.0…v2.2.0](https://github.com/udaravima/natural-reader/compare/v2.1.0...v2.2.0)
