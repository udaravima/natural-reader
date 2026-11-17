"""When an end-of-turn model would decide the caller has finished (spec 2026-11-17-call-bench-round2).

Talk waits for a fixed 0.7 s of silence. Pipecat's Smart Turn v3.2 (BSD-2-Clause, an 8 MB int8 ONNX model on a
Whisper-tiny encoder, run on the CPU) listens to the turn so far and says whether it sounds finished, so a short
silence can be enough. The policy, as Pipecat uses it: after `stop_s` (0.2 s) of silence ask the model; if it says
complete the turn ends then; if not, wait for more speech, or end after `fallback_s` (3 s) of silence.

For each caller clip this reports the wait after the last word, and the pauses inside the clip where the model
would have ended the turn: a false cut-off, the agent talking over a caller who hadn't finished. Speech and pauses
are found by loudness, which is right for clean synthetic clips; real calls need a real voice-activity detector."""
from __future__ import annotations

import math
import time
from collections.abc import Callable

import numpy as np

MODEL_REPO, MODEL_FILE = "pipecat-ai/smart-turn-v3", "smart-turn-v3.2-cpu.onnx"
RATE = 16000
WINDOW_S = 8   # the model hears the last 8 s, zeros padded at the start


def to_16k(samples: np.ndarray, rate: int) -> np.ndarray:
    samples = np.asarray(samples, dtype=np.float32)
    if rate == RATE:
        return samples
    from scipy.signal import resample_poly
    g = math.gcd(rate, RATE)
    return resample_poly(samples, RATE // g, rate // g).astype(np.float32)


def speech_bounds(samples: np.ndarray, rate: int, *, frame_s: float = 0.02, floor_db: float = -40.0,
                  min_pause_s: float = 0.2) -> tuple[list[tuple[float, float]], float]:
    """The pauses of at least `min_pause_s` between the first and the last speech (start, end), and when speech
    ends. A frame is speech when it is within `floor_db` of the loudest frame."""
    n = max(1, int(round(frame_s * rate)))
    frames = len(samples) // n
    if frames == 0:
        return [], 0.0
    rms = np.sqrt(np.mean(np.square(np.asarray(samples[:frames * n], dtype=np.float64).reshape(frames, n)), axis=1))
    peak = rms.max()
    if peak < 1e-4:
        return [], 0.0
    speech = rms >= peak * 10 ** (floor_db / 20)
    spoken = np.flatnonzero(speech)
    first, last = spoken[0], spoken[-1]
    pauses, run = [], None
    for i in range(first, last + 1):
        if not speech[i] and run is None:
            run = i
        elif speech[i] and run is not None:
            if (i - run) * frame_s >= min_pause_s - 1e-9:
                pauses.append((run * frame_s, i * frame_s))
            run = None
    return pauses, (last + 1) * frame_s


def log_mel(audio: np.ndarray, filters: np.ndarray) -> np.ndarray:
    """Whisper's log-mel features, as transformers' WhisperFeatureExtractor makes them with do_normalize=True
    (equal to 1e-5), without its matrix product: on a 32-core machine that product's threads made a 13 ms decision
    take up to 240 ms. 80 mel bins x (samples / 160) frames."""
    x = (audio - audio.mean()) / np.sqrt(audio.var() + 1e-7)
    frames = np.lib.stride_tricks.sliding_window_view(np.pad(x, (200, 200), mode="reflect"), 400)[::160]
    power = np.abs(np.fft.rfft(frames * np.hanning(401)[:-1], axis=1)) ** 2
    mel = np.einsum("tf,fm->mt", power, filters, optimize=False)
    log = np.log10(np.maximum(mel, 1e-10))[:, :-1]
    return ((np.maximum(log, log.max() - 8.0) + 4.0) / 4.0).astype(np.float32)


class SmartTurn:
    """Smart Turn v3.2 on the CPU. `predict` (audio at 16 kHz -> probability the turn is finished) replaces the
    model in tests."""

    def __init__(self, path: str | None = None, predict: Callable[[np.ndarray], float] | None = None) -> None:
        self._fake = predict
        if predict is not None:
            return
        import onnxruntime as ort
        from transformers import WhisperFeatureExtractor
        if path is None:
            from huggingface_hub import hf_hub_download
            path = hf_hub_download(MODEL_REPO, MODEL_FILE)
        options = ort.SessionOptions()
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(path, sess_options=options, providers=["CPUExecutionProvider"])
        self._mel_filters = WhisperFeatureExtractor(chunk_length=WINDOW_S).mel_filters   # (201, 80)

    def _probability(self, audio: np.ndarray) -> float:
        if self._fake is not None:
            return float(self._fake(audio))
        window = WINDOW_S * RATE
        audio = audio[-window:] if len(audio) > window else np.pad(audio, (window - len(audio), 0))
        x = log_mel(audio.astype(np.float64), self._mel_filters)[None]
        return float(self._session.run(None, {"input_features": x})[0].reshape(-1)[0])

    def predict(self, audio16k: np.ndarray) -> tuple[float, float]:
        """(probability the turn is finished, seconds the model took on this machine)."""
        started = time.perf_counter()
        probability = self._probability(np.asarray(audio16k, dtype=np.float32))
        return probability, time.perf_counter() - started


def end_of_turn(samples: np.ndarray, rate: int, model: SmartTurn, *, stop_s: float = 0.2, fallback_s: float = 3.0,
                threshold: float = 0.5, fixed_s: float = 0.7) -> dict:
    """The end-of-turn decision for one caller clip; times in seconds from the clip's start, `wait_s` from the end
    of speech. `fixed_cutoffs` are the pauses a fixed `fixed_s` silence would also have ended the turn on."""
    audio = to_16k(samples, rate)
    pauses, speech_end = speech_bounds(audio, RATE)
    cutoffs = []
    for start, end in pauses:
        if end - start >= stop_s:
            probability, _ = model.predict(audio[:int(round((start + stop_s) * RATE))])
            if probability > threshold:
                cutoffs.append(start)
    upto = int(round((speech_end + stop_s) * RATE))
    heard = audio[:upto] if len(audio) >= upto else np.pad(audio, (0, upto - len(audio)))
    probability, model_s = model.predict(heard)
    finished = probability > threshold
    return {"speech_end_s": speech_end, "wait_s": stop_s + model_s if finished else fallback_s,
            "probability": probability, "model_s": model_s, "fallback": not finished, "cutoffs": cutoffs,
            "fixed_cutoffs": [start for start, end in pauses if end - start >= fixed_s],
            "pauses": len(pauses)}
