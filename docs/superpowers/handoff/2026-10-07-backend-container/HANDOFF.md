# Handoff: backend container and quickstart

Branch `feat/backend-container`, from `development` 56ca832. Spec:
[2026-10-07-backend-container-design.md](../../specs/2026-10-07-backend-container-design.md).
Plan: [2026-10-07-backend-container.md](../../plans/2026-10-07-backend-container.md).

## What a user can do now

On Linux x86_64, `./startup.sh quickstart` takes a fresh clone to a signed-in
app. It needs podman with podman-compose 1.1+, or Docker with Compose 2.24+,
and Node; no host Python. The backend runs from a container image with the
voice model baked in.

- `start` runs the live site detached.
- `rebuild` ships new code.
- `stop` removes the containers.
- `enable-autostart` brings a podman stack back after a reboot.
- `up` and `up-with-dev-auth` run the image on the working tree.
- `BACKEND_RUNTIME=venv` keeps the bare-metal backend, from the shell or as the
  checkout's default in `.env` (verified 2026-10-11: plain `./startup.sh up` ran
  `run.py` from `.venv` on the host; only Postgres and SearXNG were containers).

Windows and macOS keep the bare-metal flow.

Documents uploaded under either runtime open under the other. The database
stores each upload's absolute path as the runtime that stored it saw it. The
backend falls back to the same `<doc_id>.<ext>` name in its own storage folder
(`doc_storage.resolve_stored`).

## Gate (2026-10-11)

| Check | Result |
| --- | --- |
| `pytest server/tests` | 1112 passed, 16 skipped |
| `pytest -m container` (real backend image; real Keycloak with the compose entrypoint) | 2 passed |
| `npx vitest run` | 518 passed |
| `npx eslint .` | clean |
| `bash -n startup.sh` | clean |

## Fresh-clone walk (2026-10-11, rootless podman 5.7, podman-compose 1.5)

A clone of the working tree in a scratch directory used compose project
`nr-walk` with its own volumes, so its Keycloak was a fresh realm import.
The user's real stack was stopped.

| # | Journey | Control | Result |
| --- | --- | --- | --- |
| 1 | Sign in | `./startup.sh quickstart` → Sign in → Keycloak `admin-user` / `password` | pass: admin shell. Engine auto-detect picked podman over a Docker CLI with no daemon |
| 2 | Upload | Reader → Choose File → **Index** | pass: listed in Library as indexed; the file is in the clone's `data/pdfs`, owned by the user; `server.log` and `audit.log` too |
| 3 | Read aloud | Play | pass on the server: `POST /v1/synthesize` 200 from the baked model. Headless, so not heard |
| 4 | Project | Library → New project → Create | pass: the card shows |
| 5 | Log out, sign in | Account → Log out → Sign in | pass: Keycloak asks for the password again |
| 6 | Survive the terminal | Ctrl-C quickstart | pass: frontend stops, `/v1/health` still answers |
| 7 | Restart policy | kill the backend's main process from the host | pass: restarted by podman (`restarts=1`), healthy. `podman kill` does **not** test this: podman counts it as a user stop and never restarts it |
| 8 | Rebuild | edit a log line in `run.py` → `./startup.sh rebuild` | pass: only the backend container was recreated; the new line is in its logs |
| 9 | Dev mode | `./startup.sh stop` → `./startup.sh up-with-dev-auth` → edit `run.py` → Ctrl-C | pass: the edit runs without a rebuild; `restart=no`; `start` during dev refuses; Ctrl-C removes everything |
| 10 | Uploads across runtimes | `stop` → `BACKEND_RUNTIME=venv ./startup.sh up-with-dev-auth` → open the container's upload; upload another → `start` → open it | pass both ways: 200 with the original bytes |

What the walk found and fixed:

- **Fresh clones never got a working Keycloak.** This predates this branch.
  `KC_PROXY_HEADERS: ${KC_PROXY_HEADERS:-}` renders as an empty string, which
  Keycloak 26 rejects, so it restarted forever. The compose Keycloak now drops
  the variable when it's empty (an entrypoint wrapper).
- **`podman kill` was the wrong test** for the restart policy (journey 7 above).

Seen in the walk but not caused by this code:

