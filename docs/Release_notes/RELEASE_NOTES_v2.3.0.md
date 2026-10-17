# v2.3.0 — Access tokens that do one thing, and read-aloud that works again

**An access token can now be limited to what it's for.** Before, every token
could do everything its owner could: read and delete documents, chat, use the
admin console, and create more tokens. So the token you pasted into the
read-aloud extension was a full key to your account. Now you choose its access
when you create it: **Read aloud** only is the default, and a token with that
access can't open your documents, your chats or the admin console, and it can't
make itself a replacement. Tokens can also expire.

**The read-aloud extension works again**, on a server with sign-in on and on
strict-CSP sites such as GitHub. Reload it in `chrome://extensions` to get
extension 0.2.0, then give it a token.

One behavior change: with `FRONTEND_ORIGIN` unset, **no other website may call
the API from a browser** (it allowed any). One migration, `019`, which only
adds a column.

## Added

### 🔑 Scoped access tokens

- **Choose the access in Settings → Account** when creating a token:

  | Access | The token can | Your account needs |
  |---|---|---|
  | Read aloud (default) | turn text into speech: the extension | `reader` |
  | Documents & projects | the library, projects, people search, and read aloud | `reader` |
  | Chat | chats and models | `chat` |
  | Admin | the admin console's API | `admin` |

  Only the kinds your account has are offered.
- **A token never gets more than its owner.** Its access is worked out on
  every request from your account's current permissions, so if an admin takes
  `reader` away from you, your read-aloud tokens stop working too.
- **Tokens can't manage tokens.** Only the signed-in app can create, list or
  revoke them, so a leaked token can't mint a replacement that outlives
  revoking it.
- **Expiry:** 30 days, 90 days, 1 year (the default) or Never.
- **The list says what each token can do**, when it was last used and when it
  expires. Tokens made before 2.3.0 show as **Full access (old)**.

### 🔊 Read-aloud extension 0.2.0

- **Sign-in with an access token.** Paste it into the extension's Options →
  Access token and click **Test connection**: it says *Connected as* your
  email, or exactly why not (no token, token rejected, can't read aloud,
  server unreachable). The token stays on that computer, never in Chrome
  sync, and web pages never see it.
- **The popup tells the truth.** Its dot was green whenever the server was up,
  even when every read was refused. Now it's green only if a read will work,
  amber if the server refuses you, red if it can't be reached.
- **Strict-CSP sites (GitHub, `claude.ai`) play audio.** The toolbar used to
  say "reading" and stay silent there, because the page's policy blocked its
  audio without an error. The toolbar now runs in its own frame, under the
  extension's rules, and no extension code is loaded into the page.
- **A server that isn't on `localhost`** works: Test connection asks Chrome
  for access to it.

## Changed

- **CORS default: no other website.** With `FRONTEND_ORIGIN` unset, the API
  answers no cross-origin browser request; it used to allow every website,
  which let any page drive your Kokoro voices from JavaScript. The app is
  same-origin and the extension no longer needs it.
- **API, tokens:**
  - `POST /v1/auth/tokens` takes `scopes`, a list from `read_aloud`,
    `documents`, `chat`, `admin`. Omitting it creates a full-access token, as
    before. A scope your account lacks, an empty or repeated list, or an
    `expires_at` in the past is a `422`.
  - `GET /v1/auth/tokens` returns each token's `scopes` (`null` = full access).
  - A scoped token calling any `/v1/auth/tokens` route gets
    `403 {"error": "token_scope"}`.
  - `GET /v1/auth/me` adds `scopes` and `read_aloud`. For a scoped token,
    `role` and `capabilities` are the narrowed ones.

## Fixed

- **A new token shows in the list straight away**, and a revoked one leaves it
  straight away. The list re-read itself after each change and sometimes
  missed it (see Known issues).

## Upgrade notes

1. **Migration `019`** adds `personal_access_tokens.scopes`. It changes no
   existing row, and every existing token keeps full access. To undo it:
   `ALTER TABLE personal_access_tokens DROP COLUMN scopes;`.
2. **Replace your old tokens.** In Settings → Account, create a narrow token
   for each use (the extension needs only **Read aloud**), put it where the
   old one was, then revoke the **Full access (old)** one.
3. **Reload the extension** in `chrome://extensions` to get 0.2.0. Version
   0.1.0 can't sign in, and it called the API from web pages, which the new
   CORS default refuses.
4. **If a page on another origin calls your API from a browser**, list that
   origin in `FRONTEND_ORIGIN`, or set `FRONTEND_ORIGIN=*` to keep the old
   behavior. Scripts, curl and the extension aren't affected: CORS applies
   only to web pages.
5. Deploy the SPA and the backend together: the new token screen sends
   `scopes`, which an older backend ignores, so its tokens would get full
   access.

## Known issues

- **A read right after a write can miss it.** The server sends its answer
  before the write's transaction commits; the commit follows a few
  milliseconds later. A client that reads again in that gap sees the old
  state. Measured on the token routes: 3 of 30 immediate re-reads missed the
  token just created. Every write route works this way. The token screen no
  longer re-reads; other screens may still briefly show a stale list until
  their next refresh.

## What you can't do yet

- **Give a token access to a single project or document.** Scopes are per
  kind of action, across everything your account can reach.
- **See or revoke another user's tokens as an admin.** Each person manages
  their own.
- **Change a token's access or expiry.** Create a new one and revoke the old.
- **Read inside sandboxed iframes** with the extension, such as embedded
  previews. It reads the top page only.

## Full changelog

[CHANGELOG.md](../../CHANGELOG.md#230---2026-10-17) · diff:
[v2.2.0…v2.3.0](https://github.com/udaravima/natural-reader/compare/v2.2.0...v2.3.0)
