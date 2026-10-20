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

## Re-walk after Task 22 (2026-10-20)

Walked 2026-10-20, 13:03–13:33, on `feat/c2.1-project-chat`. The code under test is `26ace78`. HEAD moved to `cd0e956` at 13:00, before the backend started at 13:08, and that commit changes only `EVAL.md`, so the walked code equals HEAD.

**Headline.** Every Task 22 fix that a person can see works in the running app. The client fixes (e, j, k, l, m, n) all pass, now with real mouse clicks. The reader fix (h) works too: an approved search now shows a **docs.redhat.com** web source chip, which the first walk never got. The new answer step (a) does what spec §5.8 says whenever the model offers: text first, the card under it, no card after Stop.

The surprise is a different one. In the project chat, the RHEL question now usually gets **no offer at all**. That happened in 3 of 4 chats. Each of those replies says the documents lack a RHEL 9 package list, and none gives a card, so the user's one-click path to the web is gone. Two of the four answers also gave versions from an older document that would not run this app (finding 2).

### Set-up differences from the first walk

The rules were the same as the first walk's: the throwaway Keycloak on `127.0.0.1:18081`, the scratch `natural_reader_walk` on `nr-c21-testpg` (`:5434`), the backend under `env -i`, the cloud model only, the fake SearXNG on `:18888`, Vite on 5173, and headless Chromium. The docker-compose Keycloak, the `natural_reader` database and `:5433` were not started, contacted or read. `natural_reader_eval`, `natural_reader_test` and `EVAL.md` were not touched.

- **Nothing was reused.** `natural_reader_walk` didn't exist (the first walk dropped it), and there was no Keycloak container. An earlier start of this re-walk had been cut off by a session limit before any set-up existed. So everything was rebuilt.
- **Keycloak.** A container `nr-walk-keycloak` from `quay.io/keycloak/keycloak:26.0` (patch version not checked), run with `--userns=keep-id` so it could read the scratch directory. It imported a scratch copy of `deploy/keycloak/realm-export.json`:
  - the copy gained `olive`, `max` and `walkadmin` (realm role `admin`), all with `reader` and `chat`, `emailVerified: true` and random passwords;
  - the copy's demo `admin-user` password was also replaced with a random one;
  - the Keycloak bootstrap admin password was random and never printed.
- **Backend.** `run.py` with the same explicit values as the first walk, plus `HOME` pointing at a scratch directory. No `KC_ADMIN_*` was set, and `FRONTEND_ORIGIN` was unset. `CHAT_WEB_SEARCH` was unset all through, so the mode was **ask**. Migrations 001–021 ran at start. Olive and Max were pre-enrolled as `active` rows (set-up only).
- **Documents.** The project was made through the UI: Library → **New project** → "Natural Reader docs" → **Create**.
  - The nine fixture `.md` files went in through the API as Olive, from her signed-in browser context: `POST /v1/docs`, then `PUT /v1/projects/{id}/docs/{doc}`. The first walk used the reader's Choose File instead.
  - All nine reached `indexed` · v1 in under a minute.
- **Member.** Max was added through the UI: Members → **+ Add people** → `max@example.com` → "Max Member" → **Role for the new member** = Reader → **Add**. The tab then read "Members (2)".
- **Fake SearXNG snippets.** This time each snippet was copied from the page's own text (its opening sentence or meta description). The first walk's snippets were hand-written and more specific than the pages (its finding 1 caveat). The three URLs were the same.
- **Red Hat page.** With the server's own user agent it returned 200, and trafilatura extracted 4,058 characters, the same as the first walk, including "To install Python 3.12, use: # dnf install python3.12". With a bare `Mozilla/5.0` agent, docs.redhat.com served an "Access Denied" page instead.
- **Browser.** Headless Chromium 1217 through Playwright, from a scratch Node driver kept running between steps. The contexts were:
  - Olive at 1366×900 (resized to 700×900 for the narrow menus);
  - Olive on a phone, 390×844 with `isMobile` and `hasTouch`;
  - Max at 1366×900;
  - walkadmin at 1366×900.

  Sign-in was through Keycloak's own form, starting at `/v1/auth/login`.
