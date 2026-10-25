# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Fixed

- **A file opened from a folder can be indexed.** Index on a Markdown or
  text file opened with **Open folder** used to refuse ("open the file
  itself with Choose File"). The reader now keeps the file's bytes when it
  opens it, so Index uploads that file, and it gets its own server copy and
  chat. A file in "Your Library" with the same name is still never used in
  its place.
- **Docling conversion shows its progress.** The Convert button used to say
  "Converting" until the whole PDF was done. It now shows **Preparing** while
  Docling loads its models, **Converting 3/40** as pages finish, then
  **Assembling** and, when pictures are described, **Enriching 2/5**. The
  server stores Docling's latest report in `documents.conversion_progress`
  (migration 022) and `GET /v1/docs/{id}` returns it. A conversion Docling
  fails on now offers **Retry convert** with the reason, as a failed upload
  did, instead of a plain **Convert**.
- **Project chat finds a document named with its number.** The project map
  lists documents as `17. "README.md"`. Next to names that start with their
  own number (`10-pbx.md`), the model wrote `17-README.md`, and every tool
  answered "Unknown document" until the turn ran out. A tool now takes the
  number and the name together, or the name in quotes, as long as both name
  the same document. Its "Use a number or name from the list" error lists
  them the way the map does.

## [2.4.0] - 2026-10-25

**Ask a whole project.** A chat can cover every current document in a
project, and each fact names the document and page it came from. **Web search
asks first:** the assistant offers a search on a card you can edit, and
nothing leaves the server until you press **Search the web**. Also: Settings
opens as a window, the narration keeps playing in every view, the chat sits
beside the page on a wide screen, and there's a profile with a picture and
your signed-in browsers. **A write is now committed before its response is
sent**, which fixes v2.3.0's known issue.

Behavior changes: web search no longer runs on its own (`CHAT_WEB_SEARCH=auto`
restores that); `POST /v1/tools/web_search` returns results without page
summaries; auto-read is off by default. Two migrations (`020`, `021`), both
additive. The compose Keycloak image moves to 26.8.0. Release notes:
[docs/Release_notes/RELEASE_NOTES_v2.4.0.md](docs/Release_notes/RELEASE_NOTES_v2.4.0.md).

### Added

- **Settings is a window, not a page.** It opens over whatever you have open
  (tabs: Profile, Voice & reading, Chat, Appearance, Connection, Access
  tokens), so changing the voice or turning thinking off no longer stops the
  narration or a reply. A browser left on the old Settings view opens the
  reader.
- **The narration plays in every view.** Chat, Library and Admin show a small
  header control (pause, stop, back to the page). Opening another document
  pauses it. One voice at a time: reading a reply aloud pauses the narration.
- **Chat beside the page.** In the reader on a wide screen, the chat opens in
  a column next to the page; **Ask page** and **Ask AI** on a selection land
  there instead of switching views.
- **Profile.** A profile picture (cropped and shrunk to 256 px in the browser,
  stored in the new `user_avatars` table, migration 020), a **Manage your
  account** link to the sign-in provider's account page (Keycloak's account
  console by default; `OIDC_ACCOUNT_URL` overrides or hides it), and the list
  of signed-in browsers with **Sign out** and **Sign out everywhere else**.
  `PUT`/`DELETE /v1/auth/me/avatar`, `GET /v1/users/{id}/avatar`,
  `GET /v1/auth/sessions`, `DELETE /v1/auth/sessions/{id}`,
  `POST /v1/auth/sessions/sign-out-others`; `/v1/auth/me` adds `name`,
  `first_name`, `last_name`, `username`, `avatar_url` and `account_url`.
- **Admins find people by part of a name.** Adding a project member or
  sharing a document, a site admin gets type-ahead over every account (emails
  shown, accounts awaiting approval included) whatever `USER_DIRECTORY_MODE`
  says; with the default `exact` mode they used to need a full email address.
  In every mode, "First Last" typed together matches the full name.
- **Admin → Defaults.** An admin sets what auto-read, the read-aloud timing,
  thinking, the voice and the reading speed are for anyone who hasn't chosen
  them. People keep their own choice; **Apply to everyone** replaces it the
  next time each person opens the app. `GET /v1/settings/defaults` (any
  signed-in user), `GET`/`PUT /v1/admin/defaults`,
  `POST /v1/admin/defaults/{name}/apply-to-everyone`.
- **Sessions end when unused.** `SESSION_IDLE_HOURS` (default 12; 0 = off)
  ends a sign-in the browser hasn't used for that long. `SESSION_TTL_HOURS`
  (168) stays the absolute cap.
- **Ask a whole project.** **Ask about this project** on a project page
  (Library → Projects) starts a chat about all of the project's current
  documents; the chat's scope chip reads **Project: *name***. The assistant
  gets a map of the project (each document's name, pages and headings),
  searches across the documents as one ranked list, and tells you which
  document and page each fact came from. The chip also takes a project from
  inside any chat (**Choose a project…**), and the chat list shows a project
  chat's project. A project you can no longer see reads **Project
  unavailable**; the next message is refused with "This project doesn't exist
  or you don't have access." and the chat goes back to no project. Documents
  still being indexed are listed but not searchable yet; old versions are
  never searched.
- **Citations and source chips name the document.** In a project chat a fact
  is cited **(Guide.md, page 7)** and the link opens that document at that
  page; joined citations, page lists and ranges count. The chips under a
  reply are grouped by document (**Guide.md · p. 3 · p. 6**).
- **A reader goes through many pages for one goal.** For "every command, in
  order" the assistant hands the pages to a separate small model call that
  returns only the facts, each with a quote the app checks against the page
  and drops if it isn't there. `extract_from_documents` reads up to
  `CHAT_EXTRACT_MAX_PAGES` pages a call (the closest to the goal when there are
  more) and lists what it didn't read. It also reads the web pages the
  assistant picks. Set `READER_MODEL=chat` on a CPU-only Ollama so no second
  model is loaded. Its model calls count toward the person's daily token
  budget and show in the admin's usage figures, using the provider's token
  counts, or an estimate (characters ÷ 4) when it reports none.
- **Web search asks first.** By default the assistant can only offer a web
  search: a card, **Search the web?**, shows its reason and the query in an
  editable box, with **Search the web** and **Not now**. Yes runs exactly the
  query on the card, for that one answer; a different search needs a new
  card. Facts from the web cite their site, **(example.com)**, and the pages
  read show as links under **Web sources**. `CHAT_WEB_SEARCH=ask|auto|off`
  sets the starting mode.
- **Admin → Chat options → Web search.** *Ask first*, *Search without
  asking* or *Off*, for everyone, from the next message. It says whether the
  value was set there, comes from `CHAT_WEB_SEARCH`, or is the default, and
  **Use the server setting** hands control back to the server setting.
  `GET`/`PUT /v1/admin/chat-options` (`PUT {"web_search": "ask"|"auto"|"off"|null}`;
  `null` resets).
- **Code blocks, outlines and table cells are searchable.** A Markdown code
  block is its own passage on the page it sits on, so a command can be found
  and read; each document's headings (or a PDF's bookmarks) are stored as its
  outline. Existing documents are re-chunked in the background on first use;
  only text with no embedding yet is embedded. Migration `021` adds
  `chat_sessions.scope_project_id` and `doc_outlines`.
- New settings, all with defaults in `.env.example` and
  [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md): `CHAT_PROJECT_MAP_CHARS` (6000),
  `CHAT_PROJECT_MAX_TOOL_ROUNDS` (5), `CHAT_PROJECT_FUSION_DEPTH` (50),
  `CHAT_SEARCH_PER_DOC` (2), `CHAT_EXTRACT_MAX_PAGES` (12),
  `CHAT_EXTRACT_OUTPUT_CHARS` (4000), `CHAT_WEB_SEARCH` (`ask`),
  `READER_MODEL`, `READER_INPUT_CHARS` (12000), `READER_OUTPUT_CHARS` (1200),
  `READER_MAX_CONCURRENCY` (2) and `READER_TIMEOUT_S` (60).
- API: chat turns accept `context.project_id` (an id sets the chat's project,
  `null` clears it, absent keeps it) and `context.web` (`{query}`, the
  approved search); session list and record carry `scopeProject`.

### Changed

- **The compose Keycloak image is 26.8.0** (was 26.0), and **Docling must be
  2.142 or newer** (`requirements-docling.txt`, installed only when the
  backend is built with `WITH_DOCLING=true`).
- **Auto-read is off by default, and replies are read once complete**
  (were on, and while streaming). Existing browsers forget their stored value
  for these two once, because until now every setting saved its default on
  first load as if it had been chosen.
- **A setting stores only a choice.** One you never touched follows the
  current default (the admin's, else the built-in), and changing one model
  setting no longer saves the others' defaults with it.
- **Web search asks first by default.** It used to run on its own whenever
  SearXNG was configured. A deployment that wants that again sets
  `CHAT_WEB_SEARCH=auto` (or an admin picks *Search without asking*).
- **`web_search` lists results; it no longer fetches or summarizes pages.**
  A result is its title, site, address and snippet. Reading a page is
  `read_web_pages`, through the reader, which takes the passages that match
  the goal from anywhere on the page (it used to cut each page at its first
  6,000 characters). **`WEB_SEARCH_MAX_PAGE_CHARS` is no longer read.**
  `POST /v1/tools/web_search` follows: it returns `{title, url, site,
  snippet}` per result, not `{title, url, summary, source}`. Nothing in the
  app calls it, but it is public API. `WEB_SEARCH_SUMMARY_MODEL` and
  `WEB_SEARCH_SUMMARY_TIMEOUT_S` remain only as fallbacks for `READER_MODEL`
  and `READER_TIMEOUT_S`.
- **The "Use this document" chip became the scope chip.** One menu: *No
  document*, *This document: name*, a project, or *Choose a project…*.
- **The assistant may fill a gap from general knowledge,** if it says the
  part isn't from the documents and gives it no page, and it offers a web
  search when the answer needs current or outside facts. It used to be told
  never to fill a gap from memory. This applies to a single document too.
- **Search matches part of a query.** When no passage has every word, passages
  with at least half of the query's informative words rank (document chats
  too).
- **Read Selection and Ask AI no longer float over the Chat view,** where they
  covered the Send button. To hear a reply, use its own **Read aloud** button.
  A selection already being read keeps its **Stop**, at the top of the view.

### Fixed

- **Words in a Markdown table cell can be found.** Cells ran together
  (`OIDC_ISSUERhttp://x` was one word); they are now separated in the
  searchable text. The reader's pages don't move.
- **Leaving Chat no longer stops the reply.** A deliberate effect aborted the
  stream and its read-aloud whenever you left the Chat view (and the narration
  stopped on leaving the reader); both now carry on.
- **A text page fits beside the chat.** Text and Markdown pages were sized
  to the window (80 vw), and a page wider than its column was cut off on the
  left, out of reach of the scrollbar; they now size to their column, and an
  oversized page scrolls from its left edge.
- **A write is committed before its response is sent.** Every route took its
  database connection with FastAPI's default "request" scope, which commits
  only after the response and its background tasks: an immediate re-read could
  miss the change (measured 3 of 30; now 0 of 60), and each running index held
  one of the 10 pool connections until it finished. Connections are now
  `scope="function"`, and a test fails any route that forgets.
- **Cancelling an audiobook stops the server.** Cancel aborted the browser's
  requests, but the server synthesized every cancelled page anyway, holding
  the one Kokoro lock: a one-word read right after Cancel waited 76.8 s (now
  0.3 s). The server checks between sentences and before taking the lock. The
  audiobook also shows progress while it reads the document, can be cancelled
  then, and has a Cancel item in the narrow-screen menu, which had none.
- **Thinking "off" turns thinking off on OpenRouter.** "Off" was sent as
  nothing, and OpenRouter then applies the model's default, which for many is
  on. It is now `reasoning: {"effort": "none"}` for a model OpenRouter lists
  as reasoning (not for one whose reasoning is mandatory).

