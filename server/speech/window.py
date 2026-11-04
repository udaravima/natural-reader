"""What the speech window listens with (spec 2026-11-02-speech-window). The browser runs the VAD, so these travel to
it in /v1/speech/status. The defaults are the measured ones: docs/DEPLOYMENT.md, "The speech window"."""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping

from .languages import WHISPER_LANGUAGES

logger = logging.getLogger("server.speech")

# name: (variable, default, lowest, highest). A default's type is the type of the setting.
_NUMBERS = {
    "vad_start": ("SPEECH_WINDOW_VAD_START", 0.5, 0.0, 1.0),          # Silero score that starts speech
    "vad_stay": ("SPEECH_WINDOW_VAD_STAY", 0.35, 0.0, 1.0),           # the score that keeps it going
    "min_speech_ms": ("SPEECH_WINDOW_MIN_SPEECH_MS", 250, 0, 2000),   # speech before a reply pauses
    "pre_roll_ms": ("SPEECH_WINDOW_PRE_ROLL_MS", 300, 0, 2000),       # kept from before the start
    "silence_ms": ("SPEECH_WINDOW_SILENCE_MS", 700, 100, 5000),       # silence that ends a turn
    "first_clause_chars": ("SPEECH_WINDOW_FIRST_CLAUSE_CHARS", 30, 0, 1000),   # 0: never cut a reply's opening
    "max_no_speech": ("SPEECH_WINDOW_MAX_NO_SPEECH", 0.25, 0.0, 1.0),  # Whisper's score above which a clip is noise
    "min_logprob": ("SPEECH_WINDOW_MIN_LOGPROB", -1.0, -10.0, 0.0),   # Whisper's score below which a clip is noise
    # dB below the person's own voice at which the browser stops counting a voice as theirs (src/lib/speech/gate.js);
    # 0: every voice counts, as before the gate
    "gate_db": ("SPEECH_WINDOW_GATE_DB", 9, 0, 40),
}
_warned: set[str] = set()


def _warn_once(variable: str, raw: str) -> None:
    if variable not in _warned:   # the status route reads these on every call
        _warned.add(variable)
        logger.warning("%s=%r isn't usable: using the default", variable, raw)


def _number(env: Mapping[str, str], variable: str, default, low, high):
    raw = (env.get(variable) or "").strip()
    if not raw:
        return default
    try:
        value = type(default)(raw)
    except ValueError:
        value = None
    if value is None or not low <= value <= high:
        _warn_once(variable, raw)
        return default
    return value


def _language(env: Mapping[str, str]) -> str:
    raw = env.get("SPEECH_WINDOW_LANGUAGE", "en").strip()   # empty: Auto
    if raw and raw not in WHISPER_LANGUAGES:
        _warn_once("SPEECH_WINDOW_LANGUAGE", raw)
        return "en"
    return raw


def settings(env: Mapping[str, str] = os.environ) -> dict:
    out = {name: _number(env, *spec) for name, spec in _NUMBERS.items()}
    out["vad_stay"] = min(out["vad_stay"], out["vad_start"])   # the VAD refuses a higher one
    out["language"] = _language(env)
    return out
