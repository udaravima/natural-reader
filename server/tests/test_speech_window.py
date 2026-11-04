"""The speech window's server side: the scores that tell speech from noise, and the settings the browser listens with."""
from types import SimpleNamespace

import numpy as np

from server.speech import window
from server.speech.local_whisper import LocalWhisper


def _segment(text, no_speech, logprob):
    return SimpleNamespace(text=text, no_speech_prob=no_speech, avg_logprob=logprob)


class _Model:
    def __init__(self, segments):
        self.segments = segments

    def transcribe(self, samples, language=None, **kwargs):
        return iter(self.segments), SimpleNamespace(language=language or "en", duration=1.0)


def _run(segments):
    return LocalWhisper._run(np.zeros(16000, dtype=np.float32), "en", _Model(segments))


def test_the_scores_are_the_most_speech_like_segments():
    got = _run([_segment(" Well", 0.7, -1.4), _segment(" hold on.", 0.04, -0.3), _segment(" Um", 0.2, -0.9)])
    assert got.text == "Well hold on. Um"
    assert (got.no_speech, got.avg_logprob) == (0.04, -0.3)


def test_no_segments_means_no_scores():
    got = _run([])
    assert got.text == ""
    assert (got.no_speech, got.avg_logprob) == (None, None)


def test_the_defaults_are_the_measured_values():
    assert window.settings({}) == {
        "vad_start": 0.5, "vad_stay": 0.35, "min_speech_ms": 250, "pre_roll_ms": 300, "silence_ms": 700,
        "first_clause_chars": 30, "max_no_speech": 0.25, "min_logprob": -1.0, "gate": False, "gate_db": 9,
        "language": "en",
    }


def test_the_gate_is_off_unless_the_deployment_turns_it_on():
    """Off: in a live test it cut a person's turns in half as they sat back (docs/DEPLOYMENT.md)."""
    for raw in ("on", "true", "1", "yes", " On "):
        assert window.settings({"SPEECH_WINDOW_GATE": raw})["gate"] is True
    for raw in ("off", "false", "0", "no", ""):
        assert window.settings({"SPEECH_WINDOW_GATE": raw})["gate"] is False


def test_a_gate_switch_that_is_neither_on_nor_off_keeps_it_off(caplog):
    assert window.settings({"SPEECH_WINDOW_GATE": "sometimes"})["gate"] is False
    assert "SPEECH_WINDOW_GATE" in caplog.text


def test_the_gate_margin_can_be_loosened():
    assert window.settings({"SPEECH_WINDOW_GATE_DB": "15"})["gate_db"] == 15
    assert window.settings({"SPEECH_WINDOW_GATE_DB": "3"})["gate_db"] == 3


def test_a_gate_margin_out_of_range_keeps_the_default(caplog):
    """0 no longer means off (SPEECH_WINDOW_GATE does): below 3 dB the gate would shut out the person themselves."""
    for raw in ("0", "2", "31", "-3"):
        assert window.settings({"SPEECH_WINDOW_GATE_DB": raw})["gate_db"] == 9
    assert "SPEECH_WINDOW_GATE_DB" in caplog.text


def test_a_setting_comes_from_the_environment():
    got = window.settings({"SPEECH_WINDOW_SILENCE_MS": "500", "SPEECH_WINDOW_VAD_START": "0.6",
                           "SPEECH_WINDOW_MIN_LOGPROB": "-0.8"})
    assert (got["silence_ms"], got["vad_start"], got["min_logprob"]) == (500, 0.6, -0.8)


def test_a_bad_value_keeps_the_default_and_warns(caplog):
    got = window.settings({"SPEECH_WINDOW_SILENCE_MS": "soon", "SPEECH_WINDOW_VAD_START": "7"})
    assert (got["silence_ms"], got["vad_start"]) == (700, 0.5)
    assert "SPEECH_WINDOW_SILENCE_MS" in caplog.text and "SPEECH_WINDOW_VAD_START" in caplog.text


def test_stay_can_never_be_above_start():
    """The VAD refuses that, and the window would never start."""
    got = window.settings({"SPEECH_WINDOW_VAD_START": "0.3"})
    assert got["vad_stay"] <= got["vad_start"]


def test_an_empty_language_means_auto():
    assert window.settings({"SPEECH_WINDOW_LANGUAGE": ""})["language"] == ""