## [2.3.0] - 2026-10-17

**Access tokens can be limited** to what they're for: read aloud, documents,
chat or admin, with an expiry. **The read-aloud extension works again** on a
server with sign-in on, and on strict-CSP sites such as GitHub. One behavior
change: with `FRONTEND_ORIGIN` unset, **no other website may call the API from
a browser** (it was `*`). One migration (`019`, additive). Release notes:
[docs/Release_notes/RELEASE_NOTES_v2.3.0.md](docs/Release_notes/RELEASE_NOTES_v2.3.0.md).

### Added

- **Scoped access tokens.** Settings → Account asks what a new token may do:
  **Read aloud** (the default; the TTS routes only), **Documents & projects**
  (everything `reader` covers, read aloud included), **Chat**, **Admin**. Only
  the kinds the account holds are offered. A token's scopes narrow its
  owner's current capabilities in `get_current_user`, so every route honours
  them, and they never add to the account. Scoped tokens can't create, list or
  revoke tokens (403 `token_scope`), so a leaked one can't mint a
  replacement. Tokens made before 2.3.0 keep full access and are listed as
  **Full access (old)**.
- **Token expiry in the app:** 30 days, 90 days, 1 year (default) or Never.
  The list shows each token's access, last use and expiry.
- API: `POST /v1/auth/tokens` takes `scopes` (omitted = full access, as
  before) and rejects a scope the account lacks or an expiry in the past
  (422). `GET /v1/auth/tokens` returns `scopes`. `GET /v1/auth/me` adds
  `scopes` and `read_aloud`.

### Fixed

- **The token list shows a token as soon as it's created**, and drops it as
  soon as it's revoked. It re-read the list right after each change, and the
  server can answer a write before committing it (see Known issues in the
  release notes), so the new token was sometimes missing until a reload.

- **The read-aloud extension works with sign-in on** (extension 0.2.0). Before,
  it sent no credentials, so every read on a server with sign-in on failed
  with 401, while the popup's dot still showed green. Its options page now
  takes an access token (create one under Settings → Account), kept in
  `chrome.storage.local` on that computer only. Synthesis runs in the
  extension's service worker: a request carrying a token from an ordinary web
  page would fail the server's CORS check, and the worker's host permission
  skips it. The token never enters the page. **Test connection** and the popup
  check the token through `/v1/auth/me`: green means a read will work, amber
  means the server refused you, and the line under the title says why. A
  server that isn't on `localhost` gets a site-access prompt from Test
  connection.
- **Read-aloud plays on strict-CSP sites** such as GitHub and `claude.ai`. The
  toolbar played its audio from the page, whose `media-src` policy refused the
  `blob:` audio without an error: the toolbar said "reading" and stayed
  silent. The toolbar is now an extension page in an iframe, which plays under
  the extension's own policy, and no extension code is loaded into the page.
  Dragging follows the pointer even when it outruns the bar.

### Changed

- **CORS: unset `FRONTEND_ORIGIN` now allows no cross-origin browser caller**
  (was `*`). The wildcard existed for the read-aloud extension, and it let any
  website drive your Kokoro endpoint from JavaScript. The SPA is same-origin
  and the extension now calls from its service worker, so neither needs it.
  **Upgrade note:** a page on another origin that calls the API from a browser
  stops working until its origin is listed in `FRONTEND_ORIGIN`, or `*` is set
  to keep the old behavior. Read-aloud extension 0.1.0 is among them; reload
  the extension to get 0.2.0.

## [2.2.0] - 2026-10-12

Projects become **team spaces with roles** (Reader, Contributor, Maintainer,
Owner), managed in the app. A revised file becomes **a new version** of a
project's document, with its history kept; chat and search see only current
versions. The backend can run **in a container**. Document chat searches whole
pages and exact words. Breaking API changes and five migrations, one of them
(`018`) without a down step: take a `pg_dump` and read **Upgrade notes** first.
Release notes:
[docs/Release_notes/RELEASE_NOTES_v2.2.0.md](docs/Release_notes/RELEASE_NOTES_v2.2.0.md).

### Added

- **The backend runs in a container.** A `Containerfile` (Python 3.13, the
  Kokoro voice model baked in, Docling optional) and a `backend` service in
  `docker-compose.yml` with host networking and `data/` and `logs/`
  bind-mounted from the host. A host OS upgrade no longer moves the Python
  under the app.

- **`./startup.sh quickstart [--single-user]`** takes a fresh clone to a
  running app in one command, with no host Python: it writes a local-dev
  `.env`, starts the stack and runs the frontend dev server. Re-running it
  resumes after a failure.

- **`./startup.sh start`, `stop`, `rebuild`, `logs` and `enable-autostart`**
  for live mode. `enable-autostart` brings a podman stack back after a
  reboot. `LOCAL_KEYCLOAK=auto|true|false` says whether `start` runs the
  compose Keycloak. `pytest -m container` builds and runs the real image.

- **Deployment limits on context size and keep-alive.**
  `INFERENCE_NUM_CTX_MAX` (default 32,768 tokens) and
  `INFERENCE_KEEP_ALIVE_MAX` (default 30 minutes) cap what a user's
  Settings page can ask an Ollama model for; the page offers only values
  within them.

- **"Use this document" switch.** A chip above the chat box shows the open
  document; click it to chat without the document in this chat (no search,
  no document tools). Kept per chat and per user; pins still work.

- **Source chips under answers.** A reply that used the open document shows
  the pages it came from as chips ("Sources: p. 3 · p. 8"); click one to open
  that page. They appear even when the model forgets to write "(page N)".
  When nothing in the reply matches a page, the chips show the pages that
  were searched, labelled "Searched (not cited)".

