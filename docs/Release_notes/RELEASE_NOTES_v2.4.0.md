# v2.4.0 — Ask a whole project, and a web search that asks first

**You can now ask a question of a whole project.** Before, a chat could see one
document at a time, so "how do I deploy this, step by step?" got an answer from
whichever file was open. Now **Ask about this project** on a project page starts
a chat over every current document in it. The assistant sees a map of the
project, searches all of it as one list, and cites each fact by document and
page, **(DEPLOYMENT.md, page 4)**. The link opens that document at that page.

**Web search asks first.** Before, the assistant searched the web on its own
whenever SearXNG was set up, and the page text it read went to a model you
didn't pick. Now it can only *offer*: a card shows why it wants to search and
the exact query, in a box you can edit. Nothing leaves the server until you
press **Search the web**, and the yes covers that one answer. Admins can switch
this to *Search without asking* or *Off* for everyone.

Also in this release: Settings opens as a window over what you're doing, the
narration keeps playing in every view, the chat sits beside the page on a wide
screen, and you get a profile with a picture and your signed-in browsers. **A
write is committed before its response is sent**, which fixes v2.3.0's known
issue.

Two migrations, `020` and `021`, both additive. The compose Keycloak image
moves from 26.0 to 26.8.0; read the upgrade notes before you restart it.

## Added

### 🗂️ Project chat

- **Ask about this project** in the project header (shown to members with the
  chat permission), or **Choose a project…** on any chat's scope chip. The
  chip reads **Project: *name***, and the chat list shows which project a
  chat is about.
- **Citations name the document.** **(Guide.md, page 7)**, page lists and
  ranges all link. The chips under a reply are grouped by document.
- **"Every step, in order" reads every page it needs.** A separate model
  call, the *reader*, goes through the pages for one goal. It returns only
  facts whose quotes the server finds on the page, and the assistant is told
  which pages it didn't get to. The reader's calls count toward each person's
  daily token budget.
- **Commands and tables can be found.** A Markdown code block is its own
  passage, table cells are searchable word by word, and each document's
  headings are kept as its outline.
- Only current versions are searched. Documents still being indexed are
  listed but not searchable yet. A project you lose access to reads
  **Project unavailable**, and the chat goes back to no project.

### 🌐 Web search that asks

- **The Search the web? card** shows the reason and the query, with **Search
  the web** and **Not now**. Facts from the web cite their site,
  **(example.com)**, and the pages read show under **Web sources**.
- **Admin → Chat options → Web search**: *Ask first* (the default), *Search
  without asking*, or *Off*, from the next message. It shows where the
  current value comes from, and **Use the server setting** hands control back
  to `CHAT_WEB_SEARCH`.

### 🪟 Settings, narration and profile

- **Settings is a window** with tabs (Profile, Voice & reading, Chat,
  Appearance, Connection, Access tokens). Changing the voice no longer stops
  the narration or a reply.
- **The narration plays in every view**, with a small header control. Reading
  a reply aloud pauses it, so only one voice plays at a time.
- **Chat beside the page** on a wide screen. **Ask page** and **Ask AI** land
  there.
- **Profile:** a picture, **Manage your account** (your sign-in provider's
  account page), and your signed-in browsers, with **Sign out everywhere
  else**.
- **Admin → Defaults:** an admin sets auto-read, read-aloud timing, thinking,
  voice and speed for anyone who hasn't chosen. **Apply to everyone**
  overrides people's own choices.
- **Admins find people by part of a name** when adding members or sharing,
  whatever `USER_DIRECTORY_MODE` says.
- **Sessions end when unused:** `SESSION_IDLE_HOURS`, default 12.

## Changed

- **Web search no longer runs on its own** by default (see above).
- **`POST /v1/tools/web_search` returns `{title, url, site, snippet}`** per
  result, not `{title, url, summary, source}`, and refuses with 403 when web
  search is *Off*. Nothing in the app calls it.
- **`web_search` lists results; it no longer fetches pages.** Reading a page
  goes through the reader, which takes the passages that match the goal from
  anywhere on the page. Before, each page was cut at its first 6,000
  characters. `WEB_SEARCH_MAX_PAGE_CHARS` is no longer read.
- **Auto-read is off by default, and replies are read once complete.**
- **The assistant may fill a gap from general knowledge**, if it says that
  part isn't from the documents and gives it no page. This applies to single
  documents too.
- **Search matches part of a query** when no passage has every word.
- **Read Selection and Ask AI no longer float over the Chat view**, where
  they covered Send.
- **Keycloak 26.8.0** in `docker-compose.yml`. **Docling 2.142 or newer**,
  which is installed only when you build with `WITH_DOCLING=true`.

## Fixed

- **A write is committed before its response is sent.** An immediate
  re-read could miss the change: 3 of 30 did before, 0 of 60 do now. Each
  running index also held a pool connection until it finished.
- **Cancelling an audiobook stops the server.** A one-word read right after
  Cancel used to wait 76.8 s; it now waits 0.3 s.
- **Thinking "Off" turns thinking off on OpenRouter.**
- **Leaving Chat no longer stops the reply**, and leaving the reader no
  longer stops the narration.
- **A text page fits beside the chat**, and an oversized page scrolls from
  its left edge.
