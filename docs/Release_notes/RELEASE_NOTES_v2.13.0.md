# v2.13.0 — Assistants with names, a helper model, and whole folders in one upload

**An admin can now set up several assistants and let each person pick one per
chat.** **Admin → Chat → Assistant** holds house rules that every chat follows
and named profiles on top of them, such as a Tutor and a Coach, one of them the
default. The chat sidebar gets a **Profile** picker under **Model**, and each
chat keeps the profile it used. A profile saved before this release becomes one
profile named **Assistant**, the default, so no chat reads anything different
after the upgrade.

**A small helper model takes on the routine writing.** Name one in
**Admin → Models → Helper model** and it writes tags and a one-line description
for each indexed document, a title for each new chat, and a first draft of an
assistant profile from a description. It is off until an admin names a model.

**A project takes a whole folder at once.** **Upload files**, **Upload folder**
or a drop shows what each file will become before anything is sent: a new
document, the next version of one, or left alone because nothing changed.
Uploading the same folder again sends only what changed. Behind it, indexing and
conversion now wait in a queue in the database, one at a time per worker, and
the queue survives a restart.

## Added

### 🧑‍🏫 Assistant profiles, picked per chat

- **House rules** (up to 2,000 characters) lead every chat. **Profiles** each
  have a name, a one-line description that people see under it, and their text
  (up to 8,000 characters). Each row has **Edit**, **Make default**,
  **Turn off** / **Turn on**, **Up** / **Down** and **Delete**.
- **A chat keeps its profile** when the default changes later. A turned-off
  profile disappears from the picker, and chats that already use it keep it. A
  deleted profile's chats say so and use the default from their next message.
