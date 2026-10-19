# C2.1 project chat: running-app walk (J1–J9)

Walked 2026-10-19/20 on `feat/c2.1-project-chat`. The code under test is `ec297f4`. HEAD moved to `cc5dfac` during the walk, but only `docs/` changed (no `src/` or `server/` file), so the walked code equals HEAD.

**Headline.** Every journey step has a UI control, and no step needs curl. Seven journeys pass. Two are Partial, both on answer quality in the web path, and both trace to causes found during the walk:

- J3: in "ask" mode the model offers a web search *instead of* reading the documents (finding 2);
- J3: once the user approves, the reader drops any page that answers only part of its goal, so no web source chips appear (finding 1).

## Set-up

The docker-compose Keycloak and the `natural_reader` database were not started, contacted or read.

- **Identity server.** A throwaway Keycloak 26.0.8 container, `nr-walk-keycloak`, ran on `127.0.0.1:18081` (`start-dev --import-realm`) from a scratch copy of `deploy/keycloak/realm-export.json`.
  - The checked-in export already allows `http://localhost:5173/v1/auth/callback`, so the copy only gained three walk users: `olive` (project Owner), `max` (added as a project Reader) and `walkadmin` (realm role `admin`).
  - All three have `reader` and `chat`, `emailVerified: true`, and random passwords kept in a scratch file that was deleted afterwards.
- **Database.** `natural_reader_walk` on the scratch Postgres `nr-c21-testpg` (`127.0.0.1:5434`). Migrations 001–021 ran at backend start. `walkadmin@example.com` claimed the seed admin row through `BOOTSTRAP_ADMIN_EMAIL`. Olive and Max were pre-enrolled as `active` rows; this was set-up only, as in the 2026-10-12 walk.
- **Backend.** `run.py` on `127.0.0.1:8000` under `env -i` with explicit values only, so nothing came from `.env`:
  - OIDC pointed at the throwaway realm (the client secret was read from the scratch realm copy, never printed);
  - a scratch random `SESSION_SECRET` (64 chars), and `COOKIE_SECURE=false`;
  - `DATABASE_URL` set to `natural_reader_walk`, with scratch `DOC_STORAGE_DIR` and `LOG_DIR`, and `DOCLING_ENABLED=false`;
  - `OLLAMA_URL=http://127.0.0.1:11434`, `INFERENCE_MODELS=gemma4:31b-cloud`, `SUMMARIZE_MODEL=gemma4:31b-cloud`, `READER_MODEL=chat` and `EMBEDDING_MODEL=nomic-embed-text` (local);
  - no OpenRouter key; `SEARXNG_URL` pointed at the fake SearXNG; `CHAT_WEB_SEARCH` unset, except one restart with `off` for J9's source-line check.

  `INFERENCE_MODELS` is the right variable when `INFERENCE_PROVIDERS` is unset (`server/llm/router.py`). The brief's `INFERENCE_OLLAMA_MODELS` is only read with `INFERENCE_PROVIDERS=ollama`.
- **Fake SearXNG.** A 40-line Python server in the scratchpad, on `127.0.0.1:18888`. It answered every `/search?format=json` with the same three real public pages, and logged each query it received:
  - the Red Hat Enterprise Linux 9 "Installing and using Python" guide (docs.redhat.com);
  - `https://podman.io/docs/installation`;
  - `https://docs.python.org/3/using/unix.html`.

  The SSRF guard was untouched. **Caveat:** I wrote the result snippets myself, and they are more specific than the pages (see finding 1).
- **Real page fetches.** They worked during J3's turn: the tool line read "Read 2 page(s): docs.redhat.com, podman.io."
  - One later probe of the Red Hat page got HTTP 503 from the server's own client; the next three calls got 200. So it is reachable, but not reliably.
  - Its extracted text is 4,058 characters and includes "To install Python 3.12, use: # dnf install python3.12".
- **Model.** `gemma4:31b-cloud` through local Ollama for every journey. The brief's fake slow OpenAI-compatible model wasn't needed: the cloud model streams, and the Stop control showed during every streamed reply.
- **Frontend.** Vite on 5173 with the repo's own `vite.config.js` (no walk-only config was needed).
- **Browsers.** The Playwright MCP's headed Chrome aborted on start (a GTK settings-schema error), so I drove headless Chromium 1217 through Playwright from a scratch Node script. Named contexts gave:
  - Olive (1366×900);
  - Max (1366×900);
  - Olive on a second "device";
  - Olive on a phone (390×844, `isMobile`, `hasTouch`);
  - the admin.
