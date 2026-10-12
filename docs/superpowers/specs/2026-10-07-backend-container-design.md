# Backend container and one-command quickstart — design

**Status:** approved in conversation 2026-10-07 (sections 1–4), awaiting written-spec review.
**Branch:** `feat/backend-container` (from `development`).
**Sub-project:** D (containerize), backend only. Next in the queue after this: V1 document versions.

## 1. Why

An OS upgrade (Ubuntu 26.04, 2026-10-07) moved the system `python3` to 3.14, outside the backend's supported range (3.12–3.13; `onnxruntime-openvino` has no 3.14 wheels). The backend survived only because its venv is built on a uv-managed 3.13. The backend also runs as a bare process started from a terminal: on the machine that serves the live site, Ctrl-C on `startup.sh` (whose exit trap runs `compose down`) takes the whole stack down, and nothing restarts after a reboot.

**Goal:** the backend runs from a container image that carries its own Python and native libraries, for both the live site and development. A fresh clone gets to a running app with one command.

**Non-goals:** containerizing the frontend or `node_modules`; network isolation for the backend (§3); Windows container support (Windows stays on the bare-metal venv flow, by design); production `.env` generation.

**Success:**
1. On a fresh clone, `./startup.sh quickstart` ends with the app at `http://localhost:5173`. Sign-in, upload, read aloud, projects and logout all work, with no host Python installed.
2. `./startup.sh start` runs the live backend detached. It survives closing the terminal and, after `enable-autostart`, a reboot.
3. Host OS Python changes cannot affect the containerized backend.
4. Existing uploads, logs and `.env` keep working unchanged.

## 2. The image

One image, `natural-reader-backend`, built from `Containerfile` at the repo root.

- **Base:** an official Python 3.13 slim image, pinned to the 3.13 line. The interpreter version is the image's, never the host's.
- **Layers, in cache order:**
  1. system packages the wheels need;
  2. `pip install -r requirements.txt`;
  3. Docling, only when build argument `WITH_DOCLING=true` (default `false`);
  4. the Kokoro files `kokoro-v1.0.onnx` and `voices-v1.0.bin`, downloaded from the same release URL `startup.sh init` uses (`MODEL_BASE_URL`), into `/app`;
  5. the server code (`server/`, `run.py`) last, so a code-only rebuild reuses every layer above.
- **Docling split:** `requirements.txt` keeps everything the base image needs. Docling moves to `requirements-docling.txt`, installed in layer 3 and by `startup.sh init` (the venv path keeps Docling exactly as today).
- **Runtime:**
  - working directory `/app`, so `server/model.py`'s relative model paths resolve unchanged;
  - runs as a non-root user (uid 1000, `app`); entrypoint `python run.py`;
  - `HOST` defaults to `127.0.0.1`, so the backend's loopback-only dev-bypass guard behaves exactly as today.
- **`.dockerignore`** (read by both Docker and podman; Docker ignores `.containerignore`) is an allow-list: `*`, then re-include only `server/`, `run.py` and `requirements*.txt`, then re-exclude `server/tests/` and `__pycache__/`. It therefore excludes at least `.env`, `.env.*`, `data/`, `logs/`, `tmp/`, `.venv/`, `node_modules/`, `dist/`, `.git/`, `.local/`, `*.onnx`, `voices-*.bin`, `searxng/settings.yml`, `__pycache__/`. `tmp/` is excluded explicitly because it can hold credential files.

## 3. Networking: host network

The backend container uses **host networking** (`network_mode: host`).

**Why (verified 2026-10-07):** the backend is a *client* of four services it reaches as `127.0.0.1:<port>`: Postgres `:5433`, Keycloak `:18080`, SearXNG `:18043` and Ollama `:11434`. All four are bound to loopback on purpose.
- Inside a bridge-networked container, `127.0.0.1` is the container itself.
- Reaching the compose services by name would split the OIDC issuer: the browser sees `localhost:18080`, the backend sees `keycloak:8080`. Fixing that needs `KC_HOSTNAME`, `KC_HOSTNAME_BACKCHANNEL_DYNAMIC` and a new app discovery-URL setting.
- A bridge container on this machine could not reach host-loopback Ollama at all: `host.containers.internal` resolved to 169.254.1.2, and the connection was refused.

