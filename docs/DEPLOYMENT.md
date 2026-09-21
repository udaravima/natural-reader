# Production deployment (behind nginx + TLS)

How to serve Natural Reader on real hostnames with HTTPS, fronted by nginx, with
Keycloak as the OIDC identity provider. This is the **production** counterpart to
[`../deploy/README.md`](../deploy/README.md), which covers the local-dev OIDC rig
(all `localhost`, no TLS). Read that one first if you just want login working on
your laptop.

Worked example uses two hostnames:

| Hostname | Serves | nginx sample |
| --- | --- | --- |
| `chat.oraian.net` | the built SPA **and** the `/v1/*` backend (one origin) | [`chat.oraian.net.sample`](chat.oraian.net.sample) |
| `auth.oraian.net` | Keycloak (OIDC provider) | [`auth.oraian.net.sample`](auth.oraian.net.sample) |

## The one rule that dictates the topology: same origin

The app's login session is an **`HttpOnly` cookie**. Browsers only send that
cookie back if the SPA and the backend it calls are the **same origin** — same
scheme, host, and port. So the SPA and `/v1/*` **must** live on one hostname
(`chat.oraian.net`), which is why a single nginx server block both serves
`dist/` and proxies `/v1/` to the backend.

Keycloak is deliberately a **different** origin (`auth.oraian.net`). That's fine —
OIDC is a browser *redirect* flow, not a cookie-sharing one, so the IdP never
needs to be same-origin with the app. Putting it on its own hostname is the
normal, correct shape.

## What changes going from local dev to production

Nothing in the application code changes. Deployment is entirely configuration,
in five places that all have to **agree on scheme + host** or login fails in a
different way at each mismatch:

| # | File / place | Local dev | Production |
| --- | --- | --- | --- |
| 1 | `.env` → `OIDC_ISSUER` | `http://localhost:18080/realms/natural-reader` | `https://auth.oraian.net/realms/natural-reader` |
| 2 | `.env` → `OIDC_REDIRECT_URL` | `http://localhost:5173/v1/auth/callback` | `https://chat.oraian.net/v1/auth/callback` |
| 3 | `.env` → `COOKIE_SECURE` | `false` (plain http) | `true` (TLS) |
| 4 | `.env` → `KC_PROXY_HEADERS` / `KC_HOSTNAME` | unset | `xforwarded` / `https://auth.oraian.net` |
| 5 | the **live** Keycloak realm's client | `localhost` redirect URIs | add the `https://chat.oraian.net` callback + web origin |

Plus two nginx vhosts (the two `.sample` files) and the DNS + TLS certs for both
hostnames.

The traps below are ordered by *when they bite you* as you walk the login flow.

### Trap 1 — Keycloak emits `http://` behind the proxy (KC_PROXY_HEADERS / KC_HOSTNAME)

**Symptom:** the Keycloak admin console (or login page) loads over HTTPS but the
browser console fills with *"Mixed Content … requested an insecure resource
`http://auth.oraian.net/resources/...`"* and *"Framing `http://auth.oraian.net/`
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
KC_HOSTNAME=https://auth.oraian.net  # pin the canonical public URL
```

`KC_PROXY_HEADERS` alone makes Keycloak believe the `X-Forwarded-Proto: https`
nginx already sends; `KC_HOSTNAME` pins the scheme+host so even a direct
`localhost:18080` hit reports `auth.oraian.net`. Verify:

```bash
curl -s https://auth.oraian.net/realms/natural-reader/.well-known/openid-configuration \
  | grep -o '"issuer":"[^"]*"'
# → "issuer":"https://auth.oraian.net/realms/natural-reader"   (https, not http)
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
not on it. In production the backend sends `https://chat.oraian.net/v1/auth/callback`,
but the client only lists the `localhost` dev URIs.

**The trap within the trap:** editing
[`../deploy/keycloak/realm-export.json`](../deploy/keycloak/realm-export.json)
does **not** fix this. `--import-realm` only seeds a realm that **doesn't exist
yet**, and once the realm is in Postgres the import is skipped on every boot. The
export and the running realm drift apart silently. The export edit only matters
for a *fresh* database.

**Fix the live realm** one of two ways:

- **Admin console:** `https://auth.oraian.net/admin` → realm `natural-reader` →
  Clients → `natural-reader` → Settings → **Valid redirect URIs**: add
  `https://chat.oraian.net/v1/auth/callback`; **Web origins**: add
  `https://chat.oraian.net` → Save.
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
    -s 'redirectUris=["https://chat.oraian.net/v1/auth/callback","http://localhost:8080/v1/auth/callback"]' \
    -s 'webOrigins=["https://chat.oraian.net","http://localhost:8080"]'
  ```

Keep the export in sync anyway (done already) so a rebuilt database starts
correct — it's documentation, not the live config.

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
  is why the single `chat.oraian.net` vhost serves *both* `dist/` and `/v1/`.
- Leave `COOKIE_DOMAIN` **unset** unless you know you need it — a value that
  doesn't match the origin drops the cookie the same silent way.

### Trap 4 — PDF uploads fail at ~1 MB (client_max_body_size)

**Symptom:** small docs index fine; larger PDFs fail with `413 Request Entity
Too Large`.

**Cause:** nginx's default `client_max_body_size` is **1 MB**. Document uploads
go through `POST /v1/docs/{id}/pdf` and are often much larger.

**Fix:** the `chat.oraian.net.sample` sets `client_max_body_size 100m;` inside
the `/v1/` block. Raise it if you expect bigger files.

### Trap 5 (minor) — stale `ERR_QUIC_PROTOCOL_ERROR` in Chrome

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

## Bring-up checklist

1. **DNS:** `chat.oraian.net` and `auth.oraian.net` both resolve to the host.
   (A missing `auth` record shows up as the backend failing token exchange with
   `httpx.ConnectError: Name or service not known`.)
2. **TLS certs** for both names (e.g. `certbot certonly` per host).
3. **nginx:** install both `.sample` files, adjust `server_name`/cert paths/`root`,
   `nginx -t && systemctl reload nginx`.
4. **Build the SPA:** `npm run build`; point the chat vhost's `root` at `dist/`.
5. **`.env`:** the five production values from the table above (start from
   [`../.env.example`](../.env.example)).
6. **Bring up services:** `env -u XDG_DATA_HOME .venv/bin/podman-compose up -d
   postgres keycloak searxng`, then run the backend (`HOST=0.0.0.0` or bound to
   the loopback nginx proxies to).
7. **Fix the live realm** (Trap 2) — add the https redirect URI + web origin.
8. **Verify the issuer** is `https://…` (Trap 1 curl), then log in.

## See also

- [`../deploy/README.md`](../deploy/README.md) — local-dev OIDC rig, two-user walkthrough, offboarding.
- [IDENTITY_AND_ROLES.md](IDENTITY_AND_ROLES.md) — identity model, JIT provisioning, admin/capability roles.
- [ARCHITECTURE.md](ARCHITECTURE.md) — where auth sits in the request path.
