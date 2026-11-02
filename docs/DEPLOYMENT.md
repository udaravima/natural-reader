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
  password — see the admin-password note below; the `admin/admin` in compose is
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

> **Master admin password:** `KC_BOOTSTRAP_ADMIN_USERNAME/PASSWORD` in compose
> create the master admin **only on the first boot against an empty database**.
> Because the realm persists in Postgres, later container recreations ignore
> those vars — the real password is whatever it was first set to. If it's lost,
> reset it with `bin/kc.sh bootstrap-admin` (Keycloak 26) or by seeding a fresh
> admin against the DB; the compose values are not authoritative on a persisted
> volume.

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

- **First-user-admin (branch 3)** force-grants `{admin,reader,chat}` to whoever
  first claims the seed row — **but only via the race path.** Setting
  `BOOTSTRAP_ADMIN_EMAIL` to the founder's email routes them through the
  *email-claim* path (branch 2) instead, which has **no capability floor**, so the
  empty token wins. Setting `BOOTSTRAP_ADMIN_EMAIL` (otherwise recommended) is
  exactly what removes this net.
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
  KC_ADMIN_CLIENT_SECRET=<the natural-reader-admin client secret>   # dev: natural-reader-admin-dev-secret
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

- **Indexing still needs Ollama.** Embeddings go to `OLLAMA_URL` whatever
  `INFERENCE_PROVIDERS` says, so a vLLM-only or Claude-only deployment can chat
  but can't index documents.
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
even after you set `INFERENCE_PROVIDERS` — it doesn't stop meaning
"the Ollama chat provider" and start meaning nothing. Remove `OLLAMA_URL`
after adding providers and indexing breaks, calling `localhost:11434` with
nothing listening there. There is no admin screen for any of this yet:
adding, removing, or renaming a provider is an operator `.env` change and a
restart, same as any other config in this file.

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
| `SPEECH_ENGINE` (**Voice input engine**) | `local` | `local` runs [faster-whisper](https://github.com/SYSTRAN/faster-whisper) inside the backend, on the CPU: nothing leaves the server. `openai` calls a remote server that speaks `POST {SPEECH_URL}/audio/transcriptions`. Anything else logs a warning and means `local`. Read once at startup: changing it takes a restart. |
| `SPEECH_MODELS_FILE` (none) | none | `local` only: a YAML file on the host, of extra speech models for **Chat options → Speech model**. See [Choosing a model](#choosing-a-model). |
| `SPEECH_MODEL` (**Voice input model (server setting)**) | `small` for `local`; none for `openai` | The starting model: **Chat options → Speech model** wins over it for `local`. `local`: an id from that list, a size (`tiny`, `base`, `small`, `medium`, `large-v3`, `large-v3-turbo`), a Hugging Face repo id of a CTranslate2 model, or a directory on the server (see [Choosing a model](#choosing-a-model)). `openai`: the server's model name (`whisper-1` for OpenAI itself); required, and the Overview says "SPEECH_MODEL is not set" without it. |
| `SPEECH_URL` (**Voice input server**) | none | `openai` only: the base **including `/v1`**, like `INFERENCE_<NAME>_URL`. Without it the Overview says "SPEECH_URL is not set". |
| `SPEECH_API_KEY` (**Voice input key**) | none | `openai` only: sent as the bearer key. It stays on the server and is never logged or shown. |
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
itself takes a few seconds.

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

**Languages.** One model at a time means a server can specialise in one language. A
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
(Silero) in the browser, Whisper on the server, the chat model, and Kokoro. It appears
for anyone with `chat` and `reader` when voice input is on, and it has no switch of its
own. It uses the deployment's speech model, and English is the only language it has
been tuned on. People's guide: [USER_GUIDE.md § Talking with the assistant](USER_GUIDE.md#talking-with-the-assistant).

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

### Settings

Each is an environment variable, read per request. Change one at a time: they're
measured together.

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

**Limits.** Whisper and Kokoro each run one job at a time per worker, so two people
talking at once queue, and read-aloud shares Kokoro with them. A remote speech engine
gives no scores, so any text counts as speech. On speakers the microphone may hear the
reply: a clip that began during a reply is dropped when 60% of its words are that
reply's, and headphones avoid the question. Whether Chrome's echo cancelling covers the
app's own playback is unknown.

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
   from [`../.env.example`](../.env.example)).
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