With host networking:
- every URL in `.env` keeps its meaning;
- nginx keeps proxying to `127.0.0.1:8000`, and Vite keeps proxying `/v1` to `127.0.0.1:8000`;
- no auth code or Keycloak setting changes.

Inbound exposure is unchanged: the backend binds `127.0.0.1:8000` only.

**Docker Desktop (macOS/Windows)** supports host networking only as an opt-in setting in recent versions. The docs say so. Windows users use the bare-metal flow (§7).

## 4. Data, settings and files

| Thing | Host side | Container side | Mechanism |
|---|---|---|---|
| Settings and secrets | `.env` | process environment | compose `env_file: .env`, read at container start; never copied into the image |
| Uploaded documents | `${DOC_STORAGE_DIR:-./data/pdfs}` | `/app/data/pdfs` | bind mount; compose also sets `DOC_STORAGE_DIR=/app/data/pdfs` inside |
| Logs | `${LOG_DIR:-./logs}` | `/app/logs` | bind mount; `LOG_DIR=/app/logs` inside |
| Assistant profile (optional) | `${CHAT_ASSISTANT_PROFILE_FILE}` | `/app/config/assistant-profile` (read-only) | mounted only when set (see below) |
| Docling / Hugging Face cache | named volume `natural-reader_cache` | `/app/.cache` (`HF_HOME`) | survives rebuilds; downloads once |
| Kokoro model files | — | `/app` | baked into the image (§2) |
| Source (dev mode only) | `./server`, `./run.py` | `/app/server`, `/app/run.py` | bind mount via `compose.dev.yml` |

**Rules:**
- A path setting in `.env` names the **host** path. `startup.sh` turns it into `NR_DATA_DIR` / `NR_LOG_DIR` / `NR_PROFILE_FILE`, which compose uses as the mount sources, and compose pins the in-container paths. Separate `NR_*` names are required (verified 2026-10-07): podman-compose 1.5 fills a volume's `${VAR}` from the service's own `environment:` block, so `${DOC_STORAGE_DIR}` would resolve to the in-container path, and it cannot nest defaults (`${A:-${B:-x}}`). Running compose directly without `startup.sh` uses the defaults unless `NR_*` are set. Without that, `DOC_STORAGE_DIR=/srv/docs` would point at an empty folder inside the container, and uploads would vanish on recreate.
- **`DOC_STORAGE_DIR` takes precedence over `PDF_STORAGE_DIR`,** as in the app.
- **Assistant profile:** when `CHAT_ASSISTANT_PROFILE_FILE` is set, `startup.sh` passes the extra mount and overrides the variable to the in-container path. When it's unset, nothing is mounted, and the app behaves as today (no file profile).
- **Ownership:**
  - under rootless podman, `userns_mode: keep-id` maps the container user to the invoking host user, so files written to `data/` and `logs/` stay owned by that user;
  - `startup.sh` builds with `APP_UID` = the invoking user's uid (1000 when run as root), so the container user matches the host user under Docker too.
- **`.env` changes apply on the next backend restart,** as today.

## 5. Two run modes

Both modes bind port 8000, so only one runs at a time.

| | **Live** — `startup.sh start` | **Dev** — `startup.sh up` / `up-with-dev-auth` |
|---|---|---|
| Code | baked into the image | working tree mounted in (`compose.dev.yml`); restart to apply edits, no rebuild |
| Process | detached; independent of any terminal | foreground; Ctrl-C tears the stack down (unchanged behaviour) |
| Restart | `restart: always`, plus `enable-autostart` for reboots | none |
| Auth | per `.env` | `up`: `AUTH_ENABLED=false` (loopback only); `up-with-dev-auth`: Keycloak rig |

