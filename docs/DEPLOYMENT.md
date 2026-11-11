# Production deployment (behind nginx + TLS)

How to serve Natural Reader on real hostnames with HTTPS, fronted by nginx, with
Keycloak as the OIDC identity provider. This is the **production** counterpart to
[`../deploy/README.md`](../deploy/README.md), which covers the local-dev OIDC rig
(all `localhost`, no TLS). Read that one first if you just want login working on
your laptop.

Worked example uses two hostnames:

| Hostname | Serves | nginx sample |
| --- | --- | --- |
| `chat.example.com` | the built SPA **and** the `/v1/*` backend (one origin) | [`chat.example.com.sample`](sample_configs/chat.example.com.sample) |
| `auth.example.com` | Keycloak (OIDC provider) | [`auth.example.com.sample`](sample_configs/auth.example.com.sample) |

## Running the backend in a container

The backend ships as a container image (`Containerfile`, the `backend` service in
`docker-compose.yml`). Python is pinned inside the image, so a host OS upgrade
can't move the interpreter under the app. Windows hosts keep the bare-metal flow
(`startup.ps1`).

### Pinned versions

A rebuild installs the versions the tests ran on, not whatever is newest that
day. Two files do it:

- `requirements.txt` gives every package a floor and a ceiling.
- `constraints.txt` lists the exact version of each of those packages and of
  everything they bring in (about 80), as installed when the suite last passed.
  The image installs with `-c constraints.txt`.

To move to newer versions: upgrade the venv (`pip install -U -r requirements.txt`),
run the tests, then regenerate the file from that venv and commit it with the
change. `pip freeze` lists more than the image needs (Docling's packages, tools);
keep only the lines for packages already in `constraints.txt`, plus any new
dependency. A bare-metal install (`./startup.sh init`, Windows) uses
`requirements.txt` alone, so it stays inside the ranges but isn't pinned.

Not pinned yet: the Docling extra's own packages (`requirements-docling.txt`),
the Kokoro model download (no checksum), and the SearXNG image (`:latest`).

### When Postgres isn't there at start

Compose starts the backend and Keycloak only after Postgres answers
(`depends_on: service_healthy`). After a reboot the engine restarts containers on
its own, in any order, so the backend also covers it itself: if Postgres doesn't
answer at start it keeps trying every 15 seconds for as long as it runs, and
finishes its start-up when it does. Until then every route that needs the
database answers `503 db_unavailable`, and `GET /v1/health` says so:

```json
{"status": "ok", "model_loaded": true, "database": "unavailable"}
```

`status` is read-aloud's own state (the Kokoro model), so watch `database` in a
monitor. Read-aloud needs the database too: the caller is looked up there.

Workers that start together take turns migrating (an advisory lock), each
migration commits on its own, and a worker only serves once they have all run.

### Live mode

| Command | Effect |
| --- | --- |
| `./startup.sh start` | builds the image if needed, then runs Postgres, SearXNG, Keycloak (see below) and the backend, all detached |
| `./startup.sh rebuild` | ship new code: rebuild the image, recreate only the backend; Postgres and Keycloak keep running |
| `./startup.sh logs` | follow the backend's output (the rotating `server.log` is still written under `LOG_DIR`) |
| `./startup.sh stop` | stop and remove the containers; volumes and `data/` stay |

**Rollback** to the bare-metal backend: `./startup.sh stop`, then
`BACKEND_RUNTIME=venv ./startup.sh up-with-dev-auth` (needs `./startup.sh init`).

### Surviving reboots

Every service uses `restart: always`. On **podman**, run `./startup.sh
enable-autostart` once: it enables lingering for your user (user services run
without a login session) and `podman-restart.service`, which restarts only
containers whose restart policy is `always`. On **Docker**, the daemon restarts
them itself once it starts at boot (`systemctl enable docker`). `stop` removes
the containers, so a deliberate stop stays stopped.

### Host networking

The backend runs with `network_mode: host`. It is a *client* of four services
bound to the host's loopback (Postgres `:5433`, Keycloak `:18080`, SearXNG
`:18043`, Ollama `:11434`), and the OIDC issuer URL must be the same string for
the browser and the backend. A bridged container sees its own loopback, not the
host's, so each of those would need a different URL. With host networking
nothing changes: the backend still binds `127.0.0.1:8000` (`HOST`), and nginx
keeps proxying to it. The container is supported on Linux x86_64; ARM is
untested.

### CPU only

The image runs Kokoro (and Docling) on the CPU. It passes no GPU or NPU device
into the container and ships no accelerated ONNX Runtime. Intel GPUs and NPUs
would need `/dev/dri` / `/dev/accel`, the render group and Intel's compute
runtime in the image; NVIDIA would need the NVIDIA container toolkit and an
`onnxruntime-gpu` image with CUDA libraries (gigabytes). For acceleration
today, run the backend bare metal with an accelerated build (README, Hardware
Acceleration).

### Paths are host paths

`DOC_STORAGE_DIR` (or the legacy `PDF_STORAGE_DIR`), `LOG_DIR`,
`CHAT_ASSISTANT_PROFILE_FILE` and `SPEECH_MODELS_FILE` in `.env` name locations
**on the host**. `startup.sh` mounts them into the container at fixed paths, and
files written there belong to the user who ran it. Running compose by hand? Set
`NR_DATA_DIR`, `NR_LOG_DIR` and (optionally) `NR_PROFILE_FILE` and
`NR_SPEECH_MODELS_FILE` to those host paths (absolute), and add
`-f compose.profile.yml` for the profile and `-f compose.speech.yml` for the
speech list; otherwise the defaults
`./data/pdfs` and `./logs` are used. On podman also add `-f compose.podman.yml`:
without its keep-id mapping the container can't write those folders, and
without its `in_pod: false` podman refuses to create the backend.
`LOG_AUDIT_FILE` is ignored in the container: `audit.log` is written under
`LOG_DIR`.

Only `.env` reaches the backend container, plus `HOST`, `PORT` and `WORKERS`,
which also pass through from the shell. A key exported only in your shell
profile (as the bare-metal backend would have seen it) must move into `.env`.

Documents keep working across the switch, in both directions: the database
records each upload's path as the runtime that stored it saw it, and the
backend finds the same `<doc_id>.<ext>` file in its own storage folder when
that path doesn't exist.

### Keycloak

`LOCAL_KEYCLOAK=auto|true|false` (default `auto`) decides whether `start` runs
the compose Keycloak. `auto` means yes when `OIDC_ISSUER` is
`http://localhost:18080/…` **or** `KC_HOSTNAME` is set, because `KC_HOSTNAME` only
configures the compose Keycloak. A live site fronting it as
`https://auth.example.com` therefore still gets it. Use `false` for an external IdP.

### Docling

`DOCLING_ENABLED` in `.env` also selects the image variant: `start` and `rebuild`
build with Docling (CPU-only torch, several GB larger) exactly when it is on.
Turning it on later takes `./startup.sh rebuild`. A mismatch is logged once at
startup.

Pictures a conversion keeps are PNG files under `DOC_STORAGE_DIR/figures/`, so
back that folder up with the documents. `DOCLING_MAX_PICTURES` (500 by default) caps
them per document; a picture past it stays a placeholder.

## The one rule that dictates the topology: same origin

The app's login session is an **`HttpOnly` cookie**. Browsers only send that
cookie back if the SPA and the backend it calls are the **same origin** — same
scheme, host, and port. So the SPA and `/v1/*` **must** live on one hostname
(`chat.example.com`), which is why a single nginx server block both serves
`dist/` and proxies `/v1/` to the backend.

Keycloak is deliberately a **different** origin (`auth.example.com`). That's fine —
OIDC is a browser *redirect* flow, not a cookie-sharing one, so the IdP never
needs to be same-origin with the app. Putting it on its own hostname is the
normal, correct shape.

## What changes going from local dev to production

Nothing in the application code changes. Deployment is entirely configuration.
The first five places all have to **agree on scheme + host** or login fails in a
different way at each mismatch; the sixth is what makes admin/role management
work at all:

| # | File / place | Local dev | Production |
| --- | --- | --- | --- |
| 1 | `.env` → `OIDC_ISSUER` | `http://localhost:18080/realms/natural-reader` | `https://auth.example.com/realms/natural-reader` |
| 2 | `.env` → `OIDC_REDIRECT_URL` | `http://localhost:5173/v1/auth/callback` | `https://chat.example.com/v1/auth/callback` |
| 3 | `.env` → `COOKIE_SECURE` | `false` (plain http) | `true` (TLS) |
| 4 | `.env` → `KC_PROXY_HEADERS` / `KC_HOSTNAME` | unset | `xforwarded` / `https://auth.example.com` |
| 5 | the **live** Keycloak realm's client | `localhost` redirect URIs | add the `https://chat.example.com` callback + web origin |
| 6 | `.env` → `KC_ADMIN_CLIENT_ID` / `KC_ADMIN_CLIENT_SECRET` | optional | **set** (secret must match the `natural-reader-admin` client in the live realm) — needed for the admin console's capability editor and the founder self-heal (Trap 4) |

Plus two nginx vhosts (the two `.sample` files) and the DNS + TLS certs for both
hostnames.

The traps below are ordered by *when they bite you* as you walk the login flow.

### Trap 1 — Keycloak emits `http://` behind the proxy (KC_PROXY_HEADERS / KC_HOSTNAME)

**Symptom:** the Keycloak admin console (or login page) loads over HTTPS but the
browser console fills with *"Mixed Content … requested an insecure resource
`http://auth.example.com/resources/...`"* and *"Framing `http://auth.example.com/`
violates … Content Security Policy directive: frame-src 'self'"*.

**Cause:** nginx terminates TLS and forwards plain HTTP to Keycloak on
`:18080`. Keycloak defaults to assuming it *is* the origin the client hit — plain
`http://…:18080` — so it writes `http://` into every asset URL, iframe `src`, and
its OIDC `issuer`. On an HTTPS page the browser blocks those as mixed content, and
the backend rejects tokens because the `issuer` doesn't match `OIDC_ISSUER`.

**Fix:** two env vars (already wired into `docker-compose.yml`, empty by default
so local dev is unchanged; set them in `.env`):

```ini
KC_PROXY_HEADERS=xforwarded          # trust nginx's X-Forwarded-Proto: https
KC_HOSTNAME=https://auth.example.com  # pin the canonical public URL
```

`KC_PROXY_HEADERS` alone makes Keycloak believe the `X-Forwarded-Proto: https`
nginx already sends; `KC_HOSTNAME` pins the scheme+host so even a direct
`localhost:18080` hit reports `auth.example.com`. Verify:

```bash
curl -s https://auth.example.com/realms/natural-reader/.well-known/openid-configuration \
  | grep -o '"issuer":"[^"]*"'
# → "issuer":"https://auth.example.com/realms/natural-reader"   (https, not http)
```

Recreating the container to apply these is **non-destructive** — the realm and
users live in Postgres, not the container. After changing `.env`:

```bash
env -u XDG_DATA_HOME podman rm -f natural-reader-keycloak
env -u XDG_DATA_HOME .venv/bin/podman-compose up -d keycloak
```

### Trap 2 — the live realm ≠ the export ("Invalid parameter: redirect_uri")

**Symptom:** login redirects to Keycloak and you get a Keycloak error page:
*"We are sorry… Invalid parameter: redirect_uri"* — before any login form.

**Cause:** Keycloak matches the incoming `redirect_uri` against the
`natural-reader` client's **Valid Redirect URIs** allow-list and refuses anything
not on it. In production the backend sends `https://chat.example.com/v1/auth/callback`,
but the client only lists the `localhost` dev URIs.

**The trap within the trap:** editing
[`../deploy/keycloak/realm-export.json`](../deploy/keycloak/realm-export.json)
does **not** fix this. `--import-realm` only seeds a realm that **doesn't exist
yet**, and once the realm is in Postgres the import is skipped on every boot. The
export and the running realm drift apart silently. The export edit only matters
for a *fresh* database.

**Fix the live realm** one of two ways:

- **Admin console:** `https://auth.example.com/admin` → realm `natural-reader` →
  Clients → `natural-reader` → Settings → **Valid redirect URIs**: add
  `https://chat.example.com/v1/auth/callback`; **Web origins**: add
  `https://chat.example.com` → Save.
- **`kcadm` (scriptable):** run inside the container (needs the *master* admin
  password — see the admin-password note below; `KC_BOOTSTRAP_ADMIN_PASSWORD` is
  only applied on a **first-ever** boot with an empty DB):

  ```bash
  KC=/opt/keycloak/bin/kcadm.sh
  env -u XDG_DATA_HOME podman exec natural-reader-keycloak $KC config credentials \
    --server http://localhost:8080 --realm master --user <admin> --password <pw>
  CID=$(env -u XDG_DATA_HOME podman exec natural-reader-keycloak $KC \
    get clients -r natural-reader -q clientId=natural-reader --fields id --format csv --noquotes)
  env -u XDG_DATA_HOME podman exec natural-reader-keycloak $KC update clients/$CID -r natural-reader \
    -s 'redirectUris=["https://chat.example.com/v1/auth/callback","http://localhost:8080/v1/auth/callback"]' \
    -s 'webOrigins=["https://chat.example.com","http://localhost:8080"]'
  ```

