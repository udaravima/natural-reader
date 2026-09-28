# v2.1.0 — Server-side chat, model providers, and server-indexed documents

Chat now runs **on the server**. The server builds the prompt, calls the tools
and the model, streams the reply, and saves it as it goes. So a stopped or
interrupted reply keeps what was written, and an image you attach stays
visible to the model for follow-up questions, even after a reload. Documents
are uploaded and indexed **on the server** from their real bytes, so everyone
who has the same file shares one index. Documents can also sit in several
projects at once.

This release has **breaking API changes** and **three data migrations**, one of
which hides every document registered before this release until someone
uploads its file again. Read [Upgrade notes](#upgrade-notes) before
upgrading.

## Added

### 💬 Server-side chat

Each chat turn is one request (`POST /v1/chat/sessions/{id}/turns`) that
streams typed events back (server-sent events).

- **The reply is saved while it streams.** Stop it, close the tab or lose the
  connection, and the partial reply is kept and marked **"Stopped"**.
- **A chat still replying elsewhere** (another tab or device) shows
  **"Still generating…"** and refreshes until the reply settles.
- **One reply at a time per chat.** A second send while a reply is running
  is refused with a clear message.
- **Attached images are stored with the chat**, so later questions and
  reloads still include them.
- **Every turn knows the current time** (in your browser's time zone), so
  date questions no longer need a tool call.
- **Your daily token budget covers stopped and failed replies too.** The
  provider's token count is used when it reported one, and a text-length
  estimate otherwise.

### 🔌 Model providers

Chat can use native **Ollama** and any server that speaks the **OpenAI
chat-completions** protocol (vLLM, OpenRouter, LiteLLM), set up in `.env` with
`INFERENCE_PROVIDERS` (see [.env.example](../../.env.example) and
[DEPLOYMENT.md](../DEPLOYMENT.md)).

- **Grouped model picker.** Models are grouped by provider, with badges for
  what each model can do (tools, thinking, vision).
- **Settings adapt to the model.** Settings shows only the controls the
  selected model's provider accepts.
- **One slow or broken provider no longer takes the others down.** It's
  left out of the picker, and the rest still list.
- **Admin console.** The admin console lists the configured providers, with
  host and model count and never API keys. It can't edit them yet.

Tested against native Ollama and against Ollama's own OpenAI-compatible
endpoint (`/v1`). vLLM, OpenRouter and LiteLLM use the same protocol, but none
of them was run in testing.

### 📄 Document questions without asking for a search

When you chat with an indexed document open, the server searches it **before**
the model runs. The reply shows "Used N passages from …". Small local models
often won't call the search tool themselves; this is what lets them answer
from your document. On a miss, the prompt still names the open document, so
a model that can use tools knows to search it.

### 📚 Documents uploaded and indexed on the server (A1)

- **Uploading.** The browser uploads the file and the server hashes it.
  A file the server already has is added to your library **at once**, with no
  re-processing. A new file is extracted and indexed **on the server**, using
  the reader's own page rules, so page numbers in chat citations match the
  reader.
- **Removing.** **Remove from my library** removes only your copy. Other
  people and projects keep theirs, and the stored content is deleted only
  when nobody holds it any more.
- **Protected content.** You can't change content other people use. Re-index
  or convert refuses with a plain reason ("Other people also use this
  document…", or "This document is in a project…").
- **Upload caps and refusals.** `PDF_UPLOAD_MAX_MB` (50) and
  `TEXT_UPLOAD_MAX_MB` (10) cap uploads, and refusals say why.
- **Audit log.** An audit log (`logs/audit.log`) records who-holds-what
  changes, by ID only.

### 🗂️ Documents in several projects (C0)

A document can be filed into more than one project. The Library shows one
chip per project, with an × for the person who filed it, and **New project**
creates a project from the Library.

## Changed

- **Breaking: `POST /v1/inference/chat` is removed.** Use
  `POST /v1/chat/sessions/{id}/turns`.
- **Breaking: `PUT /v1/chat/sessions/{id}` is removed.** The server writes
  messages itself. `POST /v1/chat/sessions/import` copies a chat that lived
  only in your browser, and the app does this for you the first time you
  open such a chat.
- **Breaking: `PATCH /v1/docs/{id}` no longer accepts `project_id`.** Use
  `PUT`/`DELETE /v1/projects/{id}/docs/{doc_id}`. Document responses carry
  `projects: [{id, name}]`.
- **Breaking: document upload is a single multipart `POST /v1/docs`.** The
  server computes the hash. The old register-then-send-chunks routes are gone.
- **`GET /v1/inference/models`** returns objects (`id`, `provider`, `kind`,
  `name`, `capabilities`). Model ids are `provider:model`. A saved selection
  like `qwen2.5:7b` is migrated automatically.
- **Local mode (browser straight to Ollama) is removed.** Chat always goes
  through the server.
- **The `current_time_date` tool is gone.** The time is in every prompt.
- **`CHAT_PREFETCH_MIN_SCORE` now defaults to `0.6`** (was `0.75`). At 0.75,
  with `nomic-embed-text`, most answerable questions missed.

## Fixed

- **Project owners can read documents that members file into their
  project.**
- **The picker no longer shows one model while sending another.** That
  happened after a provider was renamed. The server also refuses a model its
  provider doesn't list (`422 model_not_allowed`) instead of passing on a
  provider error.
- **The embedding model is no longer offered as a chat model.**

## Upgrade notes

- **Back up the database first**, for example
  `podman exec natural-reader-postgres pg_dump -U natural_reader natural_reader > natural_reader-pre-2.1.sql`
  (with Docker, run the same command using `docker exec`). Migrations `010`–`013` run
  automatically on the next backend start:
  - **`010` drops data.** It drops `documents.project_id`, after copying it
    into `project_documents`.
  - **`011` drops `doc_grants` and some columns.** Owners become upload
    entries, and grants become shared entries.
  - **`012` marks every existing holding unverified.** See the next point.
  - **`013` is additive:** chat messages, events and attachments.
- **Every document registered before this release disappears from its
  holders' libraries, search and chat right after the upgrade** (404).
  Nothing is deleted. Before A1 the browser sent only a hash, so no one ever
  proved they had the file.
  - **To get a document back, open it in the reader and press Index.** That
    uploads the file.
  - **The same upload restores what that person shared or filed.** Shares
    and project placements they made come back with it.
  - **Recipients get it back too**, once the sharer uploads the file, or by
    uploading their own copy.
  - **Details:** [LIBRARY.md](../LIBRARY.md#upgrade-notes-migration-012).
- **Deploy the SPA and the backend together.** Chat, upload and project
  linking all changed shape. Mixed versions fail or lose project links.
- **Proxies must pass `text/event-stream` unbuffered** (`proxy_buffering off`
  in nginx), or replies arrive all at once. The server sends a keep-alive
  every 15 s, so any proxy idle timeout above that works. See
  [DEPLOYMENT.md](../DEPLOYMENT.md).
- **`package.json` bumped** `2.0.0` → `2.1.0`.

## What you can't do yet

- **Read or chat about a document someone shared with you, or that you see
  through a project, unless you have the file yourself.** The Library lists
  it, but opening a document still works from the reader's local copy. The
  planned fix: serve stored bytes to anyone who can read the document, and
  open it from the Library.
- **Click a citation to jump to its page.** Citations such as
  "[3] (page 1)" are text only.
- **Share a single document or add project members in the app.** Both are
  API-only until the project-management screen (A0) ships (curl examples
  in [LIBRARY.md](../LIBRARY.md#sharing)).
- **Configure model providers in the app.** They're set in `.env` only.
- **Attach audio, or export a chat.**
- **Keep a reply generating through a reload of the same tab.** It's saved
  as "Stopped". A reply only finishes if the chat is also open somewhere else.
- **Rely on very small models for tool use.** `llama3.2:3b` sometimes
  writes a tool call as plain text instead of making it, and that text ends
  up as the reply. Larger models (`qwen3.5` in our testing) don't.

## Full changelog

[CHANGELOG.md](../../CHANGELOG.md#210---2026-09-28) · diff:
[v2.0.0…v2.1.0](https://github.com/udaravima/natural-reader/compare/v2.0.0...v2.1.0)
