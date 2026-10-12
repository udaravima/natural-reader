"""Wiring guards, tested via the model-free config helper so the suite never
imports server.app (which loads the 325 MB Kokoro model)."""
import ast
from pathlib import Path

import pytest

from server.auth.config import startup_guard

STRONG = "s" * 40  # >= MIN_SESSION_SECRET_LEN


def test_dev_bypass_on_public_bind_raises():
    with pytest.raises(RuntimeError):
        startup_guard(auth_enabled=False, bind_host="0.0.0.0", session_secret=STRONG)


def test_dev_bypass_on_localhost_ok():
    startup_guard(auth_enabled=False, bind_host="127.0.0.1", session_secret=None)  # no raise


def test_auth_enabled_public_bind_with_strong_secret_ok():
    startup_guard(auth_enabled=True, bind_host="0.0.0.0", session_secret=STRONG)  # no raise


def test_missing_session_secret_on_public_bind_raises():
    # An exposed server with no SESSION_SECRET would fall back to a known signing
    # key → forgeable session cookies. Refuse to boot.
    with pytest.raises(RuntimeError):
        startup_guard(auth_enabled=True, bind_host="0.0.0.0", session_secret=None)


def test_short_session_secret_on_public_bind_raises():
    with pytest.raises(RuntimeError):
        startup_guard(auth_enabled=True, bind_host="0.0.0.0", session_secret="tooshort")


def test_missing_session_secret_on_loopback_ok():
    # Local dev convenience: no SESSION_SECRET is fine on a loopback bind, where
    # the app uses an ephemeral per-process secret instead.
    startup_guard(auth_enabled=True, bind_host="127.0.0.1", session_secret=None)  # no raise


def test_create_app_installs_the_shared_exception_handlers():
    # Read, not imported (see above): create_app must call install_handlers,
    # or a DeadlockDetected reaches the caller as a 500 instead of 503 busy.
    tree = ast.parse((Path(__file__).parents[1] / "app.py").read_text())
    create_app = next(n for n in tree.body
                      if isinstance(n, ast.FunctionDef) and n.name == "create_app")
    calls = {n.func.id for n in ast.walk(create_app)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "install_handlers" in calls
