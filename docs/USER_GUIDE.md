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
Library / Admin), you're an admin — click it to open the Admin console. It
lists every account with buttons per user:

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
- Per-user inference budget and the usage dashboard are server-side already;
  the UI for them is a planned follow-up (admins can currently manage budgets
  only via the API).
- The Admin console's **Deployment config** section lists every configured
  model provider (name, kind, host, and either how many models it has or
  "unreachable") so you can see at a glance what's wired up without reading
  `.env` on the server — API keys are never shown here or anywhere else. The
  row that used to say "Ollama URL" is now **"Embeddings (Ollama) URL"**,
  since that's the only thing that setting still controls (chat models come
  from the provider list above it). There's still no button here to add or
  change a provider — that's a `.env` + restart task for whoever runs the
  server.
- The **Assistant** section sets the assistant's profile: its name,
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
- **Chat options** has one setting so far, **Web search**: **Ask first** (the
  default: the assistant can only offer a search and the person says yes on a
  card), **Search without asking**, or **Off**.
  - A change applies from each person's next message and shows a notice
    ("Web search: Ask first. Applies from the next message.").
  - The line under the choices says where the current value comes from: **Set
    here.**, **From the server setting CHAT_WEB_SEARCH.** or **The default.**
    When it was set here, **Use the server setting** removes your choice, so the
    deployer's value applies again.
  - With no search engine configured on the server, the choices are disabled and
    the section says *Web search isn't set up on this server (SEARXNG_URL).*
  - After a change, the Assistant section's **What the model receives** updates
    to show the rules a document chat gets in that mode.

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
  facts, it offers a web search instead of guessing; see the next section.

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

Your deployment may cap how many tokens each user may spend per day (UTC day —
resets at UTC midnight). The chat sidebar shows **"N tokens left today"**
while you chat.

A reply you stop partway through, or one that fails, still counts against
this budget — using the token count the provider reported, or an estimate
based on how much text was generated if it didn't report one. Stopping early
doesn't refund the tokens already spent.

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
The day is measured in **UTC**, not your timezone — "today" can end (or not
have started) at what feels like an odd hour. The reset time is always shown
in the toast and the sidebar.

---

*UI references: `src/components/settings/SettingsDialog.jsx`, `src/components/ProfileMenu.jsx`, `src/components/account/ProfilePanel.jsx`, `src/components/account/AccountPanel.jsx`, `src/components/chat/ChatSidePanel.jsx`, `src/components/chat/ScopeChip.jsx`, `src/components/chat/WebOfferCard.jsx`, `src/components/projects/ProjectPage.jsx`, `src/components/admin/ChatOptionsSection.jsx`, `src/components/NarrationMiniPlayer.jsx`, `src/components/admin/AdminConsole.jsx`, `src/components/Header.jsx`, `src/components/ViewSwitcher.jsx`, `src/components/chat/InferenceRow.jsx`, `src/components/ChatSidebar.jsx`.*