- **How controls were operated.** This differs from the first walk, which used DOM `click()`, and that bypasses hit-testing.
  - Real mouse clicks at the control's centre (`page.mouse.click`) for Read, Stop, the three reader menus, the row ⋯ menu, the outside click, the Chat options radio and **Use the server setting**.
  - Playwright locator clicks for the rest. These fail if another element covers the control.
  - Text through `fill`, and keys as real key presses.
- **Narration.** It really played this time. The progress counter advanced from 2 to 11 of 37 sentences during k.
- **Ollama.** At 13:22, mid-walk, `ollama ps` listed only `nomic-embed-text:latest`, the local embedding model; the eval agent may share it. At the end it listed nothing. No local chat model ran.

### W1–W5

| Step | Control | What was seen | Result |
|---|---|---|---|
| W1a. Documents before the offer | Project page **Ask about this project** → sidebar **Model** (first use) → composer, Enter | In the one reply that offered, the tool lines were `search_documents(query="RHEL 9 packages install") → 5 new passages`, then `offer_web_search(…)`. The three replies with no offer searched 2× and read pages 1–2 or 3–5. | **Pass** |
| W1b. One reply: text, then the card | (same) | The text cited README.md page 3. It said what the documents lack ("do not provide a specific list of RHEL 9 packages or installation commands") and had a section headed **"General Guidance (Not from documents)"** with no pages. The card sat under it in the same reply (screenshot `w1-03-fresh1.png`). The stream ran text deltas ×400 → `data-web-offer` → `data-sources` → `finish`. The text doesn't ask whether to search. | **Pass** |
| W1c. Reload | Browser reload → **Sessions** row "I'm a newcomer on RHEL 9…" | The reload itself opened a fresh, empty "No document" chat. Reopening the session row showed the same text (1,249 rendered characters before and after), the same card (same reason and query) and the chip "Project: Natural Reader docs". | **Pass** (see finding 6) |
| W1d. Approve | Card **Web search query** (edited to "RHEL 9 install Python 3.12 Node.js 22 Podman packages") → **Search the web** | SearXNG received exactly that string at 13:18:48, one second after the click at 13:18:47. The tool lines were `web_search(…) → returned 3 result(s)` and `read_web_pages(results=1) → Found facts on 1 of 1 page(s): docs.redhat.com.` The **Web sources** chip "docs.redhat.com" links to the Red Hat page in a new tab. The Python fact is followed by its site: "`sudo dnf install python3.12` (docs.redhat.com)". The Node.js and Podman lines give dnf commands with no site and no general-knowledge label. The old card turned into the inert "Offered a web search" line. | **Pass** for (h); unlabelled lines in finding 3 |
| W1e. Count | 3 fresh chats via **Ask about this project**, plus the first | See "W1 count" below | **Partial** |
| W2. Features | Project page **Ask about this project** → "What are the features of this app?" | The tools were `search_documents(query="features") → 5 new passages` and `read_document_pages(document=7, first_page=1)`. The answer was long and accurate, but every citation was "(README.md, page 1)". **Sources:** README.md p. 1. **Searched (not cited):** ARCHITECTURE.md p. 4, CHAT_WITH_PDF.md p. 1, USER_GUIDE.md p. 1 · p. 5. It doesn't go beyond README. | **Partial** (as in the first walk; Task 22 (c) was measured and removed, EVAL.md) |
| W3. No document, ask mode | Chat view → **New chat** (sidebar, "Start a fresh chat…") → chip **No document** → "What is the latest stable Python version right now?" | The first round was the offer alone. The answer step then wrote: "Based on my general knowledge, the latest stable version of Python is typically in the 3.12 or 3.13 series, but since I cannot access live updates without a search, this information may be out of date." There were no page citations, and the card ("latest stable Python version October 2026") sat under it. The stream ran text → `data-web-offer` → `finish`. The wording matches note (3). | **Pass** |
| W4a. Stop during the answer step | No-document chat → "What is the newest stable Podman release right now? Explain in detail…" → **Stop generation** once the answer text had started | Stop was clicked once the tool line `offer_web_search` showed and the text read "Since I have not yet performed a live web search". The reply kept its text, marked **Stopped**, with no card and no "Offered a web search" line (`w4-01-after-stop.png`). After a reload and reopen there was still no card. The saved reply has `status: aborted` and no `web_offer` note. | **Pass** |
| W4b. Typed request | Same chat, composer: "search the web for RHEL 9 python packages" | A card appeared, prefilled with **"RHEL 9 python 3.13 packages"**, not the typed text. A second try in a fresh no-document chat was prefilled exactly: "RHEL 9 python packages". | **Partial** (finding 4) |
| W5e. Ask button and membership | walkadmin: Admin console → **Open Natural Reader docs** · Max: Library → Projects → the project | walkadmin (`my_role: null`) saw **Edit**, **Delete project** and the Members and Activity tabs, plus "Only members can see this project's documents.", and **no** Ask about this project. Max (`reader`) saw **Ask about this project** and **Leave project**, and clicking it opened a chat with the chip "Project: Natural Reader docs". | **Pass** |
| W5j. Read Selection and Send | A document opened from the project (**Open README.md**), then **Chat** / **Chat mode**; then the reader → **Chat beside the page** → a mouse drag over document text | Read Selection is absent in the Chat view at both sizes. Send is the element at its own centre: at 1366×900, Send is 1185,846 42×42; at 390×844, it is 330,720 44×44. In the reader with the panel open and the text "A modern, feature-rich document reader…" selected, Read Selection was at 632,832 160×44 with Ask AI beside it, clear of the panel's Send at 1312,850, which is the element at its own centre (`w5j-06-reader-panel-selection.png`). | **Pass** |
| W5k. Menu openers keep their keys | Reader, **Read** (mouse) → the menus opened by mouse: **Account** at 1366; header **More actions** and PDF toolbar **More actions** at 700 → Escape → Space on the opener → Escape | In all three, Escape closed the menu (`aria-expanded` went true → false) and focus stayed on the opener. Narration kept playing: "Pause" / "Reading..." at 1366, and the bottom player's pause icon at 700, while the counter advanced 3 → 5 → 6 → 8 → 11 of 37. Space on the opener opened the menu and didn't toggle play. | **Pass** |
| W5l. The project row's ⋯ menu | Documents tab **More actions for README.md** (mouse) → Escape; reopen → mouse click on empty page; reopen → Tab into "Upload new version" → Escape | `aria-expanded` read true when open and false after each close. Escape from the opener closed it, and so did Escape from inside, which returned focus to the opener. An outside click closed it too. | **Pass** |
| W5m. Query field at 390 px | Phone: **Chat mode** → **Show Sidebar** → session row → backdrop → the open card | The query field is **278×44**; **Search the web** is 116×44 and **Not now** 77×44; the card is 304×213; there is no horizontal scroll (scrollWidth 390). | **Pass** |
| W5n. Focus after reset | Admin console → Chat options **Search without asking** (mouse) → **Use the server setting** (mouse) | After the pick: "Set here.", with the radio focused and checked. After the reset: focus is on the **checked "Ask first" radio** (`INPUT radio`, checked), with "The default." and no reset button. `GET /v1/admin/chat-options` = `{"web_search":"ask","source":"default"}`. The audit shows `old=ask new=auto`, then `old=auto new=None`, by user id. | **Pass** |

