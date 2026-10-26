# User Guide — accounts, tokens, admin, and chat budgets

This is the **end-user guide** for the multi-user features: signing in, your
account, personal access tokens, the admin section, and the chat inference
budget. (For developer internals see [ARCHITECTURE.md](ARCHITECTURE.md) and
[IDENTITY_AND_ROLES.md](IDENTITY_AND_ROLES.md); reading features are covered in
the [README](../README.md).)

- [Signing in](#signing-in)
- [Where things live: Settings, profile menu, Admin](#where-things-live-settings-profile-menu-admin)
- [Listening while you do something else](#listening-while-you-do-something-else)
- [Profile: your picture, password and signed-in browsers](#profile-your-picture-password-and-signed-in-browsers)
- [Access tokens](#access-tokens)
- [Admin console (admins only)](#admin-console-admins-only)
- [Chat: models and streaming](#chat-models-and-streaming)
- [Asking a whole project](#asking-a-whole-project)
- [When the assistant offers a web search](#when-the-assistant-offers-a-web-search)
- [The daily token budget](#the-daily-token-budget)
- [FAQ](#faq)

---

## Signing in

Open the app and you'll get a sign-in screen. Clicking it sends you to your
organization's login page (Keycloak or whatever identity provider your
deployment uses); after logging in you land back in the app automatically.

Depending on your account state you may see one of these screens instead:

| Screen | Meaning | What to do |
|---|---|---|
| **Waiting for approval** | Your account was created on first login but an admin hasn't activated it yet | Ask an admin to activate your account |
| **Account disabled** | An admin disabled your account | Contact an admin |
| **Backend error / not responding** | The server is down or unreachable | Check with whoever runs the deployment |

You stay signed in on that browser until you click **Log out** in the
**profile menu** (top-right of the header), until you haven't used it for 12
hours, or for at most 7 days (both are your deployment's defaults:
`SESSION_IDLE_HOURS`, `SESSION_TTL_HOURS`).

## Where things live: Settings, profile menu, Admin

Accounts, tokens, and settings are **not in the sidebar** — the sidebars hold
navigation (reader) and the model picker + sessions (chat). Everything about
*you* lives at the top of the window:

- **Profile menu** — your picture (or a person icon) at the top-right of the
  header. Your name, **Profile**, **Settings**, the dark-mode toggle, and
  **Log out**.
- **Settings** — a window over whatever you have open, from the header
  **gear** or the profile menu, so the narration, a reply being written and
  your place in the document all carry on underneath. Tabs: **Profile**,
  **Voice & reading**, **Chat**, **Appearance**, **Connection** and **Access
  tokens** (the only place tokens are created). **Esc**, the **×** or a click
  outside closes it; it opens on the tab you used last.
- **Admin** console — a separate view opened from the **shield** button in the
  top view switcher (Reader / Chat / Library / Admin). Only admins see it. User
  management only; **no token creation happens here.**

## Listening while you do something else

The narration keeps reading whichever view you open: Chat, Library, Admin or
Settings. Outside the reader, a small control in the header shows the
document and page with **pause**, **stop**, and the name, which takes you back
to the page being read. Pause keeps the control there; Stop removes it.
Opening a different document pauses the narration.

A reply you asked for keeps being written when you leave Chat, and is there
when you come back.

**Chat beside the page.** In the reader, the **speech-bubbles button** in the
header opens the chat in a column beside the page: the same chats as the Chat
view, with its own model picker, **New chat**, and **Open in the Chat view**.
With it open, **Ask page** and **Ask AI** on a selection add the page or text
to it and keep you in the reader. It needs a wide screen; on a phone those
open the Chat view instead (the narration carries on there too).

**One voice at a time.** Reading a reply aloud pauses the narration, and the
header says **Paused for the chat reply**. Press **Read** (or the play button)
to carry on; that also stops the reply's read-aloud.

**Read Selection.** Select text in the reader and **Read Selection** reads it
aloud (**Ask AI** beside it sends it to the chat). The two buttons don't show
in the Chat view, where they covered the Send button: to hear a reply, use the
reply's own **Read aloud** button. A selection still being read when you
switch to Chat keeps its **Stop** at the top of the view.

## Profile: your picture, password and signed-in browsers

Open **Profile** from the profile menu (or the Profile tab in Settings).

- **Picture:** **Add a picture** takes any image; it is cropped to a square
  and shrunk to 256 px in your browser before upload. It shows in the header.
  **Remove** takes it away.
- **Name, email, password and two-step sign-in** belong to the account you
  sign in with (Keycloak), and your name and email here follow it each time
  you sign in. **Manage your account** opens that account's own page, where
  you change them.
- **Signed-in browsers** lists each browser signed in to your account (marked
  **this browser** for the one you're using), when it was last used, and a
  **Sign out** for each other one, or **Sign out everywhere else**. That ends
  the browser's session here. A browser still signed in to your sign-in
  account can come back without a password until that ends too: end it under
  **Manage your account**.

## Access tokens

Open **Settings → Access tokens**. Your email and role (`admin` or `member`)
are on the first line.

Below that is the **token manager**: what the token may do (**Access**), when
it expires, a name box, a **Create** button, and your list of tokens. Each
line in the list says what that token can do, when it was last used and when
it expires, with a **Revoke** button.

### Why do these tokens exist?

Your normal login uses a **cookie** the browser attaches automatically —
perfect for the web app, useless to anything that isn't the app in your
browser. A **personal access token** is a long-lived secret
(`nrp_…`) that proves "I am this user" in a single HTTP header:

```
Authorization: Bearer nrp_your_token_here
```

Things that need one:

- **The Chrome read-aloud extension.** It can't do the interactive login, and
  it can't borrow your cookie. You paste a token into it once and it
  authenticates from then on.
- **Scripts, cron jobs, curl, notebooks** — anything calling the server's API
  outside a browser.

If you only use the web app itself, you never need a token. Creating one is
purely optional.

### Creating one

1. Open **Settings** and go to the **Access tokens** tab.
2. Under **Access**, tick only what the token is for. **Read aloud** is ticked
   by default, and that's all the extension needs.
3. Pick when it **Expires**: 30 days, 90 days, 1 year (the default) or Never.
4. Type a name (e.g. `my-laptop-extension`) and click **Create**.
5. The token appears once, with a **Copy** button. **Copy it now** — the server
   stores only a hash, so it can never be shown again. If you lose it, revoke
   and create a new one.

| Access | The token can | Your account needs |
|---|---|---|
| Read aloud | turn text into speech (the extension) | `reader` |
| Documents & projects | everything in the library and projects, people search, and read aloud | `reader` |
| Chat | chats and models | `chat` |
| Admin | the admin console's API | `admin` |

Only the kinds your account has are offered. A token never gets more than your
account: if an admin takes `reader` away from you, your read-aloud tokens stop
working too. **No token can create, list or revoke tokens**; only the signed-in
app can, so a leaked token can't make itself a replacement. Tokens made before
2.3.0 show as **Full access (old)**: they can do everything you can,
including managing tokens. Replace them with narrower ones and revoke them.

To use it in the extension: right-click the Read Aloud icon → **Options**,
paste the token into **Access token**, and click **Test connection**. It
should say *Connected as* your email. The token stays on that computer: the
extension keeps it in local storage, not in Chrome sync. If your server is not
on `localhost`, Test connection also asks Chrome to let the extension reach
it; allow it, or every read fails.

Treat a token like a password: anyone holding it can do whatever its access
allows, as you, until it expires, is revoked, or your account is disabled. Give
each device/purpose its own token with only the access it needs, so a leak
costs little and you can revoke them independently.

### Revoking

Click **Revoke** next to a token's name. Whatever was using it stops working
immediately. Revoking is the correct move for a lost laptop, a shared
screenshot, or just cleaning house.

## Admin console (admins only)

If you see a **shield** button in the top view switcher (Reader / Chat /
Library / Admin), you're an admin — click it to open the Admin console. (It's
that button only; the profile menu has no Admin entry.) It opens on
**Overview** and is split into sections, in a list down the left side. On a
phone the list becomes a picker at the top. With the list focused, **Up** and
**Down** move between sections (wrapping at the ends), **Home** and **End**
jump to the first and last.

| Section | What it holds |
|---|---|
| **Overview** | Whether the deployment works right now ([below](#overview-does-it-work-right-now)) |
| **People** | Every account, enrollment, roles, budgets, project limits |
| **Projects** | Every project, ownerless ones, taking one over |
| **Models** | The reader, context sizes, windows, which providers are local, which chat models are offered ([below](#models-the-reader-context-sizes-and-which-models-people-get)) |
| **Documents** | Limits on Docling conversion ([below](#documents-what-a-conversion-may-use)) |
| **Chat** | The assistant's profile and the web search mode |
| **Defaults for users** | What read-aloud, voice, speed and thinking show for someone who hasn't chosen them |
| **Usage** | Token use per user and per day |
| **Deployment** | The deployment timezone, and the server's own values read-only |

**People** lists every account, 50 at a time. Over the list:

- **Search people** (the box that says "Name, email or username") finds
  accounts as you type, after a short pause (300 ms). It matches part of an
  email, username, display name, first or last name, or the two together
  ("Ada Lovelace"), in any letter case. The server does the searching, so it
  covers every account, not only the ones already on screen.
- **Status** (All, Active, Pending, Disabled) and **Capability** (Any, or one
  capability such as `docling`) narrow the list. They combine with the search:
  a person has to match all of them.
- A line under them reads "Showing 50 of 212": how many are on screen, and how
  many match. **Show more** loads the next 50. With nothing matching, the list
  says "No one matches."
- Changing someone (a button, a capability tick, a budget) keeps your search
  and filters, and the rows you had opened with **Show more**, up to 200 rows;
  past 200 the list reloads as its first 200.

Each row has buttons per user:

| Button | Effect |
|---|---|
| **Activate** | Moves a `pending` account to active — the user can sign in (do this after you've vetted them) |
| **Disable** | Signs the user out **everywhere, immediately** and blocks them until reactivated. Their data is kept |
| **Make admin / Make member** | Grants or removes admin rights. Takes effect on the user's very next request — no re-login needed on the server's side; their open tab refreshes its view of the role on next page load |

Notes:

- You can't disable or demote **yourself** from this list (the buttons are
  hidden on your own row) — that's a lockout guard in the UI.
- New users appear here automatically, as `pending`, the first time they log
  in.
- **Offboarding order matters.** To remove someone: click **Disable here
  first** — that signs them out everywhere and blocks their tokens — and only
  then delete them in the login system (Keycloak), if that's how you manage
  accounts. Deleting them in Keycloak *alone* does **not** remove them here:
  their account stays active, any already-issued tokens keep working, and that
  email address becomes permanently unusable (a new account with the same
  email will be rejected with "email already linked to another identity").
- Roles are managed **here, not in the login system** — your organization's
  login (Keycloak) only verifies who you are; whether you're an admin is a
  fact this application owns.
- Each person's daily token budget is the **Budget** box on their row under
  **People** (empty = the deployment default, 0 = unlimited; press **Set**).
  **Usage** shows tokens per person and per day.
- The **Deployment** section's read-only list shows every configured model
  provider as `kind · URL` (for example `ollama · http://host:11434`), so you
  can see at a glance what's wired up without reading `.env` on the server.
  API keys are never shown here or anywhere else: a key reads only **set** or
  **not set**. The embedding server has its own row, **Embedding server**
  (that's the only thing the `OLLAMA_URL` setting still controls; chat models
  come from the providers). Whether a provider answers, and how many models it
  lists, is in **Overview → Model providers**, not here. There's still no
  button to add or change a provider: that's a `.env` + restart task for
  whoever runs the server. The [full list](#deployment-the-timezone-and-the-servers-own-values)
  is below.
- The **Assistant profile** (in **Chat**) sets the assistant's profile: its name,
  personality, tone and house rules (for example "You are Ada, the
  reading-room assistant. Answer warmly and briefly, in British English.").
  - It goes first in every chat, from the next message on; the app's own
    rules about documents and tools follow it and win where they conflict.
    So a profile can't, for example, stop the assistant citing pages.
  - The badge says where it comes from: **Set here**, **From the deployment
    file** (the server's `CHAT_ASSISTANT_PROFILE_FILE`), or **None**.
    **Reset to deployment default** removes what you set here.
  - **What the model receives** shows the whole instruction text a chat
    gets, your profile included.
  - Keep it short: every character is read before every reply (the counter
    shows about how many tokens), and up to 8,000 characters are allowed.
  - Don't name the app's tools (`web_search` and so on): describe the
    behaviour you want. The section warns you if you do.
- **Chat options** (also in **Chat**) has one setting so far, **Web search**: **Ask first** (the
  default: the assistant can only offer a search and the person says yes on a
  card), **Search without asking**, or **Off**.
  - There is no Save button: picking a choice saves it at once. A change
    applies from each person's next message and shows a notice
    ("Web search: Ask first. Applies from the next message.").
  - The line under the choices says where the current value comes from: **Set
    here.**, **From the server setting CHAT_WEB_SEARCH.** or **The default.**
    When it was set here, **Use the server setting** removes your choice, so the
    deployer's value applies again.
  - With no search engine configured on the server, the choices are disabled and
    the section says *Web search isn't set up on this server (SEARXNG_URL).*
  - After a change, the Assistant section's **What the model receives** updates
    to show the rules a document chat gets in that mode.

### Settings that share one look

Every setting in **Chat options**, **Models**, **Documents** and **Deployment**
says where its value comes from and can go back to the server's, the same way.
**Models**, **Documents** and **Deployment** also share one Save; **Chat options** saves each choice as you
pick it (see above), so it has no Save, no draft and no unsaved-changes question.
The **Assistant profile** and **Defaults for users** keep their own Save buttons.

- A line under the setting says where its value comes from: **Set here.**
  (you chose it in the console), **From the server setting** `NAME` (the
  deployer's `.env`), or **The default.**
- **Use the server setting** appears once you've set one here. It removes
  your choice, so the deployer's value (or the default) applies again.
- In **Models**, **Documents** and **Deployment**, a section has **one Save**, for
  everything you changed in it. Nothing is written until you press it.
  **Discard changes** puts the section back as saved.
- Leaving a section with unsaved changes (another section, a phone's picker,
  **Back to reader**, or the header's Reader / Chat / Library buttons and Log
  out) asks first: **Keep editing** stays and keeps your changes, **Discard
  changes** leaves without them. **Esc** is the same as Keep editing. The same
  question covers an unsaved Assistant profile or Defaults change. Reloading or
  closing the browser tab is not guarded.
- A change applies at once, on every worker: the next request, message or
  conversion reads it. There is no restart.
- **The console can only narrow what the server allows.** If `.env` allows
  only the Fast and Standard presets, ticking Accurate here changes nothing,
  and the section says the server setting turned it off.
- Every saved change writes a line to the audit log: who, which setting, the
  value before and after.

### Overview: does it work right now?

**Overview** runs a check for each part of the deployment that can be quietly
broken, and shows a state for each: **OK**, **Warning**, **Problem** or
**Off**, then one line of why. **Check again** runs them again. A check that
hangs (a provider or a search server that accepts the connection and then goes
silent) is cut off after 5 seconds with "Didn't answer within 5 seconds.", and
the others still show. A line's **Open …** link jumps to the section that
changes it.

| Check | What it looks at |
|---|---|
| **Model providers** | Which providers answer, and how many models each lists |
| **Reader** | The model that reads documents and web pages for the assistant, and where it comes from. The chat model itself is **OK**; a named model is checked: is it in the deployment's model list (`INFERENCE_MODELS` or the provider's own list; an **Error** if not, from wherever the reader is set), and is it pulled on its provider? When the reader is the chat model and some chat model is a cloud one, the line adds that reader calls count against that model's quota, and points to **Models** |
| **Context windows** | Each chat model's window, where the number comes from, and whether a project-chat turn fits in it |
| **Embeddings** | Whether the embedding model answers, and returns the width (768) the database holds; and how many documents are waiting to be rebuilt |
| **Docling** | Whether conversion is on |
| **Web search** | Whether the search server answers |

**Context windows** gives each model the number the app works with, and says
where it came from. First match wins:

1. a self-hosted Ollama model that is loaded right now: **Loaded now (Ollama)**,
   with the size it was loaded at;
2. a self-hosted Ollama model that isn't loaded, whether its provider is marked
   **Local** or **Cloud**: the size requests to it send: its own size, or the
   deployment's size, set in **Models** or by `INFERENCE_LOCAL_NUM_CTX`; with
   neither, 16,384 tokens (lower if `INFERENCE_NUM_CTX_MAX` is lower), "unless a
   person sets their own in Settings";
3. an Ollama cloud model (a name ending `-cloud` or `:cloud`, or the ollama.com
   host): its maximum;
4. any other model: what its provider reports;
5. else what you declared under **Models -> Declared context windows** (or
   `INFERENCE_<NAME>_CONTEXT_WINDOW`);
6. else **Unknown: the provider doesn't report one. Declare it in Models.**

Ollama's own maximum for a local model (131,072, say) is never shown as its
window: Ollama loads the model at a smaller size, and the number the app trims
history to must be the one it was loaded with. If every chat model chosen in
**Models** has gone from the providers' lists, the line is a **Warning**: "None
of the chat models chosen in Models is listed by a provider: nobody can chat."

### Models: the reader, context sizes, and which models people get

**Admin console -> Models** sets how the server uses the models it can reach.
A change applies from the next message, on every worker, with no restart. The
section saves once (**Save**, **Discard changes**), and each setting's source
line and **Use the server setting** work as [above](#settings-that-share-one-look).
The models and providers it lists come from the providers themselves: above the
settings it says **Loading the models...** while it asks, or **Couldn't load the
models.** if that fails (the settings still show, with empty model lists).

| Setting (the label on screen) | What it does | Server setting (default) |
|---|---|---|
| **Reader** | The model that reads documents and web pages for the chat model. **The chat model of each conversation**, or a model from the list (a model the console doesn't list stays selectable if it is already set). Save refuses a model outside the deployment's model list: "That model isn't in this deployment's model list." | `READER_MODEL`, then `SUMMARIZE_MODEL`, then `WEB_SEARCH_SUMMARY_MODEL` (the chat model) |
| **Context size for local Ollama models** | One size, in tokens, for every request to a model the app sizes (below). A whole number from 2,048 to 2,097,152 (Save stays off while a box holds anything else); the line under it names the ceiling `INFERENCE_NUM_CTX_MAX` | `INFERENCE_LOCAL_NUM_CTX` (unset: each person's own setting, else 16,384) |
| **Per-model sizes** | A size for one model, which beats the size above. One box per model the app sizes; empty = the size above | none |
| **Declared context windows** | The window history is trimmed to for a model whose provider reports none. One box per such model; 1,024 to 10,000,000 | `INFERENCE_<NAME>_CONTEXT_WINDOW` per provider (`<NAME>` as in `INFERENCE_<NAME>_URL`) |
| **Providers** | **Auto**, **Local** or **Cloud** for each provider; **Auto** names what it guessed from the address ("Auto (cloud)") | none |
| **Chat models offered** | Tick the models people may chat with. None ticked = every model the server allows | none (`INFERENCE_MODELS` and each provider's own allow-list still apply) |

**Which models get a context size.** Ollama loads a model at 4,096 tokens on a
machine with under 24 GiB of VRAM unless each request names a size, and cuts the
start of a long prompt without saying so. So the app names one on every request
to a **self-hosted Ollama model**, whatever its host name and whether you marked
its provider **Local** or **Cloud**. Not sized: Ollama cloud models (a name
ending `-cloud` or `:cloud`, or the ollama.com host), the only Ollama models
without a size, which get the window their provider reports; and every model on
an OpenAI-compatible provider (that API has no such option). For those the app
never sets a size of its own.
A person's own Settings value, if they chose one, is still passed on with each
chat message (clamped to the ceiling; an OpenAI-compatible provider ignores
it). For those models the reader's calls send none; a reader on a sized model
gets a size (see **The reader** below).

For a sized model the size is the first of these that is set, then clamped to
`INFERENCE_NUM_CTX_MAX` (32,768 unless changed):

1. **Per-model sizes** for that model;
2. **Context size for local Ollama models** (the console's, else
   `INFERENCE_LOCAL_NUM_CTX`);
3. the person's own **Settings -> Chat -> Context window**;
4. 16,384.

A bigger size reserves more memory when the model loads, so on a machine short
of RAM lower it here rather than leaving 16,384.

**What the person sees.** In **Settings -> Chat**, a self-hosted Ollama model's
**Context window** row reads "Set by your admin: N tokens." and is locked when
steps 1 or 2 set a size (their own value stays stored, and applies again once neither **Models**, the
server's `INFERENCE_LOCAL_NUM_CTX` nor a per-model size sets one for that model). Otherwise its **Auto** option reads "Auto (16,384 on this
server)", the size it will really get.

**The reader.** With **The chat model of each conversation** the reader uses the
model already loaded, at the chat's size (on a sized model, the size from the
list above: its per-model size, else the deployment's, else the person's own
value if they chose one rather than **Auto**, else 16,384, clamped), so
Ollama doesn't swap models or reload one between
chat and reader calls; it is the default on a deployment
that names no reader. The price is for a cloud chat model: reader calls count
against that model's quota (and the daily budget either way). To keep them off
it, pick a small local model: it loads at a size from the list above without
step 3 (its per-model size, else the deployment's, else 16,384, clamped), never
the person's. A reader on a model the app doesn't size (Ollama cloud, an
OpenAI-compatible provider) sends no size. Each
reader batch is cut to fit its model's window: never above `READER_INPUT_CHARS`,
and at most 16 passages.

**Providers: local or cloud.** This flag is about cost, not sizing: the
Overview's quota hint, usage figures to come, and the **Auto** label. **Auto**
guesses from the provider's address: `localhost`, a private or loopback
address, `100.64.0.0/10` (Tailscale), a single-word name such as `ollama`, or a
name ending `.local`, `.lan`, `.internal` or `.home.arpa` is local; anything else
is cloud. Choose **Local** or **Cloud** if the guess is wrong. The flag never
changes sizing: an Ollama you host is sized even when you mark it **Cloud** (a
rented GPU box whose usage should count as cloud, say), because its Ollama still
loads at 4,096 tokens unless told otherwise.

**Chat models offered.** The picker in Chat and **Settings -> Chat** shows only
the ticked models, and a request for another is refused ("That model isn't
available on this server."). The console can only narrow: the list is
intersected with what the server allows, never widened. If you tick models and
they later disappear from the providers, people are offered no model until you
untick or choose others (the Overview warns).

**Not here.** Adding or changing a provider is still a `.env` and restart task,
and so is the embedding model.

### Documents: what a conversion may use

Docling turns a PDF into Markdown (the reader offers **Convert** for PDFs). It is
the heaviest job the server does, so **Documents** lets you narrow it. It
appears only as a limit: the server's `DOCLING_ENABLED` (see the Deployment
list) still decides whether conversion exists at all.

| Setting | Choices | Server setting (default) |
|---|---|---|
| **Who may convert** | **Everyone with the reader capability**, or **Only people with the Docling role** (admins always can) | `DOCLING_CONVERT_BY` (`readers`) |
| **Presets** | Fast, Standard, Accurate; at least one | `DOCLING_PRESETS` (`fast,standard,accurate`) |
| **Allow OCR** | on / off | `DOCLING_ALLOW_OCR` (`true`) |
| **Allow picture descriptions** | on / off | `DOCLING_ALLOW_PICTURE_DESCRIPTION` (`true`) |
| **Largest conversion** | Pages one conversion may cover; 0 = no limit (an empty box is refused, and Save stays disabled until it holds a whole number) | `DOCLING_MAX_PAGES` (`0`) |

- **Accurate runs OCR, so it's off whenever OCR is off.** The section says so
  and the stored choice of presets is kept: allow OCR again and Accurate comes
  back.
- **The Docling role** is the `docling` capability. Give it to someone with
  its checkbox under **People** (it applies at once), or as a realm role in
  Keycloak (it applies at their next login). On a Keycloak set up before this
  release, create the `docling` realm role once first; until then the tick says
  "Keycloak has no “docling” role yet" and changes nothing. See
  [IDENTITY_AND_ROLES.md](IDENTITY_AND_ROLES.md#the-docling-capability).
- Under the settings, the section lists the server's upload limits
  (`PDF_UPLOAD_MAX_MB`, `TEXT_UPLOAD_MAX_MB`); those are read-only.
- **What people see.** In the reader's **Convert** dialog, an option that is
  off is greyed out with **Turned off by your admin**. With a page limit, Convert
  stays disabled with "This server converts at most N pages at a time. Choose a
  page range of N pages or fewer." until the range fits. A person who isn't
  allowed to convert at all sees "Only people your admin has given the Docling
  role can convert documents on this server." instead.
- **The server enforces it, not just the dialog.** A request that breaks a rule
  (a script, an old tab) is refused with 403 `convert_not_allowed` and a reason
  naming the rule; nothing starts converting.
- A PDF whose page count isn't known yet is counted from the stored file, so
  neither a 600-page file slips past a limit nor a 3-page one is refused.

### Deployment: the timezone and the server's own values

**Timezone** decides where a day starts and ends for three things: the daily
token budget (it resets at the next **local** midnight, including the night
the clocks change), the days in **Usage**, and the "Today is …" line a chat
falls back to when the browser sent no zone. It also sets the day shown beside
each account in **People**. Pick a zone by its name (`Asia/Colombo`). Its source
line reads **Set here.**, **From the server setting** `TZ`, or **The default.**
(UTC; before this setting existed these days were always UTC). It applies from
the next request.

Under **Server settings (read-only)** the section lists what the server runs
with and which variable sets each: model providers, allowed models, chat
timeout, daily token budget, embedding model and server,
document storage, Docling, web search server, and server workers. These can
only be changed in `.env` on the server, with a restart. **Nothing secret
shows:** the session secret, sign-in client secret and Keycloak admin client
secret read only **set** or **not set**, and a URL never shows a password or
query.

**Projects.** Every project, with its owners and counts. **Ownerless only**
shows projects whose last Owner was deleted; **Add me as Owner** takes one
over (recorded as "added by admin" and logged). **Open** shows the project
page; as an admin you can manage members, rename and delete any project, but
you see its documents only if you're a member. Each user row has a
**Project limit** (empty = the deployment default, 0 = unlimited). If the list
can't be loaded, the section shows the error and a **Retry** button rather
than an empty list.

## Library: documents on the server

**Library** (in the view switcher) lists every document you can read on the
server: the ones you uploaded, the ones someone shared with you ("shared
by …"), and the ones in a project you own or belong to ("via project").
**Open** on a row opens the document in the reader, the same as picking the
file from your computer. It is also saved to your own "Your Library" list on
the welcome screen, and Index and chat work on it straight away. If the
server has no copy of the file (it was indexed before server-side uploads,
and nobody has uploaded it since), Open tells you to upload the file again.

## Projects and people

A project is a shared shelf of documents for a team. Open **Library →
Projects** to see the projects you belong to; **New project** creates one,
and you become its Owner.

**Roles.** Each member has one role:

| Role | Can |
|---|---|
| Reader | see the project, its members, activity and documents |
| Contributor | also file documents they uploaded into the project, upload new versions and restore old ones |
| Maintainer | also remove any document, delete old versions, merge documents, rename the project, and add, remove or change members up to Maintainer |
| Owner | everything, including Owners and deleting the project |

A project always keeps at least one Owner, so the last Owner can't leave until
someone else is made Owner. You can't change your own role; another Owner (or a
Maintainer, for roles up to Maintainer) can change it for you.

**Adding people.** On a project page, open **Members → + Add people**, type a
name or their full email address, pick the person, choose a role and press
**Add**. Whether typing a name finds people depends on your organisation's
setting; a full email address always works. An admin always finds people by
two letters of a first name, last name, username or email (or "First Last"),
and sees their email addresses. "Awaiting approval" means the
account exists but an admin hasn't activated it yet. A person whose account an
admin has disabled shows "disabled": their role can't be changed, but a
Maintainer or Owner can still remove them.

**Documents.** **Documents → File a document** adds one of your uploads. If the
project already has a document with that name (letter case and spaces ignored),
or the exact same file as an older version, you're asked: **Add as new version**
(or **Restore vN as current**), **Keep both**, or **Cancel**.

**Versions.** Each document keeps its history. **v3 · 3 versions** opens it:
every version can be opened; Contributors can **Restore** an old one (it comes
back as a new version on top, nothing is lost) and use **⋯ → Upload new
version** (a file, or one from your library, with an optional note);
Maintainers can **Delete** an old version for good, and use **⋯ → Mark as
newer version of…** to merge two documents that are really one (this can't be
undone, but every file stays in the history). The Library and the project's
document list show only each document's current version. If you open an old
version, chat answers from that version and says it's an old one. A citation
in an old chat opens the version it quoted, with a banner saying it's an old
version and a link to the current one.
If you keep your own copy of a document a project has since moved past, your
Library row says so and offers **Open current**.

A Maintainer removes a document with × (with all its versions; a file nobody
else has in their library is deleted with it, so you're asked first).

**Activity** lists who changed what, newest first. If the history can't be loaded, the tab says so instead of showing an empty list.

**Sharing one document with one person.** In the Library, press **Share** on a
document you uploaded, find the person, and pick them. They can read it but
can't share it on. The same panel lists who you've shared with; × stops
sharing. If the list of people can't be loaded, the panel shows the error and a
**Retry** button.

**Leaving.** **Leave** in the project header. Documents you had in your own
library stay there.

**Asking the project.** The **Ask about this project** button in the project
header (shown to members who have the chat permission) starts a chat about all
of its documents at once; see [Asking a whole project](#asking-a-whole-project).

What you can't do yet: compare two versions or review a version before it
becomes current (planned), or rename a document without uploading a new
version.

## Chat: models and streaming

Every chat message runs on the server — your browser never talks to a model
provider directly, and there's no per-device setup to do.

**What happens when you send a message:** the server checks your document (if
one's open) for relevant passages before the model even runs, then streams the
reply back token by token, saving it as it goes. If you click **Stop**, close
the tab, or lose your connection partway through, the partial reply is kept
and marked **Stopped** rather than lost. If you open a chat whose reply is
still being written somewhere else (another tab or device), you'll see
**Still generating…** until it settles. Only one reply can be in flight per
chat at a time — sending again while one is still streaming is blocked until
it finishes or you stop it.

**What the chat is about.** A chip above the chat box says what the assistant
may look at. Click it for the menu:

- **No document**: the assistant doesn't search or read any document in this
  chat, so a general question isn't answered from a document, and messages are
  lighter.
- **This document: *name***, listed while a document is open in the reader: the
  assistant can search and read that document. A new chat starts with this
  when a document is open. For an old version the chip adds *v2 (old
  version)*.
- One entry per project that holds the open document, shown as the project's name. Picking it makes the chip read **Project: *name***.
- **Choose a project…**, which lists the projects you belong to (or says
  *You're not in any project yet.*).

The project you pick is saved with the chat, so it is the same on every device
and the chat list shows its name under the chat. Choosing **No document** or
**This document** takes the chat out of the project. Whether the open document
is used is remembered per chat, in this browser. Pins you added still go with
each message, and citations in earlier replies still open their documents, even
after you change what the chat is about. See [Asking a whole
project](#asking-a-whole-project) for what a project chat does.

**Sources:** under a reply that used your document, chips such as
**Sources: p. 3 · p. 8** open the pages the answer came from, even when the
reply doesn't cite them itself. When nothing in the reply matches a page
(for example "the document doesn't cover this"), the chips read
**Searched (not cited)** and list the pages that were looked at. In a project
chat each document gets its own chip (**Guide.md · p. 3 · p. 6**), and pages
from the web show as links under **Web sources**.

**Citations:** when a reply used your document, the page numbers it cites
("page 4", "(page 4)") are links. Clicking one opens that document in the
reader at that page, opening it from the server first if it isn't the one
you have open. If you can no longer read the document, a notice says so
and you stay in chat. A reply still streaming when you click a citation
keeps going, and is there when you come back to Chat. In a project chat a
citation names its document, "(Guide.md, page 7)", and opens that document.

**Picking a model:** the model picker in the chat sidebar is grouped by
**provider**, each group labelled with the provider's name as your deployment
configured it (for example `ollama` or `local`, plus any other server your
deployment has) and shows what each model can do — whether it supports thinking,
tools, images, and so on. In **Settings → Chat**,
the per-model controls (context window, keep-alive, thinking level, max reply
tokens) only show the knobs the selected model's provider actually supports.
Your admin may have narrowed the list to some models (**Admin console → Models**),
and may have fixed the context size of the models you host: then **Context
window** says "Set by your admin: 16,384 tokens." and can't be changed.

**When a reply fails at the provider:** the message says why. "This model is
busy or rate-limited" (common with free models) means try again shortly or
pick another model. "The provider refused this request" comes with the
provider's reason (some providers refuse input their moderation flags).
"The provider rejected this server's credentials" and "The provider account
is out of credit" are for whoever runs your deployment. A request the
provider refuses outright doesn't count against your daily token budget.

**Adding or changing model providers is not something you can do from the
app.** There's no provider-management screen yet — an administrator adds,
removes, or reconfigures providers in the server's `.env` file and restarts
it. If you want a model that isn't in the picker, ask whoever runs your
deployment to add it.

## Asking a whole project

A project is a shelf of documents for a team. A project chat lets you ask the
shelf instead of one file: "What are the features?", "How do I deploy and test
this, step by step?", "Which document says what the default is?". The assistant
looks across all of the project's documents and tells you where each fact came
from.

**Start one**

1. Open **Library** and its **Projects** tab, and click a project.
2. Click **Ask about this project** in the project header.
3. The Chat view opens with a new chat whose chip reads **Project: *name***.
   Type your question.

You can also change an existing chat: click the chip, then the project (listed
when the document you have open is in it) or **Choose a project…**.

You need the chat permission and to be a member of the project. The button
shows only for members who have the chat permission. A site admin who is not a
member can open and manage the project but doesn't see the button, because the
assistant answers about a project's documents only for its members; add the
admin to the project first.

**What the assistant sees.** It is given a list of the project's documents (each
one's name, page count and headings) and can search them together, read exact
pages, and go through many pages for one goal, such as collecting every command
in order. A second, small model call does the bulk reading and hands back only
the facts, each with a quote the app has checked against the page, so the
assistant is not working from a paraphrase. Only each document's **current
version** is used. A document that is still being indexed, or failed to index,
is listed but can't be searched yet, and the assistant says so if your question
needs it. Reading many pages takes longer than a one-document question, most of
all on a slow model.

**Reading the answer**

- Each fact from a document is followed by its document and page, such as
  **(Guide.md, page 7)**. Click it and that document opens at that page in the
  reader. A document name that isn't in the project is plain text, not a link.
- Under the answer, **Sources** has a chip per document with the pages the
  answer used (**Guide.md · p. 3 · p. 6**); click a page to open it.
  **Searched (not cited)** lists documents and pages that were looked at but
  not used.
- When the project's documents don't cover part of your question, the
  assistant says so. It may add what it knows in general, introduced as not
  from the documents and never with a page. If it needs current or outside
  facts, it may offer a web search; see the next section, which also says what
  to do when it doesn't.

**Chats in the sidebar.** A project chat shows the project's name in blue under
its title in the chat list, and opens with the same project on any device.

**If you lose access to the project.** The chip and the list show **Project
unavailable**. Send a message anyway and you get the notice "This project
doesn't exist or you don't have access."; that message isn't sent (it goes back
into the box), and the chat is no longer about the project, so send it again as
a general chat or pick another scope.

**What isn't possible yet.** A chat is about one project at a time: asking all of
your documents at once, whatever project they are in, is planned and not built.
Older versions of a project's documents aren't searched.

## When the assistant offers a web search

By default the assistant **asks before it searches the web**. Nothing leaves
the server until you say yes. When it needs facts that its documents and its own
knowledge can't give (news, prices, a version number, a system the documents
don't mention), it writes what it can say without the web and then shows a card
under its reply:

> **Search the web?**  
> *the assistant's reason*  
> [ the search it proposes ]  
> **Search the web** · **Not now**

**The card doesn't always come, most of all in a project chat.** The assistant
may answer from the documents and what it knows in general, say what the
documents don't cover, and stop there without offering. If you want the web
then, type **search the web for** and what you want, such as "search the web
for RHEL 9 packages for Python 3.13". The card comes back with a query; the
assistant may word it differently from what you typed, and you can edit it
before you press **Search the web**.

- **The search box is yours to edit.** Change the words, up to 300 characters,
  and what you see is exactly what is searched. Press **Search the web** or
  Enter in the box.
- **Search the web** sends the message "Search the web for: ..." and runs that
  one search for the next answer. The assistant then reads up to three of the
  results and answers. Each fact from a web page is followed by its site,
  **(example.com)**, and the pages it read show as links under **Web sources**.
- **Approval is for one search.** If the assistant wants a different search, it
  shows a new card. Only the newest reply's card can be answered; an older one
  reads *Offered a web search*.
- **If you press Stop** while the assistant is writing the answer above the
  card, you keep the text so far but get no card for that reply. Ask again, or
  type "search the web for ..." with what you want, to bring the card back.
- **Not now** closes the card and sends nothing; the chat carries on. If you
  reload the chat while that reply is still the newest, the card is back.
- The card only shows what is searched, so read it: the query is the only text
  that leaves the server. The app stops the assistant from copying passages of
  your documents into a query, but it can't judge meaning, and a short sensitive
  phrase could still slip into one.

Your admin can change this under **Admin → Chat options → Web search**. With
**Search without asking**, there is no card: the assistant searches and reads on
its own and cites sites the same way. With **Off**, the assistant says it can't
look things up here. If the server has no search engine set up, no card appears.

## The daily token budget

Your deployment may cap how many tokens each user may spend per day (a day in
the deployment's timezone, UTC unless an admin set another under **Deployment**
— it resets at that zone's midnight). The chat sidebar shows **"N tokens left today"**
while you chat.

A reply you stop partway through, or one that fails, still counts against
this budget — using the token count the provider reported, or an estimate
based on how much text was generated if it didn't report one. Stopping early
doesn't refund the tokens already spent. The reading the assistant hands to
its separate reader, going through many pages of a project or reading web
pages, counts too.

When you run out:

- The send button disables while your budget is at zero.
- A request that slips through returns an error and you'll see a toast:
  **"Daily inference budget exhausted — resets at …"** with the reset time.
- Nothing is lost: your message stays, the document is fine, and everything
  comes back at the reset time.

Admins can raise individual users' budgets (API today; UI pending). If you
legitimately need more, ask yours.

## FAQ

**Where do I create a personal access token?**
In **Settings → Access tokens** (open Settings from the header gear or the
profile menu). The Admin console has no token features at all.

**Do I need a personal access token to use the app or the extension?**
App: no. Extension: yes — it's the only way it can authenticate.

**Can a token be shown to me a second time?**
No. The server stores only a fingerprint. Lost token = revoke + create.

**My colleague can't log in and sees "waiting for approval".**
That's the `pending` state every brand-new account starts in. An admin must
click **Activate** in the Admin section.

**I created a user in Keycloak but they don't show in the list.**
Expected — user *creation* only happens in your login system; the app first
hears about a user when they **log in for the first time** (then they show up
as pending, waiting for activation).

**I deleted a user in Keycloak but they're still in the list — and a re-hire
with the same email can't log in.**
Deleting in the login system removes their ability to *log in*, but their app
account — documents, tokens, and all — remains, and their email stays locked
to it. To properly offboard: **Disable** them in the Admin section first
(kills their sessions and tokens), then delete in Keycloak. To free the email
for a future user, the old app row must be removed by an operator
(database-level today; an admin-side "delete user" UI is a planned follow-up).

**The assistant asked me before searching the web. Can I turn that off?**
That is the default on purpose. An admin can change it under **Admin → Chat
options** (**Search without asking**), but only an admin: it applies to
everyone on the server.

**The chip says "Project unavailable".**
The chat is about a project you can no longer see (you were removed, or it was
deleted). Pick another scope from the chip; sending a message in that state
shows "This project doesn't exist or you don't have access." and unlinks the
project from the chat.

**I was made admin but don't see the Admin button.**
Reload the page — the app refreshes your role when it re-checks your account.

**Budget says "0 tokens left" but I haven't chatted today.**
The day is measured in the **deployment's** timezone (UTC unless an admin set
another), not yours — "today" can end (or not have started) at what feels like
an odd hour. The reset time is always shown
in the toast and the sidebar.

---

*UI references: `src/components/settings/SettingsDialog.jsx`, `src/components/ProfileMenu.jsx`, `src/components/account/ProfilePanel.jsx`, `src/components/account/AccountPanel.jsx`, `src/components/chat/ChatSidePanel.jsx`, `src/components/chat/ScopeChip.jsx`, `src/components/chat/WebOfferCard.jsx`, `src/components/projects/ProjectPage.jsx`, `src/components/admin/ChatOptionsSection.jsx`, `src/components/NarrationMiniPlayer.jsx`, `src/components/admin/AdminConsole.jsx`, `src/components/admin/ConsoleShell.jsx`, `src/components/admin/OverviewSection.jsx`, `src/components/admin/DocumentsSection.jsx`, `src/components/admin/DeploymentSection.jsx`, `src/components/DoclingConvertDialog.jsx`, `src/components/Header.jsx`, `src/components/ViewSwitcher.jsx`, `src/components/chat/InferenceRow.jsx`, `src/components/ChatSidebar.jsx`.*