- **Controls.** Every control was operated with a DOM `click()` on the control itself, text was entered with `fill`, and keys were real key presses (`keyboard.press`). Screenshots were used to judge layout.
- **Sign-in.** Each context signed in at Keycloak's own form, starting from `/v1/auth/login`. I didn't separately walk the app's Sign in button; A0's walk covers it.
- **Interruption.** A session limit paused the walk on 2026-10-19. At 08:40 that day, six minutes after the first backend start, both containers and the fake SearXNG stopped. I didn't stop them.
  - `nr-c21-testpg` runs on tmpfs, so the first `natural_reader_walk` was lost.
  - On resume I restarted Keycloak (realm and users intact), recreated the database, restarted the backend, re-enrolled the two users, and redid all set-up through the UI.
  - No journey had been walked before the interruption.

**Project content (through the UI, as Olive).**

- **Project.** Library → **New project** → name "Natural Reader docs" → **Create**. The card read "Natural Reader docs · Owner · 1 member · 0 documents".
- **Documents.** The nine fixture `.md` files from `server/evals/fixtures/project_docs/` went in one at a time: Reader → **Choose File** → the toolbar picker **Project for this document** = Natural Reader docs → **Index**.
  - Indexing took 3–16 s each; README.md had 207 chunks and took about 15 s.
  - The project tab then listed all nine as "indexed · v1 · 1 version".
  - `SOURCE.txt` was left out, as the eval does (`project_qa.py` globs `*.md`).
- **Member.** Members tab → **+ Add people** → typed `max@example.com` (the default directory mode, `exact`, needs the full email; "max" found nothing) → picked "Max Member @max" → **Role for the new member** = Reader (the default is Contributor) → **Add**. The tab read "Members (2)".

## Journeys

