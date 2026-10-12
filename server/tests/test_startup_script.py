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
    assert out.stdout.strip() == f"{proj / 'data/pdfs'}|{proj / 'logs'}"
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
    log = calls.read_text() if calls.exists() else ""
    assert "loginctl" not in log and "systemctl" not in log
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


def _listening_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s


def test_start_again_while_our_backend_runs_is_not_a_port_clash(proj, tmp_path):
    calls, path = _stubbed(tmp_path)
    (tmp_path / "bin" / "podman").write_text(
        f'#!/bin/sh\necho "podman $*" >> "{calls}"\n'
        'case "$*" in *"container inspect"*) echo "true always" ;; esac\n')
    sock = _listening_port()
    try:
        out = run(proj, "cmd_start", path, {"CONTAINER_ENGINE": "podman", "PORT": str(sock.getsockname()[1])})
    finally:
        sock.close()
    assert out.returncode == 0, out.stderr


def test_start_refuses_a_port_held_by_something_else(proj, tmp_path):
    calls, path = _stubbed(tmp_path)
    sock = _listening_port()
    try:
        out = run(proj, "cmd_start", path, {"CONTAINER_ENGINE": "podman", "PORT": str(sock.getsockname()[1])})
    finally:
        sock.close()
    assert out.returncode != 0 and "already in use" in out.stderr


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
    out = run(proj, "cmd_quickstart", f"{bindir}:{tools_dir(tmp_path / 'min', ['bash', 'dirname', 'cat', 'mkdir', 'env'])}", ENGINE)
    assert out.returncode != 0 and "node" in out.stderr


def test_relative_paths_without_dot_slash_become_absolute(proj):
    # compose reads "data/pdfs" as a named volume; the app reads it as a folder.
    out = run(proj, 'export_host_paths; echo "$NR_DATA_DIR|$NR_LOG_DIR"', SYSTEM_PATH,
              {"DOC_STORAGE_DIR": "docs/store", "LOG_DIR": "mylogs"})
    assert out.stdout.strip() == f"{proj / 'docs/store'}|{proj / 'mylogs'}"


def _engine_stubs(tmp_path, scripts):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name, body in scripts.items():
        (bindir / name).write_text("#!/bin/sh\n" + body + "\n")
        (bindir / name).chmod(0o755)
    return f"{bindir}:{SYSTEM_PATH}"


def test_auto_detect_skips_a_docker_whose_daemon_is_down(proj, tmp_path):
    path = _engine_stubs(tmp_path, {"docker": 'case "$1" in info) exit 1 ;; esac', "podman": "exit 0"})
    assert run(proj, "resolve_engine", path).stdout.strip() == "podman"
    path = _engine_stubs(tmp_path, {"docker": "exit 0", "podman": "exit 0"})
    assert run(proj, "resolve_engine", path).stdout.strip() == "docker"


def test_docker_compose_v1_is_refused_with_an_upgrade_hint(proj, tmp_path):
    path = _engine_stubs(tmp_path, {
        "docker": 'case "$1" in compose) exit 1 ;; esac',
        "docker-compose": 'case "$*" in *version*) echo "docker-compose version 1.29.2, build unknown" ;; esac'})
    out = run(proj, "compose docker ps", path)
    assert out.returncode != 0 and "2.24" in out.stderr


def test_quickstart_names_missing_compose_instead_of_exiting_silently(proj, tmp_path):
    path = _engine_stubs(tmp_path, {"docker": 'case "$1" in compose) exit 1 ;; esac'})
    out = run(proj, "cmd_quickstart", path, {"CONTAINER_ENGINE": "docker", "PORT": "1"})
    assert out.returncode != 0 and "compose" in out.stderr.lower()


def _dev_backend_running(tmp_path, calls):
    (tmp_path / "bin" / "podman").write_text(
        f'#!/bin/sh\necho "podman $*" >> "{calls}"\n'
        'case "$*" in *"container inspect"*) echo "true no" ;; esac\n')


def test_start_refuses_while_a_dev_up_owns_the_backend(proj, tmp_path):
    calls, path = _stubbed(tmp_path)
    _dev_backend_running(tmp_path, calls)
    out = run(proj, "cmd_start", path, ENGINE)
    assert out.returncode != 0 and "Ctrl-C" in out.stderr
    assert "up -d" not in calls.read_text()


def test_rebuild_refuses_while_a_dev_up_owns_the_backend(proj, tmp_path):
    calls, path = _stubbed(tmp_path)
    _dev_backend_running(tmp_path, calls)
    out = run(proj, "cmd_rebuild", path, ENGINE)
    assert out.returncode != 0 and "Ctrl-C" in out.stderr
    assert "--force-recreate" not in calls.read_text()


def test_up_sets_its_teardown_trap_before_the_build(proj, tmp_path):
    # Ctrl-C during a long first build must still stop the services it started.
    calls, path = _stubbed(tmp_path, codes={"podman-compose": 0})
    (tmp_path / "bin" / "podman-compose").write_text(
        f'#!/bin/sh\necho "podman-compose $*" >> "{calls}"\n'
        'case "$*" in *" build "*) exit 3 ;; esac\n')
    out = run(proj, "cmd_up ''", path, ENGINE)
    assert out.returncode != 0
    assert calls.read_text().rstrip().endswith("down")


def test_backend_runtime_venv_can_live_in_the_env_file(proj, tmp_path):
    # Bare metal by default for this checkout, without prefixing every command.
    calls, path = _stubbed(tmp_path)
    (proj / ".env").write_text("BACKEND_RUNTIME=venv\n")
    out = run(proj, "cmd_up ''", path, ENGINE)
    # No .venv in the temp project: the venv path refuses exactly as before.
    assert out.returncode != 0 and "init" in out.stderr
    assert not calls.exists() or "backend" not in calls.read_text()


def test_the_shell_overrides_backend_runtime_in_the_env_file(proj, tmp_path):
    calls, path = _stubbed(tmp_path)
    (proj / ".env").write_text("BACKEND_RUNTIME=venv\n")
    out = run(proj, "trap - EXIT; cmd_up ''", path, {**ENGINE, "BACKEND_RUNTIME": "container"})
    assert out.returncode == 0, out.stderr
    assert re.search(r"up -d backend", calls.read_text())
