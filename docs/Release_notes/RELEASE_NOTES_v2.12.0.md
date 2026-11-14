# v2.12.0 — Secrets out of the realm file, a bell for each person, and Gemini

**If your realm was created by v2.11.0 or earlier, rotate its Keycloak secrets.**
The realm file in the repository shipped working client secrets, a seeded admin
user's password and a fixed master admin password, so every realm imported from
it started with values anyone could read. This release takes them out of the file
and tells you, at start and in Admin → Deployment, when a deployment still uses
one. An existing realm is not changed by upgrading: rotating is a step you take.
How: [DEPLOYMENT.md, "Rotating the Keycloak secrets"](https://github.com/udaravima/natural-reader/blob/master/docs/DEPLOYMENT.md#rotating-the-keycloak-secrets).

**A bell in the header** collects what used to be lost: a document that finished
or failed indexing, a conversion ready or failed, and for admins a re-embed that
paused or finished. An inbox, never a popup.

**Google Gemini can be a chat provider**, beside Ollama and any OpenAI-compatible
server, through Google's own API. It has been tested against a stand-in for
Google, not against Google itself (see Known limits).

## Security

- **The realm file holds no secrets.** `deploy/keycloak/realm-export.json` has
  placeholders that Keycloak fills from `.env` when it first creates the realm
  (`OIDC_CLIENT_SECRET`, `KC_ADMIN_CLIENT_SECRET`, `KC_SEED_USER_PASSWORD`), and
  the master admin's password comes from `KC_BOOTSTRAP_ADMIN_PASSWORD`.
  `./startup.sh` and `startup.ps1` generate all four; anything unset gets a random
  value nobody knows.
- **A deployment still on a published secret is told**: an error in the log at
  start naming the variable, and "set, but to a published default: rotate it" in
  Admin → Deployment.
- **Sign-in off (`AUTH_ENABLED=false`) is for the machine itself, even behind a
  proxy.** The only check was that the backend listens on loopback, which it also
  does behind nginx, so `./startup.sh` with no arguments on a production box made
  every visitor the admin. A request through a proxy, or for any name but
  localhost, now gets 403, and `./startup.sh up` refuses to turn sign-in off when
  `.env` describes a live deployment (`ALLOW_AUTH_OFF=1` overrides).
- **A chat reply never loads a picture.** A Markdown image was fetched as soon as
  it rendered, so a model steered by a planted page could send text out in the
  picture's address. It is shown as a link, and the nginx sample adds
  `Content-Security-Policy: img-src 'self' data: blob:`.
- **A web page the chat reads is fetched from the address that was checked.** The
  name was looked up once for the check and again for the connection, so a name
  that answered "public" and then "this machine" got through. The fetch now
  connects to the checked address, keeping the site's name for the request and its
  certificate. Tailscale's address range (100.64.0.0/10) and IPv4 addresses
  written as IPv6 are refused too.

## Added

### 🔔 Notifications for each person

- The bell shows unread notices; the ones that need action come first and turn
  the badge amber. Several of one kind close together are one row ("5 documents
  finished indexing"), a problem that goes away by itself is marked Resolved, and
  **Mark all as read** clears the badge.
- Each person gets their own documents' indexing and conversions; every admin gets
  re-embeds paused or done, rebuilds failing three times in a row, and passages
  the embedding model had to shorten.
- Read notices are kept 30 days, unread ones 90 (`NOTIFICATIONS_READ_DAYS`,
  `NOTIFICATIONS_UNREAD_DAYS`).

### ✨ Google Gemini as a model provider

- `INFERENCE_<NAME>_KIND=gemini` with a Gemini API key from Google AI Studio; the
  URL has a default. Streaming, thinking with its summary, tools, pictures and
  audio. Chat only: a Gemini provider can't be chosen for embeddings.
- **Nothing is kept at Google.** Google's API keeps each conversation for 1 to 55
  days unless told not to; every request from the app says `store: false`.
- Gemini has no "thinking off": off asks for the lowest level, and a model that
  refuses it answers at its own level, with a notice.
- How to set it up: [DEPLOYMENT.md, "Google Gemini"](https://github.com/udaravima/natural-reader/blob/master/docs/DEPLOYMENT.md).

### 🗄️ Backup and restore, written down

One dump of the database holds the app and Keycloak together (the realm lives in
the same database), and the uploaded files go with it.
[DEPLOYMENT.md, "Backup and restore"](https://github.com/udaravima/natural-reader/blob/master/docs/DEPLOYMENT.md#backup-and-restore).

## Changed

- **A rebuild installs the versions the tests ran on.** Every requirement has a
  floor and a ceiling, and the image installs through the new `constraints.txt`.
- **The image no longer carries the test packages**; they are in
  `requirements-dev.txt`.
- **CI** (`.github/workflows/ci.yml`) runs the backend suite on the pinned versions
  against a scratch Postgres, then lint, the frontend suite and the build, on every
  pull request and every push to `master` and `development`.
- `GET /v1/health` reports the database (`"database": "ok"` or `"unavailable"`).
- The test suite refuses to run against the app's own Postgres.

## Fixed

### Read-aloud

- **Read-aloud stopped silently, Pause still showing**, after Skip, after a page
  turned by hand mid-sentence, and at a page with nothing to read. It now goes on.
- **"Using browser voice" never happened**: with the voice server unreachable, Read
  retried three times and stopped. It now switches to the browser's voice. A retry
  repeats the same sentence instead of skipping one.

### Staying up

- **A Postgres that started after the backend left it broken until a restart.** It
  now keeps trying in the background; compose also waits for a healthy Postgres.
  Meanwhile routes that need it answer 503, not 500.
- A failed migration no longer serves traffic on a half-migrated schema; workers
  starting together take turns; each migration commits on its own.
- **Ten slow requests could take every database connection**: signing in held one
  for the whole request, and a document search took a second while it waited on
  the embedding model.
- The web lookup and page extraction no longer freeze read-aloud and chat streams
  on the worker for seconds.

### The app

- **A refused call no longer sends the whole app back to the loading screen**
  (which also lost a recording in progress).
- **Every PDF opened stayed in memory for the session**; it is released when
  another document replaces it.
- A document could read "Indexed" with no passages, for good, after its converted
  Markdown was deleted mid-indexing.
- One "prompt too long" error switched tools off for a model until a restart.
- Two uploads at once could crash the worker; one read-aloud batch could hold the
  voice for hours (now capped at 200,000 characters).
- Notifications: no false "converted" notice, a paused re-embed's notice clears,
  and Show more never skips or repeats a row.

## Upgrading

- **Migration 026** (`notifications`) runs at start. Rebuild both halves: the
  backend image (`./startup.sh rebuild`) and the frontend (`npm run build`).
- **Rotate the Keycloak secrets** if your realm came from v2.11.0 or earlier
  (above). Until you do, the backend logs an error at every start.
- **The Keycloak container is recreated** on the next start (compose changed); its
  data is in Postgres and stays.
- **Behind nginx with the sample config?** Add its new `img-src` line.
- **Running the tests** from a venv: `pip install -r requirements.txt -r
  requirements-dev.txt -c constraints.txt` once; `./startup.sh init` no longer
  installs pytest.
- **Downgrading:** run 026's Down block (in its header); safe at any time.
- **Coming from v2.10.0**, read v2.11.0's upgrading notes too.

## Known limits

- **Gemini has not run against Google** (no key was available). It was walked end
  to end against a stand-in that follows Google's documented stream and refuses a
  tool call returned without its reasoning, as Google does. Two things only a real
  key will confirm: that Google accepts the documented placeholder on tool calls
  the app makes itself (the document search before a question, the web-search
  consent), and what a safety block looks like.
- **Not run:** an image rebuild with the pins, the compose changes on a real
  engine, `startup.ps1` on Windows, and a restore on a real deployment (the
  procedure was tried on a scratch copy).
- Frontend tests that failed under load were never reproduced here; their wait
  limits were raised for the likely cause.
- Not pinned yet: Docling's own packages, the Kokoro model download (no
  checksum), the SearXNG image (`:latest`).

## Tests

3275 backend tests pass (16 skipped), 1906 frontend tests in 157 files, lint has
no errors, and the build works. Walked: the backend with its Postgres stopped and
then started; a throwaway Keycloak importing the new realm file; sign-in off
through a proxy refused and local allowed; read-aloud's Skip, empty page and hand
page turn in headless Chromium, old code against new; the notification bell in
Chrome; a Gemini chat with a tool call through a throwaway backend against the
stand-in server; real web pages through the pinned fetch; a backup restored on a
scratch database.

## Full changelog

[CHANGELOG.md](../../CHANGELOG.md#2120---2026-11-14) · diff:
[v2.11.0…v2.12.0](https://github.com/udaravima/natural-reader/compare/v2.11.0...v2.12.0)