The checked-in export lists only the `localhost` dev URIs (`:8080` and Vite's
`:5173`), for sign-in and for after logout. A deployment adds its own public
origin to its live realm this way; to have a rebuilt database start correct,
add the same URIs to a private copy of the export, never to the checked-in one.

**Same trap, different field — logout ("Invalid redirect uri" on
`/protocol/openid-connect/logout`).** RP-initiated logout sends
`post_logout_redirect_uri=https://chat.example.com/` (the SPA root, *not* the
callback path), matched against the client's separate **Valid post logout
redirect URIs** allow-list. A drifted live realm rejects it exactly like the
login case. Fix it the same two ways — console (Clients → `natural-reader` →
Settings → **Valid post logout redirect URIs**: add `https://chat.example.com/*`)
or `kcadm` (`-s 'attributes."post.logout.redirect.uris"=https://chat.example.com/*##https://auth.example.com/*'`).
**Gotcha:** in the realm-export/attribute form, multiple post-logout URIs are
delimited by **`##`**, not spaces or commas (`+` is the special token meaning
"reuse the Valid Redirect URIs"). A space-delimited value is silently parsed as
one bogus URI and matches nothing.

> **Master admin password:** `KC_BOOTSTRAP_ADMIN_PASSWORD` in `.env` (user
> `admin`) creates the master admin **only on the first boot against an empty
> database**. Unset, that admin gets a random password nobody knows.
> Because the realm persists in Postgres, later container recreations ignore
> those vars — the real password is whatever it was first set to. If it's lost,
> reset it with `bin/kc.sh bootstrap-admin` (Keycloak 26) or by seeding a fresh
> admin against the DB; the compose values are not authoritative on a persisted
> volume.

### Sign-in off is for one machine

`AUTH_ENABLED=false` makes every request the seed admin. It exists for a
developer's own machine and for single-user use, and two checks keep it there:

- The backend refuses to start with it on a non-loopback bind.
- Behind a reverse proxy the bind is still loopback, so each request is checked
  too: one that carries a proxy's headers (`X-Forwarded-For`, `X-Real-IP`,
  `Forwarded`, `X-Forwarded-Host`) or is addressed to any name but `localhost`,
  `127.0.0.1` or `::1` gets `403 sign_in_off_local_only`. That is also what stops a
  web page from reaching a developer's backend through DNS rebinding.

`./startup.sh up` (also what a bare `./startup.sh` runs) turns sign-in off, so it
refuses when `.env` has an `https://` issuer, `KC_HOSTNAME` or `COOKIE_SECURE=true`.
Use `./startup.sh start` on a deployment. `ALLOW_AUTH_OFF=1` overrides.

One consequence: with sign-in off the app can't be used from another device on
your network (a phone at `192.168.x.x:5173`). Turn sign-in on for that.

### Rotating the Keycloak secrets

**Do this if your realm was created by v2.11.0 or earlier.** Until then
`deploy/keycloak/realm-export.json` contained working values: the secret of the
sign-in client, the secret of `natural-reader-admin` (a service account that can
create users and reset anyone's password), the password of the seeded
`admin-user` (an app admin), and compose set Keycloak's master admin to
`admin`/`admin`. They are public. Keycloak's token endpoint has to be reachable
for sign-in to work, so anyone who can reach it can use them.

The backend tells you: at start it logs an error naming each variable that
still holds a published value, and Admin → Deployment shows "set, but to a
published default: rotate it".

1. **The two client secrets.** In the Keycloak admin console: realm
   `natural-reader` → Clients → `natural-reader` → Credentials → **Regenerate**;
   the same for `natural-reader-admin`. Put the new values in `.env` as
   `OIDC_CLIENT_SECRET` and `KC_ADMIN_CLIENT_SECRET`, then `./startup.sh rebuild`
   (or restart the backend). Sign-in fails between the two steps, so do them
   together.
2. **`admin-user`.** Once your own account is an admin, delete it (Users →
   `admin-user` → Delete), or give it a new password under Credentials.
3. **The master admin.** Sign in to the admin console as `admin`, then change the
   password (top right → Manage account), or create a new admin in the `master`
   realm and delete `admin`.
4. Check: restart the backend and look for the error line; it should be gone.

A fresh install needs none of this: `./startup.sh` generates all four values into
`.env`, and the realm file only holds `${PLACEHOLDERS}` that Keycloak fills from
them when it first creates the realm. They are read once. Changing them in `.env`
later does not change an existing realm; use the steps above.

### Trap 3 — the session cookie silently vanishes (COOKIE_SECURE / same origin)

**Symptom:** login completes, Keycloak redirects back, and the app *still* shows
the login screen — no error, it just "does nothing."

Two independent causes, both about the cookie being set but not stored/sent:

- **`COOKIE_SECURE=true` on a plain-http page.** A `Secure` cookie is dropped by
  the browser over `http://`. Set `true` **only** when the app is actually served
  over HTTPS (production); keep `false` for `http://localhost` dev.
- **SPA and backend on different origins.** If you serve the SPA from one host and
  proxy `/v1` from another, the cookie set for one isn't sent to the other. This
  is why the single `chat.example.com` vhost serves *both* `dist/` and `/v1/`.
- Leave `COOKIE_DOMAIN` **unset** unless you know you need it — a value that
  doesn't match the origin drops the cookie the same silent way.

### Trap 4 — logged in, but "active but has no access yet" (the founder lockout)

**Symptom:** login fully succeeds — Keycloak redirects back, the session cookie
sticks, `/v1/auth/me` returns your user — but the app shows *"Your account is
active but has no access yet. Ask an administrator to grant Reader or Chat
access."* On a freshly-imported realm this hits the **admin too**, and then
nobody can grant anything because the would-be admin is the one locked out.

**Cause (the counterintuitive part):** capabilities are Keycloak **realm roles**
(`reader`/`chat`/`admin`) mirrored into `users.capabilities`, and **Keycloak is
authoritative** — every login overwrites the mirror with whatever roles the token
carries. A brand-new realm assigns **no roles to anyone by default**, so the
token's `realm_access.roles` is empty and the mirror is wiped to zero on login.
Migration 008 backfilling the seed admin to `{admin,reader,chat}`, and the
first-login bootstrap, are both **transient** — the next login re-syncs from the
(empty) token. Three mechanisms are meant to seed the first admin, and it is easy
to disarm all three at once:

- **The seed admin's first claim** force-grants `{admin,reader,chat}`, whether the
  founder claims it by signing in first or, with `BOOTSTRAP_ADMIN_EMAIL` set, by
  their verified email. **But only for that first login**: the next one mirrors the
  token again, so an empty token wins from the second login on. (Up to 2.10 the
  `BOOTSTRAP_ADMIN_EMAIL` path had no floor at all, and the first login already
  locked the founder out.)
- **The realm export can grant the founder the roles directly.** The `admin-user`
  entry in [`../deploy/keycloak/realm-export.json`](../deploy/keycloak/realm-export.json)
  now carries `"realmRoles": ["admin","reader","chat"]` — but only a **fresh
  import** applies it (same `--import-realm` trap as Trap 2).
- **The app self-heals via the Keycloak Admin API** — on founder bootstrap it
  best-effort-assigns those three realm roles back into Keycloak so the next
  login's sync doesn't demote the founder — **but only when `KC_ADMIN_CLIENT_ID`
  / `KC_ADMIN_CLIENT_SECRET` are set** (row 6 above). Unset → it silently no-ops.

**Fix — any one durably unlocks; do the first two for a working system:**

- **Assign the roles in the live realm:** admin console → realm `natural-reader`
  → **Users** → your user → **Role mapping** → **Assign role** → switch the filter
  to *realm roles* → tick `admin`, `reader`, `chat` → Assign. Then **log out and
  back in** — realm roles are only read into the token at issue time, so your
  current session won't see them until you re-authenticate. This is the live
  equivalent of the export edit and needs no re-import.
- **Wire the admin service account** (row 6) so the self-heal *and* the whole
  admin-console user-management path actually function:

  ```ini
  KC_ADMIN_CLIENT_ID=natural-reader-admin
  KC_ADMIN_CLIENT_SECRET=<the natural-reader-admin client secret>
  ```

- **Fresh install shortcut:** because the corrected export assigns the founder the
  roles, a clean re-import (`podman compose down -v && … up -d`) makes the very
  first login land as full admin with no console step.

A DB-only `UPDATE users SET capabilities='{admin,reader,chat}'` lets you in this
instant but is **reverted on the next login** (login sync is authoritative) —
use it only as a throwaway, never as the fix.

### Trap 5 — PDF uploads fail at ~1 MB (client_max_body_size)

**Symptom:** small docs index fine; larger PDFs fail with `413 Request Entity
Too Large`.

**Cause:** nginx's default `client_max_body_size` is **1 MB**. Document uploads
go through `POST /v1/docs` and are often much larger.

**Fix:** the `chat.example.com.sample` sets `client_max_body_size 100m;` inside
the `/v1/` block. Raise it if you expect bigger files.

### Trap 6 (minor) — stale `ERR_QUIC_PROTOCOL_ERROR` in Chrome

**Symptom:** Chrome shows `ERR_QUIC_PROTOCOL_ERROR` on one of the hosts.

**Cause:** it's **not** an nginx problem — this nginx is `listen 443 ssl http2`
with no HTTP/3 module and sends no `Alt-Svc`, so it never offers QUIC. Chrome is
acting on a **stale Alt-Svc cache** for that IP (e.g. from a time it sat behind a
CDN that advertised h3) and keeps racing a QUIC handshake nothing answers.

**Confirm/fix:** open the host in Incognito (separate, empty Alt-Svc cache) — if
it works there, it's the cache. Kill it client-side (`chrome://flags/#enable-quic`
→ Disabled) or server-side by emitting an RFC 7838 clear signal —
`add_header Alt-Svc "clear" always;` in both 443 server blocks — which tells
browsers to forget the stale h3 mapping. Do **not** touch `proxy_http_version
1.1`; that governs nginx↔upstream, a different leg entirely.

## Model providers and chat streaming

Chat turns run on the backend (`server/chat/`, `server/llm/`) and stream to
the browser as `text/event-stream`. This section covers the two things that
change when you go from a single local Ollama to real provider config, plus
the proxy setting that keeps streaming visible instead of arriving all at
once.

### Configuring providers

Leave `INFERENCE_PROVIDERS` unset and nothing changes: there is one provider
named `ollama`, backed by `OLLAMA_URL`, limited by `INFERENCE_MODELS` — the
same behaviour as before C1. To add providers, name them in
`INFERENCE_PROVIDERS` and describe each one with `INFERENCE_<NAME>_*` vars.
Worked example — local Ollama plus one OpenAI-compatible provider (to try
the `openai` adapter without signing up for a paid key, point it at
Ollama's own `/v1` endpoint instead of a real OpenAI-compatible host — it
speaks that protocol too):

```bash
INFERENCE_PROVIDERS=local,openrouter
INFERENCE_LOCAL_KIND=ollama
INFERENCE_LOCAL_URL=http://localhost:11434
INFERENCE_OPENROUTER_KIND=openai
INFERENCE_OPENROUTER_URL=https://openrouter.example.com/api/v1
INFERENCE_OPENROUTER_API_KEY=sk-your-key
INFERENCE_OPENROUTER_MODELS=vendor/model-a,vendor/model-b
```

Model ids in the chat UI carry the provider name (`local:qwen2.5:7b`,
`openrouter:vendor/model-a`); a saved selection from before C1
(`qwen2.5:7b`) is read as `ollama:qwen2.5:7b` automatically. `KIND` is
`ollama` for Ollama's native API, or `openai` for anything that speaks the
OpenAI chat-completions shape (vLLM, OpenRouter, LiteLLM, or Ollama's own
`/v1`); for `kind=openai`, `URL` is the API base **including** the version
path — the adapter appends `/chat/completions` and `/models` itself. API
keys stay on the server: never sent to the browser, never logged, never
shown in the admin console's Deployment section (a key reads only *set* or *not set*).

**What has been run against real providers:** OpenRouter, through the
`openai` adapter, with paid `google/gemma-4-26b-a4b-it` and
`mistralai/mistral-small-3.2-24b-instruct` (2026-09-29). That covered
multi-turn chat, pins, a native `web_search` tool round, an image with a
follow-up question, provider-reported token usage, a rate-limited free
model and a rejected key, with no key in the logs. Before that (v2.1.0),
the same adapter was run against Ollama's own `/v1`. vLLM and LiteLLM speak
the same protocol but haven't been run.

### Examples: vLLM, Unsloth, OpenAI and Claude

Each server is one more name in `INFERENCE_PROVIDERS`. Anything that speaks the
OpenAI chat-completions shape is `KIND=openai`, and its `URL` ends in `/v1`.
Of these, only the Ollama provider has been run against this app (OpenRouter has
too; see below). **The others are written from each project's documentation.**