| # | Journey | Controls used | What was seen | Result |
|---|---|---|---|---|
| J1 | Overview question | Project page **Ask about this project** → sidebar **Model** picker (first use) → composer, Enter → citation "(README.md, page 1)" → source chip "p. 5" | **Ask about this project** opened the Chat view, a new chat, with the chip "Project: Natural Reader docs". "What are the features?" got a long, accurate, sectioned answer in 14 s. Its tools were `search_documents(query="features") → 5 new passages`, then `read_document_pages(document=7, first_page=1)`. **It cited only "(README.md, page 1)"**. Chips: "Sources README.md · p. 1"; "Searched (not cited) ARCHITECTURE.md · p. 4 / USER_GUIDE.md · p. 1 · p. 5", grouped by document. A retry in a fresh project chat gave the same: README only. The citation ("Open README.md at page 1") opened README.md in the reader at "1 / 10", marked Indexed. Page 1 is also the default, so I checked a jump with the "Source: page 5 of USER_GUIDE.md" chip: the reader opened USER_GUIDE.md at "5 / 6". | **Partial**: answer cites one document, not several (finding 7). Every control works. |
| J2 | Cross-document question from the reader | Project Documents tab **Open DEPLOYMENT.md** → **Chat beside the page** → panel **New chat** → chip → menu item "Natural Reader docs" → composer | The panel first showed the J1 chat (it follows the current chat). **New chat** started with the chip "This document: DEPLOYMENT.md". The menu offered "No document", "This document: DEPLOYMENT.md" (checked), "Natural Reader docs" and "Choose a project…"; picking the project set "Project: Natural Reader docs". "How do I deploy this and then test it? Give me the steps in order." gave a two-phase step list in 22 s, citing DEPLOYMENT.md pages 1, 2 and 5 and TESTING.md page 1 (chips "DEPLOYMENT.md · p. 1 · p. 2 · p. 5", "TESTING.md · p. 1"). The joined "(DEPLOYMENT.md, page 1, 5)" became two links. Clicking "Open TESTING.md at page 1" swapped the reader to TESTING.md page 1; the chip kept the project. | **Pass** |
| J3 | "I'm on RHEL 9, which packages do I need?" | Project page **Ask about this project** → composer → card **Web search query** field (edited) → **Search the web** | Default web mode: **ask** (Admin source line "The default."). **The offer came without text.** The only tool call was `offer_web_search`, with no document search first. The card read: "Search the web? / The project documents do not list the specific OS packages required for RHEL 9." with the query "Natural Reader app RHEL 9 system requirements packages". The documents do list them: README page 3 has Python 3.12–3.13, Node 20.19+/22.12+ and Podman (finding 2). I edited the query to "RHEL 9 install Python 3.12 Node.js 22 Podman packages"; the fake SearXNG received exactly that string. The answer then added web facts citing sites: "Python 3.12: … `dnf install python3.12` (docs.redhat.com)", "Podman: … `dnf install podman` (podman.io)", and "Node.js 22: The search results did not provide the specific package name…". Tools: `web_search → 3 result(s)`, `read_web_pages(results=1,2) → Read 2 page(s): docs.redhat.com, podman.io.` **No web source chips appeared**, and the saved reply has no `web_sources` note at all (finding 1). The phone run (J8) repeated the no-text offer. | **Partial**: card, edit and search work, and the edited query is what was searched; no document answer before the offer, and no web chips. |
| J4 | Not now | Card **Not now** | Asked "What is the newest stable Podman release right now?"; a new card appeared, and the old J3 card had become an inert "Offered a web search" line (only the last reply has controls). **Not now**: the browser sent **0** `/v1/` requests in the next 4 s; the stored message count stayed at 6; SearXNG got no query; the card disappeared and focus moved to the composer. The next message, "OK, no web then. Which Python versions…", was answered normally: "…supports Python 3.12–3.13 … (README.md, page 3)". | **Pass** |
| J5 | Removal | (Olive) Members tab **Remove Max Member from Natural Reader docs** · (Max) composer, Enter | Max, a project Reader, saw **Ask about this project** and **Leave project**. In his project chat he asked "What roles can a project member have?" (cited USER_GUIDE.md page 3). Olive clicked **Remove…**: Max was gone at once, "Members (1)", with no confirmation (observation 6b). Max then sent "And who can delete the project?": toast **"This project doesn't exist or you don't have access."**; the chip switched to **"No document"** (not "Project unavailable"); the text stayed in the composer; the sidebar session lost its project label; the old transcript stayed. **What next:** Enter again sent the same text as a no-document chat, answered from the earlier transcript ("Only the Owner has the permission to delete the project."), with no tools and no citations. `chat_sessions.scope_project_id` is now NULL. | **Pass** |
| J6 | A document still indexing | Set-up only: `documents.state='indexing'` for TESTING.md in the scratch DB · project Documents tab · **Ask about this project** → composer | The Documents tab showed "TESTING.md · indexing". "How do I run the backend tests, according to TESTING.md?" answered: "I'm sorry, but I cannot access the contents of TESTING.md at the moment because it is still being indexed and is not yet searchable." Tools: `read_document_pages(document=8, first_page=1) → error: Document 8 ("TESTING.md") isn't searchable yet.` The model knew document 8 was TESTING.md from the map. The state was set back to `indexed` afterwards. | **Pass** |
| J7 | Another device | (Olive, second browser context) **Chat** → session row "I'm on RHEL 9, which packages do I need?" | Every project session in the list carried the label "Natural Reader docs". A fresh chat showed "No document"; opening the RHEL session loaded its transcript (including the "Offered a web search" line and the web answer) and the chip "Project: Natural Reader docs". | **Pass** |
| J8 | Phone, J1 from the Chat view | **Chat mode** → **Show Sidebar** (44×44) → **Model** picker → **Hide Sidebar** → chip → **Choose a project…** → "Natural Reader docs" → composer → **Send (Enter)** → citation → card **Search the web** | Chat view scrollWidth **390** (no horizontal scroll) all through the chat; the chip and each menu item are 44 px tall (items 278×44). Answer as J1: README only. Citation 142×44; source chips 44×44; they wrap and stay grouped by document. The citation opened README.md page 1 (the reader view itself is 442 px wide, pre-existing 3e). The RHEL question gave the no-text card again: 304×227, query field 278×**39**, **Search the web** 116×44, **Not now** 77×44. The approved search cited "(README.md, pages 3-4; docs.redhat.com)" with README chips only. **But once a document had been opened, the fixed "Read Selection" button covered Send** (finding 3). | **Pass** (finding 3 is pre-existing) |
| J9 | Admin chat options | Header **Admin console** → section "Chat options" → radios **Search without asking** / **Off** → **Use the server setting**; "What the model receives" preview | **Search without asking**: source line "Set here." plus **Use the server setting**; `app_settings.chat_options = {"web_search":"auto"}`. The preview's rule became "use web_search … then read_web_pages … If it concerns something named in the document, find the name in the document first." plus "name its site like this: (example.com)". Olive's RHEL question in a new project chat: no card and no web call; the documents answered it (README pages 3–5). "What is the newest stable Podman release right now?" searched directly: 3 SearXNG queries, no card. **Off**: the preview lost every web line; "…Please check online." got no card, no query, and "I cannot look up live information … as I do not have access to the internet." **Use the server setting**: back to "Ask first" with "The default."; the row was deleted, and the preview was byte-identical to the original. With the backend restarted under `CHAT_WEB_SEARCH=off`: "From the server setting CHAT_WEB_SEARCH." with Off checked; "Ask first" → "Set here."; reset → back to env Off. | **Pass** (focus note 6c) |

