import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]


def _lines(path):
    return [l.strip() for l in (REPO / path).read_text().splitlines()
            if l.strip() and not l.strip().startswith("#")]


def test_dockerignore_is_an_allow_list():
    lines = _lines(".dockerignore")
    assert lines[0] == "*"
    included = {l[1:] for l in lines if l.startswith("!")}
    assert included == {"server/", "run.py", "requirements.txt", "requirements-docling.txt", "constraints.txt"}
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


COMPOSE_FILES = ["docker-compose.yml", "compose.podman.yml", "compose.dev.yml", "compose.profile.yml",
                 "compose.speech.yml", "compose.google.yml"]


def _render(tmp_path, env_lines=None, files=("docker-compose.yml",), extra_env=None):
    if not shutil.which("podman-compose"):
        pytest.skip("podman-compose not installed")
    for f in COMPOSE_FILES:
        shutil.copy(REPO / f, tmp_path / f)
    (tmp_path / "searxng").mkdir()
    if env_lines is not None:
        (tmp_path / ".env").write_text("\n".join(env_lines) + "\n")
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


def test_speech_overlay_mounts_the_file_read_only(tmp_path):
    b = _render(tmp_path, env_lines=[], files=("docker-compose.yml", "compose.speech.yml"),
                extra_env={"NR_SPEECH_MODELS_FILE": "/home/u/speech.yaml"})["backend"]
    assert "/home/u/speech.yaml:/app/config/speech-models.yaml:ro" in b["volumes"]
    assert b["environment"]["SPEECH_MODELS_FILE"] == "/app/config/speech-models.yaml"


def test_google_overlay_mounts_the_key_read_only_and_points_the_app_at_it(tmp_path):
    b = _render(tmp_path, env_lines=[], files=("docker-compose.yml", "compose.google.yml"),
                extra_env={"NR_GOOGLE_CREDENTIALS": "/home/u/.config/google.json"})["backend"]
    assert "/home/u/.config/google.json:/app/config/google-credentials.json:ro" in b["volumes"]
    assert b["environment"]["GOOGLE_APPLICATION_CREDENTIALS"] == "/app/config/google-credentials.json"