- **Placeholders:** `{{date}}` (today, in the person's timezone), `{{app}}` and
  `{{profile}}` are filled in on every message. Where a profile uses `{{date}}`
  or `{{app}}`, the app doesn't add its own sentence saying the same thing.
  Anything else in double braces is sent as written, and the editor warns you.
- **What the model receives** shows the whole instruction text a chat would get
  with the text you're editing, before you save it.
- People see a profile's name and description, never its text.

### ✦ A helper model

- **Tags and descriptions** for each indexed document: up to five tags and one
  line, written after indexing, one document at a time, and only while nothing
  is waiting to be indexed. The helper reads at most 6,000 characters of each
  document. What it writes belongs to the project or library copy it was written
  for, so two projects holding one file never see each other's tags. A project's
  Documents tab filters by tag and by text. Contributors edit the tags with the
  pencil; **Generate** and **Write tags for N documents** ask the helper again.
- **Chat titles** a few seconds after a chat's first reply. A title you typed is
  never replaced. **✦ Generate title**, beside **Rename**, asks again. A cloud
  helper never reads a chat that runs on a local model, and a reply that saw a
  file's real values is never sent to the helper.
- **A first draft of a profile:** describe the assistant and press **Generate**.
  Nothing is saved until **Save profile**.
- Helper calls show on the Usage page under **Helper**. A drafted profile counts
  towards the admin's own daily budget.

### 📁 Upload many files into a project

- **Upload files**, **Upload folder**, or drop files anywhere on a project's
  Documents tab. Before anything is sent, each file shows what it will become.
  **Upload** with nothing changed does the usual thing; when two documents share
  a name, the app asks instead of guessing.
- The files go two at a time, with progress, **Stop** and **Try again**. The
  upload carries on while you look at another tab.
- Scripts get the same rules from `POST /v1/projects/{id}/uploads` (one file per
  request, safe to run again); a plan route says first what would happen.

### ⏳ Indexing and conversion in a queue

- Each worker indexes one document at a time and runs one conversion at a time.
  Before, thirty uploads meant thirty extractions at once, a restart forgot what
  was waiting, and with several workers two of them could work on one document.
- A clean stop hands the running job back. After a crash, the job is picked up
  again after 10 minutes. A file that stops the server three times is marked
  failed, and its uploader is told.
- **"Waiting to index (3 ahead)"** in the reader, the Library and a project's
  Documents tab, with **Retry index** on a stuck project document.

### Also new

- **PDF | Both | MD:** a converted PDF can show its PDF page and its Markdown side
  by side, kept on the same page in both directions. A phone keeps **PDF** and
  **MD**.
- **The app's name from `.env`:** `APP_NAME`, `APP_TAGLINE` and `APP_COPYRIGHT`
  set what the app calls itself in chat, on the sign-in screen and on the browser
  tab. The sign-in screen and Settings end with the name, the release number and
  the copyright. Left unset, everything reads as before. Set the name before the
  first deployment: the Keycloak realm and the read-aloud extension keep their
  own names.
- **A warning before a model switch hides replies:** a line above the message
  box names the file whose real values stay with its model, and how many replies
  the new model won't be sent.

## Fixed

- **Chat buttons on touch screens:** **Rename** and **Delete** showed only on
  mouse hover, so on a phone they were invisible but still took taps.
- **Scrolling a tall Markdown page now turns the page number.**
- **A project Contributor can now resume a document's indexing.** Before, it
  answered "not found" unless the file was in their own library.
- **When a new version fails to index, its uploader is now told.**
- **`./startup.sh start` after a code change ran the old code** on podman, and
  **`./startup.sh rebuild` after a `stop` hung**: both fixed.

## Upgrading

- **Migrations 027 to 030 run at start:** the job queue, the helper's tags and
  descriptions, who wrote each chat title, and the profile library. Back up first
  (DEPLOYMENT.md, "Backup and restore"), then rebuild both halves: the backend
  image (`./startup.sh rebuild`) and the frontend (`npm run build`).
- **A saved assistant profile becomes one profile named "Assistant",** the
  default, with the same text.
- **`CHAT_ASSISTANT_PROFILE_FILE` is now used only while there are no
  profiles.** Once an admin adds one, the file is ignored.
- **The admin API changed:** `GET` and `PUT /v1/admin/assistant` are gone. Use
  `/v1/admin/assistant/profiles`, `/house-rules` and `/preview` instead. The
  admin console was their only caller.
- **New optional settings** (see `.env.example`): `HELPER_MODEL` (empty means
  off), `HELPER_TIMEOUT_S`, `HELPER_INPUT_CHARS`, `BULK_UPLOAD_MAX_FILES` (200),
  `UPLOAD_MIN_FREE_MB` (1024), `APP_NAME`, `APP_TAGLINE`, `APP_COPYRIGHT`.
- **Downgrading:** run the Down blocks in the headers of 030, 029, 028 and 027,
  in that order. Profiles other than the default, the house rules, generated
  tags and descriptions, and queued jobs are lost.
- **Coming from v2.11.0 or earlier?** Read v2.12.0's notes too: rotate the
  Keycloak secrets.

## Known limits

- **On a CPU the helper is slow:** about 30 s per document with `gemma4:e4b`.
  While documents index on a CPU-only machine, a search took 2.2 s here, against
  0.02 to 0.05 s when idle.
- **Profiles were walked only on a local model.** Not walked: a cloud model
  (Ollama cloud refused for want of credits) and Talk.
- **One rare race:** if an admin deletes a profile in the instant a message
  picks it, that one reply ends with an error. The next message works.
- **A drafted profile can change what a rule means:** a small model wrote it.
  Read it before saving.
- **Side by side at 100 to 120% zoom:** a wide PDF page is wider than its half.
  Scroll that half sideways, or zoom out.
- **The final reviews of the helper, chat titles, profile generator and profile
  library were self-reviews,** not a second reviewer's.

## Tests

3,559 backend tests pass (16 skipped), and 2,081 frontend tests in 174 files.
Lint has no errors, the build works, and CI is green on `development`.

Walked in headless Chromium against a scratch backend and database, with a
logging proxy recording what each model received:

- **Profiles:** the upgrade from a saved profile; house rules; adding,
  reordering, turning off and making default; **Generate** with `gemma4:e4b`;
  the placeholders filled in on the wire; a pick remembered after a reload; an
  old chat keeping its profile after the default moved; a deleted profile's
  chat; both screens at 360 px.
- **The helper:** tags and descriptions with the project boundary proven on the
  wire, and chat titles.
- **Uploads:** a bulk upload into a project, and the queue through a clean stop
  and a crash.
- **Side by side** in both directions.

## Full changelog

[CHANGELOG.md](../../CHANGELOG.md#2130---2026-11-16) · diff:
[v2.12.0…v2.13.0](https://github.com/udaravima/natural-reader/compare/v2.12.0...v2.13.0)
