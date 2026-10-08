# Backend Container and Quickstart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the FastAPI backend from a container image (live and dev), and let a fresh clone reach a running app with `./startup.sh quickstart`.

**Architecture:** One image built from `Containerfile` (Python 3.13, baked Kokoro model, optional Docling), run by compose with host networking. Data, logs and an optional profile file are bind-mounted from host paths that `startup.sh` computes into `NR_*` variables. `startup.sh` gains `start` (live, detached), `stop`, `rebuild`, `logs`, `enable-autostart` and `quickstart`. `up` / `up-with-dev-auth` run the container with the working tree mounted, and `BACKEND_RUNTIME=venv` keeps the old bare-metal path.

**Tech Stack:** bash (`startup.sh`), Containerfile (Docker/Buildah), compose (podman-compose 1.5 / Docker Compose v2), pytest (subprocess-driven script tests).

**Spec:** `docs/superpowers/specs/2026-10-07-backend-container-design.md`

## Global Constraints

- Nothing deployment-specific is hardcoded. Every setting is an env var with a safe default, plus a `.env.example` line where users set it. Docs use example.com.
- `.env` is never copied into the image or the build context. Never print `.env` values in output or logs.
- No user content or personal data at INFO or above.
- Python in the image: the 3.13 line (`python:3.13-slim-trixie`).
- The backend binds `127.0.0.1:8000` by default. Host networking means `HOST` keeps today's meaning.
- An existing `.env` is never modified by any command.
- **All four compose services use `restart: always`.** `podman-restart.service` restarts only `restart-policy=always` containers (verified 2026-10-07). `stop` removes containers, so `always` never overrides a deliberate stop.
- **Host-side mount paths travel only as `NR_DATA_DIR`, `NR_LOG_DIR`, `NR_PROFILE_FILE`.** Verified 2026-10-07: podman-compose 1.5 fills a volume's `${VAR}` from the same service's `environment:` block, and it cannot nest defaults.
- **Windows stays on the bare-metal venv flow.** `startup.ps1` / `startup.cmd` are not changed.
- **Commit only with the user's explicit approval** (the controller asks before each commit).
- **The user's real stack and real Keycloak are never used for tests or walks.** On this machine the compose Keycloak is their real identity server.

## Review Focus

1. **A `.env` that sets `DOC_STORAGE_DIR` or `LOG_DIR` to a custom host path:** the container must read and write that host folder, never an empty folder inside the container. (Task 3 render test, Task 4 `export_host_paths` test.)
2. **No `.env` at all** (fresh clone running `up`, or a deployer running compose by hand): compose must still render, and the data mount must land on `./data/pdfs`, not on `/app/data/pdfs`. (Task 3 render test without `.env`.)
3. **Host user uid ≠ 1000, and Docker instead of podman:** files the container writes must be owned by the invoking user. (Task 4 `NR_APP_UID` test, Task 7 ownership check.)
4. **A live deployment whose issuer is public but whose IdP is the compose Keycloak:** `start` must still launch Keycloak. (Task 4 `local_keycloak_wanted` tests.)
5. **Re-running `quickstart` after a partial failure:** it must not overwrite `.env` or `searxng/settings.yml`, and it must skip `npm install` when current. (Task 6 tests.)

---

## File Structure

| File | Responsibility |
|---|---|
| `Containerfile` (create) | the backend image |
| `.dockerignore` (create) | build-context allow-list (both engines read it) |
| `requirements.txt` (modify) | base deps; Docling removed |
| `requirements-docling.txt` (create) | Docling only |
| `docker-compose.yml` (modify) | `backend` service; `restart: always` everywhere; `cache` volume |
| `compose.podman.yml` (create) | podman-only `userns_mode: keep-id` |
| `compose.dev.yml` (create) | dev overlay: mount the working tree, no restart |
| `compose.profile.yml` (create) | optional assistant-profile mount |
| `startup.sh` (modify) | helpers + `start` / `stop` / `rebuild` / `logs` / `enable-autostart` / `quickstart`; container `up` |
| `server/services/docling_convert.py` (modify) | `check_available()` startup check |
| `server/app.py` (modify) | call `check_available()` at startup |
| `pytest.ini` (modify) | register `container` marker; deselect it by default |
| `server/tests/startup_harness.py` (create) | copy the script to a temp project, stub binaries, run functions |
| `server/tests/test_docling_check.py` (create) | Task 1 |
| `server/tests/test_container_files.py` (create) | Tasks 2–3 static and render tests |
| `server/tests/test_startup_script.py` (create) | Tasks 4–6 |
| `server/tests/test_container_image.py` (create) | Task 7 (slow, opt-in) |
| `README.md`, `docs/DEPLOYMENT.md`, `.env.example`, `CHANGELOG.md` (modify) | Task 8 |

---

### Task 1: Split Docling out of the base requirements; startup error on a mismatch

**Files:**
- Create: `requirements-docling.txt`
- Modify: `requirements.txt` (remove the `docling>=2.0` block)
- Modify: `server/services/docling_convert.py` (add `check_available`)
- Modify: `server/app.py` (`_startup` calls it)
- Modify: `startup.sh` `cmd_init` (install both files)
- Test: `server/tests/test_docling_check.py`

**Interfaces:**
- Produces: `docling_convert.check_available() -> bool` (True when Docling is enabled and importable, or not enabled; logs one ERROR and returns False when enabled but not importable).

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_docling_check.py
import logging

from server.services import docling_convert


def test_enabled_but_missing_logs_one_error(monkeypatch, caplog):
    monkeypatch.setattr(docling_convert, "DOCLING_ENABLED", True)
    monkeypatch.setattr(docling_convert.importlib.util, "find_spec", lambda name: None)
    with caplog.at_level(logging.ERROR, logger="server.services.docling_convert"):
        assert docling_convert.check_available() is False
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "./startup.sh rebuild" in errors[0].getMessage()


def test_disabled_is_silent(monkeypatch, caplog):
    monkeypatch.setattr(docling_convert, "DOCLING_ENABLED", False)
    monkeypatch.setattr(docling_convert.importlib.util, "find_spec", lambda name: None)
    with caplog.at_level(logging.DEBUG, logger="server.services.docling_convert"):
        assert docling_convert.check_available() is True
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_enabled_and_present_is_silent(monkeypatch, caplog):
    monkeypatch.setattr(docling_convert, "DOCLING_ENABLED", True)
    monkeypatch.setattr(docling_convert.importlib.util, "find_spec", lambda name: object())
    with caplog.at_level(logging.DEBUG, logger="server.services.docling_convert"):
        assert docling_convert.check_available() is True
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest server/tests/test_docling_check.py -q -p no:cacheprovider`
Expected: FAIL. `AttributeError: module 'server.services.docling_convert' has no attribute 'importlib'` (or no `check_available`).

- [ ] **Step 3: Implement**

In `server/services/docling_convert.py`, add `import importlib.util` beside the other imports, then below `is_enabled()`:

```python
def check_available() -> bool:
    """Startup check: DOCLING_ENABLED=true needs Docling installed.

    The container image installs Docling only when built with
    WITH_DOCLING=true (startup.sh matches it to .env). If the two disagree,
    say so once at startup instead of failing every conversion.
    """
    if not DOCLING_ENABLED:
        return True
    if importlib.util.find_spec("docling") is not None:
        return True
    logger.error(
        "DOCLING_ENABLED=true but Docling is not installed in this environment: "
        "conversions will fail with 503. Container: run ./startup.sh rebuild "
        "(it builds with Docling when .env enables it). Venv: pip install -r "
        "requirements-docling.txt"
    )
    return False
```

In `server/app.py` `_startup()`, add right after `startup_guard(...)`:

```python
        docling_convert.check_available()
```

and add `from .services import docling_convert` to the imports, matching the existing `from .services import …` style in that file (check the top of `app.py` and follow it).

- [ ] **Step 4: Split the requirements**

Create `requirements-docling.txt`:

```
# Document conversion (PDF/DOCX/PPTX/HTML/images → Markdown). Only needed when
# DOCLING_ENABLED=true. Pulls in transformers + torch; the first conversion
# downloads layout/table models (~500 MB to ~2 GB). The container installs
# this only when built with WITH_DOCLING=true (startup.sh matches .env).
docling>=2.0
```

In `requirements.txt`, delete the four lines from `# Document conversion (PDF/DOCX/PPTX/HTML/images → Markdown).` through `docling>=2.0`.

In `startup.sh` `cmd_init`, replace `"$VENV_DIR/bin/python" -m pip install -r requirements.txt` with:

```bash
	"$VENV_DIR/bin/python" -m pip install -r requirements.txt -r requirements-docling.txt
```

(The venv path keeps Docling exactly as today.)

- [ ] **Step 5: Run the tests and the suite**

Run: `.venv/bin/pytest server/tests/test_docling_check.py -q -p no:cacheprovider` → Expected: 3 passed.
Run: `.venv/bin/pytest server/tests -q -p no:cacheprovider` → Expected: 0 failed (Postgres on 5433 must be up).

- [ ] **Step 6: Commit** (after the user approves)

