"""Wiring guards, tested via the model-free config helper so the suite never
imports server.app (which loads the 325 MB Kokoro model)."""
import pytest

from server.auth.config import startup_guard


def test_dev_bypass_on_public_bind_raises():
    with pytest.raises(RuntimeError):
        startup_guard(auth_enabled=False, bind_host="0.0.0.0")


def test_dev_bypass_on_localhost_ok():
    startup_guard(auth_enabled=False, bind_host="127.0.0.1")  # no raise


def test_auth_enabled_any_host_ok():
    startup_guard(auth_enabled=True, bind_host="0.0.0.0")  # no raise
