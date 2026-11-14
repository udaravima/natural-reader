"""The two client secrets this repository used to ship in its realm file are public. A deployment still
using one is told so at start and in the admin console."""
import hashlib
import json
import logging

from server.auth import config as auth_config
from server.auth.config import load_auth_config, published_secrets, warn_published_secrets
from server.services.deployment_info import readonly_rows

OLD_OIDC = "natural-reader" + "-dev-secret"            # built here so the literal isn't in the tree twice
OLD_ADMIN = "natural-reader-admin" + "-dev-secret"


def test_the_old_values_are_recognised_by_hash_only():
    assert {hashlib.sha256(v.encode()).hexdigest() for v in (OLD_OIDC, OLD_ADMIN)} == auth_config.PUBLISHED_SECRET_HASHES
    cfg = load_auth_config({"OIDC_CLIENT_SECRET": OLD_OIDC, "KC_ADMIN_CLIENT_SECRET": OLD_ADMIN})
    assert published_secrets(cfg) == ["OIDC_CLIENT_SECRET", "KC_ADMIN_CLIENT_SECRET"]


def test_other_values_and_unset_ones_are_fine():
    assert published_secrets(load_auth_config({"OIDC_CLIENT_SECRET": "x" * 40})) == []
    assert published_secrets(load_auth_config({})) == []


def test_start_up_says_which_variable_to_rotate_and_never_the_value(caplog):
    cfg = load_auth_config({"KC_ADMIN_CLIENT_SECRET": OLD_ADMIN, "OIDC_CLIENT_SECRET": "x" * 40})
    with caplog.at_level(logging.ERROR, logger="server.auth.config"):
        assert warn_published_secrets(cfg) == ["KC_ADMIN_CLIENT_SECRET"]
    assert "KC_ADMIN_CLIENT_SECRET" in caplog.text and "rotate" in caplog.text.lower()
    assert OLD_ADMIN not in caplog.text


def test_the_console_marks_a_published_secret():
    rows = {r["id"]: r for r in readonly_rows({"OIDC_CLIENT_SECRET": OLD_OIDC, "KC_ADMIN_CLIENT_SECRET": "y" * 40})}
    assert rows["oidc_client_secret"]["value"] == "set, but to a published default: rotate it"
    assert rows["kc_admin_client_secret"]["value"] == "set"
    assert OLD_OIDC not in json.dumps(list(rows.values()))


def test_the_app_warns_at_start():
    from pathlib import Path
    assert "warn_published_secrets" in (Path(__file__).parents[1] / "app.py").read_text()