```bash
git add requirements.txt requirements-docling.txt server/services/docling_convert.py server/app.py startup.sh server/tests/test_docling_check.py
git commit -m "feat(backend): Docling split into requirements-docling.txt; one startup ERROR when enabled but not installed"
```

---

### Task 2: Containerfile and build-context allow-list

**Files:**
- Create: `Containerfile`
- Create: `.dockerignore`
- Test: `server/tests/test_container_files.py`

**Interfaces:**
- Produces build args: `WITH_DOCLING` (`true`/`false`, default `false`), `APP_UID` (default `1000`), `MODEL_BASE_URL`. Produces the image layout `/app/{server,run.py,kokoro-v1.0.onnx,voices-v1.0.bin,data/pdfs,logs,.cache,config}`, user `app`.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_container_files.py
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _lines(path):
    return [l.strip() for l in (REPO / path).read_text().splitlines()
            if l.strip() and not l.strip().startswith("#")]


def test_dockerignore_is_an_allow_list():
    lines = _lines(".dockerignore")
    assert lines[0] == "*"
    included = {l[1:] for l in lines if l.startswith("!")}
    assert included == {"server/", "run.py", "requirements.txt", "requirements-docling.txt"}
    # Re-excluded after the allow-list: never ship tests or bytecode.
    assert "server/tests/" in lines
    assert "**/__pycache__/" in lines


