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


def test_keycloak_starts_with_the_compose_entrypoint_and_no_proxy_setting(tmp_path):
    """The entrypoint from docker-compose.yml, with KC_PROXY_HEADERS empty as a
    fresh clone renders it: Keycloak must boot, not loop on 'Invalid value'."""
    if not ENGINE:
        pytest.skip("no container engine")
    import yaml
    kc = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]["keycloak"]
    entry = [a.replace("$$", "$") for a in kc["entrypoint"]]
    name = "nr-keycloak-entrypoint-test"
    _engine("rm", "-f", name)
    out = _engine("run", "-d", "--name", name, "--network", "none", "-e", "KC_PROXY_HEADERS=",
                  "--entrypoint", entry[0], kc["image"], *entry[1:], "start-dev")
    assert out.returncode == 0, out.stderr
    try:
        for _ in range(60):
            logs = _engine("logs", name)
            text = logs.stdout + logs.stderr
            if "started in" in text:
                break
            assert "Invalid value" not in text, text[-2000:]
            time.sleep(2)
        else:
            pytest.fail("Keycloak never started:\n" + text[-2000:])
    finally:
        _engine("rm", "-f", name)