### W1 count

The same first question, "I'm a newcomer on RHEL 9. Which packages do I need to install to run this?", was asked in four project chats, each opened with **Ask about this project**:

| Chat | Shape | Tools | What the text cited |
|---|---|---|---|
| first (W1a) | text only | 2 searches, then CHAT_WITH_PDF.md pages 1–2 | CHAT_WITH_PDF.md p. 1–2, README.md p. 4 |
| fresh 1 | **text + card** | 1 search, then the offer | README.md p. 3, plus a "General Guidance (Not from documents)" section |
| fresh 2 | text only | 2 searches, then CHAT_WITH_PDF.md p. 1 and README.md pp. 3–5 | README.md p. 3–4, CHAT_WITH_PDF.md p. 1 |
| fresh 3 | text only | 2 searches, then CHAT_WITH_PDF.md p. 1 | CHAT_WITH_PDF.md p. 1, README.md p. 4, p. 10 |

- **Text plus card: 1. Card only: 0. Text only: 3.** Over the three fresh chats alone, that's 1 / 0 / 2.
- The card-only reply that both J3 runs in the first walk got never appeared.
- No reply asked in words whether to search.

### Privacy check

The fake SearXNG logged **1 query** all walk:

| Time | Query | Approval |
|---|---|---|
| 13:18:48 | "RHEL 9 install Python 3.12 Node.js 22 Podman packages" | The **Search the web** click at 13:18:47 (W1d), on the card query Olive had edited |