def test_containerfile_pins_python_313_and_runs_unprivileged():
    text = (REPO / "Containerfile").read_text()
    assert re.search(r"^FROM docker\.io/library/python:3\.13-slim", text, re.M)
    assert "ARG WITH_DOCLING=false" in text
    assert "ARG APP_UID=1000" in text
    assert re.search(r"^USER app$", text, re.M)
    assert 'CMD ["python", "run.py"]' in text
    # Code is copied after dependencies and models, so code-only rebuilds reuse the cache.
    assert text.index("requirements.txt") < text.index("kokoro-v1.0.onnx") < text.index("COPY server/")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest server/tests/test_container_files.py -q -p no:cacheprovider`
Expected: FAIL with `FileNotFoundError` for `.dockerignore` / `Containerfile`.

- [ ] **Step 3: Create `.dockerignore`**

```
# Build-context ALLOW-list: everything is excluded unless re-included below, so a
# new secret-bearing file (.env, tmp/ credentials, data/) can never ship by default.
# Read by Docker and podman (podman also reads .containerignore; Docker does not).
*
!server/
!run.py
!requirements.txt
!requirements-docling.txt
server/tests/
**/__pycache__/
**/*.pyc
```

- [ ] **Step 4: Create `Containerfile`**

```dockerfile
# Natural Reader backend (FastAPI + Kokoro TTS). Python is pinned here so host
# OS upgrades can't move the interpreter under the app.
# Build:  ./startup.sh rebuild   (or: podman build -f Containerfile -t natural-reader-backend .)
FROM docker.io/library/python:3.13-slim-trixie

ARG WITH_DOCLING=false
ARG APP_UID=1000
ARG MODEL_BASE_URL=https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/app \
    HF_HOME=/app/.cache/huggingface

WORKDIR /app

# onnxruntime needs libgomp; Docling's OpenCV needs libGL + glib.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && if [ "$WITH_DOCLING" = "true" ]; then apt-get install -y --no-install-recommends libgl1 libglib2.0-0; fi \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-docling.txt ./
RUN pip install -r requirements.txt
# CPU-only torch keeps the Docling variant gigabytes smaller than the CUDA default.
RUN if [ "$WITH_DOCLING" = "true" ]; then \
      pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-docling.txt; \
    fi

# Kokoro voice model, in its own cached layer. server/model.py opens these
# relative to the working directory (/app).
RUN python -c "import urllib.request as u, os; \
[u.urlretrieve('$MODEL_BASE_URL/' + n, n) for n in ('kokoro-v1.0.onnx', 'voices-v1.0.bin')]; \
assert all(os.path.getsize(n) > 1_000_000 for n in ('kokoro-v1.0.onnx', 'voices-v1.0.bin'))"

RUN useradd --uid "$APP_UID" --home-dir /app --no-create-home --shell /usr/sbin/nologin app \
 && mkdir -p /app/data/pdfs /app/logs /app/.cache /app/config \
 && chown app:app /app /app/data /app/data/pdfs /app/logs /app/.cache /app/config

COPY run.py ./
COPY server/ server/

USER app
CMD ["python", "run.py"]
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest server/tests/test_container_files.py -q -p no:cacheprovider` → Expected: 2 passed.

- [ ] **Step 6: Build once by hand to prove the file works**

Run: `env -u XDG_DATA_HOME podman build -f Containerfile --build-arg APP_UID=$(id -u) -t natural-reader-backend:latest . 2>&1 | tail -3`
Expected: ends with the image ID / `Successfully tagged localhost/natural-reader-backend:latest`. (First build downloads ~1 GB of wheels plus 336 MB of models.)

- [ ] **Step 7: Commit** (after approval)

```bash
git add Containerfile .dockerignore server/tests/test_container_files.py
git commit -m "feat(container): backend Containerfile (Python 3.13, baked Kokoro model, optional Docling) and an allow-list build context"
```

---

### Task 3: Compose backend service and overlays

**Files:**
- Modify: `docker-compose.yml`
- Create: `compose.podman.yml`, `compose.dev.yml`, `compose.profile.yml`
- Test: `server/tests/test_container_files.py` (append)

**Interfaces:**
- Consumes: image build args from Task 2.
- Produces: compose service `backend` (container `natural-reader-backend`, image `natural-reader-backend:latest`), named volume `cache`, and the variables compose reads: `NR_DATA_DIR`, `NR_LOG_DIR`, `NR_PROFILE_FILE`, `NR_WITH_DOCLING`, `NR_APP_UID`, `AUTH_ENABLED`.

- [ ] **Step 1: Write the failing render tests**

Append to `server/tests/test_container_files.py`:

```python
import shutil
import subprocess

import pytest
import yaml

COMPOSE_FILES = ["docker-compose.yml", "compose.podman.yml", "compose.dev.yml", "compose.profile.yml"]


def _render(tmp_path, env_lines=None, files=("docker-compose.yml",), extra_env=None):
    if not shutil.which("podman-compose"):
        pytest.skip("podman-compose not installed")
    for f in COMPOSE_FILES:
        shutil.copy(REPO / f, tmp_path / f)
    (tmp_path / "searxng").mkdir()
    if env_lines is not None:
        (tmp_path / ".env").write_text("\n".join(env_lines) + "\n")
    import os
    env = {k: v for k, v in os.environ.items() if not k.startswith(("NR_", "DOC_STORAGE", "LOG_DIR", "AUTH_ENABLED"))}
    env.pop("XDG_DATA_HOME", None)
    env.update(extra_env or {})
    args = ["podman-compose"]
    for f in files:
        args += ["-f", f]
    out = subprocess.run(args + ["config"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return yaml.safe_load(out.stdout)["services"]


def test_backend_uses_host_network_env_file_and_pinned_paths(tmp_path):
    b = _render(tmp_path, env_lines=["AUTH_ENABLED=true"])["backend"]
    assert b["network_mode"] == "host"
    assert b["env_file"][0]["path"] == ".env" and b["env_file"][0]["required"] is False
    assert b["environment"]["DOC_STORAGE_DIR"] == "/app/data/pdfs"
    assert b["environment"]["LOG_DIR"] == "/app/logs"
    assert b["restart"] == "always"


def test_without_env_the_data_mount_is_the_repo_folder_not_the_container_path(tmp_path):
    vols = _render(tmp_path, env_lines=None)["backend"]["volumes"]
    assert "./data/pdfs:/app/data/pdfs" in vols or any(v.endswith("data/pdfs:/app/data/pdfs") and not v.startswith("/app") for v in vols)


def test_host_paths_come_from_nr_variables(tmp_path):
    vols = _render(tmp_path, env_lines=["DOC_STORAGE_DIR=/srv/docs"],
                   extra_env={"NR_DATA_DIR": "/srv/docs", "NR_LOG_DIR": "/srv/logs"})["backend"]["volumes"]
    assert "/srv/docs:/app/data/pdfs" in vols
    assert "/srv/logs:/app/logs" in vols


def test_shell_auth_enabled_overrides_env_file(tmp_path):
    b = _render(tmp_path, env_lines=["AUTH_ENABLED=true"], extra_env={"AUTH_ENABLED": "false"})["backend"]
    assert str(b["environment"]["AUTH_ENABLED"]).lower() == "false"


def test_every_service_restarts_always(tmp_path):
    services = _render(tmp_path, env_lines=[])
    assert {n: s.get("restart") for n, s in services.items()} == {
        "postgres": "always", "searxng": "always", "keycloak": "always", "backend": "always"}


def test_dev_overlay_mounts_the_working_tree_and_never_restarts(tmp_path):
    b = _render(tmp_path, env_lines=[], files=("docker-compose.yml", "compose.dev.yml"))["backend"]
    assert any(v.endswith(":/app/server:ro") for v in b["volumes"])
    assert any(v.endswith(":/app/run.py:ro") for v in b["volumes"])
    assert any(v.endswith(":/app/data/pdfs") for v in b["volumes"]), "overlay must add to, not replace, the base mounts"
    assert b["restart"] == "no"


def test_podman_overlay_keeps_the_host_user(tmp_path):
    b = _render(tmp_path, env_lines=[], files=("docker-compose.yml", "compose.podman.yml"))["backend"]
    assert b["userns_mode"] == "keep-id"


def test_profile_overlay_mounts_the_file_read_only(tmp_path):
    b = _render(tmp_path, env_lines=[], files=("docker-compose.yml", "compose.profile.yml"),
                extra_env={"NR_PROFILE_FILE": "/home/u/profile.md"})["backend"]
    assert "/home/u/profile.md:/app/config/assistant-profile:ro" in b["volumes"]
    assert b["environment"]["CHAT_ASSISTANT_PROFILE_FILE"] == "/app/config/assistant-profile"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest server/tests/test_container_files.py -q -p no:cacheprovider`
Expected: the new tests FAIL (`FileNotFoundError: compose.podman.yml`, or `KeyError: 'backend'`).

- [ ] **Step 3: Modify `docker-compose.yml`**

Change `restart: unless-stopped` to `restart: always` on `postgres`, `searxng` and `keycloak`, each with this comment above the first one:

```yaml
    # always, not unless-stopped: podman-restart.service (./startup.sh
    # enable-autostart) only restarts restart-policy=always containers after a
    # reboot. `./startup.sh stop` removes containers, so this never overrides a
    # deliberate stop.
```

Add the service before the top-level `volumes:`:

```yaml
  # The FastAPI backend (TTS, chat, documents). Host networking: it is a client
  # of Postgres :5433, Keycloak :18080, SearXNG :18043 and Ollama :11434, all
  # bound to 127.0.0.1 on the host, and the OIDC issuer must be the same URL
  # for the browser and the backend. It still binds 127.0.0.1:8000 only (HOST).
  # Host paths come from startup.sh as NR_* variables (podman-compose fills a
  # volume's ${VAR} from this service's own environment block, so the host side
  # must never share a name with an in-container setting).
  backend:
    build:
      context: .
      dockerfile: Containerfile
      args:
        WITH_DOCLING: ${NR_WITH_DOCLING:-false}
        APP_UID: ${NR_APP_UID:-1000}
    image: natural-reader-backend:latest
    container_name: natural-reader-backend
    network_mode: host
    env_file:
      - path: .env
        required: false
    environment:
      DOC_STORAGE_DIR: /app/data/pdfs
      LOG_DIR: /app/logs
      LOG_AUDIT_FILE: /app/logs/audit.log
      HF_HOME: /app/.cache/huggingface
      AUTH_ENABLED: ${AUTH_ENABLED:-true}
    volumes:
      - ${NR_DATA_DIR:-./data/pdfs}:/app/data/pdfs
      - ${NR_LOG_DIR:-./logs}:/app/logs
      - cache:/app/.cache
    restart: always
```

and extend the top-level volumes:

```yaml
volumes:
  pgdata:
  cache:
```

- [ ] **Step 4: Create the overlays**

`compose.podman.yml`:

```yaml
# Added by startup.sh when the engine is podman: map the container user to the
# invoking host user so files in data/ and logs/ stay theirs. Docker has no
# keep-id; there the image is built with APP_UID = the host uid instead.
services:
  backend:
    userns_mode: keep-id
```

`compose.dev.yml`:

```yaml
# Dev overlay (./startup.sh up / up-with-dev-auth): run the working tree, not the
# code baked into the image. Edits apply on restart; no rebuild. Never restarts
# on its own (Ctrl-C on `up` tears the stack down, as before).
services:
  backend:
    volumes:
      - ./server:/app/server:ro
      - ./run.py:/app/run.py:ro
    restart: "no"
```

`compose.profile.yml`:

```yaml
# Added by startup.sh only when CHAT_ASSISTANT_PROFILE_FILE names a readable
# file: mount it read-only at a fixed path and point the app there.
services:
  backend:
    volumes:
      - ${NR_PROFILE_FILE}:/app/config/assistant-profile:ro
    environment:
      CHAT_ASSISTANT_PROFILE_FILE: /app/config/assistant-profile
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest server/tests/test_container_files.py -q -p no:cacheprovider`
Expected: all pass. If `test_dev_overlay…` fails on "overlay must add to", podman-compose replaced the list instead of merging it. **Ruling path:** repeat the two base data mounts in `compose.dev.yml` and ledger it.

- [ ] **Step 6: Commit** (after approval)

```bash
git add docker-compose.yml compose.podman.yml compose.dev.yml compose.profile.yml server/tests/test_container_files.py
git commit -m "feat(compose): backend service (host network, bind-mounted data/logs, Docling/uid build args), podman/dev/profile overlays; restart: always so reboots bring the stack back"
```

---

### Task 4: `startup.sh` helpers and the test harness

**Files:**
- Create: `server/tests/startup_harness.py`
- Modify: `startup.sh` (helpers; `compose()` overlays; `generate_secret`; `ensure_env_file` profiles; `ensure_searxng_config` uses `generate_secret`; delete the `DOCLING_ENABLED=true` line)
- Test: `server/tests/test_startup_script.py`

**Interfaces:**
- Produces bash functions:
  - `compose_files ENGINE` prints one file per line;
  - `generate_secret` prints 64 hex characters;
  - `ensure_env_file [dev|single-user]`;
  - `local_keycloak_wanted` returns 0 or 1;
  - `docling_build_flag` prints `true` or `false`;
  - `export_host_paths` exports `NR_DATA_DIR` and `NR_LOG_DIR` and makes both directories;
  - `export_backend_env` calls `export_host_paths` and exports `NR_WITH_DOCLING`, `NR_APP_UID` and (when the profile file is readable) `NR_PROFILE_FILE`.
- Produces the global `COMPOSE_DEV` (0/1) and the constant `BACKEND_SERVICE=backend`.
- Python: `startup_harness.make_project(tmp_path) -> Path`, `stub_bin(dir, names, calls, codes=None) -> Path`, `run(project, script, path, env=None) -> CompletedProcess`.

- [ ] **Step 1: Create the harness**

```python
# server/tests/startup_harness.py
"""Run startup.sh functions in a throwaway copy of the project.

startup.sh cd's to its own directory when sourced, so tests source a COPY in
tmp_path: nothing touches the real .env, data/ or containers. Binaries the
script calls (podman, podman-compose, curl, npm, loginctl, systemctl, …) are
replaced by stubs that append "name args…" to a calls file.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROJECT_FILES = ["startup.sh", "docker-compose.yml", "compose.podman.yml", "compose.dev.yml",
                 "compose.profile.yml", "searxng/settings.yml.example", "package.json"]


def make_project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    for f in PROJECT_FILES:
        src = REPO / f
        if src.exists():
            (proj / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, proj / f)
    return proj


def stub_bin(bindir: Path, names, calls: Path, codes: dict | None = None) -> Path:
    bindir.mkdir(parents=True, exist_ok=True)
    for name in names:
        code = (codes or {}).get(name, 0)
        p = bindir / name
        p.write_text(f'#!/bin/sh\necho "{name} $*" >> "{calls}"\nexit {code}\n')
        p.chmod(0o755)
    return bindir


def tools_dir(bindir: Path, names) -> Path:
    """A PATH dir holding only symlinks to the named real tools."""
    bindir.mkdir(parents=True, exist_ok=True)
    for n in names:
        real = shutil.which(n)
        assert real, f"{n} not found on this machine"
        (bindir / n).symlink_to(real)
    return bindir


def run(project: Path, script: str, path: str, env: dict | None = None) -> subprocess.CompletedProcess:
    base = {"HOME": str(project), "PATH": path, "USER": os.environ.get("USER", "tester")}
    base.update(env or {})
    return subprocess.run(["bash", "-c", f"source ./startup.sh; {script}"], cwd=project,
                          env=base, capture_output=True, text=True, timeout=120)


SYSTEM_PATH = "/usr/bin:/bin"
```

- [ ] **Step 2: Write the failing tests**

```python
# server/tests/test_startup_script.py
import os
import re

import pytest

from server.tests.startup_harness import SYSTEM_PATH, make_project, run, stub_bin, tools_dir


@pytest.fixture
def proj(tmp_path):
    return make_project(tmp_path)


def test_generate_secret_is_64_hex(proj):
    out = run(proj, "generate_secret", SYSTEM_PATH)
    assert out.returncode == 0, out.stderr
    assert re.fullmatch(r"[0-9a-f]{64}", out.stdout.strip())


def test_generate_secret_without_python_or_openssl(proj, tmp_path):
    path = str(tools_dir(tmp_path / "min", ["bash", "dirname", "head", "od", "tr"]))
    out = run(proj, "generate_secret", path)
    assert out.returncode == 0, out.stderr
    assert re.fullmatch(r"[0-9a-f]{64}", out.stdout.strip())


def test_dev_env_file_has_the_rig_values_and_a_secret(proj):
    assert run(proj, "ensure_env_file dev", SYSTEM_PATH).returncode == 0
    env = dict(l.split("=", 1) for l in (proj / ".env").read_text().splitlines() if l and not l.startswith("#"))
    assert env["OIDC_ISSUER"] == "http://localhost:18080/realms/natural-reader"
    assert env["OIDC_REDIRECT_URL"] == "http://localhost:5173/v1/auth/callback"
    assert env["COOKIE_SECURE"] == "false"
    assert len(env["SESSION_SECRET"]) >= 48


def test_single_user_env_file_turns_sign_in_off(proj):
    assert run(proj, "ensure_env_file single-user", SYSTEM_PATH).returncode == 0
    text = (proj / ".env").read_text()
    assert "AUTH_ENABLED=false" in text and "OIDC_" not in text


def test_existing_env_file_is_never_modified(proj):
    (proj / ".env").write_text("MINE=1\n")
    run(proj, "ensure_env_file dev", SYSTEM_PATH)
    run(proj, "ensure_env_file single-user", SYSTEM_PATH)
    assert (proj / ".env").read_text() == "MINE=1\n"


@pytest.mark.parametrize("env,wanted", [
    ({"OIDC_ISSUER": "http://localhost:18080/realms/natural-reader"}, True),
    ({"OIDC_ISSUER": "https://auth.example.com/realms/natural-reader", "KC_HOSTNAME": "https://auth.example.com"}, True),
    ({"OIDC_ISSUER": "https://idp.example.org/realms/x"}, False),
    ({}, False),
    ({"OIDC_ISSUER": "http://localhost:18080/realms/natural-reader", "LOCAL_KEYCLOAK": "false"}, False),
    ({"LOCAL_KEYCLOAK": "true"}, True),
])
def test_local_keycloak_rule(proj, env, wanted):
    out = run(proj, "local_keycloak_wanted && echo yes || echo no", SYSTEM_PATH, env)
    assert out.stdout.strip() == ("yes" if wanted else "no"), out.stderr


def test_local_keycloak_rejects_a_typo(proj):
    out = run(proj, "local_keycloak_wanted", SYSTEM_PATH, {"LOCAL_KEYCLOAK": "ture"})
    assert out.returncode != 0 and "LOCAL_KEYCLOAK" in out.stderr


@pytest.mark.parametrize("value,flag", [("true", "true"), ("TRUE", "true"), ("1", "true"), ("yes", "true"),
                                        ("false", "false"), ("", "false"), (None, "false")])
def test_docling_build_flag_follows_the_app_parsing(proj, value, flag):
    env = {} if value is None else {"DOCLING_ENABLED": value}
    assert run(proj, "docling_build_flag", SYSTEM_PATH, env).stdout.strip() == flag


def test_host_paths_default_to_the_repo_folders(proj):
    out = run(proj, 'export_host_paths; echo "$NR_DATA_DIR|$NR_LOG_DIR"', SYSTEM_PATH)
    assert out.stdout.strip() == "./data/pdfs|./logs"
    assert (proj / "data/pdfs").is_dir() and (proj / "logs").is_dir()


def test_doc_storage_dir_wins_over_the_legacy_name(proj, tmp_path):
    env = {"DOC_STORAGE_DIR": str(tmp_path / "docs"), "PDF_STORAGE_DIR": str(tmp_path / "old")}
    out = run(proj, 'export_host_paths; echo "$NR_DATA_DIR"', SYSTEM_PATH, env)
    assert out.stdout.strip() == str(tmp_path / "docs")
    out = run(proj, 'export_host_paths; echo "$NR_DATA_DIR"', SYSTEM_PATH, {"PDF_STORAGE_DIR": str(tmp_path / "old")})
    assert out.stdout.strip() == str(tmp_path / "old")


def test_app_uid_is_the_invoking_user(proj):
    out = run(proj, 'export_backend_env; echo "$NR_APP_UID"', SYSTEM_PATH)
    expected = os.getuid() if os.getuid() != 0 else 1000
    assert out.stdout.strip() == str(expected)


def test_profile_overlay_only_for_a_readable_file(proj, tmp_path):
    f = tmp_path / "profile.md"
    f.write_text("Be brief.")
    out = run(proj, 'export_backend_env; compose_files podman', SYSTEM_PATH, {"CHAT_ASSISTANT_PROFILE_FILE": str(f)})
    assert "compose.profile.yml" in out.stdout.split()
    out = run(proj, 'export_backend_env; compose_files podman', SYSTEM_PATH,
              {"CHAT_ASSISTANT_PROFILE_FILE": str(tmp_path / "missing.md")})
    assert "compose.profile.yml" not in out.stdout.split()
    assert "not readable" in out.stderr


def test_compose_files_by_engine_and_mode(proj):
    assert run(proj, "compose_files docker", SYSTEM_PATH).stdout.split() == ["docker-compose.yml"]
    assert run(proj, "compose_files podman", SYSTEM_PATH).stdout.split() == ["docker-compose.yml", "compose.podman.yml"]
    assert run(proj, "COMPOSE_DEV=1; compose_files docker", SYSTEM_PATH).stdout.split() == ["docker-compose.yml", "compose.dev.yml"]


def test_compose_passes_every_overlay(proj, tmp_path):
    calls = tmp_path / "calls"
    path = f"{stub_bin(tmp_path / 'bin', ['podman', 'podman-compose'], calls)}:{SYSTEM_PATH}"
    run(proj, "compose podman ps", path)
    assert calls.read_text().strip() == "podman-compose -f docker-compose.yml -f compose.podman.yml ps"


def test_the_noop_docling_line_is_gone():
    from server.tests.startup_harness import REPO
    assert not re.search(r"^DOCLING_ENABLED=", (REPO / "startup.sh").read_text(), re.M)
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/pytest server/tests/test_startup_script.py -q -p no:cacheprovider`
Expected: most FAIL (`compose_files: command not found`, the old 64-char check fails on base64 output, `ensure_env_file single-user` writes the dev file, …).

- [ ] **Step 4: Implement in `startup.sh`**

Delete line 30 (`DOCLING_ENABLED=true`).

Add below `readonly MODEL_FILES=…`:

```bash
readonly BACKEND_SERVICE="backend"
# 1 → layer compose.dev.yml (working tree mounted) on top; set by `up`.
COMPOSE_DEV=0
```

Add before `compose()`:

```bash
# Compose files for this invocation: the base file, the podman overlay
# (keep-id), the assistant-profile overlay when export_backend_env found a
# readable profile file, and the dev overlay when COMPOSE_DEV=1.
compose_files() {
	local engine="$1"
	printf '%s\n' "$COMPOSE_FILE"
	[[ "$engine" == "podman" ]] && printf '%s\n' "compose.podman.yml"
	[[ -n "${NR_PROFILE_FILE:-}" ]] && printf '%s\n' "compose.profile.yml"
	[[ "${COMPOSE_DEV:-0}" == "1" ]] && printf '%s\n' "compose.dev.yml"
	return 0
}
```

In `compose()`, build the file list once and use it in all three branches (replacing each `-f "$COMPOSE_FILE"`):

```bash
	local -a files=()
	local f
	while IFS= read -r f; do files+=(-f "$f"); done < <(compose_files "$engine")
```

so the branches read `podman-compose "${files[@]}" "$@"`, `"$engine" compose "${files[@]}" "$@"`, `"${engine}-compose" "${files[@]}" "$@"`.

Replace `generate_secret`:

```bash
# A 64-character hex secret (above the backend's 32-char SESSION_SECRET
# minimum). No Python needed: quickstart must work on a host without it.
generate_secret() {
	if command -v openssl >/dev/null 2>&1; then
		openssl rand -hex 32
	elif [[ -r /dev/urandom ]]; then
		head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
		printf '\n'
	else
		die "Need openssl or /dev/urandom to generate a secret; add one to $ENV_FILE manually."
	fi
}
```

In `ensure_searxng_config`, replace the `if command -v openssl … fi` block with:

```bash
	sed -i "s/CHANGE_ME_openssl_rand_hex_32/$(generate_secret)/" "$target"
```

Replace `ensure_env_file`:

```bash
# Create .env on first run. $1 = dev (local Keycloak rig; mirrors
# deploy/README.md) or single-user (sign-in off, loopback only). Idempotent —
# an existing .env is never touched, so manual edits survive.
ensure_env_file() {
	local profile="${1:-dev}"
	[[ -f "$ENV_FILE" ]] && return 0
	case "$profile" in
		dev)
			log "Creating $ENV_FILE with the local-dev OIDC rig values (first run)"
			{
				echo "# Created by ./startup.sh — local-dev OIDC rig."
				echo "# Full annotated reference: .env.example. This file is gitignored."
				echo "# bash-sourced: one KEY=value per line, no inline comments."
				echo "OIDC_ISSUER=http://localhost:18080/realms/natural-reader"
				echo "OIDC_CLIENT_ID=natural-reader"
				echo "OIDC_CLIENT_SECRET=natural-reader-dev-secret"
				echo "OIDC_REDIRECT_URL=http://localhost:5173/v1/auth/callback"
				printf 'SESSION_SECRET=%s\n' "$(generate_secret)"
				echo "COOKIE_SECURE=false"
				echo "BOOTSTRAP_ADMIN_EMAIL=admin@example.com"
			} >"$ENV_FILE"
			;;
		single-user)
			log "Creating $ENV_FILE for single-user use (sign-in off, this machine only)"
			{
				echo "# Created by ./startup.sh quickstart --single-user."
				echo "# Sign-in is off; the backend refuses this on a non-loopback bind."
				echo "# Full annotated reference: .env.example. This file is gitignored."
				echo "AUTH_ENABLED=false"
			} >"$ENV_FILE"
			;;
		*) die "Unknown .env profile '$profile' (dev or single-user)." ;;
	esac
}
```

Add after `load_env_file`:

```bash
# Is the compose Keycloak this deployment's IdP? LOCAL_KEYCLOAK=auto|true|false
# (default auto). auto: yes when the issuer is the local rig, or when
# KC_HOSTNAME is set — it only configures the compose Keycloak, so a live site
# fronting it with a public hostname (issuer https://auth.example.com) counts.
local_keycloak_wanted() {
	case "${LOCAL_KEYCLOAK:-auto}" in
		true) return 0 ;;
		false) return 1 ;;
		auto)
			case "${OIDC_ISSUER:-}" in
				http://localhost:18080/* | http://127.0.0.1:18080/*) return 0 ;;
			esac
			[[ -n "${KC_HOSTNAME:-}" ]] && return 0
			return 1
			;;
		*) die "LOCAL_KEYCLOAK must be auto, true or false (got '${LOCAL_KEYCLOAK}')." ;;
	esac
}

# Build the image with Docling exactly when .env enables it (same parsing as
# server/services/docling_convert.py: 1/true/yes, any case).
docling_build_flag() {
	local v="${DOCLING_ENABLED:-false}"
	case "${v,,}" in
		1 | true | yes) printf 'true\n' ;;
		*) printf 'false\n' ;;
	esac
}

# .env path settings name HOST paths; the container pins its own. They reach
# compose as NR_* (podman-compose fills a volume's ${VAR} from the service's
# own environment block, and can't nest ${A:-${B}} defaults). DOC_STORAGE_DIR
# wins over the legacy PDF_STORAGE_DIR, as in the app. The folders are made
# here so a rootful engine doesn't create them as root.
export_host_paths() {
	NR_DATA_DIR="${DOC_STORAGE_DIR:-${PDF_STORAGE_DIR:-./data/pdfs}}"
	NR_LOG_DIR="${LOG_DIR:-./logs}"
	export NR_DATA_DIR NR_LOG_DIR
	mkdir -p "$NR_DATA_DIR" "$NR_LOG_DIR"
}

# Everything compose needs to build and run the backend for this user.
export_backend_env() {
	export_host_paths
	NR_WITH_DOCLING="$(docling_build_flag)"
	NR_APP_UID="$(id -u)"
	[[ "$NR_APP_UID" == "0" ]] && NR_APP_UID=1000
	export NR_WITH_DOCLING NR_APP_UID
	unset NR_PROFILE_FILE
	if [[ -n "${CHAT_ASSISTANT_PROFILE_FILE:-}" ]]; then
		if [[ -r "$CHAT_ASSISTANT_PROFILE_FILE" && -f "$CHAT_ASSISTANT_PROFILE_FILE" ]]; then
			NR_PROFILE_FILE="$(cd "$(dirname "$CHAT_ASSISTANT_PROFILE_FILE")" && pwd)/$(basename "$CHAT_ASSISTANT_PROFILE_FILE")"
			export NR_PROFILE_FILE
		else
			warn "CHAT_ASSISTANT_PROFILE_FILE is not readable; the assistant gets no file profile."
		fi
	fi
	if [[ -n "${LOG_AUDIT_FILE:-}" ]]; then
		warn "LOG_AUDIT_FILE is ignored by the container: audit.log is written under $NR_LOG_DIR."
	fi
}
```

`id` and `basename` are coreutils and are on `SYSTEM_PATH`.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest server/tests/test_startup_script.py -q -p no:cacheprovider` → Expected: all pass.
Run: `bash -n startup.sh` → Expected: no output (syntax OK).

- [ ] **Step 6: Commit** (after approval)

```bash
git add startup.sh server/tests/startup_harness.py server/tests/test_startup_script.py
git commit -m "feat(startup): helpers for the backend container — compose overlays, host-path NR_* exports, LOCAL_KEYCLOAK rule, Docling build flag, python-free secrets, single-user .env; drop the no-op DOCLING_ENABLED line"
```

---

### Task 5: `start`, `stop`, `rebuild`, `logs`, `enable-autostart`, and `up` in the container

**Files:**
- Modify: `startup.sh` (`wait_for_backend`, `cmd_start`, `cmd_rebuild`, `cmd_logs`, `cmd_enable_autostart`, `cmd_up` container path, `cmd_up_venv` (today's `cmd_up` renamed), `usage`, `main`)
- Test: `server/tests/test_startup_script.py` (append)

**Interfaces:**
- Consumes: the Task 4 helpers.
- Produces: commands `start`, `stop` (`down` stays an alias), `rebuild`, `logs`, `enable-autostart`; `BACKEND_RUNTIME=container|venv` (default `container`) on `up` / `up-with-dev-auth`.

- [ ] **Step 1: Write the failing tests**

Append:

```python
def _stubbed(tmp_path, extra=(), codes=None):
    calls = tmp_path / "calls"
    names = ["podman", "podman-compose", "curl", "loginctl", "systemctl", "npm", "node", *extra]
    bindir = stub_bin(tmp_path / "bin", names, calls, codes)
    # node -v must print a supported version for check_node_version.
    (bindir / "node").write_text(f'#!/bin/sh\necho "node $*" >> "{calls}"\necho v22.12.0\n')
    return calls, f"{bindir}:{SYSTEM_PATH}"


# PORT=1: check_backend_port_free must not see a real backend on :8000.
ENGINE = {"CONTAINER_ENGINE": "podman", "PORT": "1"}


def test_start_runs_the_stack_detached_with_keycloak_for_the_local_rig(proj, tmp_path):
    calls, path = _stubbed(tmp_path)
    run(proj, "ensure_env_file dev", path)
    out = run(proj, "cmd_start", path, ENGINE)
    assert out.returncode == 0, out.stderr
    log = calls.read_text()
    assert "up -d postgres searxng" in log
    assert "up -d keycloak" in log
    assert re.search(r"podman-compose .* build backend", log)
    assert re.search(r"podman-compose .* up -d backend", log)
    assert "compose.dev.yml" not in log          # live mode runs the baked code
    assert "/v1/health" in log                   # waited for the backend


def test_start_skips_keycloak_for_an_external_idp(proj, tmp_path):
    calls, path = _stubbed(tmp_path)
    (proj / ".env").write_text("OIDC_ISSUER=https://idp.example.org/realms/x\n")
    assert run(proj, "cmd_start", path, ENGINE).returncode == 0
    assert "up -d keycloak" not in calls.read_text()


def test_rebuild_touches_only_the_backend(proj, tmp_path):
    calls, path = _stubbed(tmp_path)
    assert run(proj, "cmd_rebuild", path, ENGINE).returncode == 0
    log = calls.read_text()
    assert re.search(r"build backend", log)
    assert re.search(r"up -d --force-recreate --no-deps backend", log)
    assert "postgres" not in log and "keycloak" not in log


def test_logs_follows_the_backend(proj, tmp_path):
    calls, path = _stubbed(tmp_path)
    run(proj, "cmd_logs", path, ENGINE)
    assert re.search(r"logs -f backend", calls.read_text())


def test_enable_autostart_on_podman_prints_and_enables(proj, tmp_path):
    calls, path = _stubbed(tmp_path)
    out = run(proj, "cmd_enable_autostart", path, {**ENGINE, "USER": "sam"})
    assert out.returncode == 0, out.stderr
    log = calls.read_text()
    assert "loginctl enable-linger sam" in log
    assert "systemctl --user enable --now podman-restart.service" in log
    assert "enable-linger" in out.stdout and "podman-restart.service" in out.stdout


def test_enable_autostart_on_docker_changes_nothing(proj, tmp_path):
    calls, path = _stubbed(tmp_path, extra=["docker"])
    out = run(proj, "cmd_enable_autostart", path, {"CONTAINER_ENGINE": "docker", "PORT": "1"})
    assert out.returncode == 0
    assert "loginctl" not in calls.read_text() and "systemctl" not in calls.read_text()
    assert "restart" in out.stdout


def test_up_runs_the_working_tree_with_sign_in_off(proj, tmp_path):
    calls, path = _stubbed(tmp_path)
    out = run(proj, "trap - EXIT; cmd_up ''", path, ENGINE)
    assert out.returncode == 0, out.stderr
    log = calls.read_text()
    assert re.search(r"-f compose\.dev\.yml .*up -d backend", log)
    assert re.search(r"logs -f backend", log)
    assert "up -d keycloak" not in log


def test_up_with_dev_auth_writes_the_rig_env_and_starts_keycloak(proj, tmp_path):
    calls, path = _stubbed(tmp_path)
    out = run(proj, "trap - EXIT; cmd_up dev-auth", path, ENGINE)
    assert out.returncode == 0, out.stderr
    assert (proj / ".env").exists()
    assert "up -d keycloak" in calls.read_text()


def test_backend_runtime_venv_keeps_the_old_path(proj, tmp_path):
    calls, path = _stubbed(tmp_path)
    out = run(proj, "cmd_up ''", path, {**ENGINE, "BACKEND_RUNTIME": "venv"})
    # No .venv in the temp project: the venv path refuses exactly as before.
    assert out.returncode != 0 and "init" in out.stderr
    assert not calls.exists() or "backend" not in calls.read_text()


def test_unknown_backend_runtime_is_refused(proj, tmp_path):
    calls, path = _stubbed(tmp_path)
    out = run(proj, "cmd_up ''", path, {**ENGINE, "BACKEND_RUNTIME": "docker"})
    assert out.returncode != 0 and "BACKEND_RUNTIME" in out.stderr
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest server/tests/test_startup_script.py -q -p no:cacheprovider -k "start or rebuild or logs or autostart or up_ or runtime"`
Expected: FAIL (`cmd_start: command not found`, …).

- [ ] **Step 3: Implement in `startup.sh`**

Add after `wait_for_keycloak`:

```bash
# True when a GET to $1 succeeds. Without curl or wget there is no way to
# check, so report success and let the caller's next step surface problems.
http_ok() {
	if command -v curl >/dev/null 2>&1; then
		curl -sf -o /dev/null "$1"
	elif command -v wget >/dev/null 2>&1; then
		wget -q -O /dev/null "$1"
	else
		return 0
	fi
}

# Poll the backend's health route. The first start loads the 300 MB voice
# model, which takes a while on CPU.
wait_for_backend() {
	local url="http://127.0.0.1:${PORT:-8000}/v1/health" i
	log "Waiting for the backend at $url ..."
	for ((i = 1; i <= 90; i++)); do
		if http_ok "$url"; then
			log "Backend is ready."
			return 0
		fi
		sleep 2
	done
	warn "Backend not answering after 180s — see '$0 logs'."
	return 1
}

# Postgres + SearXNG, then (when this deployment's IdP is the compose one)
# the keycloak schema and Keycloak. Shared by `start` and the container `up`.
start_services() {
	local engine="$1" with_keycloak="$2"
	ensure_searxng_config
	log "Starting containers ($engine): $POSTGRES_SERVICE searxng"
	compose "$engine" up -d "$POSTGRES_SERVICE" searxng
	wait_for_postgres "$engine"
	if [[ "$with_keycloak" == "1" ]]; then
		ensure_keycloak_schema "$engine"
		log "Starting containers ($engine): $KEYCLOAK_SERVICE"
		compose "$engine" up -d "$KEYCLOAK_SERVICE"
		wait_for_keycloak
	fi
}

# Live mode: everything detached, the backend running the code baked into
# its image. Restarts with the machine after `enable-autostart`.
cmd_start() {
	local engine
	engine="$(resolve_engine)"
	load_env_file
	export_backend_env
	check_backend_port_free
	local kc=0
	local_keycloak_wanted && kc=1
	start_services "$engine" "$kc"
	log "Building the backend image (Docling: $NR_WITH_DOCLING)"
	compose "$engine" build "$BACKEND_SERVICE"
	compose "$engine" up -d "$BACKEND_SERVICE"
	wait_for_backend
	log "Running (live mode). Logs: '$0 logs' · stop: '$0 stop' · after code changes: '$0 rebuild' · survive reboots: '$0 enable-autostart'"
}

# Rebuild the image and recreate only the backend; Postgres, SearXNG and
# Keycloak keep running untouched.
cmd_rebuild() {
	local engine
	engine="$(resolve_engine)"
	load_env_file
	export_backend_env
	log "Rebuilding the backend image (Docling: $NR_WITH_DOCLING)"
	compose "$engine" build "$BACKEND_SERVICE"
	compose "$engine" up -d --force-recreate --no-deps "$BACKEND_SERVICE"
	wait_for_backend
}

cmd_logs() {
	local engine
	engine="$(resolve_engine)"
	compose "$engine" logs -f "$BACKEND_SERVICE"
}

# Podman: user containers come back after a reboot only with lingering on
# (user services run without a login session) and podman-restart.service
# enabled (it starts restart-policy=always containers). Docker's daemon does
# this itself.
cmd_enable_autostart() {
	local engine
	engine="$(resolve_engine)"
	if [[ "$engine" == "docker" ]]; then
		log "Docker restarts 'restart: always' containers when its daemon starts — nothing to change here. Make sure the daemon starts at boot (e.g. systemctl enable docker)."
		return 0
	fi
	log "Running: loginctl enable-linger $USER  (your user services keep running without a login session)"
	loginctl enable-linger "$USER"
	log "Running: systemctl --user enable --now podman-restart.service  (restarts restart-policy=always containers at boot)"
	systemctl --user enable --now podman-restart.service
	log "Done. A stack started with '$0 start' now comes back after a reboot."
}
```

Rename today's `cmd_up` to `cmd_up_venv` (body unchanged), and add the new `cmd_up`:

```bash
# Dev: the backend container runs the working tree (compose.dev.yml), in the
# foreground via its logs; Ctrl-C tears the stack down as before.
# BACKEND_RUNTIME=venv runs the old bare-metal backend instead.
cmd_up() {
	local mode="$1"
	case "${BACKEND_RUNTIME:-container}" in
		venv) cmd_up_venv "$mode"; return ;;
		container) ;;
		*) die "BACKEND_RUNTIME must be container or venv (got '${BACKEND_RUNTIME}')." ;;
	esac
	local engine
	engine="$(resolve_engine)"
	if [[ "$mode" == "dev-auth" ]]; then
		ensure_env_file dev
		load_env_file
		[[ -n "${OIDC_ISSUER:-}" ]] || warn "OIDC_ISSUER is not set — login will return 503. Check $ENV_FILE."
		log "Auth ENABLED (OIDC issuer: ${OIDC_ISSUER:-<unset>})"
	else
		load_env_file
		AUTH_ENABLED=false
		export AUTH_ENABLED
		log "Auth DISABLED (single-user dev bypass; loopback bind only)"
	fi
	export_backend_env
	check_backend_port_free
	local kc=0
	[[ "$mode" == "dev-auth" ]] && kc=1
	start_services "$engine" "$kc"
	COMPOSE_DEV=1
	log "Starting the backend container with your working tree mounted"
	compose "$engine" build "$BACKEND_SERVICE"
	compose "$engine" up -d "$BACKEND_SERVICE"
	trap "trap - INT TERM EXIT; log 'Stopping containers ($engine)'; COMPOSE_DEV=1 compose $engine down" INT TERM EXIT
	wait_for_backend || true
	if [[ "$mode" == "dev-auth" ]]; then
		log "Ready — sign in at http://localhost:5173 (Keycloak user: admin-user / password)"
	else
		log "Ready — frontend: npm run dev (then http://localhost:5173)"
	fi
	log "Code edits apply on restart: Ctrl-C and run this again."
	compose "$engine" logs -f "$BACKEND_SERVICE"
}
```

`cmd_down` stays. In `main()` add:

```bash
		start)              cmd_start ;;
		stop | down)        cmd_down ;;
		rebuild)            cmd_rebuild ;;
		logs)               cmd_logs ;;
		enable-autostart)   cmd_enable_autostart ;;
```

(and remove the old separate `down)` line). Update `usage()` to list `start`, `stop` (alias `down`), `rebuild`, `logs`, `enable-autostart`, `BACKEND_RUNTIME=venv` and `LOCAL_KEYCLOAK`, each in one or two lines in the existing style, and update `up`'s description to "the backend container runs your working tree".

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest server/tests/test_startup_script.py -q -p no:cacheprovider` → Expected: all pass.
Run: `bash -n startup.sh` → Expected: no output.

- [ ] **Step 5: Commit** (after approval)

```bash
git add startup.sh server/tests/test_startup_script.py
git commit -m "feat(startup): start/stop/rebuild/logs/enable-autostart; up runs the backend container on the working tree; BACKEND_RUNTIME=venv keeps the bare-metal path"
```

---

### Task 6: `quickstart`

**Files:**
- Modify: `startup.sh` (`check_quickstart_prereqs`, `quickstart_summary`, `cmd_quickstart`, `usage`, `main`)
- Test: `server/tests/test_startup_script.py` (append)

**Interfaces:**
- Consumes: `cmd_start`, `ensure_env_file`, `ensure_searxng_config`, `check_node_version`, `resolve_engine`, `compose`.
- Produces: command `quickstart [--single-user]`.

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_quickstart_dev_end_to_end_with_stubs(proj, tmp_path):
    calls, path = _stubbed(tmp_path)
    out = run(proj, "cmd_quickstart", path, ENGINE)
    assert out.returncode == 0, out.stderr
    log = calls.read_text()
    assert "npm install" in log
    assert "up -d keycloak" in log and "up -d backend" in log
    assert log.rstrip().endswith("npm run dev")          # frontend last, in the foreground
    assert (proj / ".env").exists() and (proj / "searxng/settings.yml").exists()
    assert "admin-user / password" in out.stdout
    assert ".env.example" in out.stdout                   # production pointer
    assert (proj / ".local/container-engine").read_text() == "podman"


def test_quickstart_single_user_skips_keycloak(proj, tmp_path):
    calls, path = _stubbed(tmp_path)
    out = run(proj, "cmd_quickstart --single-user", path, ENGINE)
    assert out.returncode == 0, out.stderr
    assert "AUTH_ENABLED=false" in (proj / ".env").read_text()
    assert "up -d keycloak" not in calls.read_text()
    assert "Sign-in is off" in out.stdout


def test_quickstart_rerun_keeps_env_and_searxng_and_skips_npm_install(proj, tmp_path):
    calls, path = _stubbed(tmp_path)
    (proj / ".env").write_text("MINE=1\n")
    (proj / "searxng").mkdir(exist_ok=True)
    (proj / "searxng/settings.yml").write_text("mine\n")
    (proj / "package-lock.json").write_text("{}")
    (proj / "node_modules").mkdir()
    import time
    time.sleep(0.01)
    (proj / "node_modules/.package-lock.json").write_text("{}")
    out = run(proj, "cmd_quickstart", path, ENGINE)
    assert out.returncode == 0, out.stderr
    assert (proj / ".env").read_text() == "MINE=1\n"
    assert (proj / "searxng/settings.yml").read_text() == "mine\n"
    assert "npm install" not in calls.read_text()


def test_quickstart_rejects_an_unknown_option(proj, tmp_path):
    calls, path = _stubbed(tmp_path)
    out = run(proj, "cmd_quickstart --prod", path, ENGINE)
    assert out.returncode != 0 and "--single-user" in out.stderr


def test_quickstart_names_a_missing_container_engine(proj, tmp_path):
    path = str(tools_dir(tmp_path / "min", ["bash", "dirname", "cat"]))
    out = run(proj, "cmd_quickstart", path)
    assert out.returncode != 0 and "docker" in out.stderr and "podman" in out.stderr


def test_quickstart_names_missing_node(proj, tmp_path):
    calls = tmp_path / "calls"
    bindir = stub_bin(tmp_path / "bin", ["podman", "podman-compose", "curl"], calls)
    out = run(proj, "cmd_quickstart", f"{bindir}:{tools_dir(tmp_path / 'min', ['bash', 'dirname', 'cat', 'mkdir'])}", ENGINE)
    assert out.returncode != 0 and "node" in out.stderr
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest server/tests/test_startup_script.py -q -p no:cacheprovider -k quickstart`
Expected: FAIL (`cmd_quickstart: command not found`).

- [ ] **Step 3: Implement**

```bash
# Everything quickstart needs from the host — no Python. Prints the engine.
check_quickstart_prereqs() {
	local engine
	engine="$(resolve_engine)"
	compose "$engine" version >/dev/null 2>&1 \
		|| die "No compose support for $engine — install podman-compose (podman) or the Docker compose plugin."
	check_node_version >&2
	command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 \
		|| die "Neither curl nor wget is installed (needed to check the services came up)."
	printf '%s' "$engine"
}

quickstart_summary() {
	local profile="$1"
	log "Backend is running at http://127.0.0.1:${PORT:-8000} (live mode; it keeps running after this)."
	if [[ "$profile" == "single-user" ]]; then
		log "Sign-in is off (single-user, this machine only)."
	else
		log "Sign in at http://localhost:5173 as admin-user / password"
	fi
	log "Next: '$0 stop' stops everything · '$0 logs' follows the backend · '$0 enable-autostart' brings it back after reboots"
	log "Production? Start from .env.example and docs/DEPLOYMENT.md — quickstart only writes a local dev .env."
}

# Fresh clone → running app. Each step is skipped when already done, so a
# re-run resumes after a failure. Ends by running the frontend dev server in
# the foreground; Ctrl-C stops only the frontend.
cmd_quickstart() {
	local profile="dev"
	case "${1:-}" in
		"") ;;
		--single-user) profile="single-user" ;;
		*) die "Unknown option '$1' (quickstart takes only --single-user)." ;;
	esac
	local engine
	engine="$(check_quickstart_prereqs)"
	mkdir -p "$(dirname "$ENGINE_STATE_FILE")"
	printf '%s' "$engine" >"$ENGINE_STATE_FILE"
	ensure_env_file "$profile"
	ensure_searxng_config
	if [[ -f node_modules/.package-lock.json && node_modules/.package-lock.json -nt package-lock.json ]]; then
		log "Frontend dependencies are up to date."
	else
		log "Installing frontend dependencies (npm install)"
		npm install
	fi
	CONTAINER_ENGINE="$engine" cmd_start
	quickstart_summary "$profile"
	log "Starting the frontend dev server — Ctrl-C stops only the frontend; '$0 stop' stops the rest."
	exec npm run dev
}
```

In `main()` add `quickstart)  cmd_quickstart "${1:-}" ;;`, and in `usage()` add `quickstart [--single-user]` at the top of the command list with a two-line description.

The step order differs slightly from spec §6 (npm install runs before the image build, inside `cmd_start`). It's equivalent, because each step is independent and skipped when done. No ruling needed.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest server/tests/test_startup_script.py -q -p no:cacheprovider` → Expected: all pass.

- [ ] **Step 5: Commit** (after approval)

```bash
git add startup.sh server/tests/test_startup_script.py
git commit -m "feat(startup): quickstart — clone to running app in one command (dev or --single-user), resumable"
```

---

### Task 7: Slow image test (opt-in)

**Files:**
- Modify: `pytest.ini`
- Create: `server/tests/test_container_image.py`

- [ ] **Step 1: Register the marker and deselect it by default**

`pytest.ini` becomes:

```ini
[pytest]
pythonpath = .
asyncio_mode = auto
testpaths = server/tests
markers =
    container: builds and runs the backend image (slow; run with -m container)
addopts = -m "not container"
```

- [ ] **Step 2: Write the test**

```python
# server/tests/test_container_image.py
"""Build the backend image and run it: the baked model loads, TTS works, and
files it writes on a bind mount belong to the invoking user. Slow (first build
downloads ~1.3 GB): pytest -m container."""
import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from server.tests.dbutil import TEST_URL

pytestmark = pytest.mark.container
REPO = Path(__file__).resolve().parents[2]
ENGINE = shutil.which("podman") or shutil.which("docker")
IMAGE = "natural-reader-backend:test"
NAME = "nr-container-test"
PORT = 18099


def _engine(*args, **kw):
    env = {k: v for k, v in os.environ.items() if k != "XDG_DATA_HOME"}
    return subprocess.run([ENGINE, *args], cwd=REPO, env=env, capture_output=True, text=True, **kw)


@pytest.fixture(scope="module")
def image():
    if not ENGINE:
        pytest.skip("no container engine")
    uid = os.getuid() or 1000
    out = _engine("build", "-f", "Containerfile", "--build-arg", f"APP_UID={uid}", "-t", IMAGE, ".", timeout=3600)
    assert out.returncode == 0, out.stderr[-3000:]
    return IMAGE


def test_image_serves_tts_and_writes_files_as_the_user(image, tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    userns = ["--userns", "keep-id"] if os.path.basename(ENGINE) == "podman" else []
    _engine("rm", "-f", NAME)
    out = _engine("run", "-d", "--name", NAME, "--network", "host", *userns,
                  "-e", f"DATABASE_URL={TEST_URL}", "-e", "AUTH_ENABLED=false",
                  "-e", f"PORT={PORT}", "-v", f"{logs}:/app/logs", image)
    assert out.returncode == 0, out.stderr
    try:
        base = f"http://127.0.0.1:{PORT}"
        for _ in range(90):
            try:
                if httpx.get(f"{base}/v1/health", timeout=2).json().get("model_loaded"):
                    break
            except httpx.HTTPError:
                pass
            time.sleep(2)
        else:
            pytest.fail("backend never became healthy:\n" + _engine("logs", NAME).stdout[-3000:])
        r = httpx.post(f"{base}/v1/synthesize", json={"text": "Hello from the container."}, timeout=120)
        assert r.status_code == 200, r.text
        assert len(r.json()["audio_base64"]) > 1000
        server_log = logs / "server.log"
        assert server_log.exists()
        assert server_log.stat().st_uid == os.getuid()
    finally:
        _engine("rm", "-f", NAME)
```

- [ ] **Step 3: Verify the default run deselects it and the opt-in run passes**

Run: `.venv/bin/pytest server/tests -q -p no:cacheprovider 2>&1 | tail -1` → Expected: `… passed, … skipped, 1 deselected` with 0 failed.
Run: `.venv/bin/pytest -m container -q -p no:cacheprovider 2>&1 | tail -1` → Expected: `1 passed` (Postgres on 5433 is optional; the backend starts without it).

- [ ] **Step 4: Commit** (after approval)

```bash
git add pytest.ini server/tests/test_container_image.py
git commit -m "test(container): opt-in image test — model loads, TTS answers, bind-mounted files belong to the user"
```

---

### Task 8: Docs

**Files:** `README.md`, `docs/DEPLOYMENT.md`, `.env.example`, `CHANGELOG.md`

- [ ] **Step 1: README "🚀 Getting Started"**

Insert directly under `## 🚀 Getting Started`:

````markdown
### Quickstart (Linux, macOS)

```bash
git clone https://github.com/<owner>/natural-reader && cd natural-reader
./startup.sh quickstart            # or: ./startup.sh quickstart --single-user
```

It checks for a container engine (podman or Docker, with compose) and Node 20.19+/22.12+. **No host Python is needed:** the backend runs in a container. It writes a local-dev `.env` (never touching an existing one), builds the backend image, starts Postgres, Keycloak, SearXNG and the backend, then the frontend dev server. Open <http://localhost:5173> and sign in as `admin-user` / `password`. `--single-user` turns sign-in off and skips Keycloak.

| Command | What it does |
|---|---|
| `./startup.sh start` | live mode: the whole stack, detached, the backend running the code baked into its image |
| `./startup.sh stop` | stop and remove the containers (data stays in volumes and `data/`) |
| `./startup.sh rebuild` | rebuild the backend image after code changes; recreate only the backend |
| `./startup.sh logs` | follow the backend's output |
| `./startup.sh enable-autostart` | podman: come back after a reboot (enables lingering and `podman-restart.service`) |
| `./startup.sh up` / `up-with-dev-auth` | dev: the backend container runs your working tree; Ctrl-C stops everything |
| `BACKEND_RUNTIME=venv ./startup.sh up` | the bare-metal backend (needs `./startup.sh init` and Python 3.12–3.13) |

**Windows** uses the bare-metal flow (`startup.ps1` / `startup.cmd`, Python 3.12–3.13) described below; the container commands are for Linux and macOS.
````

Leave the existing sections below as the bare-metal reference, and add one line at the top of "### 1. Clone & Install Dependencies": `> Bare-metal setup (Windows, or BACKEND_RUNTIME=venv). On Linux/macOS, the Quickstart above is the shorter path.`

- [ ] **Step 2: `docs/DEPLOYMENT.md`**

Add a section `## Running the backend in a container` after the topology table, with these subsections:
- **Live mode:** `start`, `rebuild` to ship, `logs`, `stop`; rollback with `stop` then `BACKEND_RUNTIME=venv ./startup.sh up-with-dev-auth`.
- **Surviving reboots:** podman `enable-autostart` (linger + `podman-restart.service`, which only restarts `restart: always` containers); Docker's daemon.
- **Host networking:** why (four loopback clients plus the shared OIDC issuer); nginx unchanged at `127.0.0.1:8000`; Docker Desktop on macOS needs host networking switched on (recent versions, opt-in).
- **Paths are host paths:** `DOC_STORAGE_DIR` / `PDF_STORAGE_DIR` / `LOG_DIR` / `CHAT_ASSISTANT_PROFILE_FILE` name host locations. `startup.sh` mounts them; running compose by hand needs `NR_DATA_DIR` / `NR_LOG_DIR` / `NR_PROFILE_FILE` set to them. `LOG_AUDIT_FILE` is ignored (audit.log goes under `LOG_DIR`).
- **Keycloak:** `LOCAL_KEYCLOAK=auto|true|false`, and the `auto` rule.
- **Docling:** `DOCLING_ENABLED` in `.env` selects the image variant at `start` / `rebuild`; CPU-only torch.

- [ ] **Step 3: `.env.example`**

Near the storage settings add:

```
# Container note: DOC_STORAGE_DIR / LOG_DIR / CHAT_ASSISTANT_PROFILE_FILE are HOST
# paths; ./startup.sh mounts them into the backend container. LOG_AUDIT_FILE is
# ignored in the container (audit.log is written under LOG_DIR).
```

Near the Docling setting add `# DOCLING_ENABLED=true also builds the container image with Docling (./startup.sh rebuild).`. Near the Keycloak/`KC_*` block add:

```
# Whether ./startup.sh start launches the compose Keycloak: auto (default) = yes
# when OIDC_ISSUER is http://localhost:18080/… or KC_HOSTNAME is set; false for an
# external IdP.
# LOCAL_KEYCLOAK=auto
```

- [ ] **Step 4: `CHANGELOG.md` `[Unreleased]`**

- **Added:** the backend container (`Containerfile`, compose `backend` service, host networking, bind-mounted `data/` and `logs/`); `./startup.sh quickstart`; `start` / `stop` / `rebuild` / `logs` / `enable-autostart`; `LOCAL_KEYCLOAK`; the opt-in `pytest -m container`.
- **Changed:** `up` / `up-with-dev-auth` run the backend container on the working tree (`BACKEND_RUNTIME=venv` for the old path); Docling moved to `requirements-docling.txt`; all compose services use `restart: always`; generated secrets are 64 hex characters, created without Python.
- **Removed:** the `DOCLING_ENABLED=true` line in `startup.sh`. It was never exported, so it never reached the backend; `.env` controls Docling.
- **Upgrade notes:**
  - `./startup.sh stop` (or `down`) before switching an existing install;
  - the first `start` builds the image (~1.3 GB of downloads, more with Docling);
  - existing `data/`, `logs/` and `.env` are used as they are.

- [ ] **Step 5: Check the docs carry no real hostnames**

Run: `git diff -- README.md docs/DEPLOYMENT.md .env.example CHANGELOG.md | grep -inE "https?://[a-z0-9.-]+\.(net|org|io|com)" | grep -viE "example\.(com|org)|github\.com|localhost|pytorch\.org|keycloak\.org"`
Expected: no output.

- [ ] **Step 6: Commit** (after approval)

```bash
git add README.md docs/DEPLOYMENT.md .env.example CHANGELOG.md
git commit -m "docs: quickstart, container commands, live mode and autostart, host-path semantics, LOCAL_KEYCLOAK; Windows stays bare-metal"
```

---

### Task 9: Gate and fresh-clone walk

**Files:** `docs/superpowers/handoff/2026-10-07-backend-container/HANDOFF.md` (create)

- [ ] **Step 1: Gate**

Run the full gate:

```bash
.venv/bin/pytest server/tests -q -p no:cacheprovider
npx vitest run
npx eslint .
.venv/bin/pytest -m container -q -p no:cacheprovider
bash -n startup.sh
```

Expected: 0 failed; eslint clean; the container test passes.

- [ ] **Step 2: Walk preconditions**

- The user's real stack is stopped: `podman ps --format '{{.Names}}'` shows none of `natural-reader-*`.
- Ports 5433, 8000, 18080, 18043 and 5173 are free.
- If podman shows the post-upgrade netns error, stop and ask the user to reinstall podman first.

- [ ] **Step 3: Fresh clone and quickstart**

```bash
W=$(mktemp -d)/nr-walk && git clone -q --branch feat/backend-container "$PWD" "$W" && cd "$W" && ./startup.sh quickstart
```

Run it in the background with its output captured. The clone's `.env` is the generated dev one, and its compose project is `nr-walk` (directory name), with its own `pgdata` volume, so the realm is a fresh import.

- [ ] **Step 4: Journeys in the browser at `http://localhost:5173`**

| # | Journey | Control | Pass when |
|---|---|---|---|
| 1 | Sign in | Sign in → Keycloak form, `admin-user` / `password` | the app shell loads as admin |
| 2 | Upload | Reader → open a small PDF | it appears in Library; the file exists under the clone's `data/pdfs` and is owned by the user |
| 3 | Read aloud | play | audio plays (TTS from the baked model) |
| 4 | Project | Library → New project | the card shows |
| 5 | Log out, sign in | profile menu → Log out → Sign in | Keycloak asks for the password |
| 6 | Survive the terminal | close the quickstart process (the frontend stops) | `curl -sf http://127.0.0.1:8000/v1/health` still answers |
| 7 | Restart policy | `podman kill natural-reader-backend`, wait 10 s | it is running again (`restart: always`) |
| 8 | Rebuild | change a log string in the clone's `server/`, `./startup.sh rebuild` | only the backend was recreated; the new string appears in `./startup.sh logs` |
| 9 | Dev mode | `./startup.sh stop`; `./startup.sh up-with-dev-auth` | the backend runs the working tree; Ctrl-C stops everything |

`enable-autostart` is verified by reading its printed commands only. It changes the user's systemd settings, so it runs on the real machine only with the user's say-so.

- [ ] **Step 5: Cleanup**

In the clone: `./startup.sh stop`; remove the clone's volumes (`podman volume rm nr-walk_pgdata nr-walk_cache`) and image tag if created; delete the clone directory. Confirm `podman ps -a` shows no `natural-reader-*` containers from the walk.

- [ ] **Step 6: Handoff**

Write `HANDOFF.md` containing:
- the gate table and the walk table (each journey: control, result);
- what is API-only or env-only (expected: nothing new);
- rulings;
- deferred minors;
- the open step: switching the user's live site with `./startup.sh start` on their real `.env`, which the user decides.

- [ ] **Step 7: Commit** (after approval)

```bash
git add docs/superpowers/handoff/2026-10-07-backend-container/HANDOFF.md
git commit -m "docs(handoff): backend container gate and fresh-clone walk"
```
