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

`DOC_STORAGE_DIR` (or the legacy `PDF_STORAGE_DIR`), `LOG_DIR` and
`CHAT_ASSISTANT_PROFILE_FILE` in `.env` name locations **on the host**.
`startup.sh` mounts them into the container at fixed paths, and files written
there belong to the user who ran it. Running compose by hand? Set `NR_DATA_DIR`,
`NR_LOG_DIR` and (optionally) `NR_PROFILE_FILE` to those host paths (absolute)
and add `-f compose.profile.yml` for the profile; otherwise the defaults
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
go through `POST /v1/docs/{id}/pdf` and are often much larger.

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
shown in the admin console's Deployment config panel.

**What has been run against real providers:** OpenRouter, through the
`openai` adapter, with paid `google/gemma-4-26b-a4b-it` and
`mistralai/mistral-small-3.2-24b-instruct` (2026-09-29). That covered
multi-turn chat, pins, a native `web_search` tool round, an image with a
follow-up question, provider-reported token usage, a rate-limited free
model and a rejected key, with no key in the logs. Before that (v2.1.0),
the same adapter was run against Ollama's own `/v1`. vLLM and LiteLLM speak
the same protocol but haven't been run.

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
(any `kind=ollama` provider that is not marked Cloud, whatever its host, except
`-cloud` / `:cloud` models and the ollama.com host). It is the first of these
that is set, clamped to `INFERENCE_NUM_CTX_MAX` (32768 tokens unless set):

1. the admin's size for that model (**Admin console → Models → Per-model sizes**);
2. the deployment's size: **Models → Context size for local Ollama models**, else
   `INFERENCE_LOCAL_NUM_CTX` (2048-2097152 tokens);
3. the person's own **Settings → Chat → Context window**;
4. 16384 tokens.

Chat history is trimmed to that same number, and the reader uses it too (next
section). A larger size reserves more memory when the model loads, and a model
already loaded at another size reloads once on the next message, so size it down
on a machine short of RAM. Nothing is sent for Ollama cloud models and
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
the reader, which providers count as local (a cost flag; **Cloud** also stops an
Ollama provider's models from being sized), and which chat models are offered
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
| `READER_MODEL` | none: falls back to `SUMMARIZE_MODEL`, then `WEB_SEARCH_SUMMARY_MODEL`, then the chat model of the turn | a model id | The model that reads. `chat` (any letter case) means the model of the turn itself, which is also what you get when none of the three is set; `SUMMARIZE_MODEL=chat` means the same for the reader. **Admin console → Models → Reader** wins over all three. |
| `READER_INPUT_CHARS` | `12000` characters (about 3,000 tokens) | 500-200000 | The most source text in one reader batch: a ceiling, never exceeded. A batch is cut shorter to fit the reader's context window when that is smaller (never below 500), and holds at most 16 passages. A document page longer than this is read in pieces that overlap by 200 characters; a web page is cut into passages of about 1,000 characters and the best-matching ones are kept up to this size. |
| `READER_OUTPUT_CHARS` | `1200` characters | 100-20000 | The most one batch may return. The reader is told to stay under it and its reply is capped at about that length; a reply cut off mid-way counts as a failed batch. |
| `READER_MAX_CONCURRENCY` | `2` batches | 1-32 | Batches running at once. |
| `READER_TIMEOUT_S` | `60` seconds | 1-3600 | Time one batch may take. `WEB_SEARCH_SUMMARY_TIMEOUT_S` is read when this is unset; with both unset, the reader uses 60 seconds. A batch that fails is listed under `failed` in the result (documents; `not_read` is only for pages left out by the page cap) or falls back to a short excerpt (web). |

A reader on a self-hosted Ollama model is loaded at the size described under
[Context sizes](#context-sizes-windows-and-the-models-section), the same for
every call of the turn: the chat's size (the person's own value when no admin
size is set) when the reader is the chat model, so
Ollama doesn't reload it between chat and reader calls, and the model's own size
(never the person's) when it is a different model. A reader on a model that isn't
sized (Ollama cloud, `kind=openai`) sends no size. Batches are cut to fit.

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
as for any reply. The usage figures don't say which model a batch ran on; the
server log's `reader usage` line does.

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
- `GET /v1/admin/chat-options` returns `{web_search, source, searxng}`
  (`source` is `admin`, `env` or `default`; `searxng` is false when
  `SEARXNG_URL` is empty). `PUT` takes `{"web_search": "ask" | "auto" | "off" | null}`;
  `null` removes the admin's choice. Both need the `admin` capability.
- `POST /v1/chat/sessions/{id}/turns` accepts `context.project_id` (a project
  id scopes the chat to it, `null` clears the scope, leaving it out keeps what
  the chat has) and `context.web` (`{query}`, up to 300 characters: the person's
  approval of one web search for this turn). A turn for a project the caller
  isn't a member of is refused with 404 and the message "This project doesn't
  exist or you don't have access.", and the chat's project is cleared.
- The chat session list and the session record carry `scopeProject`
  (`{id, name}` or `null`); `name` is `null` when the caller can no longer see
  the project.

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
