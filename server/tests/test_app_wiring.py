"""Wiring guards, tested via the model-free config helper so the suite never
imports server.app (which loads the 325 MB Kokoro model)."""
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
