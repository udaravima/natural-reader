"""v2.4 Task F: a user's context size and keep-alive are clamped to limits the
deployment sets — on a shared Ollama they decide everyone's RAM."""
import logging

import pytest

from server.services.inference_limits import InferenceLimits, clamp, limits_json, load_inference_limits


def test_defaults_and_no_limit():
    assert load_inference_limits({}) == InferenceLimits(num_ctx_max=32768, keep_alive_max_s=1800)
    assert load_inference_limits({"INFERENCE_NUM_CTX_MAX": "0", "INFERENCE_KEEP_ALIVE_MAX": "-1"}) == \
        InferenceLimits(None, None)
    assert load_inference_limits({"INFERENCE_KEEP_ALIVE_MAX": "2h"}).keep_alive_max_s == 7200
    assert load_inference_limits({"INFERENCE_KEEP_ALIVE_MAX": "600"}).keep_alive_max_s == 600


def test_bad_values_fall_back_to_the_default_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING):
        got = load_inference_limits({"INFERENCE_NUM_CTX_MAX": "lots", "INFERENCE_KEEP_ALIVE_MAX": "soon"})
    assert got == InferenceLimits(32768, 1800)
    assert "INFERENCE_NUM_CTX_MAX" in caplog.text and "INFERENCE_KEEP_ALIVE_MAX" in caplog.text


LIMITS = InferenceLimits(num_ctx_max=32768, keep_alive_max_s=1800)


@pytest.mark.parametrize("num_ctx,keep_alive,want", [
    (65536, None, (32768, None)),          # too big: the cap
    (8192, None, (8192, None)),            # within: unchanged
    (None, -1, (None, 1800)),              # "always": the cap, in seconds
    (None, "-1", (None, 1800)),
    (None, "2h", (None, 1800)),
    (None, 0, (None, None)),               # unload after every reply: everyone reloads; not sent
    (None, "0", (None, None)),
    (None, "10m", (None, "10m")),          # within: sent as given
    (None, 600, (None, 600)),
    (None, "nonsense", (None, None)),      # unreadable: not sent
])
def test_clamping(num_ctx, keep_alive, want):
    assert clamp(num_ctx, keep_alive, LIMITS) == want


def test_no_limits_pass_everything_but_zero_through():
    assert clamp(2_000_000, -1, InferenceLimits(None, None)) == (2_000_000, -1)
    assert clamp(None, 0, InferenceLimits(None, None)) == (None, None)


def test_the_limits_as_the_browser_reads_them():
    assert limits_json(LIMITS) == {"numCtxMax": 32768, "keepAliveMaxS": 1800}
    assert limits_json(InferenceLimits(None, None)) == {"numCtxMax": None, "keepAliveMaxS": None}


def test_the_turn_settings_are_clamped(monkeypatch):
    from server.routers import chat_turns
    monkeypatch.delenv("INFERENCE_NUM_CTX_MAX", raising=False)
    monkeypatch.delenv("INFERENCE_KEEP_ALIVE_MAX", raising=False)
    s = chat_turns.SettingsIn(think="off", num_ctx=65536, keep_alive=-1, num_predict=None)
    got = chat_turns.call_settings(s)
    assert (got.num_ctx, got.keep_alive) == (32768, 1800)