- **Words in a Markdown table cell can be found.**

## Upgrade notes

1. **Back up first**, including the Postgres volume that holds Keycloak's
   data. **Keycloak 26.8.0 updates its database on its first start**, and an
   updated database can't go back to 26.0. Pull the image
   (`docker compose pull keycloak` or the podman equivalent), then restart.
2. **Migrations `020` and `021`** run on startup, and neither changes an
   existing row.
   - `020` adds `user_avatars`. To undo: `DROP TABLE user_avatars;`.
   - `021` adds `chat_sessions.scope_project_id` and `doc_outlines`. To undo:
     `DROP TABLE doc_outlines; ALTER TABLE chat_sessions DROP COLUMN scope_project_id;`.
3. **Existing documents are rebuilt in the background on first use**, to add
   code passages and outlines.
   - Only text with no stored vector is embedded again: 16% of chunks on the
     test set (code and table chunks), 15 s for 9 documents on a CPU.
   - Chats don't wait for it, and the old chunks keep answering meanwhile.
   - Before upgrading, run
     `SELECT embedding_model, count(*) FROM doc_chunks WHERE embedding IS NOT NULL GROUP BY 1;`.
     Rows with an empty `embedding_model` are embedded again too.
4. **Pick the reader's model.** It uses `READER_MODEL`, else
   `SUMMARIZE_MODEL`, else `WEB_SEARCH_SUMMARY_MODEL`, else `llama3.2:3b`.
   Either pull that model, or set `READER_MODEL=chat` so the reader uses the
   chat's own model. That keeps a CPU-only Ollama from loading a second model.
5. **To keep the old web behavior**, set `CHAT_WEB_SEARCH=auto` or pick
   *Search without asking* in Admin → Chat options.
6. **Deploy the SPA and the backend together.** The new chat sends
   `context.project_id` and `context.web`, and reads a new `data-web-offer` stream event.
   An older SPA on this backend has no web card, so in *Ask first* mode it
   can never approve a search.
7. Once per browser, auto-read and its timing forget their stored values and
   follow the new defaults.

New settings, all optional with defaults in `.env.example` and
[DEPLOYMENT.md](../DEPLOYMENT.md#project-chat-the-reader-and-web-search):

| Setting | Default |
|---|---|
| `CHAT_WEB_SEARCH` | `ask` |
| `CHAT_PROJECT_MAP_CHARS` | 6000 |
| `CHAT_PROJECT_MAX_TOOL_ROUNDS` | 5 |
| `CHAT_PROJECT_FUSION_DEPTH` | 50 |
| `CHAT_SEARCH_PER_DOC` | 2 |
| `CHAT_EXTRACT_MAX_PAGES` | 12 |
| `CHAT_EXTRACT_OUTPUT_CHARS` | 4000 |
| `READER_MODEL` | see note 4 |
| `READER_INPUT_CHARS` | 12000 |
| `READER_OUTPUT_CHARS` | 1200 |
| `READER_MAX_CONCURRENCY` | 2 |
| `READER_TIMEOUT_S` | 60 |
| `SESSION_IDLE_HOURS` | 12 |
| `OIDC_ACCOUNT_URL` | Keycloak's account page |

## Known issues

These were measured on a cloud model (`gemma4:31b-cloud`, chat and reader),
with a 9-document and a 50-document copy of this project's own docs.

- **The web card doesn't always come in a project chat.** For a question the
  documents only half answer, the assistant often says what's missing and
  doesn't offer. The card came in 4 of 6 eval runs and 1 of 4 walk chats.
  Type "search the web for …" to bring it up. The query may be reworded, and
  you can edit it before you approve.
- **"What are the features?" draws on README.md alone** and misses the
  documents about accounts, projects and admin.
- **Citations point at the right document 87.9% of the time with 9
  documents, and 82.1% with 50** (the target was 90%). In 1 of 6 runs of a
  question the documents don't cover, the answer cited a page anyway.
- **Pressing Stop while the answer above the card is being written** keeps
  the text but loses the card. Ask again, or type "search the web for …".
- **Not yet measured on a small local model** (`gemma4:e4b`). Expect weaker
  routing there.
- The idle Read Selection button still floats over the Library view, and
  removing a project member asks for no confirmation.

## What you can't do yet

- **Ask across your whole library**, or across part of a project. A chat
  covers no document, one document, or one whole project.
- **Upload a new file from a project page.** Upload in the reader and pick
  the project there, or file a document already in your library.
- **Chat with a project you aren't a member of**, even as a site admin. Add
  yourself under Members first.
- **Allow the web for a whole chat.** In *Ask first* mode each search needs
  its own click.
- **Tune project chat in the app.** Only the web mode is in Admin; the rest
  are environment variables.
- **Give documents descriptions, or see a project overview** (planned, C2.2).
  **Compare versions** (planned, V2).
- **Keep a chat open across a browser reload.** The reload opens a new chat,
  and the old one is in **Sessions**.

## Full changelog

[CHANGELOG.md](../../CHANGELOG.md#240---2026-10-25) · diff:
[v2.3.0…v2.4.0](https://github.com/udaravima/natural-reader/compare/v2.3.0...v2.4.0)