**Commands:**
- **New:**
  - `start`: build if needed, then start Postgres, SearXNG, Keycloak (see the rule below) and the backend, detached;
  - `stop`: stop everything (today's `down`; `down` stays as an alias);
  - `rebuild`: rebuild the image and recreate only the backend container, leaving the others untouched;
  - `logs`: follow the backend container's output;
  - `enable-autostart`: podman only. It runs `loginctl enable-linger "$USER"` and `systemctl --user enable --now podman-restart.service`, printing each change before making it. Under Docker it explains that the daemon's restart policy already covers reboots.
- **Changed:** `up` and `up-with-dev-auth` start the backend container (dev overlay) instead of `.venv/bin/python run.py`.
- **Fallback:** `BACKEND_RUNTIME=venv` on `up` / `up-with-dev-auth` runs today's venv backend, unchanged.
- **Kept:** `init`, which sets up the venv path, models and frontend build, as today.

**Whether `start` launches the compose Keycloak:** new setting `LOCAL_KEYCLOAK=auto|true|false` (default `auto`).
- `auto` starts it when `OIDC_ISSUER` is on `localhost:18080` **or** `KC_HOSTNAME` is set. `KC_HOSTNAME` only configures the compose Keycloak, so a deployment fronting it with a public hostname (this machine's case) is detected.
- `false` is for deployers using an external IdP.

The issuer host alone is not enough: a live site's issuer is public (`https://auth.example.com`), yet its IdP can still be the compose Keycloak behind nginx.

**Docling build flag:** `start`, `rebuild`, `up` and `quickstart` read `DOCLING_ENABLED` from `.env` and build with `WITH_DOCLING` to match. If the backend starts with `DOCLING_ENABLED=true` but Docling isn't importable, it logs one ERROR at startup naming the fix (`./startup.sh rebuild`). The conversion endpoint's existing 503 stays.

**Removed:** the unexported `DOCLING_ENABLED=true` line at `startup.sh:30` (from `ed83a3f`). A plain shell variable never reached `run.py`, so it never had an effect; Docling is controlled by `.env`.

## 6. One-command quickstart

```bash
git clone <repo> && cd natural-reader
./startup.sh quickstart            # or: ./startup.sh quickstart --single-user
```

Each step runs only if not already done, so re-running resumes:

1. **Prerequisites:** a container engine (podman or docker) with compose support, and Node `^20.19.0 || >=22.12.0`. Missing tools are reported by name with an install hint. Host Python is **not** required.
2. **`.env`,** written only if absent:
   - **Default (dev):** the local Keycloak rig values `ensure_env_file` writes today (`OIDC_*` for `localhost:18080` / `localhost:5173`, generated `SESSION_SECRET`, `COOKIE_SECURE=false`, `BOOTSTRAP_ADMIN_EMAIL=admin@example.com`).
   - **`--single-user`:** `AUTH_ENABLED=false` and no `OIDC_*`.
   - **Secret generation** works with no host Python: `openssl` is preferred, `/dev/urandom` + `base64` is the fallback.
   - **An existing `.env` is never modified.**
3. **`searxng/settings.yml`** from the template with a random `secret_key` (the existing `ensure_searxng_config`).
4. **Backend image** built (Docling per `.env`).
5. **`npm install`,** skipped when `node_modules` is newer than `package-lock.json`.
6. **Start:** Postgres, SearXNG, Keycloak (dev; waits for the realm import) and the backend container in **live mode** (detached), then a health wait on `http://127.0.0.1:8000/v1/health`.
7. **Print:**
   - the URL;
   - the dev sign-in (`admin-user` / `password`), or "sign-in off" for `--single-user`;
   - the next steps: `stop`, `logs`, `enable-autostart`.
8. **Frontend:** run the Vite dev server (`npm run dev`) in the foreground. Ctrl-C stops only the frontend; the backend and its services keep running until `./startup.sh stop`.

**Production** `.env` is never generated: quickstart's final message points to `.env.example` and `docs/DEPLOYMENT.md`.

## 7. Windows

Windows users run the bare-metal flow, `startup.ps1` / `startup.cmd` (venv + Python 3.12–3.13), which this change does not alter. README and DEPLOYMENT.md say so plainly: the container commands and `quickstart` are for Linux and macOS.

## 8. Testing

**Fast (default pytest run, `server/tests/test_startup_script.py` and `test_container_files.py`):**
- `startup.sh` functions, sourced in a temp directory via `bash -c 'source startup.sh; …'` (the script already supports being sourced):
  - dev and `--single-user` `.env` contents; secret length ≥ 48 characters;
  - an existing `.env` is never modified;
  - secret generation without python3 on `PATH`;
  - Docling flag read from `.env`;
  - the `LOCAL_KEYCLOAK=auto` rule (local issuer, `KC_HOSTNAME` set, neither, explicit `false`);
  - prerequisite messages with a stripped `PATH`;
  - quickstart step skipping on re-run.
- **Compose rendering** (skipped when no compose is installed): the backend service has `network_mode: host`, `env_file: .env`, the data and log mounts, and a custom `DOC_STORAGE_DIR` on the host side of the mount.
- **`.containerignore`** contains every entry listed in §2.
- **Docling mismatch:** `DOCLING_ENABLED=true` with Docling unimportable produces exactly one startup ERROR.

**Slow (opt-in marker `container`):** build the image; run it with host networking against `natural_reader_test`; `/v1/health` returns 200; one `/v1/synthesize` returns audio (proves the baked model and onnxruntime load); a file it writes under the mounted `logs/` is owned by the invoking user.

**Running-app walk (fresh clone, this machine):**
1. Clone into a temporary directory and run `./startup.sh quickstart`.
2. In the browser: sign in, upload a document, read it aloud, create a project, log out, sign in again (the password is asked).
3. `start` → `enable-autostart` → kill the backend container; it comes back by itself.
4. Uploads on the host are owned by the user.
5. Clean up the clone, its containers and its volumes.

Two constraints on the walk:
- **The user's real stack must be stopped during the walk,** because container names are fixed. The clone's `.env`, realm and volumes are separate from the real ones, so no real account or data is touched.
- **Switching the live site to the container is a separate step the user decides,** not part of the walk.

## 9. Docs

- **README "Getting started":** clone + `./startup.sh quickstart`; the commands table; Windows = bare metal.
- **`docs/DEPLOYMENT.md`:** live mode (`start`, `rebuild`, `logs`, `enable-autostart`); host networking and Docker Desktop's opt-in; host-path semantics of `DOC_STORAGE_DIR` / `LOG_DIR` / `CHAT_ASSISTANT_PROFILE_FILE`; rollback to the venv (`stop`, then `BACKEND_RUNTIME=venv ./startup.sh up-with-dev-auth`).
- **`.env.example`:** path settings are host paths when running the container; `DOCLING_ENABLED` also selects the image variant; the new `LOCAL_KEYCLOAK` setting (`auto` by default).
- **`CHANGELOG.md`:** Added (container, quickstart, new commands), Changed (`up` runs the container; Docling moved to `requirements-docling.txt`), Removed (the no-op `DOCLING_ENABLED` line).

**API-only or env-only after this change:** nothing new. Every action is a `startup.sh` command; `.env` remains the only file a user edits.

## 10. Risks

| Risk | Effect | Handling |
|---|---|---|
| Unpinned `requirements.txt` | two builds can pull different versions | as today for the venv; pinning is a separate decision |
| Image size | ~1 GB base, ~3–4 GB with Docling | Docling is opt-in; layer order keeps code rebuilds small |
| `podman-restart.service` restarts only `restart-policy=always` containers (verified: its `ExecStart` filters on `always`) | with `unless-stopped`, nothing would return after a reboot | all four services use `restart: always`; `stop` removes containers (`compose down`), so `always` never overrides a deliberate stop |
| Host user uid ≠ 1000 | files in `data/` owned by another uid | `APP_UID` built from the invoking user's uid |