**Unapproved queries: 0.** The other five offers (W3, W4a, the two W4b cards and the fresh-1 card before approval) sent nothing. The auto mode in W5n was set for about two seconds, and no message was sent during it.

The server logs (`server.log` 378 lines, `audit.log` 22, stdout 380) were searched for:

- `@example.com` and `%40`;
- RHEL, Podman, podman and "packages";
- the fetched sites, the project name and README.md;
- phrases from the W1–W4 questions.

The only hits were the bootstrap line `Bootstrap admin email set to walkadmin@example.com` and "packages" inside a `site-packages` path in an authlib warning. The audit records ids only.

### Findings

Severity follows the review ledger. "Seen" is what the screen, the stream or the saved reply showed. "Inferred" is my reading of why.

1. **Important: in the project chat, the RHEL question usually gets no offer.**
   - **Seen.** In 3 of 4 chats the model answered from the documents and never called `offer_web_search`. Each of those replies says the documents lack what was asked: "the documentation doesn't provide a specific dnf command for RHEL 9" and "the documents do not provide a specific list of RHEL 9 dnf packages". None has a card. A newcomer who wants the dnf names has no one-click path; they must know to type "search the web for …". EVAL.md's diagnostic counts text plus card in 5 of 6 runs for `rhel-packages`.
   - **Inferred, not verified.** The eval asks "I'm on RHEL 9, which packages do I need?". The walk asks "I'm a newcomer on RHEL 9. Which packages do I need to install to run this?". "To run this" points at the app, which the documents do cover, so the model may see no gap. I didn't ask the eval's wording in the app.
2. **Important (answer quality): 2 of 4 RHEL answers gave requirements from the older CHAT_WITH_PDF.md that would not run this app.**
   - **Seen.** The first chat and fresh 3 both read `read_document_pages(document=2, first_page=1)` and answered "Node.js 18+", "Python 3.10+" and "Docker", citing "(CHAT_WITH_PDF.md, page 1)".
   - **The fixtures say otherwise.** README.md's requirements table gives Node.js 20.19+ or 22.12+ ("older Node will fail to start") and Python 3.12–3.13 ("The chat server needs 3.12+"). CHAT_WITH_PDF.md's table, from v1.6.0, says 18+ and 3.10+.
   - **Stakes.** A newcomer who follows the answer installs a Node and a Python that the app refuses. Fresh 2 got README's numbers right. The answer cites its page correctly, so the citation checks pass. The fault is choosing the stale source.
   - **Inferred.** Nothing in the map or the rules tells the model which document is current when two disagree.
3. **Minor: in the approved web answer, the lines not taken from a page carry neither a site nor a general-knowledge label.**
   - **Seen.** The answer gave "`sudo dnf module enable nodejs:22`" and "`sudo dnf install podman`" with no site. Only result 1 was read. podman.io's extracted text does contain `sudo dnf install podman` and a "# RHEL 9+" line, so reading result 2 would have given a site.
   - **Not checked:** whether the approved-turn rules ask for a label on general knowledge.