- **AppArmor on this machine blocks podman's network teardown.** The kernel log
  shows `apparmor="DENIED" operation="signal" profile="pasta" ... signal=term
  peer="podman"`. When the last container leaves a compose network, removal
  prints `rootless netns: kill network process: permission denied`, and
  sometimes needs a second `podman rm`. No data is affected. The fix needs root
  (below). Reinstalling podman does not fix it; the profile came with the OS.
- **Playwright's synthetic mouse clicks** on the app's buttons reported success
  but changed nothing in this session, while DOM `click()` on the same buttons
  worked. The walk used DOM clicks. Human clicks were not tested here.
- **podman-compose recreate noise.** When `docker-compose.yml` changes between
  two `start`s, podman-compose prints "has dependent containers" and "name
  already in use" while recreating Postgres under Keycloak, then recovers. The
  end state was correct.

## API-only or env-only

Nothing new that a UI user needs. `LOCAL_KEYCLOAK`, `BACKEND_RUNTIME` and the
host-path variables are deployment settings by design, documented in
`.env.example` and `docs/DEPLOYMENT.md`.

## Final review (fresh reviewer, whole working tree)

Verdict before fixes: not ready. Every Critical and Important finding was fixed
in one pass, each with a test that failed first:

- **C1:** the podman pod and keep-id clash meant the backend was never created.
  Fixed with `x-podman: in_pod: false`.
- **C2:** absolute `bytes_path` made old uploads unreachable. Fixed with
  `resolve_stored` at every read and delete site.
- **I1:** auto-detect chose a Docker with no daemon, and docker-compose v1 was
  allowed.
- **I2:** quickstart exited silently when compose was missing.
- **I3:** `start` or `rebuild` during a dev `up` let that terminal's Ctrl-C tear
  down the live stack.
- **I4:** the docs advertised macOS; the container now states Linux x86_64.
- **M1, M2, M3, M5, M6:** re-graded to Important by their effect on users:
  - a relative `DOC_STORAGE_DIR` broke compose;
  - shell `PORT`, `HOST` and `WORKERS` never reached the container;
  - Ctrl-C during the first build left services running;
  - by-hand podman compose needs the podman overlay;
  - Windows `init` lost Docling after the requirements split.

## Rulings

- Moved the plan's mid-module test imports to the top of the file. Cost if wrong: none.
- Fixed two plan test defects (a calls file that never exists; a minimal PATH missing `env`). Test-only.
- `start` treats a busy port held by its own live backend as fine, so re-running `start` or `quickstart` works (later split into live and dev modes for I3). Cost if wrong: a real port clash surfaces as a compose bind error instead.
- Kept the repo's real clone URL in the README instead of the plan's `<owner>` placeholder.
- Fixed stale docs outside the plan's file list. One was a README note and `run.py` comment advising `HOST=0.0.0.0` for containers, which with host networking would expose port 8000 on every interface.
- The walk cloned and then applied the uncommitted diff, because commits wait for approval.
- Re-graded five minors to Important (listed above).
- The Windows Docling line (M6) is untested: there's no Windows machine here.

## Deferred minors

- **M4:** podman-compose passes `.env` entries as `-e KEY=VALUE` on the `podman create` command line, so other local users can briefly see them in `/proc`. Docker Compose v2 uses the API instead.
- **M7:** SELinux hosts (Fedora, RHEL) need `:z` on the bind mounts. This predates the branch for the SearXNG and Keycloak mounts.
- **M8:** the container is CPU-only (no `/dev/dri`), and `.openvino_cache` sits outside the cache volume, so it's rebuilt on every recreate.
- **podman-compose recreate noise** when the compose file changes between starts (see the walk notes).

## Open steps (the user's decision)

1. **AppArmor fix for pasta** (root). Add a signal rule to the profile and reload it:
   ```bash
   sudo sed -i '/^profile pasta /a\  signal (receive) peer=podman,' /etc/apparmor.d/usr.bin.pasta
   sudo apparmor_parser -r /etc/apparmor.d/usr.bin.pasta
   ```
   A package update may replace that file; dpkg asks first.
2. **Switching the live site** to the container: `./startup.sh stop`, then `./startup.sh start` with the real `.env`, then optionally `./startup.sh enable-autostart`. Existing uploads keep working (journey 10). The real `.env` sets `KC_HOSTNAME`, so `start` runs the compose Keycloak (`LOCAL_KEYCLOAK=auto`).
3. **Commits.** All of this work is uncommitted, pending approval.
