"""Helper model phase 1, Task 1: the `helper` setting of the models section (spec 2026-11-15 §4.1).
Empty means off: nothing generates. The admin's value, "" included, wins over HELPER_MODEL."""
from __future__ import annotations

import pytest

from server.services import console_settings, model_settings


@pytest.fixture(autouse=True)
def _fresh_warnings():
    console_settings._warned.clear()


def test_helper_defaults_off():
    ms = model_settings.from_env({})
    assert (ms.helper, ms.helper_source) == ("", "default")


def test_helper_from_env():
    ms = model_settings.from_env({"HELPER_MODEL": " ollama:gemma4:e4b "})
    assert (ms.helper, ms.helper_source) == ("ollama:gemma4:e4b", "env")
    assert model_settings.from_env({"HELPER_MODEL": "off"}).helper == ""
    assert model_settings.from_env({"HELPER_MODEL": "two words"}).helper == ""   # unusable: the default, off


def test_admin_value_wins_over_env():
    section = model_settings.SECTION
    env = {"HELPER_MODEL": "ollama:gemma4:e4b"}
    values = console_settings._resolve(section, {"helper": "ollama:qwen3:8b"}, env)
    assert (values["helper"].value, values["helper"].source) == ("ollama:qwen3:8b", "admin")
    off = console_settings._resolve(section, {"helper": ""}, env)               # the admin's Off beats the env
    assert (off["helper"].value, off["helper"].source) == ("", "admin")


def test_chat_is_not_a_helper_model():
    # A background job has no conversation, so "the chat's own model" means nothing for the helper.
    with pytest.raises(ValueError):
        model_settings.parse_helper("chat")
    with pytest.raises(ValueError):
        model_settings.parse_helper("two words")
    assert model_settings.parse_helper("Off") == "" and model_settings.parse_helper("") == ""


def test_view_lists_helper():
    values = console_settings._resolve(model_settings.SECTION, {}, {"HELPER_MODEL": "ollama:gemma4:e4b"})
    out = model_settings.view(values, {})
    assert out["helper"]["value"] == "ollama:gemma4:e4b" and out["helper"]["env"] == "HELPER_MODEL"