4. **Minor: "search the web for X" brings the card back, but X can change.**
   - **Seen.** In the chat that held the stopped Podman reply, the card was prefilled with "RHEL 9 python 3.13 packages". The typed text was "RHEL 9 python packages". In a fresh chat it was exact.
   - Consent still holds: the user sees and can edit the query before anything leaves, and SearXNG got nothing.
   - The prefill is the model's call to `offer_web_search`; no code copies X into it.
   - **The answer under that card was also wrong in two ways.** It said "Since I am currently waiting for the search results", though nothing runs until the click. It also suggested `sudo dnf module install python3.11`, while the Red Hat page says Python 3.11 ships "as the python3.11 package suite".
5. **Minor: after Stop, the saved text runs a few words past what the screen showed.** At the click the screen read "…for today, October 20,". After a reload the reply ends "…October 20, 2026,". **Inferred:** the server kept the deltas it had made before the abort reached it.
6. **Minor (pre-existing): a browser reload opens a new empty chat**, not the one you were in. The chip shows "No document". The chat is one click away in **Sessions**, and the J7 path is the same.
7. **Minor (pre-existing): on the phone, the open sidebar covers its own Hide Sidebar button.**
   - **Seen.** At 390 px, the header's **Hide Sidebar** (16,10 44×44) sits under the sidebar's blank top strip: `elementFromPoint` returns the `ASIDE` (`fixed … z-40`, `pt-16`). A mouse click on the button can't reach it.
   - Tapping the dimmed backdrop closes the sidebar (verified at 355,400), so the user isn't stuck.
   - The first walk's "Hide Sidebar" step used DOM `click()`, which skips hit-testing, so it couldn't have seen this.
8. **Walk note: one wrong click of mine.** In W3 my first "New chat" locator matched the Conversation section header, which collapsed the section. My fallback then switched the open features chat's chip to "No document". No message was sent from that state, the features chat kept its project label in **Sessions**, and W2 had already been recorded. The W3 chat was then started with the sidebar's **New chat** ("Start a fresh chat (saved session preserved)").

### Not checked

- **The note's exact text as sent to the model.** The stream doesn't carry it. Form (3) is inferred from W3's wording and `offer_note()` in `server/chat/web_consent.py`.
- **Note form (2)**, one document: no single-document offer was walked.
- **The answer step's other paths:** budget exhausted, a provider failure, and Stop on an approved turn where the model offers again.
- **The chat side panel at phone width.** (j) at 390 px covered the Chat view only.
- **The PDF toolbar menu with a real PDF.** It was tested with README.md, a Markdown document, at 700 px, where it shows.
- **The RHEL count with the eval's own wording.**
- **Not now.** It wasn't re-walked; first walk J4 covers it.

### Removed afterwards

- **Processes.** Each PID was checked by its command line, then stopped with SIGTERM, and all exited:
  - the driver 4021434, after its browser was closed;
  - the backend 4020548;
  - Vite 4021011;
  - the fake SearXNG 4020088.

  No Chromium process was left. Ports 8000, 5173, 18081, 18888 and 18990 are free.
- **Container.** `nr-walk-keycloak` was stopped and removed. Only `nr-c21-testpg` remains, still running.
- **Database.** `natural_reader_walk` was dropped. `nr-c21-testpg` keeps `natural_reader_eval` and `natural_reader_test`, which I didn't touch.
- **Scratch files deleted:** the passwords file, the realm copy (it held the passwords too), the client secret and session secret files, the scratch document store, the scratch `HOME` and the fetched page copies.
- **Kept** in `/tmp/claude-1000/-home-fire310w-Documents-Github-natural-reader/ed5a4c77-101f-455a-a6c0-8d9f09fac689/scratchpad/rw/`:
  - `shots/`: 41 screenshots, e.g. `w1-03-fresh1.png`, `w1-06-web-answer.png`, `w3-02-answer.png`, `w4-01-after-stop.png`, `w5j-06-reader-panel-selection.png`, `w5m-01-phone-card.png`;
  - `logs/`: `searxng-queries.log`, the SSE bodies of each turn, and the backend and Vite output;
  - `logdir/`: the server and audit logs;
  - `pids.txt`;
  - the scripts: the driver, the fake SearXNG and the backend launcher, which holds no secret.

Nothing was committed, and no tracked file other than this one was changed.
