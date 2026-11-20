"""Turns Silero's scores into a caller's turns: "speech started", "misfire", "speech ended, here's the clip".

The same rules and knobs as Talk's browser VAD (`server/speech/window.settings()`):
- a run begins at the first window scoring at least `vad_start`;
- inside a run, a window scoring at least `vad_stay` is speech, and a lower one is silence, which a speech window
  resets;
- `SpeechStarted` fires once the run's speech reaches `min_speech_ms`;
- `silence_ms` of consecutive silence ends the run: a `Misfire` if speech never started, else `SpeechEnded` with
  the audio, `pre_roll_ms` of it from before the run began."""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SpeechStarted:
    pass


@dataclass(frozen=True)
class Misfire:
    """The run ended before it held `min_speech_ms` of speech: a click or a cough, not a turn."""


@dataclass(frozen=True)
class SpeechEnded:
    clip: np.ndarray   # the pre-roll, then every window of the run through its trailing silence


Event = SpeechStarted | Misfire | SpeechEnded


class Segmenter:
    def __init__(self, settings: dict, rate: int = 16000) -> None:
        window_ms = 512 * 1000 / rate
        self._start = settings["vad_start"]
        self._stay = settings["vad_stay"]
        self._min_speech = math.ceil(settings["min_speech_ms"] / window_ms)    # windows of speech that count
        self._max_silence = math.ceil(settings["silence_ms"] / window_ms)      # windows of silence that end a run
        self._before: deque[np.ndarray] = deque(maxlen=math.ceil(settings["pre_roll_ms"] / window_ms))
        self._run: list[np.ndarray] | None = None   # the run's windows, pre-roll first; None between runs
        self._speech = 0
        self._silence = 0
        self._started = False

    def step(self, prob: float, window: np.ndarray) -> list[Event]:
        """Feed one window's score and samples; returns the events it causes, usually none."""
        if self._run is None:
            if prob < self._start:
                self._before.append(window.copy())
                return []
            self._run = [*self._before, window.copy()]
            self._before.clear()
            self._speech, self._silence, self._started = 1, 0, False
        else:
            self._run.append(window.copy())
            if prob >= self._stay:
                self._speech += 1
                self._silence = 0
            else:
                self._silence += 1
        events: list[Event] = []
        if not self._started and self._speech >= self._min_speech:
            self._started = True
            events.append(SpeechStarted())
        if self._silence >= self._max_silence:
            events.append(SpeechEnded(np.concatenate(self._run)) if self._started else Misfire())
            self._run = None
        return events