```bash
INFERENCE_PROVIDERS=local,vllm,unsloth,openai,claude
INFERENCE_LOCAL_KIND=ollama
INFERENCE_LOCAL_URL=http://localhost:11434

# vLLM on a GPU machine
INFERENCE_VLLM_KIND=openai
INFERENCE_VLLM_URL=http://gpu-box.example.com:8001/v1
INFERENCE_VLLM_API_KEY=change-me          # whatever you passed to --api-key

# Unsloth Studio (create the key in Studio's Settings; it's shown once)
INFERENCE_UNSLOTH_KIND=openai
INFERENCE_UNSLOTH_URL=http://localhost:<studio-port>/v1
INFERENCE_UNSLOTH_API_KEY=sk-unsloth-...

# OpenAI: name the models you want, or the picker also lists its embedding,
# speech and image models
INFERENCE_OPENAI_KIND=openai
INFERENCE_OPENAI_URL=https://api.openai.com/v1
INFERENCE_OPENAI_API_KEY=sk-...
INFERENCE_OPENAI_MODELS=<chat-model-a>,<chat-model-b>

# Claude, through Anthropic's OpenAI-compatible layer
INFERENCE_CLAUDE_KIND=openai
INFERENCE_CLAUDE_URL=https://api.anthropic.com/v1
INFERENCE_CLAUDE_API_KEY=sk-ant-...
INFERENCE_CLAUDE_MODELS=claude-sonnet-5-5,claude-haiku-5-5
INFERENCE_CLAUDE_CONTEXT_WINDOW=<the model's documented window>
```

On the vLLM side:

```bash
vllm serve Qwen/Qwen3-8B --port 8001 --api-key change-me --max-model-len 32768 \
  --enable-auto-tool-choice --tool-call-parser hermes --reasoning-parser qwen3
```

What each one will surprise you with:

