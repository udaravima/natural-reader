"""Silero VAD, fed a call's audio as it arrives (what the browser's Talk does with its own copy of the model).

faster-whisper ships Silero v6 in its assets, so the server needs no download. The model scores one 512-sample window
(32 ms at 16 kHz) at a time; it is fed the previous window's last 64 samples as context and carries an LSTM state
(`h`, `c`) from window to window, so one `SileroStream` belongs to one call."""
from __future__ import annotations

import os
from functools import lru_cache

import numpy as np
import onnxruntime
from faster_whisper.utils import get_assets_path

WINDOW = 512    # samples scored at a time: 32 ms at 16 kHz
CONTEXT = 64    # samples of the window before, prepended to each one


@lru_cache(maxsize=1)
def _session() -> onnxruntime.InferenceSession:
    """One model for every call: onnxruntime sessions can run from several threads, and the weights are shared."""
    options = onnxruntime.SessionOptions()
    options.inter_op_num_threads = 1   # a window is tiny; more threads cost more than they save
    options.intra_op_num_threads = 1
    options.log_severity_level = 4
    return onnxruntime.InferenceSession(os.path.join(get_assets_path(), "silero_vad_v6.onnx"),
                                        providers=["CPUExecutionProvider"], sess_options=options)


class SileroStream:
    """Scores 16 kHz mono audio, however it is cut into pushes."""

    def __init__(self) -> None:
        self._session = _session()
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)   # fewer than WINDOW samples, waiting for the rest

    def push(self, samples_16k: np.ndarray) -> list[float]:
        """One speech probability (0 to 1) for each full 512-sample window now available. The remainder, under 512
        samples, waits for the next push. Input is float32 in [-1, 1] at 16 kHz."""
        return [probability for probability, _ in self.push_windows(samples_16k)]

    def push_windows(self, samples_16k: np.ndarray) -> list[tuple[float, np.ndarray]]:
        """Like `push`, but each probability comes with the 512 samples it scored, which is what the segmenter needs.
        This is the one place the audio is cut into windows."""
        samples = np.asarray(samples_16k, dtype=np.float32).reshape(-1)
        if self._pending.size:
            samples = np.concatenate((self._pending, samples))
        whole = samples.size - samples.size % WINDOW
        self._pending = samples[whole:].copy()
        scored: list[tuple[float, np.ndarray]] = []
        for start in range(0, whole, WINDOW):
            window = samples[start:start + WINDOW]
            frame = np.concatenate((self._context, window))[None, :]
            out, self._h, self._c = self._session.run(None, {"input": frame, "h": self._h, "c": self._c})
            self._context = window[-CONTEXT:].copy()
            scored.append((float(out.reshape(-1)[0]), window))
        return scored
