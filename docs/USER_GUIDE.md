# User Guide — accounts, tokens, admin, and chat budgets

This is the **end-user guide** for the multi-user features: signing in, your
account, personal access tokens, the admin section, and the chat inference
budget. (For developer internals see [ARCHITECTURE.md](ARCHITECTURE.md) and
[IDENTITY_AND_ROLES.md](IDENTITY_AND_ROLES.md); reading features are covered in
the [README](../README.md).)

- [Signing in](#signing-in)
- [The sidebar's account-area sections](#the-sidebars-account-area-sections)
- [Account section: your profile and personal access tokens](#account-section-your-profile-and-personal-access-tokens)
- [Admin section (admins only)](#admin-section-admins-only)
- [Chat: Server vs Local Ollama](#chat-server-vs-local-ollama)
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
you click **Log out** at the bottom of the Account section.

## The sidebar's account-area sections

The reader sidebar (left panel) ends with two collapsible sections, one after
the other — this is the mix-up to avoid:

- **Account** — everyone has it. Your email + role, **personal access tokens**,
  and Log out. This is the only place tokens are created.
- **Admin** — only visible if your role is `admin`. User management: activate,
  disable, promote/demote. **No token creation happens here.**

Both have a small header (person icon / shield icon) you click to expand. They
look similar because they're both plain lists in the same sidebar — but they
answer to different permissions and do different jobs.

## Account section: your profile and personal access tokens

The first line shows `your@email · role` (role is `admin` or `member`).

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

1. Expand **Account** in the sidebar.
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

## Admin section (admins only)

If you see a shield-icon **Admin** section, you're an admin. It lists every
account with buttons per user:

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
- Roles are managed **here, not in the login system** — your organization's
  login (Keycloak) only verifies who you are; whether you're an admin is a
  fact this application owns.
- Per-user inference budget and the usage dashboard are server-side already;
  the UI for them is a planned follow-up (admins can currently manage budgets
  only via the API).

## Chat: Server vs Local Ollama

In the chat sidebar, **Inference source** picks where chat requests go:

- **Server** (default) — requests go through the server's gateway. The model
  list shows only models the deployment allows, and your usage counts against
  the daily budget (below). Works from any device, no local setup.
- **Local Ollama** — requests go straight from your browser to your own
  Ollama. You pick host/port and any local model. No allowlist, no budget, but
  the server's chat tools (web search, document search) don't apply, and the
  browser must be able to reach your Ollama.

The choice is remembered per browser.

## The daily token budget

On **Server** mode, the deployment may cap how many tokens each user may spend
per day (UTC day — resets at UTC midnight). The chat sidebar shows
**"N tokens left today"** while you chat.

When you run out:

- The send button disables while your budget is at zero.
- A request that slips through returns an error and you'll see a toast:
  **"Daily inference budget exhausted — resets at …"** with the reset time.
- Nothing is lost: your message stays, the document is fine, and everything
  comes back at the reset time. Switching the source to **Local Ollama** is the
  escape hatch if you have one.

Admins can raise individual users' budgets (API today; UI pending). If you
legitimately need more, ask yours.

## FAQ

**Why is there token stuff "in the admin panel"?**
There isn't — it's the neighboring **Account** section, which every user has.
The Admin section has no token features at all.

**Do I need a personal access token to use the app or the extension?**
App: no. Extension: yes — it's the only way it can authenticate.

**Can a token be shown to me a second time?**
No. The server stores only a fingerprint. Lost token = revoke + create.

**My colleague can't log in and sees "waiting for approval".**
That's the `pending` state every brand-new account starts in. An admin must
click **Activate** in the Admin section.

**I was made admin but don't see the Admin section.**
Reload the page — the app refreshes your role when it re-checks your account.

**Budget says "0 tokens left" but I haven't chatted today.**
The day is measured in **UTC**, not your timezone — "today" can end (or not
have started) at what feels like an odd hour. The reset time is always shown
in the toast and the sidebar.

---

*UI references: `src/components/account/AccountPanel.jsx`, `src/components/admin/AdminPanel.jsx`, `src/components/Sidebar.jsx`, `src/components/chat/InferenceSourceSelect.jsx`, `src/components/ChatSidebar.jsx`, `src/hooks/useChatEngine.js`.*