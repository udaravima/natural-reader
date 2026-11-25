"""What a transcribed clip is: Talk's rules (src/lib/speech/checks.js) in Python, for a phone call.

A clip can be line noise that the VAD let through (Whisper writes words for noise, but its own scores give it away),
the reply coming back down the line, or the caller talking. The cases in server/tests/fixtures/speech are shared with
the JS tests so the two can't drift."""
from __future__ import annotations

import re
from typing import Literal

from server.speech.base import Transcript

MIN_ECHO_WORDS = 3  # a short word proves nothing: "stop" can be in the reply and still be meant
ECHO_SHARE = 0.6  # of a clip's words found in what was just said

_WORD = re.compile(r"(?:[^\W_]|')+")
_SAYS_SOMETHING = re.compile(r"[^\W_]")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def heard_speech(
    text: str, no_speech: float | None, avg_logprob: float | None, *, max_no_speech: float, min_logprob: float
) -> bool:
    """Is this clip a person talking? None scores (an engine that gives none) are taken at its word."""
    if not text or not _SAYS_SOMETHING.search(text):
        return False
    if no_speech is not None and no_speech >= max_no_speech:
        return False
    return avg_logprob is None or avg_logprob >= min_logprob


def is_echo(text: str, said: list[str]) -> bool:
    """Is this clip the reply coming back? `said`: the sentences spoken just now."""
    heard = _words(text)
    if len(heard) < MIN_ECHO_WORDS:
        return False
    known = {word for sentence in said for word in _words(sentence)}
    return sum(word in known for word in heard) / len(heard) >= ECHO_SHARE


def verdict(t: Transcript, said: list[str], settings: dict) -> Literal["noise", "echo", "speech"]:
    """`settings`: server/speech/window.settings() (`max_no_speech`, `min_logprob`)."""
    if not heard_speech(
        t.text, t.no_speech, t.avg_logprob,
        max_no_speech=settings["max_no_speech"], min_logprob=settings["min_logprob"],
    ):
        return "noise"
    return "echo" if is_echo(t.text, said) else "speech"