## Findings

Severity follows the review ledger: Critical, Important, Minor.

### 1. Important: the web reader drops a page that answers part of its goal, so approved web answers get no web chips

**What the user sees (J3).** The answer says "Python 3.12: … `dnf install python3.12` (docs.redhat.com)", but there's no web source chip to open, and nothing shows which page said it. The tool line "Read 2 page(s): docs.redhat.com, podman.io." reads as success.

**The causal chain** (reproduced outside the app with the server's own functions and the same model):

1. The chat model called `read_web_pages` with a three-part `need`: "Confirm the exact package names for Python 3.12 and Podman on RHEL 9, and look for Node.js 22 installation methods/packages on RHEL 9."
2. The reader brief (`server/chat/reader.py` `BRIEF`) says "If the passages don't have it, return found false and no facts." gemma4:31b-cloud reads "it" as the *whole* goal.
3. The results, on the Red Hat page, whose text states "To install Python 3.12, use: # dnf install python3.12":
   - with the three-part need, the raw reply was `{"found": false, "facts": []}` in **5 of 5** calls;
   - with the one-part need "The package name and dnf command for Python 3.12 on RHEL 9.", it was `found: true` with two verified quotes in **3 of 3**.

   On podman.io the reader was right to find nothing: its RHEL entry says only "Follow the official docs."
4. `read_web_pages` sets `entry["read"] = True` only when `result.found` (`server/chat/tools/read_web_pages.py`). `web_sources()` lists only read entries, and `_source_notes` writes no `web_sources` note, so the client has no chips to draw.
5. The summary counts a `found: false` page as "read", so the tool line says "Read 2 page(s)" when nothing came back.
6. The model then answered from the search *snippets* and cited their sites. In this walk the snippets were text I wrote for the fake SearXNG. Its "(podman.io)" fact is in my snippet, not on podman.io's RHEL entry. So a cited site can name a page that doesn't say what's attributed to it.

**Stakes.** In ask mode the user approved a search and should see where the facts came from. They get site names with no links. A multi-part need is the normal case: the model bundles everything it lacks into one call.

**Not verified:** whether a real SearXNG's shorter snippets would push the model to say "not found" instead of citing; the fake's informative snippets made the citing easy. The second web answer (J8, phone) didn't call `read_web_pages` at all and cited "docs.redhat.com" from the snippet.

**For the controller:** options include rewording the brief ("return what the passages do have for any part of the goal; found false only when nothing serves it"), counting only found pages in the summary, and deciding whether snippet-only facts may carry a site citation. The brief is pinned by `test_reader_brief_is_exact`.

### 2. Important: in "ask" mode the RHEL question gets an offer instead of the documents' answer

**Seen** in 2 of 2 tries (desktop J3, phone J8). The first and only tool call was `offer_web_search`, with no `search_documents`. The offer reason states, unchecked, that "The project documents do not list the specific OS packages required for RHEL 9." Spec §5.9 J3 expects "answer with cited requirements plus labelled general knowledge, then an offer card".

**The documents do answer it.** The same question in **auto** mode (J9) searched the documents first and answered from README pages 3–5: Podman, Node 20.19+/22.12+, Python 3.12–3.13, Git, wget/curl, Postgres 16 + pgvector. A follow-up in ask mode (J4) found "Python 3.12–3.13 (README.md, page 3)".

**Likely cause** (inferred, not verified). The two modes' rule lines differ. Auto's says "If it concerns something named in the document, find the name in the document first."; ask's line ("call offer_web_search with the topic in a few words and why it's needed") has no such clause. I read these from the admin preview, which is the **document-scope** prompt; the project-scope prompt wasn't inspected. Task 21a's eval cases should show whether this is systematic.

### 3. Important (pre-existing, not C2.1): the fixed "Read Selection" button covers the chat's Send button

Once any document has been opened in the session, the floating "Read Selection" button (`ReadSelectionButton`) sits over Send. At Send's centre, `elementFromPoint` returns "Read Selection":

| Viewport | Send box | Read Selection box |
|---|---|---|
| Desktop Chat view, 1366×900 | 1185,846 42×42 | 1182,832 160×44 |
| Phone, 390×844 | 330,720 44×44 | 214,704 160×44 |

A mouse or touch user can't press Send; Enter still sends, and the placeholder says so. Project chat makes this more visible because its journeys move between the reader and Chat. The 2026-10-12 walk's observation 3d saw the same overlay over the Documents tab at 390 px.

### 4. Minor: Escape on an open menu's button closes the menu *and* stops narration (profile, header overflow, PDF toolbar menus)

Task 17's `[role="menu"]` exemption works only once focus is inside the menu. These three menus (`ProfileMenu`, `HeaderOverflowMenu`, `PdfToolbarMenu`) leave focus on their opener when they open, with no arrow keys. So in the most natural moment, right after opening by mouse or Enter, Escape reaches the window shortcut. Space has the same problem: on the opener, it toggles play/pause.

Measured in the reader with narration playing (status and button read before and after):

| Case | Menu | Narration |
|---|---|---|
| Enter → Escape (focus on opener) | closes | **stopped** in all three: the profile menu showed "Playback Stopped" with the button "Read"; for the other two, at 700 px where the status text is hidden, the button went from "Pause" to "Read" |
| Enter → Tab (into the first item) → Escape | closes | continues; focus drops to `<body>`, not back to the opener |
| Enter → Tab to an item → Space | item activates ("Dark mode" toggled, "Fit width" applied) | continues |

I didn't check whether a stop loses the reading position.

### 5. Minor (pre-existing, V1): the project document row's ⋯ menu can't be closed with Escape or an outside click

`ProjectDocRow`'s menu has no key handler, no outside-click handler and no `aria-expanded`. With focus on an item or on the opener, Escape leaves it open, and so does a real mouse click elsewhere. Only toggling ⋯ again closes it. Space on "Upload new version" activated it (the upload panel opened). Narration is unaffected here, because the reader shortcuts only run in the reader view (`inReader` in `useKeyboardShortcuts`).

### 6. Smaller observations (not fixed)

- **a. The project Documents tab has no control to upload a brand-new file.** "File a document…" lists only files already in your library. A first upload goes Reader → Choose File → "Project for this document" → Index. It works, but it isn't on the project page. Pre-existing (A1/V1).
- **b. Removing a member is one click with no confirmation.** Documents get a confirmation step. Pre-existing (A0); whether that's intended wasn't checked.
- **c. After "Use the server setting", focus falls to `<body>`**, because the clicked button unmounts. A sibling of Task 19's deferred note about a failed reset.
- **d. The model twice called `read_document_pages` without `document` in project scope** (J6, J8). The error lists the documents, and the model retried correctly on the next round, costing one tool round each time.
- **e. The offer card's query field is 39 px tall at 390 px**, under the 44 px used elsewhere.
- **f. Pre-existing, seen again:**
  - the reader's "Project for this document" shows "No project" for a project document (3f);
  - the reader header is 442 px wide at 390 (3e);
  - the status text stays "Reading..." while paused, with the button reading "Read".
- **g. Walk artifacts, not product bugs:**
  - after my backend restarts, headless Chromium logged "Audio block NotSupportedError: Failed to load because no supported source was found", so narration sat at "Wait for interaction..." while still counted as playing; the stop/pause signals above are unaffected;
  - docs.redhat.com returned HTTP 503 to the server's fetch once.

### 7. Minor: J1's overview question cites one document

"What are the features?" was answered from README.md page 1 in 3 of 3 tries (two desktop, one phone). ARCHITECTURE.md and USER_GUIDE.md were searched but not cited. The answer is correct, because README's Features section is the canonical list. But spec §5.9 expects several documents, and the model never consulted the map beyond one search. That's answer quality, for the eval to judge, not a control gap.

## API-only or env-only steps

None of J1–J9 needed the API. Every step had a UI control. Set-up only:

- Pre-enrolling Olive and Max as `active` rows. Admin console → Enroll exists; A0's walk covers it.
- J6's `indexing` state was set in the scratch DB. A real slow index produces the same state.
- `CHAT_WEB_SEARCH` is env-only by design: it is the deployment's starting value. The admin's "Use the server setting" returns to it, and the source line names it.

**Still true for a user:** web facts in an approved answer can't be opened from the reply (finding 1). The user sees site names but gets no link, until the reader keeps partial answers.

## Extra checks (from task reviews)

**Reader side panel** (DEPLOYMENT.md open, narration playing, status "Reading...", button "Pause"):

| Check | Seen | Result |
|---|---|---|
| Focus the scope chip, Space | Menu opens (`aria-expanded=true`), focus on the checked item "Natural Reader docs"; narration continues | Pass |
| Space on a menu item | Picks it, menu closes; narration continues | Pass |
| Escape in the open menu | Closes, focus back on the chip; narration continues | Pass |
| Offer card query field, Space | Types a space ("…version x"); narration continues | Pass |
| Escape with focus on **Search the web** | Nothing happens; the card stays and narration continues | Pass |
| Space on **Not now** | Card dismissed; no SearXNG query; narration continues | Pass |
| Space with focus on the page body | Pauses (button "Read"), and Space again resumes ("Pause") | Pass |

Space on **Search the web** wasn't pressed: it would run a search, and Enter in the field is unit-tested.

**The other menus:** see finding 4 (profile, header overflow and PDF toolbar menus, the last two at 700 px, where they appear) and finding 5 (project document row). In short:

- Space activates an item in all four, once focus is inside the menu.
- Escape stops narration from the opener of the three reader-header menus.
- Escape doesn't close the row menu at all.

## Privacy check

The walk's `server.log` (537 lines), `audit.log` (26 lines) and the backend's stdout, covering all three backend runs after the rebuild, were searched for:

- the three walk emails, `@example.com` and `%40`;
- all nine document names, "Natural Reader docs" and "Natural%20Reader";
- the J3 and J9 query words (RHEL, Podman/podman, packages), the fetched sites (docs.redhat.com, podman.io, docs.python.org), the user names, and phrases from the J1/J2 questions.

The only hits were:

- `Bootstrap admin email set to walkadmin@example.com`, once per backend start: the deployer's own `BOOTSTRAP_ADMIN_EMAIL`, a line that predates this branch;
- "packages" inside a `site-packages` path in an authlib deprecation warning.

No access-log line carried a query string. `audit.log` records IDs only, e.g. `project.created project=<uuid> by=<uuid>`.

## Removed afterwards

- **Processes.** The PIDs I started were stopped one at a time, each checked by its command line before `kill`:
  - the backend runs 3095099, 3389042, 3427311 and 3427816;
  - Vite 3095576;
  - the fake SearXNG 3388776 (the first one, 3094046, had already died in the interruption);
  - the browser driver 3390700 (needed SIGKILL after 8 s).

  No Chromium processes were left behind. Ports 8000, 5173, 18888 and 18990 are free.
- **Container.** `nr-walk-keycloak` was stopped and removed (`podman rm` succeeded on the first try).
- **Database.** `natural_reader_walk` was dropped. `nr-c21-testpg` keeps running with `natural_reader_eval` and `natural_reader_test`, which I didn't touch.
- **Scratch files.** Deleted: the realm copy, the passwords file, the session secret, the scratch document store, the server logs, the probe scripts, the driver and the fake SearXNG.
- **Kept** in `/tmp/claude-1000/-home-fire310w-Documents-Github-natural-reader/ed5a4c77-101f-455a-a6c0-8d9f09fac689/scratchpad/walk/`:
  - `shots/`: 56 screenshots, e.g. `j1-02-answer.png`, `j3-01-answer-offer.png`, `j5-02-toast.png`, `j8-08-offer-card.png`;
  - `notes/`: the PID log, the SearXNG query log, and the four rules previews (`preview-ask-default.txt`, `preview-auto.txt`, `preview-off.txt`, `preview-reset-default.txt`).
- **Not removed:** `~/.cache/ms-playwright-mcp/mcp-chrome-0ac177b`. It is the Playwright MCP's own profile directory, created by its failed launch.

Nothing was committed, and no repo file other than this one was changed.