- **`CHAT_PREFETCH=auto|on|off`** controls the search the app runs before
  the model. The default, `auto`, runs it only for a model that can't search
  the document itself; a model with tools searches for itself. Measured on
  `gemma4:e4b`: 9/9 without the pre-search, 5/9 with it (handed passages,
  it answered from them and missed facts they didn't hold). The eval takes
  `--prefetch auto|on|off` for one run.

- **Passages found before the model runs arrive as a search result, not as
  part of your message** (with `CHAT_PREFETCH=on` and a model with tools).
  Text planted in a document can no longer pose as something you wrote.

- **An assistant profile for the deployment.** Admins give the assistant a
  name, personality, tone and house rules in the admin console's new
  **Assistant** section, with a preview of everything the model receives.
  A deployer can ship it as a file instead (`CHAT_ASSISTANT_PROFILE_FILE`).
  It comes first in every chat; the app's own rules about documents and
  tools follow it and take precedence.

- **The assistant follows one strategy for each message.** Small talk and
  "say that more simply" get a direct reply, without searching. Questions
  that could be about the open document are searched in the document before
  the web, and the assistant checks that a passage actually answers before
  using it. Live questions (news, prices, weather) go to the web. It also
  knows the document search isn't a web search engine, so it searches with
  short phrases and exact names, labels and numbers. Follow-ups now refer to
  your previous answer: the app's clock line no longer leads your message
  (today's date is in the assistant's instructions instead).

- **Windows startup script.** `startup.cmd` (and `startup.ps1`) do on
  Windows what `startup.sh` does on Linux and macOS: `init`, `up`,
  `up-with-dev-auth` and `down`. They run on the PowerShell built into
  Windows 10/11, with no execution-policy change needed. See README §3,
  "On Windows".

- **Open a Library document in the reader.** Each Library row has an
  **Open** button that fetches the document from the server and opens it
  like a file you picked; Index and chat work on it straight away. Someone
  a document was shared with, or who sees it through a project, can now
  read it and ask about it; before, sharing only added a line to their
  list. The route behind it, `GET /v1/docs/{id}/file`, answers 404 to anyone
  who can't read the document.

- **Click a citation to open that page.** When a reply used a document, its
  "page N" and "(page N)" citations are links: clicking one opens that
  document in the reader at that page, fetching it from the server if it
  isn't the one open. If you can no longer read it, a notice says so and
  you stay in chat. Replies that used no document are unchanged.

- **The model can read whole pages.** Asked "what's on page 12?" or about
  a table a search pointed to, the model reads up to 3 whole pages of the
  open document at once. It gets the exact text instead of a
  1,500-character excerpt. The limit per read is `CHAT_READ_PAGES_MAX_CHARS`
  (default 12,000).

- **An evaluation you can run against your own model.**
  `scripts/eval_doc_qa.py --model <id>` asks the model five questions about
  a document with planted facts, through the real chat path, and reports
  for each whether it found the fact, cited the right page, or said the
  document doesn't cover it. The five cover one fact, a two-step trail, a
  table label, a page read, and a fact that isn't there.

- **Projects for teams, with roles (A0).** Every project has members with a
  role: Reader (read the documents), Contributor (also file their own
  documents), Maintainer (also remove documents, rename, manage members up to
  Maintainer) and Owner (everything, including Owners and deleting the
  project). A project keeps at least one Owner. Library → Projects lists your
  projects; a project page has Documents, Members and Activity tabs.
- **Find people to add or share with.** Type a name or a full email address.
  `USER_DIRECTORY_MODE` (exact | domain | open), `USER_DIRECTORY_DOMAINS` and
  `USER_DIRECTORY_SHOW_EMAIL` decide who can be found; the default finds people
  only by their full email.
- **Share a document from the Library.** A Share button on documents you
  uploaded; see and stop existing shares.
- **Project activity history.** Who added or removed whom, role changes,
  renames, documents filed and removed. `PROJECT_EVENTS_RETENTION_DAYS`
  (default 0 = keep forever).
- **Admin: every project and recovery.** Admin → Projects lists all projects
  with their owners; "Ownerless only" and "Add me as Owner" recover a project
  whose last Owner was deleted. Per-user project limits
  (`PROJECT_LIMIT_PER_USER`, default 20) and `PROJECT_CREATION`
  (readers | admins). Enroll takes username, first and last name.

- **Document versions in projects.** A revised file becomes a new version of a
  project's document instead of a second document: upload it from the
  document's ⋯ menu, or file a same-named file and choose *Add as new
  version*. Each document keeps its history: open any version, restore one
  (it comes back on top; nothing is lost), delete an old one, or merge two
  documents that are really one. The Library and the project listings show
  only current versions; if you open an old version, chat answers from it and
  says it's an old version. An old chat's citation opens the version it
  quoted, labelled, and the reader shows an old-version banner with a link to
  the current one.
  Activity records who added, restored, merged or deleted versions.

### Changed

- **Kokoro uses the CPU build of ONNX Runtime (`onnxruntime`).** The
  OpenVINO build in `requirements.txt` never actually ran: `kokoro-onnx`
  also installs `onnxruntime`, both write the same `onnxruntime/` folder,
  and the app fell back to CPU without saying so, on Linux and in the
  image. The image is about 500 MB smaller. To accelerate a bare-metal
  install, swap the build as the README's Hardware Acceleration section
  shows, then check the startup providers line.

- **`up` / `up-with-dev-auth` run the backend container on your working
  tree** (code edits apply on restart). `BACKEND_RUNTIME=venv` keeps the
  bare-metal backend, from the shell or as the checkout's default in `.env`. Docling moved to `requirements-docling.txt`. Every
  compose service now uses `restart: always`, because podman's boot service
  restarts only those. Generated secrets are 64 hex characters, made without
  Python.

- **Python 3.12 or 3.13 is required** (was 3.10–3.13): the chat server
  already used a 3.12 function, so on 3.10 and 3.11 the stream-close path
  failed. `startup.sh` and `startup.cmd` now look for 3.12/3.13 only and say
  how to install one (`uv python install 3.13`).

- **Logs rotate by time.** `server.log` and `audit.log` start a new file at
  midnight (`LOG_FILE_ROLL_OVER_TIME`), keeping `LOG_FILE_BACKUPS` old ones.
  `LOG_FILE_MAX_MB` is gone.

- **`POST /v1/docs/{id}/search` answers 409 `reindexing`** ("This document
  is being re-indexed for the current search model. Try again shortly.")
  while a document is rebuilt for a new embedding model, instead of
  comparing vectors from two models.

- **"Keep model warm: Always" now means up to `INFERENCE_KEEP_ALIVE_MAX`**
  (30 minutes by default), and a context size above `INFERENCE_NUM_CTX_MAX`
  is reduced to it: on a shared server both used memory for everyone. A
  single-user deployment can lift them (`-1` and `0`). Saved settings keep
  working; the Settings page shows the capped value.

- **The model can follow a trail through the document.** An answer can now
  use up to 3 rounds of tool calls (`CHAT_MAX_TOOL_ROUNDS`, was 1). For
  example, it can find the dataset a chapter uses, then search for who
  collected it. Guard-rails:
  - a per-answer limit on tool results (`CHAT_TOOL_RESULT_BUDGET_CHARS`);
  - an identical repeated call isn't run twice;
  - web search refuses queries that copy the document's text, so document
    text isn't sent to the web.

  While it works, the reply shows what it is doing, and "Still searching…
  (round n)" from the second round on.

- **The whole of every page is searchable.**
  - Long pages are split into overlapping parts of about 1,200 characters (`CHUNK_MAX_CHARS`, `CHUNK_OVERLAP_CHARS`). Before, the bottom half of most PDF pages was never embedded.
  - Text is embedded with the prefixes the embedding model was trained with (`search_document: ` and `search_query: ` for nomic-embed-text; `EMBEDDING_DOCUMENT_PREFIX` and `EMBEDDING_QUERY_PREFIX` for other models).
  - Documents indexed before this change are rebuilt in the background the first time they're used, and stay searchable meanwhile.

- **Search finds labels and names, not only meaning.** The document search
  also matches the exact words of the query, in any language. "Table 4.2",
  "MIMIC-IV" or "§3.1" are found even where meaning-based search ranks them
  low.

- **Document search returns evidence, not noise.** The document search
  tool is now called `search_documents`. It leaves out weak matches
  (`CHAT_SEARCH_MIN_SCORE`, default 0.45) and returns the rest nearest
  first, with no score or relevance label: measured, closeness can't tell a
  passage that answers from one that doesn't, so the model reads them to
  decide (docs/CHAT_WITH_PDF.md §6.5). It doesn't repeat
  a passage the model already has in the same answer, so a second search
  finds something new. Citations in chats saved before this change still
  open their document.

- **Breaking (API):** `PUT /v1/projects/{id}/members/{user_id}` now needs a
  body `{"role": "reader|contributor|maintainer|owner"}`; a body-less call is
  a 422. Project objects drop `is_owner` and `owner_user_id` for `my_role`,
  `member_count`, `doc_count`, `created_by` and `can`.
- Deleting a user no longer deletes the projects they created; only their
  memberships go. A project left without an Owner appears in Admin → Projects.
- Admins file and remove project documents only through a member role.
- People's username, first and last name are refreshed from the login token.
- Access-log lines no longer include query strings, so search and people-lookup
  text never reaches the logs.

- **API (breaking):** `PUT /v1/projects/{id}/docs/{doc_id}` returns
  `200`/`201 {document_id, version}` instead of an empty `204`, takes the
  query parameters `as_version_of` and `keep_both`, and answers
  `409 name_exists` / `409 older_version` until the caller passes one of them.
  `DELETE /v1/projects/{id}/docs/{doc_id}` is removed: use
  `DELETE /v1/projects/{id}/documents/{document_id}`, which removes the
  document with its history. New routes under `/v1/projects/{id}/documents`
  list documents and versions, upload (`…/versions`, `…/versions/from-library`),
  restore, delete a version, merge and remove.
- **API: Library and counts.** `GET /v1/docs` lists current versions only and
  its rows gain `newer_in`; the project chips on each row gain `document_id`,
  `version` and `versions_count`. `GET /v1/docs/{id}` gains `versions_in`.
  `doc_count` (project list, project page, admin console) counts documents,
  not versions.
- A project's name for a document is stored when it is filed and no longer
  changes when the filer renames or removes their own copy.

### Removed

- **The `DOCLING_ENABLED=true` line in `startup.sh`.** It was never
  exported, so it never reached the backend; `.env` controls Docling.

### Fixed

- **Uploading a large file no longer freezes your other requests.** Every
  request from the same login (cookie or access token) used to hang until
  the upload's indexing finished, about a minute for a big file. Signing in
  a request stamped "last seen" on the request's own database connection,
  and FastAPI keeps that connection open until the background indexing
  ends, so the stamp's row lock blocked the next request's stamp. The stamp
  is now committed at once on its own short connection, at most once a
  minute per session or token (`last_seen_at` / `last_used_at` are now
  accurate to the minute).

- **Windows bare metal:** the backend no longer crashes printing ✓ / → on
  a cp1252 console (UTF-8 console streams, and `PYTHONUTF8=1` in
  `startup.ps1`). Chat and documents work: uvicorn and `asyncio.run` now
  use a selector event loop, because psycopg can't use the Windows default.
  Postgres is reached at `127.0.0.1:5433` rather than `localhost`, which
  Windows tries as IPv6 `::1` first.

- **Log out ends the Keycloak session too.** The app cleared its OIDC session
  before reading the ID token it keeps for logout, so Keycloak never got the
  `id_token_hint`. It then fell back to a confirmation page or, with the
  checked-in dev realm, refused the return address ("Invalid redirect uri"),
  and the next "Sign in" skipped the password. The dev realm export now
  allows `http://localhost:8080` and Vite's `http://localhost:5173` for
  sign-in and after logout. It also no longer carries any real deployment's
  hostnames, which the docs and sample nginx configs now spell
  `chat.example.com` / `auth.example.com`
  (`docs/sample_configs/*.example.com.sample`).
- **Admin console on a phone:** user rows wrap instead of pushing Disable and
  Delete off-screen, and the budget and project-limit fields carry visible
  captions ("tokens/day", "projects"). The Library's project filter no longer
  runs past the screen edge with a long project name.
- **Project activity** reads "added themselves" / "changed their own role"
  when an admin adds or promotes themselves.
- An admin project limit above 2147483647 is refused with 422 instead of a
  500 from the database.
- Migrations 014–016 never recorded their version and re-ran (harmlessly) on
  every startup; every migration now records itself, and a test checks it.

- **Searching one document is exact, whatever else is in the library.**
  The search could lose the open document's best passages when other
  indexed documents were closer to the question. It now ranks only the
  open document's own passages. This affects both the automatic search
  before the model answers and the document search tool.

- **The model gets clear rules about the document, not hints mixed into
  your message.** The rules the model follows (answer from the document's
  passages, cite pages as "(page N)", say when the document doesn't cover
  something) now come in one system message that names only the tools it
  actually has. Text from the document is marked as quoted material, so
  instructions hidden in a PDF aren't obeyed. A small model is no longer told
  to call a tool it can't use on the last step of an answer (it used to reply
  with the tool call written out as text). Web search results are treated
  as information, never as instructions. A model whose chat template has no
  system role (e.g. Gemma 2 or Mistral 7B v0.1 served by vLLM) gets the
  rules at the start of your message instead, remembered per model.

- **A file opened from a workspace folder no longer borrows another
  document's index.** If "Your Library" held a different file with the same
  name, Index and chat used that file's server copy. Folder files have no
  server document of their own, and Index on one now says so ("open the file
  itself with Choose File to index it").

- **Read aloud never plays a sentence from the page you just left.** A clip
  still being made when the page changed (turning the page, opening another
  document, clicking a citation) used to be kept for the same position on
  the new page; it is now thrown away.

- **Everything the browser remembers belongs to the signed-in user.** After
  "Your Library", three more things were still shared by everyone who used
  the browser: old browser-only chats, the last workspace folder (which
  reopened for the next person), reading positions and the unsent chat
  message in the composer. Each now belongs to
  the user who signed in; the next user sees none of it. As with the
  library, a browser's existing chats, folder, positions and draft go to the
  first user who signs in after the update.

- **A provider refusal says what happened.** A free OpenRouter model that
  was rate-limited used to show "The model provider returned an error:
  Provider returned error". Now a `429` says the model is busy or
  rate-limited and to try again or pick another model (with the provider's
  own detail when it gives one), a rejected key (`401`) says an admin needs
  to check the API key, `402` says the account is out of credit, and `403`
  says the provider refused the request, with its reason (OpenRouter uses
  `403` for input its moderation flags).
  A refusal before any reply still isn't counted against the daily budget.
  OpenRouter is now verified end to end (paid Gemma 4 and Mistral Small
  3.2); `.env.example` and `docs/DEPLOYMENT.md` say the key goes in
  `INFERENCE_<NAME>_API_KEY` — a bare `OPENROUTER_API_KEY` is ignored.

- **Small server fixes and stale docs.** A model id that names a provider
  but no model (`local:`) is refused as not allowed instead of being sent to
  Ollama as a model called "local:". The startup sweep for chat turns whose
  worker died now logs how many claims it cleared, including ones whose
  reply had already finished. At shutdown, the server also waits for reply
  saves that start while it is waiting (up to the same 5 seconds).
  `docs/CHAT_WITH_PDF.md` and the README's endpoint table now describe the
  server-side upload and extraction pipeline instead of the old
  client-side chunk upload.

- **Reader console errors on opening a PDF.** Stepping pages or zooming
  quickly no longer logs pdf.js's "Cannot use the same canvas during
  multiple render() operations": the previous page render is cancelled
  first. A recent file on the welcome screen is no longer a button inside a
  button (it opens with Enter or Space too, and its remove control stays
  separate). The reader's read-aloud audio keeps each clip alive while it's
  loaded, instead of discarding it at the end of a sentence or page, which
  could make the browser fetch a discarded `blob:` URL
  (`ERR_FILE_NOT_FOUND`).

- **"Your Library" on the welcome screen belongs to the signed-in user.** It
  used to be one list per browser, so on a shared machine the next person to
  sign in saw, and could open, the previous person's files. Each saved file
  now belongs to the user who opened it, and each user sees only their own
  (the five-file limit is per user too). Files saved before this release go
  to the first user who signs in on that browser, so a single-user install
  keeps its library. Signing out only hides the list; nothing is deleted.

- **A tool call a model writes as text now runs instead of becoming the
  answer.** Small models such as `llama3.2:3b` often reply with
  `{"name": "search_document", "parameters": {...}}` as plain text, sometimes
  in a ```` ```json ```` fence, and that JSON was saved as the reply. The server
  now holds a step's opening text while it could still be such a call (at most
  2,000 characters); if it is one object naming a tool offered on that step
  and the provider sent no real tool call, it runs as a tool call and the JSON
  is neither shown nor saved. Anything else streams as before, and prose is
  never held. A model its provider has seen reject tools now reports no tool
  support (its picker loses the "tools" badge), so it is offered none and
  nothing it writes is taken as a call.

- **`web_search` page fetches are capped by size, time and type.** A fetched
  page is now streamed and cut off at `WEB_SEARCH_MAX_RESPONSE_BYTES` instead
  of being buffered in full first — a large file (e.g. a 111 MB PDF) no
  longer holds a chat turn open for minutes. Each fetch (redirects included)
  has a total deadline, `WEB_SEARCH_FETCH_TOTAL_S` (default 15s), and a
  non-text `Content-Type` (anything but `text/*`/`application/xhtml+xml`) is
  skipped before any of the body is read. The SSRF guard is unchanged.

- **The Index button no longer gets stuck on "Index" after opening a brand
  new file.** Opening a PDF, text, or Markdown file for the first time set
  the reader's file name before the file finished writing to IndexedDB; the
  effect that hashes the open document to check its backend status read the
  bytes back immediately, found nothing, and never retried, so the button
  stayed on "Index" for the rest of the session even after the server had
  indexed it — reopening the file was the only fix. The file now saves to
  IndexedDB first, and the reader opens only once that's done. If the save
  itself fails, the document still opens, with a toast: "Couldn't save this
  file locally — Index and chat about it may not work until you reopen it."

### Upgrade notes

- **Switching to the backend container:** run `./startup.sh stop` (or
  `down`) first. The first `start` builds the image (~1.3 GB of downloads,
  more with Docling). Existing `data/`, `logs/` and `.env` are used as they
  are, and documents uploaded before the switch still open (and ones
  uploaded in the container open after a rollback to the venv). On podman,
  run `./startup.sh enable-autostart` once for reboots; an empty
  `pod_natural-reader` left by the old layout can go (`podman pod rm
  pod_natural-reader`). Only `.env` (plus `HOST`/`PORT`/`WORKERS`) reaches the
  container, so move any key exported only in your shell into `.env`. The
  container runs on Linux x86_64; Windows and macOS keep the bare-metal flow.

- **Migration `014`** adds `documents.embedding_profile`. It is additive only.
- **Migration `015`** adds `doc_chunks.text_search`, a stored full-text
  column with a GIN index. Postgres rewrites `doc_chunks` once to fill it:
  on a large library the backend's first start after upgrading takes longer.
- **After upgrading, every indexed document is rebuilt once, in the
  background, the first time someone chats with it or searches it.** Its
  old chunks keep answering meanwhile. The rebuild re-embeds the whole
  document: expect about one embedding call per 1,000 characters of text, one
  document at a time.
- **Changing `EMBEDDING_MODEL` no longer needs a manual re-index**, as long
  as the new model has the same dimension (`EMBEDDING_DIM`). Each document
  rebuilds on first use. Until it does, it can't be searched: vectors from
  two models can't be compared.

- **Take a `pg_dump` before upgrading.** Migration `018` changes the primary
  key of `project_documents` and has no down step, so the dump is the only way
  back. Existing project documents become version 1 of themselves, with the
  names they show today. Existing duplicates are not merged automatically: use
  **Mark as newer version of…** on the Documents tab.
- **Custom scripts that call `PUT` or `DELETE /v1/projects/{id}/docs/{doc_id}`
  will break** (see Changed). The app itself is updated in the same release;
  nothing in `extension/`, `scripts/` or `deploy/` calls them.

## [2.1.0] - 2026-09-28

Chat runs **on the server**: the server builds the prompt, calls tools and the
model through configurable **model providers** (native Ollama plus
OpenAI-compatible servers), streams the reply and saves it as it goes.
Documents are **uploaded and indexed on the server** from their real bytes, and
can sit in several projects. Breaking API changes and three data migrations —
read **Upgrade notes** first. Release notes:
[docs/Release_notes/RELEASE_NOTES_v2.1.0.md](docs/Release_notes/RELEASE_NOTES_v2.1.0.md).

### Added

- **Server-side upload and indexing (A1).** The browser uploads the file
  (`POST /v1/docs`, multipart) and the server hashes it: a file the server
  already has is added to your library at once with no re-processing; a new
  one is extracted and indexed on the server with the reader's own page rules
  (committed parity fixtures), so citation page numbers match the reader.
  Upload caps `PDF_UPLOAD_MAX_MB` (50) and `TEXT_UPLOAD_MAX_MB` (10); refusals
  say why.
- **Library entries (A1).** Each person holds their own entry for shared
  content: **Remove from my library** removes only yours, and content is
  deleted when nobody holds it (plus a startup sweep). The Library shows
  "shared by …" and "via project" badges. Sharing a single document is
  API-only (`PUT`/`DELETE /v1/docs/{id}/shares/{user_id}`) until the
  project-management screen ships.
- **Content that other people use can't be changed under them.** Re-index and
  convert are refused with a plain reason when someone else holds the
  document ("Other people also use this document…") or a project does ("This
  document is in a project…"); an admin can still do it.
- **Audit log** (`LOG_AUDIT_FILE`, default `logs/audit.log`): who-holds-what
  changes (entries, shares, placements, garbage collection), IDs only.
- **New project from the Library.** Library → **New project** creates a
  project you own.
- **Documents can belong to several projects.** New `project_documents` join table
  (migration `010`, backfilled from `documents.project_id`, which is dropped).
  Link/unlink with `PUT`/`DELETE /v1/projects/{id}/docs/{doc_id}`; the Library shows
  one chip per project with add/remove.
- **Server-side chat.** Each turn runs on the server and streams typed events.
  The reply is saved on the server as it streams: if you stop it, close the
  tab, or lose the connection, the partial reply is kept and marked "Stopped";
  a chat whose reply is still being written elsewhere (another tab or device)
  shows "Still generating…" and refreshes until it settles. Any image you
  attach stays visible to the model for follow-up questions, including after a
  reload.
- **Model providers.** Native Ollama plus any OpenAI-compatible server (vLLM,
  OpenRouter, LiteLLM), configured in `.env` (`INFERENCE_PROVIDERS`). The
  model picker groups models by provider and shows what each model can do.
- **Stage 0.** The open document is searched before the model runs, and the
  reply shows "Used N passages from …"; the time is in the prompt.
- **Admin console: model providers at a glance.** The Deployment config
  section now lists every configured model provider (name, kind, host, and
  either a model count or "unreachable"); API keys are never shown. The old
  "Ollama URL" row is now "Embeddings (Ollama) URL", since that's the only
  thing it still configures.

### Fixed

- **Project owners can read documents members file into their project.** The read
  check only looked at `project_members`, and owners never get a membership row.
  "Can read" is now one SQL definition shared by every read path.
- **A saved model id whose provider was renamed is now migrated instead of silently
  mismatched.** The model picker used to show its first option while still sending
  the stale id underneath, which a provider rejects. The server also now refuses a
  model that isn't in its provider's own model list (`422 model_not_allowed`, "This
  model isn't available.") instead of forwarding it and surfacing a provider error.
- **The embedding model no longer appears in the chat model picker.** Picking it
  always failed; `GET /v1/inference/models` now omits `EMBEDDING_MODEL`.
- **Stopping a reply the moment you send it no longer locks the chat.** A Stop
  (or closed tab) before the reply started streaming used to leave the chat
  refusing new messages ("A reply is still being written in this chat.") for
  about a minute. The empty reply is now marked "Stopped" at once.
- **One stalled model provider no longer empties the model picker.** A provider
  that connects but never answers is now given 5 seconds to list its models,
  then reported unreachable; the other providers' models still show, and Send
  keeps working.
- **Prompts now fit strict chat templates (Gemma, Mistral and similar on
  vLLM).** The prompt has at most one system message, first, and user and
  assistant turns strictly alternate, as those templates require. Before, such
  a model rejected every chat from its second message with a provider error.
- **Long silent phases no longer get cut by a proxy.** While a tool runs or the
  model reads a long prompt, the server now sends a keep-alive every 15
  seconds, so a proxy's idle timeout (nginx 60 s, Cloudflare 100 s) doesn't end
  the reply early.
- **Replies that searched before answering are readable.** Text the model wrote
  before and after a tool call used to run together ("Let me search.The
  answer…"); each part is now its own paragraph.

### Changed

- **Breaking:** `PATCH /v1/docs/{id}` no longer accepts `project_id` (422), which
  also fails any tag change sent in the same request. `GET /v1/docs` and document
  status responses return `projects: [{id, name}]` (projects you can see; a doc's
  owner sees all of its links) instead of `project_id`/`project_name`.
- **Breaking: document upload is one multipart `POST /v1/docs`**, hashed by
  the server. The old register-then-send-client-chunks routes are gone; an
  old SPA gets a clear 404/405.
- **Breaking: `POST /v1/inference/chat` is removed.** Chat turns are
  `POST /v1/chat/sessions/{id}/turns` (server-sent events).
- **Breaking: `PUT /v1/chat/sessions/{id}` is removed.** The server writes
  messages itself; `POST /v1/chat/sessions/import` copies a legacy browser-only
  chat.
- **`GET /v1/inference/models`** returns objects `{id, provider, kind, name,
  capabilities}` instead of name strings.
- **Local mode (browser → Ollama directly) is removed.** Chat always goes
  through the server, which by default uses the same `OLLAMA_URL` as before.
- **Pins now sit near the top of the prompt** instead of just before your
  message, so providers can reuse cached work across turns.
- **The `current_time_date` tool is gone.** The current time is in every
  prompt.
- **`CHAT_PREFETCH_MIN_SCORE` default lowered from `0.75` to `0.6`**, measured
  against `nomic-embed-text`: at `0.75` the model missed the document answer
  and fell back to `web_search` every time in testing.
- **The daily token budget now charges stopped and failed replies too.** A
  reply you stop, or one that fails partway through, still counts against
  your daily token budget — the provider's own token count when it reported
  one, otherwise an estimate from the text length.

### Upgrade notes

- Migration `010` is the first migration that **drops data**
  (`documents.project_id`, after copying it into `project_documents`). The
  backend applies it automatically on its next start — not on merge. **Back up
  first**, e.g.
  `podman exec natural-reader-postgres pg_dump -U natural_reader natural_reader > natural_reader-pre-010.sql`
  (docker: same command with `docker exec`).
- **Deploy the SPA and the backend together** (rebuild + `deploy.sh` in the
  same window as the backend restart). Mixed versions lose data silently: an
  old SPA against the new backend gets a 422 on `PATCH /v1/docs` (the tags in
  that request are lost too); a new SPA against the old backend has no
  `PUT /v1/projects/{id}/docs/{doc_id}` route, so project links are silently
  dropped (logged to the browser console only).
- There is **no code rollback past 010** without restoring the backup.
- Migration `011` (content vs library entries) **drops `doc_grants` and some
  `documents` columns**: owners become upload entries, grants become shared
  entries. Back up first.
- Migration `012` marks every existing holding **unverified**. Right after the
  upgrade, **every document registered before 2.1 disappears from its holders'
  libraries, search and chat** (404); nothing is deleted. A holder gets it
  back by opening the file in the reader and pressing **Index** (that uploads
  it); the upload also restores the shares and project placements they made.
  Details: [docs/LIBRARY.md](docs/LIBRARY.md#upgrade-notes-migration-012).
- Migration `013` is additive: it runs on the next backend start. Back up
  first as usual.
- Deploy the SPA and backend together.
- A saved selection like `qwen2.5:7b` becomes `ollama:qwen2.5:7b`
  automatically.
- Proxies that buffer `text/event-stream` make replies appear all at once
  (see DEPLOYMENT.md).
- Audio attachments are still disabled in the UI (unchanged).

## [2.0.0] - 2026-09-23

Major release: Natural Reader becomes a **multi-user, authenticated, hosted
application**. Every API route now requires a principal (OIDC session or personal
access token); inference is brokered through a server-side gateway with per-user
budgets; documents gain a projects/grants sharing model with a browsable Library;
capabilities (reader/chat/admin) are Keycloak realm roles enforced end to end; and
the SPA is reorganized around a dedicated Settings page and a profile menu. The
API-auth change is breaking for pre-1.9.0 open deployments — see **Changed**.

### Added
- **Inference gateway (model router).** The SPA's chat no longer talks to Ollama
  directly — it goes through authenticated backend endpoints
  (`GET /v1/inference/models`, `POST /v1/inference/chat`) using the session cookie
  (or a PAT), so the previously unauthenticated browser→Ollama path is closed.
  The chat passthrough is **byte-faithful NDJSON streaming** (the SPA's tool loop,
  thinking-trace fallbacks, and image attachments are unchanged) behind a strictly
  validated request envelope — unknown fields are a 422, never a silent
  passthrough, and `/api/pull`/`/api/delete` & co. are unreachable through app
  auth. A new **Inference source: Server | Local Ollama** setting in the chat
  sidebar switches between the gateway (default) and the old direct mode.
  ([server/routers/inference.py](server/routers/inference.py), [src/lib/chatTransport.js](src/lib/chatTransport.js))
- **Model allowlist.** `INFERENCE_MODELS` (comma-separated) caps what the gateway
  serves — the SPA's model dropdown only offers allowlisted models and
  non-listed chat requests 422. Unset = all models (dev convenience). One config
  module (`server/services/model_router.py`) now owns every server-side model
  choice: chat allowlist, `SUMMARIZE_MODEL` (replaces `WEB_SEARCH_SUMMARY_MODEL`,
  which still works), and `EMBEDDING_MODEL`.
- **Per-user daily token budgets.** `INFERENCE_DAILY_TOKEN_BUDGET` sets a default
  daily prompt+eval token allowance (from Ollama's final-chunk real counts,
  UTC-midnight reset); over-budget requests get a 429 with remaining/reset detail
  that the SPA surfaces as a toast + a "N tokens left today" meter, and the send
  button disables at zero. Admins can override per user
  (`PATCH /v1/admin/users/{id}`) and view usage via
  `GET /v1/admin/inference/usage`. Aborted streams are never accounted; budget
  checks fail open if Postgres is down — chat never dies with the DB.
  (migration `007_inference_budgets.sql`, [server/services/inference_budget.py](server/services/inference_budget.py))
- **Multi-user auth (OIDC) + per-user data.** The backend is now multi-user:
  OIDC login (`/v1/auth/login|callback|logout|me`, any discovery-based provider,
  PKCE S256), JIT user provisioning (first login becomes the admin; others land
  `pending` until an admin activates them), DB-backed browser sessions and
  personal access tokens (`nrp_…`, sha256-at-rest, shown once — for the browser
  extension and scripts), per-user ownership of documents and chat sessions
  (other users' rows are 404s, not 403s), and admin user management
  (activate/disable/role; disabling hard-revokes sessions). The SPA is gated
  behind an auth screen per account state. Migrations `005`/`006`.
  Deep-dive: [docs/IDENTITY_AND_ROLES.md](docs/IDENTITY_AND_ROLES.md); user guide:
  [docs/USER_GUIDE.md](docs/USER_GUIDE.md).
- **Production deployment behind nginx + TLS.** New guide
  [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) covers serving the SPA + backend on one
  origin (`chat.example.com`) with Keycloak on its own (`auth.example.com`): two
  nginx vhost samples ([docs/sample_configs/chat.example.com.sample](docs/sample_configs/chat.example.com.sample),
  [docs/sample_configs/auth.example.com.sample](docs/sample_configs/auth.example.com.sample)), the five config
  values that must agree, and the traps (KC proxy headers, live-realm ≠ export,
  `COOKIE_SECURE`, `client_max_body_size`, stale QUIC). `docker-compose.yml` gains
  `KC_PROXY_HEADERS`/`KC_HOSTNAME` passthrough (empty by default) so Keycloak emits
  `https://` URLs and a matching `issuer` behind a TLS-terminating proxy. The two
  nginx samples now live together under `docs/`; the old
  `deploy/nginx/natural-reader.conf` is removed.
- **Local OIDC rig.** `./startup.sh up-with-dev-auth` runs a Keycloak container
  (realm `natural-reader`, seeded from `deploy/keycloak/realm-export.json`)
  whose realm state persists in the shared Postgres (schema `keycloak`,
  created on first volume init via `deploy/postgres/init/` — survives container
  recreation, so `sub` UUIDs and the app's `users.oidc_sub` links stay stable).
  Walkthrough: [deploy/README.md](deploy/README.md).
- **Security hardening** on the API surface: loopback-only default bind, strict
  `doc_id` (sha256 hex) validation blocking path traversal, SSRF guard on the
  web-search fetcher (public IPs only, re-checked per redirect hop), TTS request
  size caps, and startup guards that refuse insecure auth configs on
  non-loopback binds.
- **Capabilities: Keycloak realm roles enforced end to end.** Feature access is
  no longer a single `role` column — `reader`, `chat`, and `admin` are Keycloak
  realm roles mirrored into `users.capabilities` (migration `008`) and enforced
  deny-by-default on the feature routes (`require_capability`). The SPA reads
  capabilities from `/v1/auth/me`, gates the reader/chat/admin views on them, and
  re-probes on a `403 missing_capability`; an active user with no capabilities
  lands on a distinct "access not yet granted" screen rather than a broken app.
  Admins enroll users (creating the Keycloak user with an invite or one-time
  temp password, with a graceful fallback when SMTP is absent), edit
  capabilities, and disable/delete — all propagated to Keycloak, with a
  last-active-admin guard on every path that could remove the final admin and
  hard-fail (never fail-open) if the Keycloak write errors.
  ([server/services/keycloak_admin.py](server/services/keycloak_admin.py),
  [server/deps/capabilities.py](server/deps/capabilities.py),
  [src/components/admin/AdminConsole.jsx](src/components/admin/AdminConsole.jsx))
- **Document Library with a projects / grants sharing model (RAG Phase 0).**
  Documents move from strictly private to *shareable*: a document is readable by
  its owner, by members of a project it's assigned to, or by an explicit
  per-document grantee — writes stay owner-only, and non-readers still get a 404
  (never a 403 that would leak existence). New endpoints back it —
  `GET /v1/docs` (own / member / granted, with `q`/project/tag filters),
  `PATCH /v1/docs/{id}` (tags, project assignment, admin reassignment), owner-only
  project CRUD, project members, and per-doc read grants — with IDOR guards on
  cross-tenant project assignment. The SPA gains a **Library** page: list, search,
  project/tag filters, share indicators, and optional project + tags on
  upload/register. Migration `009` adds `projects`, `project_members`,
  `doc_grants`, and `documents.project_id`/`tags`. Access model:
  [docs/LIBRARY.md](docs/LIBRARY.md).
  ([server/routers/docs.py](server/routers/docs.py),
  [server/routers/projects.py](server/routers/projects.py),
  [src/components/library/LibraryPage.jsx](src/components/library/LibraryPage.jsx))
- **Consolidated Settings page + profile menu (app shell).** Voice & reading,
  chat & inference (with a per-model inference selector), connection, appearance,
  and account (personal access tokens) settings — previously scattered across the
  reader sidebar and chat sidebar — now live on one dedicated **Settings** view
  reachable from a header gear button and a new profile menu (identity, Settings,
  dark-mode toggle, log out). The reader sidebar keeps navigation only; the chat
  sidebar keeps the model picker and sessions. The chat composer draft also moved
  up to `App`, so it now survives switching tabs.
  ([src/components/settings/SettingsPage.jsx](src/components/settings/SettingsPage.jsx),
  [src/components/ProfileMenu.jsx](src/components/ProfileMenu.jsx))

### Changed
- **Breaking: every API route now requires authentication.** Pre-1.9.0 the
  backend was open; now `/v1/*` (TTS, docs, chat sessions, tools, inference)
  resolves a principal from the session cookie or a Bearer PAT and denies by
  default. Deployments that relied on the open API must set `AUTH_ENABLED=false`
  (loopback only) or provision tokens. The SPA is same-origin with the backend
  (blank `apiHost`) — the only cookie-compatible setup; a stale `localhost`
  value is migrated away on boot.
- **nginx: delete the `/api/` block.** With the gateway live, Ollama becomes
  backend-only (loopback bind, nothing proxied). The reference configs
  (`deploy/nginx/natural-reader.conf`, `docs/sample_configs/chat.example.com.sample`) and the
  README example have dropped it; `proxy_buffering off` now matters on `/v1/`
  (that's where NDJSON streams).
- Server-side summarize (web_search) and embedding calls route through
  `model_router` instead of reading `OLLAMA_URL`/model env vars in each service.

### Fixed
- **Reader toolbar collapses into a "⋯" menu on mobile instead of running
  off-screen.** The PDF options bar was a single non-wrapping row, so on a phone
  the wide right-hand cluster (Fit/Width, Ask-page, and the converted-doc
  view/export controls) was pushed past the screen edge and became unreachable.
  Below the `md` breakpoint those secondary actions now collapse into a compact
  "⋯ More" dropdown (`PdfToolbarMenu`, mirroring the header's overflow menu); the
  bar also wraps as a fallback and uses tighter mobile padding. Desktop keeps the
  full inline toolbar unchanged. ([src/components/PdfViewer.jsx](src/components/PdfViewer.jsx),
  [src/components/PdfToolbarMenu.jsx](src/components/PdfToolbarMenu.jsx))
- **Enroll and delete buttons now show a busy state while their request runs.**
  Clicking "Enroll" (admin console) or "Confirm delete" (library) fired an async
  request with no visible acknowledgement — it felt like the click hadn't
  registered, and the button could be clicked again mid-flight. Both now disable
  and show a spinner ("Enrolling…" / "Deleting…") until the request settles,
  which also blocks accidental double-submits.
  ([src/components/admin/AdminConsole.jsx](src/components/admin/AdminConsole.jsx),
  [src/components/library/LibraryPage.jsx](src/components/library/LibraryPage.jsx))
- **Chat draft lost when switching tabs.** The SPA has no router — each view
  (reader/chat/library/admin) fully unmounts the others — and the chat composer's
  in-progress text lived in `ChatView`'s local state, so leaving chat and coming
  back discarded a half-typed message. The composer's draft (and pending image
  attachments) now live in `App`; the text draft is persisted (it survives a
  reload too), while attachments are kept in memory to avoid packing base64 blobs
  into `localStorage`. ([src/components/ChatView.jsx](src/components/ChatView.jsx),
  [src/App.jsx](src/App.jsx))
- **Founder lockout on a fresh Keycloak realm ("account is active but has no
  access yet").** Capabilities are Keycloak realm roles mirrored into
  `users.capabilities`, and login sync treats Keycloak as authoritative — so a
  founder whose Keycloak user had **no realm roles** was wiped to zero
  capabilities on login and locked out, with the seed-admin bootstrap unable to
  save them: setting `BOOTSTRAP_ADMIN_EMAIL` routes the founder through the
  email-claim path (no capability floor) instead of the first-login force-grant,
  and the app's self-heal that re-assigns the roles in Keycloak is a no-op unless
  `KC_ADMIN_CLIENT_ID`/`KC_ADMIN_CLIENT_SECRET` are set. The checked-in
  `deploy/keycloak/realm-export.json` now assigns `admin-user` the
  `reader`/`chat`/`admin` realm roles **directly** (applied on a fresh import),
  and the trap + a recovery runbook are documented in `docs/DEPLOYMENT.md`
  (Trap 4), `docs/IDENTITY_AND_ROLES.md`, and `.env.example`. No code change —
  the fix is realm config + operator guidance.
- **OIDC callback 500 on an IdP error redirect.** When Keycloak bounced back to
  `/v1/auth/callback` with `error=...` instead of a `code` (an expired auth flow,
  a cancelled login, a denied consent), Authlib raised `OAuthError` and it
  surfaced as an unhandled **500**. The callback now detects an `error` param and
  catches `OAuthError` around the token exchange, redirecting to the SPA (which
  re-probes `/auth/me` → 401 → login) instead. ([server/routers/auth.py](server/routers/auth.py))
- **Logout "Invalid redirect uri".** The `natural-reader` client's
  `post.logout.redirect.uris` in `realm-export.json` was space-delimited;
  Keycloak splits that attribute on `##`, so both URIs were parsed as one bogus
  value and `post_logout_redirect_uri=https://chat.example.com/` matched nothing.
  Fixed the delimiter; documented the login-vs-logout allow-list drift (a live
  realm still needs the URI added by hand — `--import-realm` only seeds a fresh
  DB) in `docs/DEPLOYMENT.md` Trap 2.
- **Library view bounced back to reader.** `useViewModeGuard` permitted a view
  only when the user held a capability of the same name — but Library is
  intentionally ungated (no `library` capability), so opening it was coerced to
  the first held capability view. The guard now recognizes capability-free views
  (`CAP_FREE_VIEWS = ['library']`) and leaves them alone.
  ([src/hooks/useViewModeGuard.js](src/hooks/useViewModeGuard.js))

## [1.9.0] - 2026-08-10

### Added
- **Web search (`web_search` tool).** The chat model can now search the live web
  through a self-hosted **SearXNG** instance: for each result the backend fetches the
  page, extracts the readable text with trafilatura, and summarizes it with a small
  model (`llama3.2:3b` by default), returning per-result summaries the chat model
  synthesizes and cites. Fan-out is bounded by a semaphore like the embedding
  pipeline, and a page that fails (timeout, paywall, blocked) degrades to its search
  snippet rather than dropping. The endpoint is synchronous (a tool call can't poll)
  and **SSRF-guarded** — only `http`/`https` to hosts resolving to public IPs, every
  redirect hop re-checked, so a result can't point the fetcher at your internal
  network or a cloud metadata endpoint. SearXNG runs from `docker-compose` bound to
  `127.0.0.1:18043` (API-only), and `./startup.sh up` bootstraps its config on first
  run. New env: `SEARXNG_URL`, `WEB_SEARCH_*` (see `.env.example`).
  ([server/services/web_search.py](server/services/web_search.py), [server/routers/tools.py](server/routers/tools.py), [src/lib/chatTools/webSearch.js](src/lib/chatTools/webSearch.js))
- **File logging.** The backend now writes to a size-rotating `logs/server.log`
  (10 MB × 5 backups) alongside the console, and finally wires up the app's own
  loggers so `INFO` messages surface instead of being swallowed by the missing
  handler. Tunable via `LOG_LEVEL`, `LOG_DIR`, `LOG_FILE_MAX_MB`, `LOG_FILE_BACKUPS`;
  `logs/` is gitignored. ([server/logging_config.py](server/logging_config.py))
- **Per-model Ollama inference settings.** A new `Inference` block in the chat sidebar
  exposes four request parameters — **context window** (`options.num_ctx`), **keep-alive**
  (`keep_alive`, including *Always* = `-1`), **thinking level** (`think`), and **max reply
  tokens** (`options.num_predict`) — stored per model name, because a 9.7B and a 3B want
  different context sizes on the same machine. The thinking control replaces the old
  boolean toggle with five states: `Off`/`On` send booleans (identical to previous
  behaviour, and the old `enableThinking` preference migrates into it automatically),
  while `Low`/`Medium`/`High` send Ollama's graduated levels. A model that accepts the
  boolean but rejects a level is retried once with plain thinking rather than losing it.
  Every setting defaults to **unset**, which omits the key from the request entirely — so
  installing this changes nothing until you opt in. That is deliberate: Ollama already
  sizes context from available memory (4k under 24 GB, 32k at 24–48 GB, 256k above), and a
  hardcoded default would *downgrade* well-equipped machines. All of it is per-request, so
  no `OLLAMA_CONTEXT_LENGTH`, no systemd override, and no daemon restart is needed.
  ([src/hooks/inference.js](src/hooks/inference.js))
- **Context meter + truncation warning.** A `~3.3k / 16k ctx` estimate sits above the
  composer (amber past 75% of the window; no denominator when the window is left on Auto),
  and a cut-off reply now raises a toast naming the real token counts.

### Changed
- **SearXNG is bound to `127.0.0.1` and API-only.** The search container publishes on
  localhost instead of all interfaces (it's an unauthenticated meta-search), on a
  non-default port (`18043`), and its config drops the HTML output format — leaving
  only the JSON API the backend uses.

### Project
- Added an **MIT `LICENSE`** (the project had none) and a **`CONTRIBUTING.md`** plus a
  Contributing section in the README, making the terms of use and how to contribute
  explicit.

### Fixed
- **Silently truncated replies.** A reply could stop mid-sentence with the only evidence
  being `done_reason: length` inside a collapsed stats disclosure. The cause was that the
  chat engine never sent an `options` object at all, so Ollama applied its own default
  context window — a real case hit prompt 3317 + generation 779 = exactly 4096. The window
  is now configurable and exhaustion is surfaced, on both the initial response and the
  tool follow-up (whose history is strictly larger, and so more likely to overflow).

## [1.8.3] - 2026-08-09

### Added
- **Pinned chat context:** "Ask AI" on a selection (or "Ask page") now *pins* the
  excerpt to the conversation — it stays attached across follow-ups instead of
  being sent once. Multiple pins accumulate as removable chips, are saved with the
  chat session (restored on reload), and are bounded (max 6 pins / 12k chars).
  Whole-document breadth continues to come from semantic retrieval.
- **Chat tool scaffold.** Frontend tool-calling gained a `current_time`/date tool and
  the `web_search` tool stub, plus a refactor of the tool response structure that the
  1.9.0 backend builds on.

### Fixed
- Ask-AI context is no longer stranded at the front of a multi-turn chat (the model
  previously reported "no content attached" on follow-up questions).

## [1.8.0] - 2026-07-28

### Added
- **Chrome "Read Aloud" extension (`extension/`, v0.1).** A Manifest V3 browser extension that reads the selected text or the whole page aloud on any site through the same local Kokoro TTS backend the app uses (`http://localhost:8000`). Vanilla JS, **no build step** — load `extension/` unpacked. A floating Shadow-DOM toolbar (play/pause, stop, seek, voice, speed, drag, close) is injected on demand via the right-click menu or the popup; whole-page reads are split into ~30-sentence chunks and played back-to-back with prefetch-one-ahead, so audio starts after the first chunk instead of the whole page. Includes a popup (read buttons + backend status dot) and an options page (default voice, speed, backend URL, test-connection). Loopback-only host permissions; page/selection text is rendered `textContent`-only (XSS-safe); the backend is unchanged. Pure `shared/` modules are unit-tested (20 tests); built TDD via subagent-driven development. See [extension/README.md](extension/README.md); design spec + plan in [docs/superpowers/](docs/superpowers/).
- **Markdown folder workspace.** Open a folder (File System Access API, with a `webkitdirectory` picker fallback) so a Markdown/text document's cross-file links actually navigate: relative `.md`/`.txt` links open the target in the reader, `#heading` anchors scroll to the heading (via `rehype-slug` ids), and relative images render from the folder. Adds Back/Forward history with a header workspace badge; workspace state is persisted in IndexedDB (new v4 store) and restored on load (File System Access re-permission prompt, or a re-pick for the snapshot fallback). Link hrefs pass through a safe-scheme allowlist that rejects `javascript:` / `data:` / other dangerous schemes. New Vitest + Testing Library test infrastructure underpins it. ([src/lib/workspace.js](src/lib/workspace.js), [src/lib/WorkspaceContext.jsx](src/lib/WorkspaceContext.jsx), [src/components/WorkspaceLink.jsx](src/components/WorkspaceLink.jsx), [src/utils/resolvePath.js](src/utils/resolvePath.js))

### Changed
- **`startup.sh` modernised into a real lifecycle script.** Rewritten with `set -euo pipefail`, container-engine auto-detection (docker / podman), and pre-flight **version checks** (Node `≥20.19` or `≥22.12`, Python `3.10–3.13`) before it runs anything. Model downloads are now **atomic and idempotent** — the Kokoro ONNX model and voice pack are fetched to a `.partial` temp and moved into place only on success, and already-present files are skipped instead of re-downloaded. `up` starts the `run.py` backend under a pidfile; `down` — and Ctrl-C during `up` — **SIGTERM** the backend and bring the containers down cleanly via a trap (engine-aware, so nothing is left running). The README gained a tiered **Software Requirements** section.

### Caveats / known limitations
- **Extension site compatibility.** The extension works on ordinary pages (Wikipedia, blogs, docs) but can fail on sites with a **strict Content-Security-Policy** (e.g. `claude.ai`) — the content script's dynamic module import gets blocked and a small "could not start on this page" notice shows instead of the toolbar. It also only runs in the **top page**, so text inside a **sandboxed iframe** (e.g. an embedded artifact) reports "nothing to read", and the localhost fetch from an HTTPS page may be gated by Chrome's **Private Network Access** depending on Chrome version. These are documented in [extension/README.md](extension/README.md#site-compatibility-known) and slated for a follow-up (route synthesis through the service worker).
- **No highlight-follow** in the extension (no word-by-word highlight), and **loopback backend only** (`localhost` / `127.0.0.1`) — remote/hosted backends are not yet supported.

## [1.7.3] - 2026-05-23

### Fixed
- **PDF canvas went blank after toggling Reader ↔ Chat.** Toggling to Chat unmounts `PdfViewer`; toggling back mounts a fresh `<canvas>` DOM node, but the render `useEffect` only re-fired when `[pdfDoc, currentPage, scale, fileType]` changed — none of which moved on a view-mode toggle — so the new canvas stayed empty until the user manually stepped the page. Fixed by exposing `canvasRef` from `usePdfEngine` as a callback ref: every time the `<canvas>` attaches, a `canvasMountNonce` state bumps and the render effect re-paints the current page against the fresh node. ([src/hooks/usePdfEngine.js](src/hooks/usePdfEngine.js))

## [1.7.2] - 2026-05-23

### Fixed
- **Tablet header was cut off.** The secondary-action buttons hid at `sm:` (640 px) and the inline group did not have enough room between 640 px and 1024 px — the buttons that didn't fit were just clipped. Inline buttons now hide at `lg:` (1024 px) instead, and the `⋯` overflow menu activates `lg:hidden` so phones AND tablets / squeezed desktop windows reach every action through the dropdown. Full desktop (≥ 1024 px) layout is unchanged. ([src/components/Header.jsx](src/components/Header.jsx), [src/components/HeaderOverflowMenu.jsx](src/components/HeaderOverflowMenu.jsx))
- **Floating "Read Selection" / "Ask AI" buttons overlapped the mobile bottom nav.** Their `bottom-6` position sat directly on top of `MobileBottomNav`, so taps on the next-sentence / play buttons were stealing the floating-button area. Bumped to `bottom-24 right-4 md:bottom-6 md:right-6` so phones get clearance over the nav and desktop is unchanged. ([src/components/overlays/ReadSelectionButton.jsx](src/components/overlays/ReadSelectionButton.jsx))

### Changed
- **Distraction-free mode keeps basic playback controls visible.** The earlier bare "Exit" pill was too minimal for actual reading sessions. New floating pill at bottom-center carries **prev sentence / play-pause / next sentence**, a clickable **page-number input** (jump pages without leaving the mode), and **Exit**. In chat mode or with no document open, the pill collapses to just Exit. `F` still toggles the mode. New component [src/components/DistractionFreeBar.jsx](src/components/DistractionFreeBar.jsx).

## [1.7.1] - 2026-05-23

### Fixed
- **Chat session reload — messages appeared in the wrong order.** The save path bulk-inserts every message in one transaction (same `created_at`), and the user prompt + the empty assistant placeholder are created in the same `Date.now()` tick (same `timestamp`). The final tiebreak fell to `id`, where `a-<ts>` sorts before `u-<ts>` lexicographically — producing the `[response, query, response, query]` pattern. Fixed in two layers: the SELECT now adds a role-based `CASE` between `timestamp` and `id` (`user` before `assistant`) so existing saved sessions read back correctly without a migration, and `sendMessage` bumps the assistant's `timestamp + id` suffix by 1 ms so new pairs are monotonic without depending on the tiebreak. ([server/routers/chat_sessions.py](server/routers/chat_sessions.py), [src/hooks/useChatEngine.js](src/hooks/useChatEngine.js))

### Added
- **Distraction-free reading mode.** Press `F` (or click the new `Maximize2` button in the header) to hide the Header, sidebar, mobile bottom nav, and the PDF toolbar — leaving just the document content and a small floating **Exit** pill in the top-right. Persisted across reloads. Listed in the keyboard-shortcuts modal. Works in both reader and chat. ([src/App.jsx](src/App.jsx), [src/hooks/useKeyboardShortcuts.js](src/hooks/useKeyboardShortcuts.js))
- **Mobile overflow menu (`⋯`) in the header.** Several actions were `hidden sm:*` and simply vanished on phones (dark mode toggle, TTS-backend switch, page-audio download, audiobook export, keyboard shortcuts, home, distraction-free). They're now all reachable through a small dropdown rendered only on small screens (`sm:hidden`). Desktop layout unchanged. ([src/components/HeaderOverflowMenu.jsx](src/components/HeaderOverflowMenu.jsx))
- **Audiobook export — parallel page synthesis.** The page-by-page loop in `downloadBookAudio` now runs up to 3 page-synth requests in flight at once via a small worker-pool pattern. Pages are slotted by index so the final WAV concatenation preserves document order even though synthesis completes out-of-order. Cancel still works mid-pool. ([src/hooks/useTtsEngine.js](src/hooks/useTtsEngine.js))
- **Multi-worker uvicorn support.** `run.py` honours a new `WORKERS=N` env var (also `HOST` and `PORT`). With one worker the audiobook pipelining is a small overlap win; with N workers (each loading its own Kokoro model) it actually fans out across CPU cores so audiobook jobs scale roughly linearly. Trade-offs (RAM per worker, Postgres pool sizing, GPU caveat) documented in [docs/CHAT_WITH_PDF.md](docs/CHAT_WITH_PDF.md) §10.

### Caveats
- **Multi-worker uvicorn loads one Kokoro model per worker** (~300–500 MB on the ONNX-CPU build). Budget RAM accordingly; on a 4 GB host stick to 1–2 workers.
- **Postgres pool is per-process** — default `max_size=10` × N workers = up to 10 N connections. Default Postgres `max_connections` is 100, so re-tune above ~9 workers.
- **The mobile overflow menu only renders below the `sm` breakpoint** (640 px). It's deliberately invisible on tablets / desktops where the inline buttons handle the same actions.

## [1.7.0] - 2026-05-23

### Added
- **Docling PDF → Markdown conversion** — new backend pipeline that converts PDFs into layout-aware Markdown using [Docling](https://github.com/DS4SD/docling) (MIT, IBM/DS4SD). Click **Convert** in the PDF toolbar to open an options dialog with a **Fast / Standard / Accurate** quality preset (the Accurate path uses the VLM `granite_docling` pipeline when available), an OCR force-toggle for scanned PDFs, table extraction toggle, image handling (drop / embed-base64 / VLM describe), and an optional page-range limiter. After conversion the doc is automatically re-chunked and re-embedded from the cleaner Markdown text so retrieval picks up tables and headings the native pdf.js extractor was missing.
- **PDF ↔ Markdown reader toggle** — once a doc is converted, the PDF toolbar gains a small **PDF | MD** segmented control. **MD** swaps the PDF canvas for a new `MarkdownReader` that renders the docling output per page with anchored separators, syncs the toolbar's page number with whichever page is in view, and lets TTS / "Read selection" / "Ask AI" work on the cleaner text. Reader view is per-doc and persists across navigation.
- **Export converted Markdown** — Download icon in the toolbar (visible when `conversion_state='converted'`) saves the full document MD as `{filename}.md` via blob download.
- **Delete converted Markdown** — Trash icon (with confirm prompt) wipes `doc_pages` + the chunks/embeddings derived from them and resets `conversion_state` so the **Convert** button reappears. The retained PDF on disk stays in place so reconversion is one click.
- **Retained PDF on the server** — `POST /v1/docs/{id}/pdf` (multipart, ≤ `PDF_UPLOAD_MAX_MB`, default 50 MB) parks the bytes in `./data/pdfs/{doc_id}.pdf` so reconversion with different settings doesn't require a re-upload. New `DELETE /v1/docs/{id}/pdf` removes them. Deleting the document row cleans up the file automatically.
- **Per-message audio export in chat** — every assistant chat message gets an **Audio** action button (next to Read aloud / Copy). Click it to synthesize the whole message through Kokoro and download a `.wav` named after the active session title + assistant-message index (e.g. `Session Title_msg2.wav`). Shows an inline spinner while generating; only one message synthesizes at a time. Hidden when Kokoro isn't selected.
- **Audiobook export — full document as a single WAV** — new **Library** icon in the header (reader mode + Kokoro only) extracts every page's text and synthesizes them one page at a time via `/v1/batch_synthesize`, stitches the WAVs client-side into one file, and downloads `{filename}_audiobook.wav`. While exporting, the button morphs into a progress card showing `current/total` + the current label ("Synthesizing page 14…") with an **X** to cancel. Failed pages are skipped with a toast at the end instead of aborting the whole job. New client-side WAV concatenator (`src/utils/wavConcat.js`) parses RIFF chunks, reuses the first file's header, concatenates `data` payloads, and rewrites RIFF + `data` sizes.
- **Home button to return to the library** — new Home icon in the header (reader mode, doc open) stops TTS, drops the loaded document, clears the active doc id, and brings up the welcome screen with the recent-books list. Reading progress was already persisted, so reopening the doc resumes where you left off.

### Changed
- **MarkdownReader and MarkdownPageRenderer width** widened from `min(820px, 80vw)` to `min(1200px, 95vw)` so MD prose, tables, and code blocks have room to breathe on wide screens.
- **MarkdownReader no longer maintains an inner scroll container** — scrolling and page-nav scroll happen via the existing PdfViewer outer scroller. Combined with a small "programmatic-scroll quiet window" (`PROG_SCROLL_QUIET_MS = 800`), this fixes the feedback loop where a "next page" click was getting overridden mid-scroll by the IntersectionObserver. The observer now uses the viewport (`root: null`) and respects the quiet window.
- **Server schema** — new migration `server/sql/003_docling.sql` adds `documents.pdf_path`, `documents.conversion_state`, `documents.conversion_options` (JSONB), `documents.conversion_error`, `documents.converted_at`, and a new `doc_pages` table (`doc_id`, `page`, `markdown`). `_fetch_doc_status` now returns `conversion_state` / `converted_page_count` / `conversion_options` / `has_pdf` so the frontend can mirror the full lifecycle. Stale `conversion_state='converting'` rows are reset on startup the same way stale `state='indexing'` already was.
- **`_run_index_job` lock** renamed from `_index_locks` to `_doc_job_locks` and shared with `_run_convert_job` since they touch the same `doc_chunks` rows. The old `_get_index_lock` / `_index_locks` names remain as aliases.
- **Python deps** — adds `docling>=2.0` and `python-multipart` to `requirements.txt`. Docling pulls in transformers + torch (CPU); first conversion downloads layout/table model weights (~500 MB to ~2 GB total). Gate the feature with `DOCLING_ENABLED=true` env var so the rest of the server boots without the heavy stack installed.
- **Persistent storage** — `./data/pdfs/` is created on app startup and added to `.gitignore`. Set `PDF_STORAGE_DIR` to override.
- **ESLint config** — `globalIgnores` now also covers `.venv` and `node_modules` so vendored minified JS inside the Python virtualenv (e.g. the `mpire` dashboard's bundled bootstrap.js) doesn't pollute lint output.

### Fixed
- **MD-file zoom & toolbar controls now feel responsive** — the box is much wider, page next/prev actually scrolls to the requested page in the converted MD view, and the observer no longer fights programmatic scrolls.

### New endpoints
- `POST /v1/docs/{doc_id}/pdf` — multipart upload, persists raw PDF bytes for later reconversion.
- `DELETE /v1/docs/{doc_id}/pdf` — remove retained PDF bytes (keeps markdown + chunks).
- `POST /v1/docs/{doc_id}/convert` — `202 Accepted`; kicks off a docling background job with the supplied options.
- `GET /v1/docs/{doc_id}/markdown` (optionally `?page=N`) — returns the converted Markdown as `text/markdown`.
- `DELETE /v1/docs/{doc_id}/markdown` — wipe converted markdown + derived chunks/embeddings; doc row + retained PDF stay in place.

### Caveats
- **Docling conversion is heavy.** Standard preset on a 50-page text PDF takes a few minutes on CPU; Accurate (VLM) is slower still. The Convert button polls every 2 s for up to 20 min before warning the user. Cancel isn't wired yet — kill the backend if you need to abort.
- **Audiobook export is single-threaded** because kokoro-onnx is. A long book may take many minutes; the rest of the app stays usable while it runs.
- **WAV concat assumes identical PCM format across pages.** Since every page in a single export uses the same voice + speed, that holds. Don't change voice mid-export.
- **Page-range applies to conversion only**, not RAG retrieval. Search still returns top-K across all converted pages.
- **Reconverting a doc** wipes `doc_pages` then `doc_chunks` then re-embeds. The PDF on disk stays.

## [1.6.0] - 2026-05-16

### Added
- **Document Chat & RAG end-to-end** — five-PR arc that lets the local LLM see, search, and autonomously query the document you're reading. Full walkthrough at [docs/CHAT_WITH_PDF.md](docs/CHAT_WITH_PDF.md). Headline bits below.
- **Postgres + pgvector backend (PR 1)** — `docker-compose.yml` brings up `pgvector/pgvector:pg16` on host port 5433. New `server/db.py` runs an async `psycopg` pool with retry/backoff, applies file-based SQL migrations (`server/sql/001_init.sql`, `002_tool_calls.sql`) on startup, and resets any stale `state='indexing'` rows to `chunks_uploaded` so a crash mid-job is recoverable. Pool registers the pgvector codec per-connection so Python lists bind as `vector(...)`.
- **Chat sessions move to Postgres (PR 1)** — five new tables (`chat_sessions`, `chat_messages`, `chat_events`, `documents`, `doc_chunks`) and a `schema_migrations` version table. New endpoints: `GET/PUT/PATCH/DELETE /v1/chat/sessions/{id}`, `GET /v1/chat/sessions`. New `src/lib/sessionStore.js` abstraction merges Postgres (new) and IndexedDB (legacy read-only) under one API; writes always target Postgres, and editing a legacy IDB session forks to a fresh pg session leaving the original intact. Legacy IDB chats show with a small amber **LOCAL** badge in the sidebar.
- **Per-message doc context (PR 2)** — new toolbar **Ask page** button (sends the current page text, capped ~8000 chars) and new floating **Ask AI** action paired with the existing **Read Selection** button (sends the highlighted snippet). Both prefill a removable `DocContextChip` above the chat input and land in `useChatEngine.sendMessage(text, attachments, docContext)` as a synthetic system preamble. The chip persists on user messages so reloading a session re-renders the chip. Lazy sha256 of file bytes via Web Crypto (`src/utils/docHash.js`) gives every doc a stable `doc_id` independent of filename.
- **Manual document indexing (PR 3)** — `IndexButton` in the PDF toolbar runs `POST /v1/docs` (register) → `POST /v1/docs/{id}/chunks` (batches of 50) per file type: one chunk per PDF page, one per Markdown block (code blocks skipped), one per TXT pseudo-page. Idempotent on `(doc_id, text_hash)` so re-indexing is safe. New `extractAllChunks()` helper in `usePdfEngine.js` handles all three formats.
- **Embeddings + semantic retrieval (PR 4)** — `server/services/embeddings.py` wraps Ollama's `/api/embeddings` with `Semaphore(4)` concurrency and a 768-dim assertion (hard-locked to `nomic-embed-text` for v1). `POST /v1/docs/{id}/index` runs a FastAPI `BackgroundTasks` job under a per-doc `asyncio.Lock`, embeds chunks 16 at a time, and flips the doc state to `indexed` or `failed`. `POST /v1/docs/{id}/search` returns top-k chunks by cosine similarity (HNSW). Frontend polls status every 2 s (300-poll / 10-min cap). New **"Use whole document"** checkbox on the doc-context chip folds top-3 retrieved chunks into a second system preamble with bracketed `[1] (page 5) …` citations the model is told to use.
- **Autonomous tool calling (PR 5)** — once a doc is indexed and a tools-capable chat model is selected, the model gets a `search_document` tool in every `/api/chat` request and can decide on its own whether to invoke it. Tool-call loop is frontend-orchestrated (no backend changes): Ollama returns `message.tool_calls`, the frontend executes against `/v1/docs/{id}/search`, then re-POSTs `/api/chat` (without `tools`) so the model produces the final text answer. Single round-trip cap to prevent loops. Models without tool support silently ignore the field. Thinking + tools fallback: on a 4xx from the initial request, retry once without `tools` (keep thinking on) and toast once per session. A new transient `toolStatus` indicator ("🔄 Searching document…") shows on the streaming bubble, and a collapsible **🔎 ToolCallsDisclosure** mirrors the existing stats disclosure.
- **Pluggable tool registry** — `src/lib/chatTools/` houses one tool per file; each exports `{name, definition, when(ctx), execute(args, ctx)}` and the registry filters by `when(ctx)` on every send. Adding `web_search`, `read_url`, etc. later is one new file + one registry-index line. `src/lib/chatTools/_example.js` documents the shape end-to-end.
- **Persisted tool calls** — new migration `server/sql/002_tool_calls.sql` adds a `tool_calls JSONB` column on `chat_messages`. Each invocation is stored as `[{name, arguments, result_summary}]` so the 🔎 disclosure re-renders correctly on session reload.
- **docker-compose service** for Postgres so first-run is a single command.

### Changed
- **Python deps** — adds `psycopg[binary,pool]>=3.2`, `pgvector>=0.3`, `httpx>=0.27` to `requirements.txt`. Kokoro / TTS deps unchanged.
- **`useChatEngine` API** — now accepts `apiHost`, `apiPort`, `currentDocId`, `currentDocIndexState`. `sendMessage` gained a third `docContext` argument. Backward-compatible — old callers pass `null` and get pre-1.6 behavior.
- **`server/app.py`** — mounts two new routers (`chat_sessions`, `docs`), starts/stops the embedding HTTP client + DB pool around startup/shutdown.

### Caveats
- **Embedding dimension is hard-locked at 768** (`nomic-embed-text`). Switching models requires dropping `doc_chunks.embedding` and re-indexing every document. Per-model column sharding is on the roadmap; not in scope for 1.6.
- **Tool calling requires a tools-capable Ollama model** (qwen2.5, llama3.1+, mistral, gemma2, …). Other models still chat fine — the `tools` field is silently dropped server-side.
- **Backend down** still doesn't break chat: Ollama is independent, so streaming + TTS keep working. Only Postgres-dependent calls (session save, document register/search) return 503, with a one-time toast on the first failure.
- **Multi-iteration tool calls** are not supported — PR 5 caps at a single round-trip to prevent runaway loops. Multi-step agent workflows need budget/loop controls before they're safe.
- **No-chip retrieval** isn't wired — the "Use whole document" toggle only appears when a chip is attached. Autonomous tool calling (PR 5) covers the same ground without manual setup; a manual no-chip toggle stays on the deferred list.

## [1.5.1] - 2026-05-10

### Added
- **Markdown (`.md`) reader support** — drop or pick a `.md` / `.markdown` file and it joins the existing library alongside PDFs and `.txt` files. Renders properly via `react-markdown` + `remark-gfm` (headings, lists, code blocks, tables, blockquotes) instead of showing raw `**bold**` / `# heading` markup.
- **Paragraph-level highlight while reading** — the block (paragraph, list, heading, table, blockquote) containing the currently-spoken sentence gets a subtle background tint and auto-scrolls into view. Pagination is paragraph-aware: pages cap at ~40 sentences but always end on a block boundary. Block detection is AST-driven (`mdast-util-from-markdown` + GFM) so the highlight aligns with what react-markdown actually renders, including non-blank-separated headings and HRs. Offset-based block lookup keeps alignment correct under arbitrary nesting (e.g. nested lists inside list items).
- **Markdown-aware TTS for the reader** — sentences are passed through `mdast-util-to-string` per block before being handed to the TTS engine, so `**bold**` reads as "bold" (not "star star bold star star"), `[link text](url)` reads as "link text", and fenced code blocks are skipped entirely.
- **Library tile for markdown** — `.md` files in the recents list show a `FileCode` icon with a purple gradient to distinguish them from `.txt` (teal) and `.pdf` (blue).

## [1.5.0] - 2026-05-10

### Added
- **Image attachments in chat** — paperclip button, drag & drop onto the chat view, and `Ctrl + V` paste from clipboard. Files preview as thumbnails above the prompt and inside user bubbles after sending; attached images are sent to Ollama via the per-message `images: [base64]` field. Size cap 10 MB per image.
- **Same-origin / reverse-proxy support** — leaving the **Host** field blank in either sidebar now issues requests as relative URLs (`/v1/synthesize`, `/api/chat`, …) so an nginx/Caddy front can serve the frontend and proxy both backends from a single hostname. New `src/utils/url.js` helper centralizes URL construction and accepts bare hosts, `http(s)://`-prefixed hosts, or no host at all.
- **Model response stats footer** — every assistant bubble has a small collapsible `⚡ N tok · X.X s · Y.Y tok/s` line. Expanded view shows model name, total time, load time, prompt eval (tokens + time), generation (tokens + time + throughput), and `done_reason` when non-trivial. Pulled directly from Ollama's final NDJSON chunk.
- **Stick-to-bottom scroll** — chat list auto-tails new tokens only when the user is already near the bottom (within 80 px). Scrolling up pauses the auto-tail so you can read history during a long stream; returning to the bottom resumes it.
- **Reverse-proxy production deployment docs** — README now includes a worked nginx example covering both `/v1/*` and `/api/*`, plus notes on `proxy_buffering off` for streaming, Ollama Host-header allowlist (override or `OLLAMA_ORIGINS`), and serving `.mjs` files with the right MIME type so Firefox can load PDF.js's worker.
- **Security & Hardening section in README** documenting the threat model for client-side API calls and ready-to-paste recipes for HTTP Basic Auth, IP allowlist, rate limiting, and tighter Kokoro CORS. The sample nginx config (`docs/sample_configs/chat.example.com.sample`) ships the same recipes as commented-out blocks so applying them is copy/paste.

### Changed
- **Voice recording / audio attachments paused** — Ollama's image projector chokes on non-image bytes (`unknown data type` on raw audio), and there's no native audio path in the daemon for this gemma4 build. The mic button + `<VoiceRecorder>` component were removed from the visible UI, and audio files are filtered out of drop / paste with a friendly toast. Component files and utilities remain on disk so re-enabling is a small re-toggle once a transcription step (Whisper) lands.

### Fixed
- **Chat bubble horizontal stretch** — the stats footer's grid `1fr` second column was forcing the bubble's flex column to expand to its `max-w-[85%]`. Switched to `max-content` for both columns and added `w-fit` to the disclosure wrapper, so bubbles size to their actual content again.

## [1.4.0] - 2026-05-09

### Added
- **Local AI Chat (Ollama integration)** — new top-level Reader↔Chat toggle in the header. Streams responses from a local Ollama server (`/api/chat`), with model picker auto-populated from `/api/tags` and configurable host/port (default `localhost:11434`).
- **Plain text (.txt) file support** — drag, drop, or pick a `.txt` file to read it the same way as PDFs. Files are paginated into pseudo-pages of ~40 sentences and use the existing TTS, sentence highlight, library, and resume-progress flow. New `TextPageRenderer` component; `fileType` discriminator on library records.
- **Multi-session chat memory** — every chat is auto-saved to IndexedDB (new `chat_sessions` store, DB migrated to v3). Sessions list in the sidebar with switch / rename / delete; auto-named from the first prompt; LRU-capped at 50 sessions.
- **Per-session event log** — each session records `sent` / `received` / `aborted` / `error` events with timestamps. Viewable as a collapsible "Session log" section in the sidebar.
- **Per-message read-aloud controls** — every assistant bubble has a "Read aloud" button to start/stop TTS for that message specifically. Works with auto-TTS off; you can re-read finished messages later.
- **Per-message copy button** — one-click copy of any chat message (user or assistant) to clipboard, with toast confirmation.
- **Markdown rendering in chat** — assistant replies render as proper Markdown (lists, code blocks, tables, headings, links) via `react-markdown` + `remark-gfm`. User messages stay plain.
- **Reasoning model support** — toggle "Enable thinking" in the chat sidebar to send `think: true` to Ollama and surface `message.thinking` traces (for deepseek-r1, qwen3-thinking, gpt-oss, etc.). Thinking renders in a collapsible disclosure that auto-expands while streaming and auto-collapses once the answer arrives.
- **Streaming vs after-complete TTS modes** — toggle between reading sentences as they stream in, or waiting for the full reply. Mode is locked at message-start so toggling mid-stream applies to the next message.
- **Markdown-aware TTS** — chat speech strips Markdown markup (`**`, `__`, `#`, fenced code, link URLs) before synthesis, so the audio reads visible text only — no more "star star bold star star".
- **Collapsible chat sidebar sections** — Settings, Sessions, Conversation, and Session log are independently collapsible with their own scroll containers, plus an outer scroll for the whole sidebar.

### Changed
- **TTS engine refactor** — extracted reusable `synthesizeText`, `playChatUrl`, `playChatSpeech`, and `stopChatPlayback` helpers; the chat side composes them on its own audio channel (`chatAudioRef`) so chat playback and reader playback can never collide. Reader playback is now gated by an `enabled` prop and pauses automatically when switching to chat mode.
- **Bounded chat TTS prefetch** — chat synthesis now mirrors the reader's pattern: only the current sentence plus the next two are in flight at any moment. Eliminates the cascading per-sentence timeouts that previously caused chat TTS to stop after a few sentences on long replies.
- **Centralized toast + IndexedDB transactions** — single transaction in `saveBook`/`updateBookMeta`, fewer round-trips, race-condition-free. Toast helper consolidated in `App.jsx`.

### Fixed
- **Stop button for chat TTS** — clicking Stop on a chat message now silences audio immediately, drops queued sentences, and resets the playback chain (previously the in-flight sentence and queued ones kept playing).
- **Long chat replies cutting off mid-message** — the prefetch bound (above) prevents Kokoro's serialized inference from timing out queued sentences and silently skipping them. Replies after horizontal rules and other mid-message structure now read all the way through.

## [1.3.0] - 2026-03-05

### Added
- Health check endpoint (`GET /v1/health`) for frontend availability detection.
- Configurable API request timeouts and unlimited batch timeout toggle.
- Collapsible settings section in the sidebar.
- Duplicate synthesis request prevention (deduplicates in-flight fetches).
- Backend auto-failover with toast notification when Kokoro is unreachable.

### Changed
- **Major refactor**: Frontend split into modular React hooks (`usePdfEngine`, `useTtsEngine`, `usePersistedState`, `useKeyboardShortcuts`, `useMobileDetect`, `useTheme`).
- **Major refactor**: Backend restructured from single `server.py` into a `server/` package (`app.py`, `endpoints.py`, `model.py`, `schemas.py`).
- Entry point changed from `python server.py` to `python run.py`.
- Removed verbose TTS endpoint logging for cleaner server output.
- State management optimized — removed unused variables and refined `useState`/`useEffect` patterns.

### Fixed
- **TTS auto-continue on page change** — reading now automatically continues on the next page without needing to pause and replay. Fixed race condition where the playback loop would re-run with stale text from the previous page.
- **StrictMode double-run** — playback index update reordered to prevent React StrictMode from skipping sentences.
- Improved `useEffect` dependency linting across hooks.

## [1.2.0] - 2026-02-02

### Added
- GPU support for Kokoro TTS (CUDA, OpenVINO for Intel Arc/NPU).
- "Continue from here" and "Selective Read" features in PDF Reader.
- Batch synthesis endpoint (`/v1/batch_synthesize`) for merging audio.

### Fixed
- UI overlaps on mobile devices.
- PDF frame resizing and header layout issues.

## [0.1.0] - 2026-01-31

### Added
- Initial release