- **Embeddings go to the Ollama at `OLLAMA_URL` unless you say otherwise.** A
  vLLM-only or cloud-only deployment sets `EMBEDDING_PROVIDER` to one of its
  providers, or chooses it in **Admin console → Models → Embeddings** (see
  [Embeddings](#embeddings-provider-model-and-re-embedding)). Claude has no
  embeddings API: pair it with another provider for those.
- **vLLM:** without `--enable-auto-tool-choice` and the `--tool-call-parser` of the
  model's family (`hermes` for Qwen) it refuses tool requests. The app then turns
  that model's tools off, and it never searches the document.
- **Unsloth** means two things. Its quantized models on Hugging Face run through
  the Ollama you already have, with no config change:
  `ollama pull hf.co/unsloth/<repo>:Q4_K_M`, then they're listed under your Ollama
  provider. Unsloth Studio is a server of its own, configured as above.
- **Claude** works through Anthropic's compatibility layer, which Anthropic
  describes as a way to test models rather than a production path. Chat, tools,
  images and token counts work per its documentation. It has no prompt caching,
  ignores the **Thinking** setting (`reasoning_effort`) and strips audio. Whether it
  answers this app's model listing (`GET /models` with a Bearer key) is **unverified**.
  A provider whose listing fails disappears from the picker, with only a log line.

**Trap:** the key goes in `INFERENCE_<NAME>_API_KEY`, where `<NAME>` is the
provider's name from `INFERENCE_PROVIDERS` in capitals —
`INFERENCE_OPENROUTER_API_KEY` for a provider named `openrouter`. A bare
`OPENROUTER_API_KEY` (the name OpenRouter's own docs use) is **ignored**,
and every request then fails with "The provider rejected this server's
credentials".

**What people see when a provider refuses** (the error code stays
`provider_error`; a request the provider refuses outright produces no output
and no usage, so it isn't counted against the daily budget):

| Provider answer | Message in the chat |
|---|---|
| `429` | "This model is busy or rate-limited at the provider. Try again in a moment, or pick another model." — plus the provider's own detail when it sends one (OpenRouter's `error.metadata.raw`, redacted and trimmed) |
| `401` | "The provider rejected this server's credentials. An admin needs to check the API key." |
| `402` | "The provider account is out of credit." |
| `403` | "The provider refused this request." plus the provider's reason. Not treated as a key problem: OpenRouter uses `403` when a moderated model flags the input. |
| anything else | "The model provider returned an error:" and the provider's own message, redacted |

**Trap:** `OLLAMA_URL` keeps its old job of running document **embeddings**
even after you set `INFERENCE_PROVIDERS`, unless `EMBEDDING_PROVIDER` (or the
console) names another provider. Remove `OLLAMA_URL` after adding providers
and indexing breaks, calling `localhost:11434` with nothing listening there.
Adding, removing or renaming a provider is still an operator `.env` change
and a restart; the console only chooses among the configured ones.

Full `.env.example` block: [`../.env.example`](../.env.example), just after
`INFERENCE_TIMEOUT_S`.

### Context sizes, windows and the Models section

Ollama loads a model at **4,096 tokens** on a machine with under 24 GiB of VRAM
unless the request names a size, and quietly cuts the start of a longer prompt.
So the server names a size on every request to a **self-hosted Ollama model**
(any `kind=ollama` provider, whatever its host, except `-cloud` / `:cloud` models
and the ollama.com host; the admin's Local/Cloud mark in **Models → Providers**
affects cost labelling only, never the size). It is the first of these
that is set, clamped to `INFERENCE_NUM_CTX_MAX` (32768 tokens unless set):

1. the admin's size for that model (**Admin console → Models → Per-model sizes**);
2. the deployment's size: **Models → Context size for local Ollama models**, else
   `INFERENCE_LOCAL_NUM_CTX` (2048-2097152 tokens);
3. the person's own **Settings → Chat → Context window**;
4. 16384 tokens.

Chat history is trimmed to that same number, and the reader uses it too (next
section). A larger size reserves more memory when the model loads, and a model
already loaded at another size reloads once on the next message, so size it down
on a machine short of RAM. Nothing is sent for Ollama cloud models (the only
Ollama models without a size: they get the window their provider reports) and
`kind=openai` providers (`num_ctx` is an Ollama option); a person's own value
for an Ollama cloud model is still passed on.

A model whose provider reports no context window (most OpenAI-compatible
servers) is never trimmed until a window is declared:
`INFERENCE_<NAME>_CONTEXT_WINDOW` (1024-10000000 tokens, `<NAME>` as in
`INFERENCE_<NAME>_URL`, so `INFERENCE_OLLAMA_CONTEXT_WINDOW` when
`INFERENCE_PROVIDERS` is unset) declares one for the whole provider, and
**Models → Declared context windows** for one model (that wins). A window the
provider reports always wins over a declared one.

**Admin console → Models** sets the same things without a restart, and wins over
the environment: the deployment's size and per-model sizes, declared windows,
the reader, which providers count as local (a cost flag that never changes a
model's size), and which chat models are offered
(it can only narrow `INFERENCE_MODELS` and each provider's allow-list).
`INFERENCE_NUM_CTX_MAX` is not editable there; Models shows it as the ceiling.
It applies from the next message, on every worker.
[USER_GUIDE.md](USER_GUIDE.md#models-the-reader-context-sizes-and-which-models-people-get)
has the screen.

**Upgrading to this release changes three defaults** (no migration):
- The reader moves to the chat model on a deployment that sets none of
  `READER_MODEL`, `SUMMARIZE_MODEL` or `WEB_SEARCH_SUMMARY_MODEL`. It used to be
  `llama3.2:3b`. With a cloud chat model, reader calls count against its quota;
  choose a local reader under **Models → Reader** to keep them off it.
- Self-hosted Ollama models get 16384 tokens instead of Ollama's 4096 (on under
  24 GiB of VRAM), unless a person or the admin sets a size. A larger context
  reserves more memory when the model loads: size it down under **Models**, or
  with `INFERENCE_LOCAL_NUM_CTX`, on a machine short of RAM.
- A person's own context size stops applying to those models once the admin
  sets one.

### Embeddings: provider, model and re-embedding

Search compares a question's vector with every passage's, so all of them must
come from one model. Which model that is lives in the database (the
`embedding_state` row in `app_settings`), separate from the model an admin
*chooses*. The two differ only while the library is being re-embedded.

**Choosing.** `EMBEDDING_PROVIDER` names one of `INFERENCE_PROVIDERS` (its URL
and key are reused); unset, it's the Ollama at `OLLAMA_URL`. `EMBEDDING_MODEL`
defaults to `nomic-embed-text`. **Admin console → Models → Embeddings** wins over
both: pick a provider and a model, press **Check**, then **Use this model**.
**Back to the server's setting** removes the console's choice. An `ollama`
provider is called at `/api/embed`, an `openai` one at `<its URL>/embeddings`,
32 passages per request. Embedding models are left out of the chat model list
(Ollama says which models only embed, and a known embedding family is one on any
provider), so a model pulled to try here isn't offered for chatting.

**What Check admits.** The vectors column holds 768 numbers. A model is admitted
when it gives 768 by itself, or when it belongs to a family trained to be
shortened (`qwen3-embedding`, `embeddinggemma`, `nomic-embed-text`,
`text-embedding-3-*`), in which case it is asked for 768. Anything else is
refused, and the refusal gives its size. The trap this avoids: Ollama cuts any
model's vector to the size asked for and renormalizes it with no error, so a
1024-number model (`bge-m3`, `mxbai-embed-large`) asked for 768 returns 768
numbers that search noticeably worse. Ollama's own `ollama show` is no guide
either: it prints a model's internal width, which for `embeddinggemma-2` is 512
while it gives 768. Check measures what comes back. A missing model says
"isn't on" the provider; a chat model says it "doesn't make embeddings".

**What Use does.** The same model on another provider switches at once: the
vectors don't change. Another model, or other prefixes, starts a **re-embed**:

1. A second column, `doc_chunks.embedding_next`, is added with its own HNSW
   index and filled 32 passages at a time with the new model, by one worker at
   a time (a claim another worker takes over after 3 minutes of silence, judged
   by the database's clock, so a restart or a crash resumes where it was). A
   passage the model refuses as too long (HTTP 400, 413 or 422: vLLM and OpenAI
   refuse what Ollama would shorten) is sent again on its own and halved until
   it fits, down to 256 characters; a model that refuses even a short sentence
   pauses the re-embed with its error.
2. Meanwhile questions are still embedded with the current model and search the
   current column, so **every document stays searchable**. Both models must stay
   reachable until the switch; if the new one stops answering, the re-embed
   pauses, the Overview says why, and it retries every 30 seconds. Models →
   Embeddings shows a bar, the share done and the time left ("1,234 of 5,000
   passages (24%), about 7 minutes left"), counted from the table, so passages
   indexed meanwhile count too; the time left is the passages left at the
   filler's running rate. While the box is open it says when the re-embed is
   done, paused, or stopped elsewhere.
3. When every passage has a new vector, one transaction drops the old column,
   renames the new one into its place and makes the new model current. A
   passage indexed during the re-embed is caught before the switch.
4. **Cancel the re-embed** (Models → Embeddings) drops the new column before the
   switch; the library stays on its model.

Changing `EMBEDDING_MODEL` (or a prefix) in `.env` starts the same re-embed at
the next start. A model Check would refuse is refused there too: the library
stays on its model and the Overview says why. A model that didn't answer (not
pulled yet, the provider down) is tried again every 5 minutes, so pulling it is
enough; one that answered with the wrong size stays refused until the setting
changes. Changing it again before the switch stops the re-embed: back to the
model in use stops it, another model starts over, and a refused one stops it
and says why.

**What it costs.** One more copy of the vectors and an index while it runs:
768 four-byte numbers per passage, about 3 KB, plus the index, so roughly
6 KB per passage in all. On a laptop CPU with no GPU (i9-13980HX), re-embedding
took about **2.5 minutes per 1,000 passages with `nomic-embed-text`** (6.4
passages a second, measured mid-run) and **about 4 to 6 with
`embeddinggemma-2:740m`** (313 passages in 80 s and in 109 s on two runs, model
load included). Expect a GPU to be much faster; that wasn't measured.

**Rebuilds of one document** (after a chunker change, or for a document indexed
before an upgrade) are queued in the `doc_rebuilds` table and claimed by one
worker at a time with `FOR UPDATE SKIP LOCKED`, so with `WORKERS > 1` a document
is rebuilt once, a restart resumes the queue, and the Overview counts it. A
rebuild that fails waits 15 minutes before it is tried again.

**The Overview never loads a model.** On Ollama it reads two lists: `/api/tags`
(is the model pulled?) and `/api/ps` (is it in memory now?). Whether it gives 768
numbers was measured once, when it was chosen; a model made current from `.env`
is checked once by the background loop after a start (loaded for that check and
unloaded after, unless something had it loaded already), and the answer is kept
in `embedding_state`, so a wrong size shows as a problem without anyone loading
it again. An `openai` provider that lists the model at `/models` is treated the
same way; one that doesn't (OpenRouter's list is chat models) is checked by
embedding one sentence. **Check** in Models → Embeddings unloads a model it had
to load, unless **Use** follows: the re-embed loads it seconds later anyway.

**Upgrading to this release.** Migration 025 adds `doc_rebuilds`; the first
start records the environment's model as current. Nothing is re-embedded,
with one exception: a deployment whose `EMBEDDING_MODEL` is an `embeddinggemma`
now gets that family's trained prefixes, so its documents are rebuilt on first
use, as after any prefix change. `EMBEDDING_TIMEOUT_S` now defaults to 120 s,
since one request carries 32 passages. **Downgrading:** cancel any re-embed first
(the previous image doesn't know the second column), then run 025's Down block.

**Checked live** on Ollama 0.40.13's own OpenAI-compatible `/v1` as an `openai`
provider: Check, the same model switching at once, the Overview, searching, and
a 313-passage re-embed through `/v1/embeddings`; and two backends sharing one
database, where one filled and the switch happened once. **Not called:** vLLM,
OpenAI and OpenRouter themselves; their requests follow the same OpenAI API.

### Streaming through a proxy

> Replies stream as `text/event-stream`. nginx honours the
> `X-Accel-Buffering: no` header the server sends, and the sample config's
> `proxy_buffering off` covers it. Some CDNs and tunnels (Cloudflare among
> them) may still buffer event streams. The symptom is a reply that appears
> all at once after a long pause. Turn off buffering for `/v1/chat/` in that
> proxy.
>
> While a reply is silent (a tool running, a long prompt being read before
> the first word), the server sends a `: keep-alive` comment every 15 seconds,
> so the proxy's idle/read timeout must be longer than 15 s. nginx's default
> `proxy_read_timeout` (60 s) is fine.

### Operational notes

- **One reply in flight per chat.** Sending a second message while the first
  is still streaming gets `409 turn_in_progress`; the in-progress turn is
  untouched.
- **A crashed worker's turn recovers on its own.** A turn heartbeats every
  15 seconds while it runs; if that heartbeat goes quiet for about 60 seconds
  (the worker died mid-reply), the next thing that touches that chat — opening
  it, or the startup sweep — marks the reply `aborted` and frees the chat for
  a new message. This is safe with several `WORKERS`: a heartbeat that's
  still fresh is left alone, so one worker's restart never kills a turn a
  different worker is actively streaming.
- **Shutdown waits briefly for in-flight writes.** On a graceful shutdown the
  server waits up to 5 seconds for turns' final "complete"/"aborted" write to
  land before closing the database pool, so a restart during a reply doesn't
  usually leave it stuck `streaming` until the next startup sweep.
- **Model names without a `provider:` prefix** (the pre-C1 `SUMMARIZE_MODEL`,
  a bare name such as `llama3.2:3b`, and `READER_MODEL`) resolve to the first configured provider
  of kind `ollama`. Web search no longer summarizes pages; the reader does the
  reading (next section).

## Project chat, the reader and web search

Three things arrive together, and each has a knob a deployer may care about.

- **A chat can be about a whole project.** The assistant gets a map of the
  project (every current document's name, page count and outline) and searches
  and reads across all of them. Migration `021` adds the chat's project and a
  table of document outlines; it is additive and runs at startup.
- **A reader sub-agent does the bulk reading.** For "go through these pages for
  X" the chat model hands the job to a separate, small model call that reads in
  batches and returns only the facts, each with a quote the app has checked
  against the source. This is the same call that reads web pages.
- **Web search asks first.** By default the assistant can only *offer* a search
  and the person says yes on a card. Nothing leaves the server before that.

**Behaviour changes on upgrade.**
- **Web search was "on its own"; the default is now "ask".** A deployment with
  SearXNG that relied on the assistant searching by itself needs
  `CHAT_WEB_SEARCH=auto`, or an admin's choice in **Admin console →
  Chat options**, to keep that.
- **Existing documents are re-chunked on first use.** Fenced code blocks and
  outlines are new, so each indexed document is rebuilt in the background the
  first time someone chats with or searches it, and its old chunks keep
  answering until the new set swaps in. Text that already has an embedding
  under the same model is not embedded again, so the rebuild embeds only the
  new code chunks and the Markdown tables whose cells were glued together
  before. (This comes from the code and its tests, not from timing a large
  library, so watch the first days on a big one.) A document with no stored
  file keeps what it has.
- **`WEB_SEARCH_MAX_PAGE_CHARS` is no longer read.** It cut each web page at its
  first 6,000 characters before summarizing it; pages are now cut into
  passages and the ones that match the goal are read from anywhere on the page.
  Delete it from `.env`.
- **`POST /v1/tools/web_search` returns different fields** (see the API
  section below).

### Settings

Every value is an environment variable, read at startup unless noted. All of
them are in [`../.env.example`](../.env.example).

- **The `CHAT_*` and `READER_*` values** are checked: one that is not a number
  (or, for `CHAT_WEB_SEARCH`, not one of the listed words), or is outside its
  range, logs a warning and the default is used (for the reader's model and
  timeout, the next name in the chain).
- **The numeric `WEB_SEARCH_*` values** are checked the same way: a value that
  is not a number, or is outside its range, logs a warning and the default is
  used (the table under "Web search" gives each range). An empty value is the
  default, silently. `WEB_SEARCH_SUMMARY_MODEL` and `WEB_SEARCH_SUMMARY_TIMEOUT_S`
  are read by the reader and checked like its own values.

**The project map and project search**

| Variable | Default | Range | What it does |
|---|---|---|---|
| `CHAT_PROJECT_MAP_CHARS` | `6000` characters | 500-100000 | Size of the project map in the system message, sent with every message. 6000 is about 1,500 tokens and leaves room to answer in an 8,192-token window. Over it, outlines are shortened step by step and the assistant gets a `document_outline` tool. Raise it only if your models run with a large context window. |
| `CHAT_PROJECT_MAX_TOOL_ROUNDS` | `5` rounds | 0-20 | Tool rounds in a project chat before the final answer. `CHAT_MAX_TOOL_ROUNDS` (3) still applies to the other chats. `0` turns the tools off in project chats. |
| `CHAT_PROJECT_FUSION_DEPTH` | `50` candidates | 1-500 | Candidates taken from the meaning ranking and from the exact-word ranking before they are fused, in a project search. A single-document search uses 20. |
| `CHAT_SEARCH_PER_DOC` | `2` passages | 1-10 | Passages one document may give a project search before other documents get a turn. A short list is filled from the skipped ones, so a document that holds all the answers still fills it. |
| `CHAT_EXTRACT_MAX_PAGES` | `12` pages | 1-200 | Pages one `extract_from_documents` call hands to the reader. Over it, the pages closest to the question are read and the rest are listed as not read. 12 is about three or four reader batches. |
| `CHAT_EXTRACT_OUTPUT_CHARS` | `4000` characters | 500-50000 | Most one `extract_from_documents` result may take, measured as compact JSON. The model reads it indented, so what reaches it is typically 15-25% larger (more when the facts are short). Over the limit, the last facts are dropped and `more` says how many. |

**The reader**

| Variable | Default | Range | What it does |
|---|---|---|---|
| `READER_MODEL` | none: falls back to `SUMMARIZE_MODEL`, then `WEB_SEARCH_SUMMARY_MODEL`, then the chat model of the turn | a model id | The model that reads. `chat` (any letter case) means the model of the turn itself, which is also what you get when none of the three is set; `SUMMARIZE_MODEL=chat` means the same for the reader. **Admin console → Models → Reader** wins over all three; it can only name a model the allow-lists (`INFERENCE_MODELS`, `INFERENCE_<NAME>_MODELS`) let the chat use, and the Overview flags a reader from any source that they don't. |
| `READER_INPUT_CHARS` | `12000` characters (about 3,000 tokens) | 500-200000 | The most source text in one reader batch: a ceiling, never exceeded. A batch is cut shorter to fit the reader's context window when that is smaller (never below 500), and holds at most 16 passages. A document page longer than this is read in pieces that overlap by 200 characters; a web page is cut into passages of about 1,000 characters and the best-matching ones are kept up to this size. |
| `READER_OUTPUT_CHARS` | `1200` characters | 100-20000 | The most one batch may return. The reader is told to stay under it and its reply is capped at about that length; a reply cut off mid-way counts as a failed batch. |
| `READER_MAX_CONCURRENCY` | `2` batches | 1-32 | Batches running at once. |
| `READER_TIMEOUT_S` | `60` seconds | 1-3600 | Time one batch may take. `WEB_SEARCH_SUMMARY_TIMEOUT_S` is read when this is unset; with both unset, the reader uses 60 seconds. A batch that fails is listed under `failed` in the result (documents; `not_read` is only for pages left out by the page cap) or falls back to a short excerpt (web). |

A reader on a self-hosted Ollama model is loaded at the size described under
[Context sizes](#context-sizes-windows-and-the-models-section), the same for
every call of the turn. When the reader is the chat model it uses the chat's
size (steps 1-4 there: the per-model size, else the deployment's, else the
person's own value if they chose one rather than Auto, else 16384, clamped), so
Ollama doesn't reload it between chat and reader calls. A different
model gets the same steps without the person's value (its per-model size, else
the deployment's, else 16384, clamped). A reader on a model that isn't sized
(Ollama cloud, `kind=openai`) sends no size.
Batches are cut to fit.

**The reader is the chat model unless you say otherwise.** With none of
`READER_MODEL`, `SUMMARIZE_MODEL` or `WEB_SEARCH_SUMMARY_MODEL` set (and no
choice in **Models → Reader**) the reader uses the model of the conversation:
no second model is loaded into RAM, which on a CPU-only Ollama would otherwise
cost seconds to minutes of swapping on every project question. The price: the
chat model does the reading too, so a slow chat model makes `extract` slow (on a
CPU the batches run one after another whatever `READER_MAX_CONCURRENCY` says),
and with a **cloud** chat model every reader batch is a call counted against its
quota. Name a small model in `READER_MODEL`, or under **Models → Reader**, when
the chat model is a large or paid one: every batch is a full model call, and a
small model is cheaper and faster for it. (Before this release the default was
`llama3.2:3b`.)

**Reader calls count toward the daily budget and show in usage.** Each batch
is recorded against the person who asked, in the same daily totals as their
chat replies (the per-user daily token budget and the admin console's usage
figures), as one request. The figures are **measured** when the provider
reports token counts, which Ollama and OpenAI-compatible providers do, and
**estimated** otherwise: characters sent ÷ 4 and characters returned ÷ 4,
rounded up, the same estimate a chat reply gets. A batch that fails or times
out counts only if it produced something. A person who runs out mid-read
isn't cut off half-way through the tool: the assistant's next step is refused,
as for any reply. Reader batches are booked to the reader model's own row
(kind `reader`) in **Admin -> Usage**; the server log's `reader usage` line
still names the model.

A project question can be silent for a while when the reader is working. The
server's 15-second keep-alive (see [Streaming through a
proxy](#streaming-through-a-proxy)) is what keeps a proxy from closing the
stream.

**Web search**

| Variable | Default | What it does |
|---|---|---|
| `CHAT_WEB_SEARCH` | `ask` | `ask`, `auto` or `off`, case-insensitive; anything else is ignored with a warning. The starting value: an admin's choice in the console wins over it. |
| `SEARXNG_URL` | `http://localhost:18043` | The SearXNG instance. Every mode needs it. Set it to an empty value to turn web search off for the whole server; the console then shows its control disabled. |
| `WEB_SEARCH_RESULT_COUNT` | `5` results | Results one search lists, 1-10. A search no longer fetches pages or calls a model. |
| `WEB_SEARCH_MAX_CONCURRENCY` | `4` fetches | Pages fetched at once when the assistant reads results, 1-32. |
| `WEB_SEARCH_FETCH_TIMEOUT_S` | `8` seconds | Per request: the SearXNG query and each redirect hop of a page fetch, 1-120. |
| `WEB_SEARCH_FETCH_TOTAL_S` | `15` seconds | Whole deadline for one page, redirects and body included, 1-300. |
| `WEB_SEARCH_MAX_RESPONSE_BYTES` | `2000000` bytes | Largest page body read, 10000-50000000. |
| `WEB_SEARCH_SUMMARY_MODEL`, `WEB_SEARCH_SUMMARY_TIMEOUT_S` | none | Old names, kept as fallbacks for `READER_MODEL` and `READER_TIMEOUT_S`. With `READER_TIMEOUT_S` and `WEB_SEARCH_SUMMARY_TIMEOUT_S` both unset, the reader uses 60 seconds. |

### How web search asks

| Mode | What the assistant can do |
|---|---|
| `ask` (default) | Only `offer_web_search`: it proposes a short query and a reason; if it has written no answer yet it gets one more step, with no tools, to write what the documents say and don't (and what it knows in general, labelled), and then the turn ends and the person sees that text and a card with the query in an editable box, **Search the web** and **Not now**. **Search the web** runs exactly the query on the card, for the next message only; the assistant may then read 1-3 of those results, or offer a different search (which needs its own yes). The web search tool is not offered, so the rule is enforced in code, not asked of the model. |
| `auto` | The assistant searches the web itself and reads the pages it picks. |
| `off` | No web tool at all, and `POST /v1/tools/web_search` answers 403 "Web search is off on this server." without searching. |

Which setting applies, first match wins: the admin's choice in **Admin console
→ Chat options → Web search**; then `CHAT_WEB_SEARCH`; then `ask`. The
database is read once per message, so a change applies from the next message on
every worker. **Use the server setting** in the same section deletes the
admin's choice, so `CHAT_WEB_SEARCH` (or `ask`) applies again, and the section
says which of the three decided. Changing it writes an audit line with who, the
old mode and the new one. The query a person approves is the one that leaves:
invisible and direction-changing characters are removed from it on the card and
again on the server.

### API changes

- `POST /v1/tools/web_search` (a public route; nothing in the app calls it)
  used to fetch each page and return `{title, url, summary, source}`. It now
  returns the search results only: `{title, url, site, snippet}`, with no
  page fetch and no model call. A caller that read `summary` needs to read
  `snippet`, or fetch the page itself. When the web search mode is `off` it
  refuses with 403; `ask` and `auto` don't affect it (the caller chose the query).
- `GET /v1/admin/chat-options` returns `{web_search, source, searxng, voice_input,
  voice_source}` (`source` and `voice_source` are `admin`, `env` or `default`;
  `searxng` is false when `SEARXNG_URL` is empty). `PUT` takes
  `{"web_search": "ask" | "auto" | "off" | null}` and/or `{"voice_input": true | false | null}`;
  `null` removes the admin's choice, and a body naming neither is refused (422).
  Both need the `admin` capability. See [Voice input](#voice-input).
- `POST /v1/chat/sessions/{id}/turns` accepts `context.project_id` (a project
  id scopes the chat to it, `null` clears the scope, leaving it out keeps what
  the chat has) and `context.web` (`{query}`, up to 300 characters: the person's
  approval of one web search for this turn). A turn for a project the caller
  isn't a member of is refused with 404 and the message "This project doesn't
  exist or you don't have access.", and the chat's project is cleared.
- The chat session list and the session record carry `scopeProject`
  (`{id, name}` or `null`); `name` is `null` when the caller can no longer see
  the project.

## Voice input

The microphone button in the chat message box. A person speaks, the server turns
the recording into text, and the text lands in the message box for them to edit
and send. Nothing is sent for them. **It is off by default**: until an admin turns
it on, nobody sees the button, nothing is downloaded and no CPU is used.

**Turning it on.** **Admin console → Chat options → Voice input → On**, or
`CHAT_VOICE_INPUT=on` in `.env` as the starting value. The console's choice wins
over `.env`, and **Use the server setting** in that section hands control back.
Switching it on starts the engine loading in the background, without making the
click wait; the first time, the local engine downloads its model. **Admin console →
Overview → Voice input** shows where that stands: **Warning** "Getting ready:
downloading or loading *model*." and then **OK** "local · small · runs on this
server". The change applies from the next recording on every worker, with no
restart. A server restarted with voice already on starts loading the model at
startup (about 3 s from the cache), and a worker that never loaded one starts
when the first person's button asks.

### The settings

All six are in [`../.env.example`](../.env.example). The **Deployment** page of the
admin console lists four of them read-only (engine, model, server and key), under
the labels in the first column; the switch has its own control, and
`SPEECH_CPU_THREADS` isn't listed. It prints a variable's name only when the value
comes from `.env`; a default shows under the label alone. The key never shows: it
reads **set** or **not set**.

| Variable (Deployment label) | Default | What it does, and when it applies |
|---|---|---|
| `CHAT_VOICE_INPUT` (none: its switch is **Chat options → Voice input**) | `off` | `on` or `off`, the starting value for the switch. Anything else is ignored with a warning, and off applies. |
| `SPEECH_ENGINE` (**Voice input engine**) | `local` | `local` runs [faster-whisper](https://github.com/SYSTRAN/faster-whisper) inside the backend, on the CPU: nothing leaves the server. `openai` calls a remote server that speaks `POST {SPEECH_URL}/audio/transcriptions`. `google` uses Google Cloud ([Google Cloud speech](#google-cloud-speech)). Anything else logs a warning and means `local`. Since v2.7.0 it is the starting choice: **Admin → Chat options → Speech recognition** wins over it, with no restart, while the engine it names is configured. |
| `SPEECH_MODELS_FILE` (none) | none | `local` only: a YAML file on the host, of extra speech models for **Chat options → Speech model**. See [Choosing a model](#choosing-a-model). |
| `SPEECH_MODEL` (**Voice input model (server setting)**) | `small` for `local`; none for `openai` | The starting model: **Chat options → Speech model** wins over it for `local`. `local`: an id from that list, a size (`tiny`, `base`, `small`, `medium`, `large-v3`, `large-v3-turbo`), a Hugging Face repo id of a CTranslate2 model, or a directory on the server (see [Choosing a model](#choosing-a-model)). `openai`: the server's model name (`whisper-1` for OpenAI itself); required, and the Overview says "SPEECH_MODEL is not set" without it. |
| `SPEECH_URL` (**Voice input server**) | none | `openai` only: the base **including `/v1`**, like `INFERENCE_<NAME>_URL`. Without it the Overview says "SPEECH_URL is not set". |
| `SPEECH_API_KEY` (**Voice input key**) | none | `openai` only: sent as the bearer key. It stays on the server and is never logged or shown. |
| `SPEECH_MODELS_LOADED` (none) | `3` | `local` only: how many speech models a worker keeps loaded, 1 to 4 ([Offering models to people](#offering-models-to-people)). A ceiling: a model loads only when someone speaks its language. Memory is per worker. Anything else logs a warning and 3 applies. |
| `SPEECH_CPU_THREADS` (none) | half the machine's cores **divided by `WORKERS`**, at least 1: `max(1, cores // (2 × WORKERS))` | `local` only: the threads one transcription uses (a count), per worker. Every worker has its own engine, so with the default all the workers together stay at half the machine and the rest stays free for chat, narration and indexing. A value you set is used as is, per worker, so with `WORKERS` above 1 it multiplies. A value that isn't a number logs a warning and the default applies. |

### What it costs on the server

**Image and disk.** The Python packages (`faster-whisper`, `ctranslate2`, `av`) add
about 276 MB to the backend image (`ctranslate2` 146, `av` 128, `faster-whisper`
2). They are in `requirements.txt`, not an optional extra, so the button works on
a quick-start box the moment it is switched on. The `small` model is another 464
MB under `HF_HOME` (in the container, the `cache` volume), downloaded once.

**Time, as measured on one CPU-only test machine with `small`** (32 cores and one worker, so the
default 16 threads). It is one machine, not a promise: a smaller box will be
slower, and none of this was measured on yours.

| What | Measured |
|---|---|
| First turn-on: download plus load | 46.6 s |
| A later load (a restart, or a worker starting) | about 3 s |
| 6 s of speech | 1.5-1.7 s to transcribe |
| 101 s of speech | 9.0 s alone, 11.9 s while a local chat reply was running |
| A cloud model's reply during a 101 s transcription | not slowed |
| A **local** model's reply during a 101 s transcription | about 1.5-2 times as long |

The local engine transcribes **one recording at a time per worker process**. A
second one waits up to 60 s, then the person sees "Voice input is busy. Try again
in a moment." (503). Each person can have one running **per worker** (409; the count is not shared between workers). Cancel in the browser
ends the request, but a transcription already running finishes in its thread (a
thread can't be stopped) and holds the slot until it does: at most one 2-minute
recording's worth of CPU.

**`WORKERS` above 1: each worker loads its own model**, so memory is multiplied by
the worker count, as with Kokoro. Measured in the real
image on the same CPU-only machine, Kokoro included: **1.26 GB** after the model
loaded and **1.95 GB** after use (the model alone is 464 MB on disk). Speed in
that image: 5.7 s of speech in 1.7 s; 100.8 s of speech in 21.5 s with read-aloud
narration running alongside. One sentence of narration took 2.1 s alone and
3.5-5.2 s during a transcription. One machine, not a promise.

### Choosing a model

**Admin console → Chat options → Voice input → Speech model** lists the models and
switches between them with no restart. One model runs for the whole server. The
change saves at once, and with voice on the new model starts loading: **Overview →
Voice input** shows **Warning** "Getting ready…" and then **OK** with the model's
name. The old model is dropped straight away, so voice says "still getting ready"
until the new one is up, and a recording already running finishes on the old one.
With `WORKERS` above 1, each worker swaps at its next speech request. If you mean to
use a particular model from the start, choose it **before** turning voice on, or
`small` downloads first for nothing.

The list has three sources, and a later one replaces an earlier entry with the same
id:

| Source | How to add to it |
|---|---|
| Built in | The Whisper sizes, plus a few fine-tuned models that download only if chosen |
| A deployer's file | `SPEECH_MODELS_FILE`: a path on the host, read once at startup. `startup.sh` mounts it into the container |
| The admin console | **Add a model from Hugging Face**, under the list |

**Adding one in the console.** Enter the repo as `owner/name`. It's looked up on
Hugging Face straight away, so a typo, a private repo, or a repo that isn't Whisper
is refused with the reason. It's pinned to a commit (the latest, unless you give a
revision), so it can't change under you. Language codes are optional: left empty, a
single language on the model card is used if Whisper knows it. Nothing downloads
until someone chooses it. **Added here**, under the list, has a **Remove** for each
model you added, in use or not. Its downloaded files stay in `HF_HOME`, and if it was
in use, the server setting applies again. A gated or private repo needs `HF_TOKEN` in
the backend's environment.

A fine-tune for a language Whisper doesn't list, such as Dhivehi, can be added, but
nothing tells it what language it's hearing. That case hasn't been tried.

**In a file.** The same shape as a model list you might already keep; each entry
needs an `engine` and a `repo`:

```yaml
models:
  whisper-small-example:
    engine: converted-whisper     # or faster-whisper, or transformers-whisper
    repo: example-org/whisper-small-example
    revision: <a commit hash>     # pin it: otherwise it follows main, converted once
    languages: [si]               # Whisper codes; leave out for every language
    license: apache-2.0
    commercial: check             # yes, no or check: shown beside the model
    note: Shown under the list when this model is chosen.
```

A bad entry is left out with a warning in the log, and the rest stay.
`SPEECH_MODEL` can name any id in the list as well as a size, a repo or a directory.
The admin's choice wins over it, and **Use the server setting** hands control back.
A directory has to exist where the backend runs, which in a container means inside
it: use the route below, or a mount of your own.

**How each engine runs.**

| `engine` | What it takes | What happens |
|---|---|---|
| `faster-whisper` | A size, a CTranslate2 repo (it has a `model.bin`), or, in a file or `SPEECH_MODEL` only, a directory | Downloads once and loads |
| `converted-whisper` | A Transformers-format repo (`model.safetensors` or `pytorch_model.bin`), which is how most fine-tunes ship | Downloads once, then converts on this server into `HF_HOME/speech-converted/`, in a separate process that hands its memory back |
| `transformers-whisper` | The same kind of repo | Runs as it is, inside the backend. Slower and heavier: use it to compare, not to serve |

**The trap: `torch`.** The last two need `transformers` and `torch`, and only the
Docling build has them (`NR_WITH_DOCLING=true`). On the plain image those models show
disabled in the list with the reason, unless the model is already converted on that
server: then it needs neither package.

**Converting somewhere else.** This is the way on the plain image, and on a server
that can't reach Hugging Face. The model has to be in the list with that repo and
revision: the built-in fine-tunes are, and anything else goes in your file as a
`converted-whisper` entry. Then, from the repo root on a machine with `torch` and
`transformers`, or inside a container of the Docling build, run the app's own
converter. It also writes the two files many fine-tunes leave out, which is why the
plain `ct2-transformers-converter` fails on all the built-in ones. The folder's name
has to be exact:

```bash
REPO=<org>/<whisper-model-name>
REV=<the commit in the entry>
NAME="${REPO/\//--}--$REV--int8"
python -m server.speech.convert "$REPO" "$REV" "./$NAME"
```

Copy that folder into `HF_HOME/speech-converted/` on the server. In a container that's
on the `cache` volume:

```bash
podman exec <backend> mkdir -p /app/.cache/huggingface/speech-converted
podman cp "./$NAME" <backend>:/app/.cache/huggingface/speech-converted/
```

The model now shows as available and loads with no download. A `converted-whisper`
entry still needs Hugging Face if that folder isn't there.

**The built-in fine-tunes.** Each is about 967 MB to download and, once converted,
242 MB on disk. Ready in 105-126 s on a test line, download included; conversion
itself takes a few seconds. The download goes into the conversion's own scratch
folder and is deleted with it, so only the 242 MB stays. One worker converts a model
at a time; another that wants it meanwhile waits for that result instead of
downloading it again.

**Originals left by 2.10 and earlier.** Those versions downloaded the original into the
Hugging Face cache and kept it, about 925 MB per converted fine-tune, never read
again. To get the space back, delete each converted model's repo from the cache's
`hub` folder (only `converted-whisper` repos: a `faster-whisper` model loads from
there):

```bash
podman exec <backend> sh -c 'ls -d /app/.cache/huggingface/hub/models--*'   # find them
podman exec <backend> rm -rf /app/.cache/huggingface/hub/models--<owner>--<name>
```

| Id | For | Measured on a CPU-only machine |
|---|---|---|
| `whisper-small-si` (run12) | Sinhala | 8.4 s of speech in about 2 s. Readable on all 8 test clips, read by eye: those clips came with no transcripts, so no error rate. Stock `small` gave gibberish on them. English comes out as gibberish here (its model card measures 98.7% word error), so don't choose it for a server that also takes English |
| `whisper-small-si-run15` | Sinhala | About the same time and, by eye, the same quality on those clips. Not scored. Its model card reports about the same Sinhala scores as run12, with English at 8% word error |
| `whisper-small-si-hf` | Comparing | The run12 weights unconverted: 8.9 s against 1.9 s for the same clip, and 2.28 GB peak memory against 0.78 GB |
| `whisper-small-ta` | Tamil | On 12 Tamil clips the median character error was 0.14, against 0.35 for stock `small`. By clip: 0-3% on clean read speech, 6-35% on volunteer and dialect recordings, and worse on one single-word clip, where it added words. Not tried on Sri Lankan Tamil |
| `whisper-turbo` | English | The pinned `large-v3-turbo`. Good on English; weak on Tamil and unusable on Sinhala |

A separate benchmark by the project's owner on clean read speech, 15 clips per
language on CPU, scored run12 at 0.028 character error and the Tamil model at 0.029.
That's a best case: real recordings were visibly worse. The licences differ: `commercial`
in the list says `check` for the Sinhala models, whose training data includes audio
of unknown licence.

**Languages.** With one model, a server specialises in one language;
[Offering models to people](#offering-models-to-people) gives each language its own. A
server that takes English should stay on a stock model such as `small`, which in
the walk got English and Spanish right and made several word errors in Hindi. People
pick a language under **Settings → Chat → Speech language**, or leave **Auto**. Three
things to know about fine-tunes:

- **Auto doesn't work on them, so the app doesn't use it.** Language detection called
  Sinhala Kannada, at 0.92 confidence, and the Tamil model's config lists no languages
  to detect from. So with Auto, a model made for one language is told that language.
  The Settings row says what the server's model is for.
- **A language a person chooses is honoured,** because some fine-tunes still read
  other languages. On one that doesn't, the result is gibberish.
- **English words inside Sinhala speech** come out in Sinhala letters, spelled
  differently each time.

### Offering models to people

One model no longer has to hear everyone. Under **Admin → Chat options → Speech model**,
**Offered to people** has a tick for each model made for particular languages (the
Sinhala and Tamil fine-tunes, the English-only sizes). A ticked model hears its language
whenever a person says they speak it: the **Speaking** chip beside the microphone and in
Talk, or **Settings → Chat → Speech language**. The model above the ticks hears every
other language, and Auto. Of two ticked models for one language, the first in the list
wins. A model that hears every language can't be ticked: the one above already does.

- **Ticking it downloads it**, in the background and without loading it, into
  `HF_HOME`, which every worker shares. Until it's there the console says
  **Downloading: people see it once it's here**, and people don't see it. Ticking again
  retries a download that failed. Sizes: `base` ~145 MB, `small` ~480 MB, `medium`
  ~1.5 GB, `large-v3` ~3 GB; a fine-tune that needs converting is converted once.
- **Only a ticked model is ever loaded for a person.** The browser sends a language,
  and the server picks the model from the list.
- **Several models per worker.** A worker keeps up to `SPEECH_MODELS_LOADED` loaded
  (default 3, 1 to 4). It is a ceiling: a model loads only when someone speaks its
  language. The model above keeps its place; of the others, the least recently used goes
  first, never one that is hearing a clip. A clip for a model that isn't loaded waits for
  it (about 1 s from disk) instead of being refused; if it can't load, the model above
  hears the clip, in the language asked for. Each answer names the model that heard it,
  and so does the log line (`model=`).
- **Recordings take turns** in one slot per worker, whatever their model, so a slow
  model slows everyone on that worker.

**What it costs**, measured 2026-11-04 (faster-whisper 1.2.1, int8, 4 threads, a 2 s
clip, the resident memory of one process):

| | Memory |
|---|---|
| The process before any model | 70 MB |
| `small` loaded / after its first clip | 584 MB / 756 MB |
| `small` and `base` both held, after a clip each | 886 MB |
| … then `base` dropped | 482 MB |

Memory is per worker: multiply by `WORKERS`. In a walk of one Talk conversation in
Sinhala, English and Tamil (2026-11-04, one worker, Kokoro reading the replies), the
whole backend went from 1,238 MB to 2,854 MB: the Sinhala model added 781 MB, `small`
(already loaded) 176 MB, the Tamil model 659 MB. `medium` is about three times `small`,
`large-v3` about six (estimates by parameter count). A dropped model's memory is handed back with `malloc_trim`: without it about
250 MB of each stayed with the process. Loading from disk took about 1 s for `small`
and 0.8 s for `base` (with the clip), on an idle machine.

**Talk's speech-or-noise check** (`SPEECH_WINDOW_MAX_NO_SPEECH`,
`SPEECH_WINDOW_MIN_LOGPROB`) was measured on `small`. The same clips on other models,
with the app's decoding:

| | `small` | `base` | `base.en` |
|---|---|---|---|
| 7 English questions, 3 of them 6 dB quieter | 7 speech | 7 speech | 7 speech |
| Highest `no_speech` among them (limit 0.25) | 0.03 | 0.03 | 0.01 |
| 6 four-second pieces of a colleague's voice, ~13 dB below | 4 speech | 3 speech | 5 speech |
| Room noise | noise | noise | noise |

On the Sinhala and Tamil fine-tunes, sent as their language (three clips each: one
sentence and two short greetings, **synthetic Gemini-TTS voices, not real people**):

| | Sinhala fine-tune | `small` as Sinhala | Tamil fine-tune | `small` as Tamil |
|---|---|---|---|---|
| The 3 clips | 3 speech, `no_speech` 0.00 | 1 speech, 2 noise (0.32, 0.34); text "වවවව…" | 3 speech, `no_speech` 0.00 | the sentence right; both greetings noise (0.49, 0.26) |
| An English question, a colleague's English | speech, transliterated | | speech, transliterated | |
| Room noise | noise (the VAD removed it) | | noise | |

So a language's own model is what lets short Sinhala and Tamil turns through: `small`
drops them as noise. **The fine-tunes say `no_speech` 0.00 for anything with a voice
in it**, so on them that limit never fires, and only the VAD, the empty-text check and
the echo check stand between Talk and a stray voice. Real Sinhala and Tamil speakers in
Talk are untested.


### How it listens

The local engines use the settings below, chosen by measuring real Sinhala, Tamil and
English recordings. They are in `server/speech/local_whisper.py`.

- **A voice activity detector always runs** (Silero, bundled with faster-whisper).
  Without it, 4 s of silence came out as 82 characters of invented Tamil, and a 3 s
  pause lost a whole second Sinhala sentence. With it, silence gives no text at all,
  and speech on both sides of a 20 s pause survives.
- **Its threshold is 0.4, not the default 0.5.** At 0.5 it cut real speech out of
  volunteer recordings, and the median Tamil error rose from 0.14 to 0.24. Below 0.35,
  loud steady noise started to come out as invented words.
- **`temperature=0`:** by default Whisper retries a clip it doesn't understand, up
  to six tries in all. Turning that off took stock `small` on 8 Sinhala clips from
  171 s to 27 s, and the worst Tamil clip from 18.2 s to 2.7 s. A 269-word English
  recording came out word for word the same. Those retries can rescue a clip now and
  then, so a clip that fails now fails once.
- **Left at Whisper's defaults on purpose:** `beam_size=1` saved about 13% and changed
  real words ("balance" became "field"), and `condition_on_previous_text=False` saved
  about a quarter on recordings over 30 s but lost the last two words of that English
  one.

English is the primary language for now, and it came through all of this unchanged: the
English and Spanish test sentences read the same as before, and 101 s of English speech
took 8.7 s.

**A failed load.** A model that can't download or load (no network, a wrong name, a
Transformers-format folder) shows on **Admin → Overview** as **Problem**. Each
worker retries a failed load by itself, **at most once every 5 minutes**, when a
person's message box or a recording asks (so a failing download can't restart on
every page load, and a transient failure heals on every worker within about five
minutes). People who press the microphone meanwhile see "Voice input isn't working
right now. Ask your admin."; "still getting ready" is only for a load in progress.
To retry at once, fix the cause, then turn **Voice input** **Off** and **On** again
in **Chat options**: that starts a new load, with no restart, on the worker that
takes the click; the other workers retry on their own within 5 minutes.

### A remote engine (`SPEECH_ENGINE=openai`)

Any server that speaks OpenAI's transcription route works. Set the base **with
`/v1`**; the app adds `/audio/transcriptions`:

```bash
# OpenAI itself
SPEECH_ENGINE=openai
SPEECH_URL=https://api.openai.com/v1
SPEECH_MODEL=whisper-1
SPEECH_API_KEY=<your key>

# a self-hosted vLLM serving a Whisper model
SPEECH_ENGINE=openai
SPEECH_URL=http://speech.example.com:8001/v1
SPEECH_MODEL=<the model name vLLM serves>
```

Readiness is a `GET {SPEECH_URL}/models` when the Overview or the message box asks:
reachable and accepting the key means ready. Neither example was run against a
real server here: the remote engine is unit-tested against a fake, so try it once
and watch the Overview before telling people.

**A remote engine can cost money per minute, and the app has no budget for it.**
The daily token budget counts tokens; speech is seconds. The only limits are
2 minutes per recording, 10 MB, and one recording at a time per person per worker. If that
matters, watch the provider's billing.

### Recordings and your reverse proxy

The app keeps a recording in memory for one request and never writes it to disk,
the database or a log (a log line carries the person, the engine, the seconds, the
language and the time taken, never the words). **A reverse proxy can write it to
disk though**: nginx buffers a request body bigger than `client_body_buffer_size`
into a temp file before passing it on. If that matters to you, turn request
buffering off for the speech routes. In the sample vhost
([`sample_configs/chat.example.com.sample`](sample_configs/chat.example.com.sample))
put a nested block inside the `location /v1/ { … }` block. It must repeat
`proxy_pass` (never inherited into a nested location) and the `proxy_set_header`
lines (a block that sets any header of its own drops the outer ones), while
`client_max_body_size` and `proxy_read_timeout` are inherited:

```nginx
    location /v1/ {
        # … the existing block, unchanged …

        location /v1/speech/ {
            proxy_pass http://127.0.0.1:8000/v1/speech/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_http_version 1.1;
            proxy_request_buffering off;
            client_max_body_size 11m;
            proxy_buffering off;
        }
    }
```

Or, to keep it simple, put `proxy_request_buffering off;` in the existing `/v1/`
block itself, and know that it then applies to PDF uploads too (they then stream to
the backend instead of being buffered first, which is fine for the app but means a
slow client holds a backend worker for the whole upload). The other way is to raise
`client_body_buffer_size` above 10 MB. Either way `client_max_body_size` must allow
a little over 10 MB for `/v1/speech/transcriptions` (the sample vhost's `100m` on
`/v1/` already does; a stricter limit would turn every recording into a `413`). The
example block above was run on nginx 1.28.3 over plain http (a recording passed
through it); it was not run behind https or a real certificate. Test it on yours
with `nginx -t` and one recording.

### https for the microphone

Browsers allow the microphone only on https, or on `localhost`. Over plain http the
button shows, disabled, with the reason "Voice input needs a secure (https)
connection". With the production topology above that is already true; it bites on a
LAN address over http.

### Upgrading

**Migration 024** runs at startup. It only widens a check on `chat_attachments` so
a message can carry a text file; it has a Down (in the file's header) for a
rollback. **Run `pip install -r requirements.txt` after pulling** on a bare-metal
install (the container image has it built in): voice input needs `faster-whisper`
and `av`. Without them the backend still starts, and the **Overview**'s **Voice
input** line says which package is missing. Voice stays off, and nothing downloads,
until someone turns it on. An
existing **Web search** setting is not touched: both options live in the same
`chat_options` row and each save leaves the other alone.

## The speech window

**Talk** is a spoken conversation built from separate parts: voice activity detection
(Silero) in the browser, Whisper on the server, the chat model, and Kokoro. It runs in
the chat itself, from a button beside **Send**: the typing box makes way for its
controls, and what is said goes in as ordinary chat messages. It uses the deployment's
speech model, and English is the only language it has been tuned on. People's guide:
[USER_GUIDE.md § Talking with the assistant](USER_GUIDE.md#talking-with-the-assistant).

**Who can use it.** Anyone with the `talk` capability, on top of `chat` and `reader`,
while voice input is on; an admin always may. Give it under **Admin → People** or as the
`talk` realm role in Keycloak. **A realm created before 2.6 doesn't have that role:
create it once by hand** (Keycloak admin console, **Realm roles → Create role**, name
`talk`), or the People tick refuses and says so. A role given in Keycloak applies at the
person's next login. The server refuses a spoken turn from anyone without it (403), so
the hidden button isn't the only lock. Details:
[IDENTITY_AND_ROLES.md § The `talk` capability](IDENTITY_AND_ROLES.md#the-talk-capability).

**What leaves the browser.** Listening happens on the person's device. The server gets
a clip only once speech has been detected, so nothing streams while the room is quiet.
It's a WAV sent to `/v1/speech/transcriptions`, one per turn. The browser fetches about
17 MB (about 6 MB if the server compresses it) from `/vad/` at the first **Start**, never
from a CDN, and nobody else fetches it. A proxy must serve `.wasm` as `application/wasm` and `.mjs` as
JavaScript; a Content-Security-Policy needs `'wasm-unsafe-eval'` in `script-src`.

### Fast on a CPU

| Change | Why |
|---|---|
| **Speech model `base.en`** (**Admin → Chat options → Speech model**) | 0.4 s for a short clip against 1.0 s for `small`, at about 9% word errors against 7%. `small.en` is the choice if accuracy matters more. It applies to the whole deployment, the microphone included, so don't use an English-only model if people dictate in other languages |
| **A cloud chat model** | A local model on the same CPU added about 1.4 s, because it competes with Kokoro for cores |
| `TTS_CPU_THREADS` (default 6) | Kokoro was slower on all 24 cores than on 6, and about twice as slow again with 32 threads |
| `KOKORO_MODEL_PATH` | `kokoro-v1.0.fp16.onnx` is half the size and about 15% faster, but the audio isn't identical (it took 8.90 s where the stock file took 8.70 s), so listen first. Never `int8` on a CPU: smaller, and 4 times slower |

First sound after the person stops talking, median of 12 turns with
`gemma4:31b-cloud` through Ollama, on a 32-thread CPU with no GPU, in Chromium on the
same machine:

| Setup | Whisper | Model | Kokoro | **First sound** | Worst |
|---|---|---|---|---|---|
| As first built, `small` | 0.97 s | 0.63 s | 1.22 s | 3.91 s | 5.07 s |
| Tuned, `small.en` | 0.95 s | 0.62 s | 0.51 s | 2.80 s | 4.01 s |
| **Tuned, `base.en`** | 0.37 s | 0.49 s | 0.47 s | **2.11 s** | 3.12 s |
| Tuned, `base.en`, local `gemma4:e4b` | 0.43 s | 1.55 s | 1.07 s | 3.55 s | 4.51 s |

Each row includes the 0.7 s of silence that ends a turn. Two changes in "tuned" need no
setting. A long opening is cut at its first comma past 30 characters, because Kokoro's
time grows with the text ("Sure." 0.27 s, a long sentence 0.84 s). And a spoken turn
asks the model to keep its first sentence to a few words. Thinking is always off, and a
spoken turn is never offered the web search card, because nobody could press it.

### Telling speech from noise

Neither check works alone:

| Passes (real browser path) | Spoken words | Hard noises |
|---|---|---|
| VAD: 250 ms of speech, then Whisper's scores | 93 of 100 | 2 of 28, with `base.en` |

- **Length alone can't do it.** A 500 ms minimum (LiveKit's default) keeps almost no
  real "stop": they last about 320 ms.
- **Whisper's text can't either.** It wrote words for most noises ("Thank you.", "Woof,
  woof!"). Its `no_speech` score can, which is why the response carries it. 0.25 suits
  `base.en`, `small.en` and `small`; 0.4, the first guess, let 9 of 16 noises through on
  `base.en`.
- **Silero's memory dulls it to short words.** It runs on from whatever it last heard,
  so in a continuous stream a real "stop" fired 6 times in 25. The window clears it
  after the silence that ends a turn, which brought that to 24 in 25. A test that
  starts each clip with a fresh model can't see this: run a stream.

The cost of a noise that gets past the VAD is a pause of about a second. Playback waits
and Whisper decides, while the model and Kokoro keep working behind the pause, so
nothing is lost. The 28 noises are the coughs, sneezes, barks and laughs of the 160
tested that were likeliest to pass. The recordings are clean: **a real room and real
speakers are untested**.

### Talk in a busy room

Silero tells speech from noise, not near from far. A clean voice turned down by 35 dB,
to 5 dB above the room's hiss, still scores 0.97, and 95% of its frames stay above the
0.35 that keeps a turn going. So no threshold separates the person at the microphone
from a colleague two desks away. The colleague's voice holds every turn open and pauses replies.

So Talk can ignore voices much quieter than the person's (`src/lib/speech/gate.js`).
It is **off by default**: it compares voices by loudness, and a person who sits back
from the microphone gets quieter too. Each person switches it on for their own device
under **Settings → Talk → Ignore quieter voices**; `SPEECH_WINDOW_GATE=on` makes on the
default for everyone.

**How it decides.** A frame more than the margin below the person's level counts as
silence. The margin is `SPEECH_WINDOW_GATE_DB` (9 dB), which a person changes as **How
much quieter**, and it decides both starting a turn and staying in one. Which level:

- **In a turn, that turn's own**, learned as it goes from the frames that already count
  as the person's. Someone who sat back is judged by how loud they are now.
- **Between turns, the loudest of the person's last three turns that Whisper accepted
  as speech.** A cough alone teaches nothing, one loud turn sets the bar for the next
  three, and a person who moved back is followed after three. Nothing heard between
  turns teaches it.

The gate decides only when a turn starts and ends: the clip sent to Whisper is
untouched.

**Two simpler rules failed.**

- **One running level, raised fast and lowered slowly, with a 4 dB rule to start**, the
  first version. In its first real room (2026-11-04), a person who sat back from the
  microphone had their turns cut in half: the log showed sentences arriving as two clips
  (4.7 s then 3.1 s, 8.4 s then 2.1 s). A louder turn set a bar the quieter ones
  couldn't reach.
- **The last accepted turn alone.** Walked in Chromium: one quieter turn brought the
  colleague within the margin, the colleague's turns were accepted as speech (they are
  speech), the bar became the colleague's, and Talk answered the colleague's sentences. The loudest of three turns delays
  that; it doesn't prevent it (the walk below).

**Sitting back**, on 20 recordings of real people (five speakers, Common Voice among
them): each speaker's turns played again quieter, after one turn at their usual level.
The share of their speech that ended up inside a turn:

| Their voice vs. an earlier turn | No gate | First version | Now (9 dB) |
|---|---|---|---|
| 3 dB quieter | 66–100% | 33–100% | 62–100% |
| 6 dB quieter (about twice as far from the microphone) | 69–100% | 0–75% | 65–100% |
| 9 dB quieter | 69–100% | 0–61% | 40–95% |

Below 100% without the gate are pauses longer than the 0.7 s that ends a turn. One of
the 20 recordings is lost at any margin: it is more than 20 dB quieter than the one
before it.

**A colleague who never stops**, simulated through Silero v5 and the VAD library's own
start and stop rules: five English turns at −22 dBFS, steady, with office echo (RT60
0.5 s). Seconds from the person's last word to the end of their turn, and the turns the
other voice opened on its own, each of which would pause a playing reply:

| The other voice quieter by | No gate | Now, 9 dB | Now, 6 dB |
|---|---|---|---|
| 8 dB | no turn ends | 1.2–1.9 s; it opened 6 | 0.3–1.1 s, one never ends; 1 |
| 11 dB | 4 of 5 never end | 0.4–1.8 s; 3 | 0.2–1.2 s; 1 |
| 14 dB | 2 of 5 never end | 0.4–1.2 s; 1 | 0.2–0.6 s; 1 |
| 18 dB | turns end; it paused replies 6 times | 0.4–0.6 s; 1 | 0.2–0.6 s; 1 |

The single turn it opens is the one before the person first speaks (the first turn,
below). Cutting in stays at 0.24–0.29 s at 9 dB (Talk's minimum is 0.25 s); at 6 dB one
cut-in in five took 0.96 s. The first version's 4 dB start kept a nearer colleague out
better (at 8 dB it opened 3 turns, at 11 dB 1), which is what 6 dB is for now.

**Walked in Chromium** (2026-11-04): the real VAD library through a simulated
microphone, Whisper `small`, a colleague reading a script that never stops, about 13 dB
below the person on average, with echo. The first question at the person's usual level,
three more from 6 dB further back:

| Gate | Turns ended after the last word | Clips sent that were the colleague's alone |
|---|---|---|
| Off | never | (one turn that never ends) |
| First version (9, start 4) | 0.6–3.7 s | 19 of 22 |
| Now, 9 dB | 1.6–3.7 s | 41 of 43 |
| Now, 6 dB | 0.6–2.1 s | 52 of 56 |

Every rule ends the person's turns, which no setting did without the gate. **None keeps
a colleague talking nonstop at that level out**: once one of their sentences opens a turn,
Whisper accepts it, because it is speech, and loudness can't say whose it is. The walks
ran for different lengths, so compare the shares, not the counts. An earlier walk of the
first version sent only the questions; it differed in two ways, Whisper `base.en` and
every question at the usual level. Which one kept the colleague out isn't known. It
wasn't Whisper rejecting the colleague's voice: on 4 s pieces of the same recording,
`base.en` calls 5 of 6 speech, `small` 4 and `base` 3, while all seven of the person's
questions are speech on all three (`no_speech` at most 0.03 against a limit of 0.25).

**Setting it.** 9 dB suits a voice well below the person's: the simulation keeps a
colleague 14 dB or more below out of every turn but the first. If it cuts the person
off, raise it, or they sit closer; lower keeps a nearer voice out of more starts, and
cuts more of theirs. With the gate on, switch **Automatic gain** off too: it turns a far voice up the
moment the person stops. It stays on by default because it also evens out a person who
moves. Against a colleague who talks nonstop nearby, the gate isn't enough: use a
headset microphone, or tap the circle to send.

**Known limits.**

- **The first turn.** Until Talk has heard the person, every voice counts. If someone
  else is talking when **Talk** starts, their words open the first turn, and they are
  sent.
- **Whose voice it is.** Loudness can't tell; once a colleague's sentence opens a turn,
  it is sent. When the person's voice comes near the colleague's (the person sat far back,
  or the colleague is near, or talks nonstop), the colleague's turns get through. Sit closer, or use a
  headset microphone: at 3 cm from the mouth, against a colleague at 4 m, it gives
  roughly 30–40 dB of separation in open air (an estimate, not measured). The real fix
  is to recognise the person's voice (speaker verification), which Talk doesn't do yet.
- **Some phones keep their own gain** whatever the app asks. **What it heard** says so
  when the person asked for it off, and **Copy numbers** includes what the browser
  applied and the settings Talk listened with.
- The clips are synthetic voices with simulated echo, plus the 20 recordings above: **a
  real office is untested** beyond one real room's try of the first version.

When a room still holds a turn open, the person taps the circle to send.

### Settings

Each is an environment variable, read per request. Change one at a time: they're
measured together.

Five of them are also a person's own, under **Settings → Talk**, on each device: the gate
and its margin, the pause that ends a turn, the speech that counts as a turn, and how
sure the VAD must be (with the three microphone switches, which are the browser's own and
default to on). The variable sets everyone's default, each control shows it beside the
person's value, and **Reset to defaults** goes back to it. A person who moves a control
back to the default follows the variable again when an admin changes it.

| Variable | Default | Meaning |
|---|---|---|
| `SPEECH_WINDOW_VAD_START` | `0.5` | Silero score, 0 to 1, that starts speech |
| `SPEECH_WINDOW_VAD_STAY` | `0.35` | Score that keeps it going. Never above the start score |
| `SPEECH_WINDOW_MIN_SPEECH_MS` | `250` | Milliseconds of speech before a reply pauses |
| `SPEECH_WINDOW_PRE_ROLL_MS` | `300` | Milliseconds kept from before the start, so the first word isn't clipped |
| `SPEECH_WINDOW_SILENCE_MS` | `700` | Milliseconds of silence that end a turn, and clear the VAD's memory. Lower is faster, and cuts in on slow talkers |
| `SPEECH_WINDOW_FIRST_CLAUSE_CHARS` | `30` | Cut a reply's opening at a comma past this many characters. `0`: never |
| `SPEECH_WINDOW_MAX_NO_SPEECH` | `0.25` | Whisper's `no_speech` at or above which a clip is noise |
| `SPEECH_WINDOW_MIN_LOGPROB` | `-1.0` | Whisper's `avg_logprob` below which a clip is noise |
| `SPEECH_WINDOW_LANGUAGE` | `en` | Sent with every clip. Empty: Whisper detects it |
| `SPEECH_WINDOW_GATE` | `off` | Ignore voices much quieter than the person's ([Talk in a busy room](#talk-in-a-busy-room)). `on` or `off`. Each person can switch it for their own device |
| `SPEECH_WINDOW_GATE_DB` | `9` | With the gate on: decibels below the person's voice at which another stops counting as theirs, to start a turn and to stay in it. 3 to 30. Lower keeps a nearer voice out; higher lets the person's softer words through |

**Limits.** Whisper and Kokoro each run one job at a time per worker, so two people
talking at once queue, and read-aloud shares Kokoro with them. A remote speech engine
gives no scores, so any text counts as speech. On speakers the microphone may hear the
reply: a clip that began during a reply is dropped when 60% of its words are that
reply's, and headphones avoid the question. Whether Chrome's echo cancelling covers the
app's own playback is unknown. On a phone the page asks to keep the screen on while
Talk runs (the Screen Wake Lock API); a browser that refuses, on low battery say, may
let the screen sleep, and with it the microphone.

## Google Cloud speech

**Optional, and off until you configure it.** Google Cloud can hear speech in place of
Whisper (the microphone and Talk), and speak the chat's replies and Talk in place of
Kokoro, through Gemini-TTS. It is for languages the local models handle badly, and for
comparing. Gemini-TTS lists Tamil as generally available and Sinhala in Preview. Documents,
audiobooks, a message's **Audio** download and the browser extension keep Kokoro whatever
you choose.

**What changes when it's on.** Recordings go to Google instead of staying on the server,
and so does every sentence of a reply that is read aloud. Google bills per minute heard
and per character spoken; the app has no spending cap, so set a budget and alerts in
Google's console. The Talk panel says "Heard by Google Cloud", and **Settings → Chat**
tells each person when Google reads the replies.

### In Google Cloud

1. **APIs** (APIs & Services → Library): Cloud Speech-to-Text, Cloud Text-to-Speech and
   Vertex AI. Gemini-TTS checks a Vertex AI permission.
2. **A recognizer** (Speech-to-Text → Recognizers → Create), for hearing. It lives in a
   project with Cloud Speech-to-Text on, and its full name names *that* project: copy the
   name from the recognizer's page, not from another credential. (A name with the wrong
   project gets "the Cloud Speech-to-Text API is off in project …" on the Overview.) Two
   that work, both checked live on 2026-11-03 with English, Sinhala and Tamil:
   - **`long` in `global`, with `en-GB`, `si-LK` and `ta-IN`.** Quick (about 1.2 s for a
     short question) and right in all three when the person names their language. It
     can't tell them apart, though: asked for no language, it hears only the first in its
     list, so English and Tamil come out as Sinhala. The app says so in **Settings → Chat**
     ("Auto hears only Sinhala") and on the Overview, and offers only the recognizer's
     languages. Set **Admin → Defaults → Speech language** to the one most people speak.
   - **`chirp_3` with the language `auto`** (tested in `us`). It tells English, Sinhala
     and Tamil apart on its own and adds punctuation, about 1 s slower than `long`.

   [Google's table](https://docs.cloud.google.com/speech-to-text/docs/languages) lists
   what each model and location supports. Leave the recognizer's features as you want
   them: the app never changes them, and only sends the language when the person picked
   one.

   **Several recognizers, one per language.** No one Google model suits Sinhala, Tamil
   and English together: Google lists Sinhala (`si-LK`) for `chirp` and `chirp_2` only,
   never `chirp_3`. So make a recognizer per language (or pair), and tick them in the
   console (below). Checked live on 2026-11-04 with real recordings, each through
   Google's default recognizer with the model named:

   | Model | Location | Languages | Result |
   |---|---|---|---|
   | `chirp_3` | `us` | `ta-IN`, `en-GB` | Right in both, and it picks between the two by itself when the person is on Auto |
   | `chirp_2` | `asia-southeast1` | `si-LK` | Right. Google's console lets a `chirp_2` recognizer hold one language |
   | `chirp_2` | `us-central1` | `si-LK` | Right |
   | `chirp_2` | `asia-southeast1` | `ta-IN` | Right on three real recordings, through the app (1.6–2.2 s); no punctuation |
   | `chirp_2` | `asia-southeast1` | `si-LK`, sent an English clip named English | Right: the app sends the person's language with the clip (`en-US` when no recognizer lists English), lowercase and without punctuation |
   | `chirp_3` | `asia-southeast1` | `en-GB`, `ta-IN` | **Refused on every clip**: "no longer generally available in region asia-southeast1" |

   **The last row is a trap:** Google's console still creates a `chirp_3` recognizer in
   `asia-southeast1`, and reading it works, so the console and the Overview say it's
   ready while every clip fails. People see "Voice input isn't working right now"; the
   backend log has Google's own words (`Google recognize answered 403 for <id>: …`).
   Google's Chirp 3 page lists `us` and `eu` as its regions. A language no ticked
   recognizer lists isn't on the people's **Speaking** chip, so for English on the chip,
   tick a recognizer that lists it. Check a recognizer's
   language codes in the console's list too: `sl-SI` reads "Slovenian" there, and is easy
   to pick for Sinhala.
3. **A service account** (IAM & Admin → Service accounts) with **Cloud Speech Client**
   (`roles/speech.client`) on the recognizer's project, and **Vertex AI User**
   (`roles/aiplatform.user`) for the voice. If Google says the caller lacks
   `serviceusage.services.use`, add **Service Usage Consumer**.
4. **Its key:** the account → Keys → Add key → **JSON**. The file has
   `"type": "service_account"` in it. An OAuth client ID file (from APIs & Services →
   Credentials) is a different thing and won't work: the Overview says so. If key
   creation is refused, the organisation policy `iam.disableServiceAccountKeyCreation`
   is on for the project.

### In the app

| Variable | Default | Meaning |
|---|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | (unset) | The key file. With `startup.sh` it's a path on the host, and is mounted read-only into the container (`compose.google.yml`); the file must be readable by the backend's user (`NR_APP_UID`, 1000) |
| `GOOGLE_SPEECH_RECOGNIZER` | (unset) | One recognizer's full name, `projects/<project>/locations/<location>/recognizers/<id>`, used while none is ticked in the console. Set (or a recognizer ticked): Google can be chosen to hear speech |
| `GOOGLE_TTS_MODEL` | (unset) | A Gemini-TTS model, such as `gemini-2.5-flash-tts`. Set: Google can be chosen for the chat's voice |
| `GOOGLE_TTS_VOICE` | `Kore` | One of Gemini-TTS's 30 voices, until an admin picks one |
| `GOOGLE_TTS_LANGUAGE` | `en-US` | The language of a sentence that isn't mostly Sinhala or Tamil letters |
| `SPEECH_ENGINE` | `local` | `local`, `openai` or `google`: who hears speech until an admin chooses |
| `CHAT_VOICE` | `kokoro` | `kokoro` or `google`: who reads the chat until an admin chooses |
| `GOOGLE_TIMEOUT_S` | `30` | Seconds before "Google didn't answer" |
| `GOOGLE_CLOUD_PROJECT` | (unset) | The project billed for the voice when the credentials don't name one (gcloud's own login, say). A service-account key names its own |

Then **Admin → Chat options**: **Speech recognition** offers every engine, and one that
isn't configured says what it needs; **Google recognizers**, shown once
`GOOGLE_APPLICATION_CREDENTIALS` or `GOOGLE_SPEECH_RECOGNIZER` is set, lists every
recognizer in the key's project, read from Google (each region is its own address, so
it takes about 3 s), with its model, languages and location. Tick the ones to use: a
clip goes to the first ticked recognizer that lists its language, and Auto (with any
language none lists) to the first, so order them with **Move up**. Only a recognizer
Google listed can be saved, because its location is the host the server sends its token
to. Listing needs the key to be allowed `speech.recognizers.list`; when Google refuses,
the list says so in Google's words. Google names a recognizer's project by its number
(`projects/123456789012/…`), not its id: the same recognizer either way; **Voice for the chat and Talk** offers
Kokoro or Google, and with Google one voice for everyone. Both apply from the next
recording or sentence, with no restart. **Admin → Overview** shows the Voice input and
Chat voice lines, with the reason when Google refuses: a key it won't take, a missing
role, or an API that's off, named with its project.

### How it behaves

- **Hearing:** each recording is decoded on the server and sent as 16 kHz WAV, one call
  per recording. Google's synchronous call takes a minute at most, so with Google the
  microphone stops at **55 s** instead of 2 minutes.
- **Talk:** Google gives none of Whisper's scores, so a clip counts as speech when Google
  returns words, and as noise when it returns nothing. In the live check, hiss and coughs
  never got that far: the VAD dropped them, and nothing was sent.
- **Speed.** Talk's time from the end of a question to the first sound of the answer,
  measured on 2026-11-03 from one home connection with a cloud LLM (median of five
  English questions; yours will differ with your distance to Google):

  | Hears | Speaks | First sound |
  |---|---|---|
  | Whisper `base.en` here | Kokoro | 2.1 s |
  | Google (`long`) | Kokoro | 3.0 s |
  | Google (`long`) | Gemini-TTS | 7.1 s (Sinhala 6.3 s, Tamil 5.4 s) |

  Gemini-TTS takes 2.3 s or more for any sentence, however short, so for English the
  quicker mix is Google hearing with Kokoro speaking. For Sinhala and Tamil, Gemini-TTS
  is the only voice the app has. `gemini-2.5-flash-lite-preview-tts` is about 0.4 s
  quicker per sentence, and is a Preview model.
- **Talk's language:** the person's own **Speech language** when they chose one, else
  `SPEECH_WINDOW_LANGUAGE` (`en`). **So on Auto every Talk clip is sent as English**, and
  Google hears Sinhala as if it were English. The **Speaking** chip in Talk says so
  (*Auto (English)*) and switches the language in one tap; the message box's microphone
  sends nothing on Auto, so there Google hears the recognizer's first language.
- **Speaking:** one call per sentence, at most 4,000 bytes. Each sentence's language comes
  from its letters: mostly Sinhala is `si-LK`, mostly Tamil `ta-IN`, anything else
  `GOOGLE_TTS_LANGUAGE`. If Google fails, the sentence is skipped and the person sees
  Google's reason; there is no silent fallback to Kokoro.
- **Each person's voice:** the admin's voice is the default, and each person can pick
  another of Google's under **Settings → Chat → Reply voice**, after **Hear it**: a short
  sample in English, Sinhala and Tamil, fetched from Google once per voice per session
  (a few hundred characters of quota). Turning Google on or off stays the admin's.

## Notifications

The header's bell (v2.12) needs nothing set up: migration 026 runs at start, and the
notices are in the app only (no email, no outside service). What an operator can change
is how long they're kept:

| Variable | Default | Meaning |
| --- | --- | --- |
| `NOTIFICATIONS_READ_DAYS` | 30 | days a read notice is kept after it was read; `0` keeps it forever |
| `NOTIFICATIONS_UNREAD_DAYS` | 90 | days an unread notice is kept after it arrived; `0` keeps it forever |

The cleanup runs at startup and then every 24 hours in each worker; several workers
deleting the same rows is harmless. A value that isn't a whole number of days, or is
negative, logs one warning and keeps the default. Every active admin gets the admin
notices (re-embed paused or done, rebuilds failing 3 times in a row, passages shortened);
an admin added later doesn't get older ones.

## Bring-up checklist

1. **DNS:** `chat.example.com` and `auth.example.com` both resolve to the host.
   (A missing `auth` record shows up as the backend failing token exchange with
   `httpx.ConnectError: Name or service not known`.)
2. **TLS certs** for both names (e.g. `certbot certonly` per host).
3. **nginx:** install both `.sample` files, adjust `server_name`/cert paths/`root`,
   `nginx -t && systemctl reload nginx`.
4. **Build the SPA:** `npm run build`; point the chat vhost's `root` at `dist/`.
5. **`.env`:** the six production values from the table above — the five
   scheme+host ones **and** `KC_ADMIN_CLIENT_ID`/`KC_ADMIN_CLIENT_SECRET` (start
   from [`../.env.example`](../.env.example)). **Before the first start**, set
   your own `OIDC_CLIENT_SECRET`, `KC_ADMIN_CLIENT_SECRET`,
   `KC_BOOTSTRAP_ADMIN_PASSWORD` and `KC_SEED_USER_PASSWORD` (`openssl rand -hex 32`
   each): Keycloak reads them once, when it creates the realm.
6. **Bring up services:** `./startup.sh start` (Postgres, Keycloak, SearXNG and
   the backend container, which stays bound to the loopback nginx proxies to),
   then `./startup.sh enable-autostart` so it survives reboots.
7. **Fix the live realm** (Trap 2) — add the https redirect URI + web origin.
8. **Grant the founder their realm roles** (Trap 4) — either re-import the
   corrected export into a fresh DB, or assign `admin`/`reader`/`chat` to your
   user in the admin console and re-login. Skip and you log in to "no access yet".
9. **Verify the issuer** is `https://…` (Trap 1 curl), then log in.

## See also

- [`../deploy/README.md`](../deploy/README.md) — local-dev OIDC rig, two-user walkthrough, offboarding.
- [IDENTITY_AND_ROLES.md](IDENTITY_AND_ROLES.md) — identity model, JIT provisioning, admin/capability roles.
- [ARCHITECTURE.md](ARCHITECTURE.md) — where auth sits in the request path.