def test_podman_never_puts_the_keep_id_backend_in_a_pod(tmp_path):
    # podman-compose puts services in a pod by default, and podman refuses
    # --pod together with --userns (keep-id). Capture the real arguments with
    # a fake podman: nothing is created.
    if not shutil.which("podman-compose"):
        pytest.skip("podman-compose not installed")
    for f in COMPOSE_FILES:
        shutil.copy(REPO / f, tmp_path / f)
    (tmp_path / "searxng").mkdir()
    calls = tmp_path / "calls"
    fake = tmp_path / "fake-podman"
    fake.write_text(f'#!/bin/sh\necho "$*" >> "{calls}"\necho "[]"\n')
    fake.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "XDG_DATA_HOME"}
    out = subprocess.run(["podman-compose", "--podman-path", str(fake), "-f", "docker-compose.yml",
                          "-f", "compose.podman.yml", "up", "-d", "--no-build", "backend"],
                         cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    create = [l for l in calls.read_text().splitlines()
              if l.startswith(("create", "run")) and "natural-reader-backend" in l]
    assert create, out.stderr + calls.read_text()
    assert "--userns keep-id" in create[0] or "--userns=keep-id" in create[0]
    assert "--pod" not in create[0]


def test_shell_port_host_and_workers_reach_the_container(tmp_path):
    (tmp_path / "set").mkdir()
    (tmp_path / "unset").mkdir()
    b = _render(tmp_path / "set", env_lines=[], extra_env={"PORT": "8001", "HOST": "127.0.0.2", "WORKERS": "3"})
    env = {k: str(v) for k, v in b["backend"]["environment"].items()}
    assert (env["PORT"], env["HOST"], env["WORKERS"]) == ("8001", "127.0.0.2", "3")
    d = _render(tmp_path / "unset", env_lines=[])
    env = {k: str(v) for k, v in d["backend"]["environment"].items()}
    assert (env["PORT"], env["HOST"], env["WORKERS"]) == ("8000", "127.0.0.1", "1")


def test_keycloak_drops_an_empty_proxy_headers_setting(tmp_path):
    # Keycloak refuses KC_PROXY_HEADERS="" and restarts forever; a fresh
    # clone's .env doesn't set it. The entrypoint unsets it when empty.
    # (deploy/keycloak-entrypoint.sh; test_the_keycloak_entrypoint_never_lets_a_placeholder_through_empty runs it.)
    kc = _render(tmp_path, env_lines=[])["keycloak"]
    assert kc["entrypoint"] == ["/bin/bash", "/opt/keycloak/nr-entrypoint.sh"]
    assert kc["command"][0] == "start-dev"


def test_the_backend_and_keycloak_start_after_postgres_answers(tmp_path):
    # Without the condition they start alongside a Postgres that is still booting. (After a reboot the engine
    # restarts containers on its own, in any order: the backend's background retry covers that, server/db.py.)
    services = _render(tmp_path, env_lines=[])
    for name in ("backend", "keycloak"):
        assert services[name]["depends_on"]["postgres"]["condition"] == "service_healthy", name


def test_keycloak_gets_its_secrets_from_env_and_none_is_written_in_the_file(tmp_path):
    kc = _render(tmp_path, env_lines=["OIDC_CLIENT_SECRET=aaa", "KC_ADMIN_CLIENT_SECRET=bbb",
                                      "KC_SEED_USER_PASSWORD=ccc", "KC_BOOTSTRAP_ADMIN_PASSWORD=ddd"])["keycloak"]
    env = kc["environment"]
    assert (env["OIDC_CLIENT_SECRET"], env["KC_ADMIN_CLIENT_SECRET"], env["KC_SEED_USER_PASSWORD"],
            env["KC_BOOTSTRAP_ADMIN_PASSWORD"]) == ("aaa", "bbb", "ccc", "ddd")
    assert any(str(v).endswith(":/opt/keycloak/nr-entrypoint.sh:ro") for v in kc["volumes"])
    # Unset, they reach the entrypoint empty, and it replaces each with a random value.
    (tmp_path / "bare").mkdir()
    bare = _render(tmp_path / "bare", env_lines=[])["keycloak"]["environment"]
    assert not bare["KC_BOOTSTRAP_ADMIN_PASSWORD"] and not bare["OIDC_CLIENT_SECRET"]


def test_the_realm_file_holds_placeholders_not_secrets():
    import json
    realm = json.loads((REPO / "deploy/keycloak/realm-export.json").read_text())
    secrets = {c["clientId"]: c["secret"] for c in realm["clients"] if "secret" in c}
    assert secrets == {"natural-reader": "${OIDC_CLIENT_SECRET}", "natural-reader-admin": "${KC_ADMIN_CLIENT_SECRET}"}
    values = [c["value"] for u in realm["users"] for c in u.get("credentials", [])]
    assert values == ["${KC_SEED_USER_PASSWORD}"]


def _entrypoint(tmp_path, env):
    kc = tmp_path / "kc.sh"
    kc.write_text('#!/bin/bash\nfor n in OIDC_CLIENT_SECRET KC_ADMIN_CLIENT_SECRET KC_SEED_USER_PASSWORD '
                  'KC_BOOTSTRAP_ADMIN_PASSWORD; do echo "$n=${!n}"; done\n'
                  'echo "PROXY=${KC_PROXY_HEADERS-unset}"; echo "ARGS=$*"\n')
    kc.chmod(0o755)
    out = subprocess.run(["bash", str(REPO / "deploy/keycloak-entrypoint.sh"), "start-dev", "--import-realm"],
                         env={"PATH": os.environ["PATH"], "KC_BIN": str(kc), **env}, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return dict(line.split("=", 1) for line in out.stdout.splitlines()), out.stderr


def test_the_keycloak_entrypoint_never_lets_a_placeholder_through_empty(tmp_path):
    got, warned = _entrypoint(tmp_path, {"OIDC_CLIENT_SECRET": "mine", "KC_PROXY_HEADERS": ""})
    assert got["OIDC_CLIENT_SECRET"] == "mine" and "OIDC_CLIENT_SECRET" not in warned
    randoms = [got[n] for n in ("KC_ADMIN_CLIENT_SECRET", "KC_SEED_USER_PASSWORD", "KC_BOOTSTRAP_ADMIN_PASSWORD")]
    assert all(re.fullmatch(r"[0-9a-f]{64}", r) for r in randoms) and len(set(randoms)) == 3
    assert "KC_SEED_USER_PASSWORD is not set" in warned and not any(r in warned for r in randoms)
    assert got["PROXY"] == "unset" and got["ARGS"] == "start-dev --import-realm"
