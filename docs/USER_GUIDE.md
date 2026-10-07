# User Guide — accounts, tokens, admin, and chat budgets

This is the **end-user guide** for the multi-user features: signing in, your
account, personal access tokens, the admin section, and the chat inference
budget. (For developer internals see [ARCHITECTURE.md](ARCHITECTURE.md) and
[IDENTITY_AND_ROLES.md](IDENTITY_AND_ROLES.md); reading features are covered in
the [README](../README.md).)

- [Signing in](#signing-in)
- [Where things live: Settings, profile menu, Admin](#where-things-live-settings-profile-menu-admin)
- [Account: your profile and personal access tokens](#account-your-profile-and-personal-access-tokens)
- [Admin console (admins only)](#admin-console-admins-only)
- [Chat: models and streaming](#chat-models-and-streaming)
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

You stay signed in on that browser (a cookie, valid ~7 days by default) until
you click **Log out** in the **profile menu** (top-right of the header).

## Where things live: Settings, profile menu, Admin

Accounts, tokens, and settings are **not in the sidebar** — the sidebars hold
navigation (reader) and the model picker + sessions (chat). Everything about
*you* lives at the top of the window:

- **Profile menu** — the person icon at the top-right of the header. Your
  identity, a **Settings** shortcut, the dark-mode toggle, and **Log out**.
- **Settings** page — open it from the header **gear**, or from the profile
  menu. One page with sections for Voice & reading, Chat & inference,
  Connection, Appearance, and **Account** (your email + role and **personal
  access tokens** — the only place tokens are created).
- **Admin** console — a separate view opened from the **shield** button in the
  top view switcher (Reader / Chat / Library / Admin). Only admins see it. User
  management only; **no token creation happens here.**

## Account: your profile and personal access tokens

Open **Settings** (header gear, or the profile menu) and go to the **Account**
section. The first line shows `your@email · role` (role is `admin` or `member`).

Below that is the **token manager**: a name box, a **Create** button, your list
of tokens, and a **Revoke** button next to each.

### Why do these tokens exist?

Your normal login uses a **cookie** the browser attaches automatically —
perfect for the web app, useless to anything that isn't the app in your
browser. A **personal access token** is a long-lived secret
(`nrp_…`) that proves "I am this user" in a single HTTP header:

```
Authorization: Bearer nrp_your_token_here
```

Things that need one:

- **The Chrome read-aloud extension.** It talks to the server from arbitrary
  web pages — it cannot do the interactive login dance, and it can't borrow
  your cookie. You paste a token into it once and it authenticates from then
  on.
- **Scripts, cron jobs, curl, notebooks** — anything calling the server's API
  outside a browser.

If you only use the web app itself, you never need a token. Creating one is
purely optional.

### Creating one

1. Open **Settings** and go to the **Account** section.
2. Type a name (e.g. `my-laptop-extension`) and click **Create**.
3. The token appears once, with a **Copy** button. **Copy it now** — the server
   stores only a hash, so it can never be shown again. If you lose it, revoke
   and create a new one.

Treat a token like a password: anyone holding it can act as you (chat, read
your documents) until it's revoked or your account is disabled. Give each
device/purpose its own token so you can revoke them independently.

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

**Projects.** Every project, with its owners and counts. **Ownerless only**
shows projects whose last Owner was deleted; **Add me as Owner** takes one
over (recorded as "added by admin" and logged). **Open** shows the project
page; as an admin you can manage members, rename and delete any project, but
you see its documents only if you're a member. Each user row has a
**Project limit** (empty = the deployment default, 0 = unlimited).

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
| Contributor | also file documents they uploaded into the project |
| Maintainer | also remove any document, rename the project, and add, remove or change members up to Maintainer |
| Owner | everything, including Owners and deleting the project |

A project always keeps at least one Owner, so the last Owner can't leave or
step down until someone else is made Owner.

**Adding people.** On a project page, open **Members → + Add people**, type a
name or their full email address, pick the person, choose a role and press
**Add**. Whether typing a name finds people depends on your organisation's
setting; a full email address always works. "Awaiting approval" means the
account exists but an admin hasn't activated it yet.

**Documents.** **Documents → File a document** adds one of your uploads. A
Maintainer removes a document with × (a document nobody else has in their
library is deleted with it, so you're asked first).

**Activity** lists who changed what, newest first.

**Sharing one document with one person.** In the Library, press **Share** on a
document you uploaded, find the person, and pick them. They can read it but
can't share it on. The same panel lists who you've shared with; × stops
sharing.

**Leaving.** **Leave** in the project header. Documents you had in your own
library stay there.

What you can't do yet: chat with a whole project at once (planned), or upload
a revised file as a new version of a document (planned).

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

**Use this document:** with a document open, a chip above the chat box
shows its name and **Use this document**. Click it to switch the document
off for this chat: the assistant then doesn't search or read it (the chip
says **Not used**, with the name struck through), so a general question
isn't answered from the document, and messages are lighter. The choice is
kept per chat and per signed-in user; a new chat starts with it on. Pins
you added still go with each message, and citations in earlier replies
still open the document.

**Sources:** under a reply that used your document, chips such as
**Sources: p. 3 · p. 8** open the pages the answer came from, even when the
reply doesn't cite them itself. When nothing in the reply matches a page
(for example "the document doesn't cover this"), the chips read
**Searched (not cited)** and list the pages that were looked at.

**Citations:** when a reply used your document, the page numbers it cites
("page 4", "(page 4)") are links. Clicking one opens that document in the
reader at that page, opening it from the server first if it isn't the one
you have open. If you can no longer read the document, a notice says so
and you stay in chat. Like any switch to the reader, clicking a citation
while a reply is still streaming stops that reply (it's kept, marked
**Stopped**).

**Picking a model:** the model picker in the chat sidebar is grouped by
**provider**, each group labelled with the provider's name as your deployment
configured it (for example `ollama` or `local`, plus any other server your
deployment has) and shows what each model can do — whether it supports thinking,
tools, images, and so on. On the **Settings** page (Chat & inference section),
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
On the **Settings** page, in the **Account** section (open Settings from the
header gear or the profile menu). The Admin console has no token features at all.

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

**I was made admin but don't see the Admin button.**
Reload the page — the app refreshes your role when it re-checks your account.

**Budget says "0 tokens left" but I haven't chatted today.**
The day is measured in **UTC**, not your timezone — "today" can end (or not
have started) at what feels like an odd hour. The reset time is always shown
in the toast and the sidebar.

---

*UI references: `src/components/settings/SettingsPage.jsx`, `src/components/ProfileMenu.jsx`, `src/components/account/AccountPanel.jsx`, `src/components/admin/AdminConsole.jsx`, `src/components/Header.jsx`, `src/components/ViewSwitcher.jsx`, `src/components/chat/InferenceRow.jsx`, `src/components/ChatSidebar.jsx`.*