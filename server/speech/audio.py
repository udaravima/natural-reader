"""Bounded audio decoding, shared by both engines and run before anything else (spec §4.2, §5.6).

faster-whisper's own decode_audio builds the whole recording before anyone can look at its length; low-bitrate
Opus packs hours into 10 MB, so one request could cost hundreds of MB of float32. This stops as soon as the
running sample count passes the cap, so peak memory is about `max_seconds` of audio."""
from __future__ import annotations

import io

import av
import numpy as np

from .base import MAX_AUDIO_SECONDS, AudioTooLong, BadAudio

SAMPLE_RATE = 16000


def decode_capped(audio: bytes, max_seconds: float = MAX_AUDIO_SECONDS) -> np.ndarray:
    """16 kHz mono float32 samples. Stops decoding as soon as it passes max_seconds."""
    limit = int(max_seconds * SAMPLE_RATE)
    chunks: list[np.ndarray] = []
    total = 0

    def take(frames) -> None:
        nonlocal total
        for frame in frames:
            arr = frame.to_ndarray().reshape(-1)
            total += arr.size
            if total > limit:
                raise AudioTooLong(f"A recording can be at most {int(max_seconds)} seconds")
            chunks.append(arr)

    try:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        with av.open(io.BytesIO(audio), mode="r") as container:
            if not container.streams.audio:
                raise BadAudio("The recording has no audio")
            for frame in container.decode(audio=0):
                take(resampler.resample(frame))
            take(resampler.resample(None))
    except (AudioTooLong, BadAudio):
        raise
    except Exception as e:  # noqa: BLE001 - PyAV raises many types for undecodable input
        raise BadAudio("The recording could not be read") from e
    if not chunks:
        raise BadAudio("The recording has no audio")
    return np.concatenate(chunks).astype(np.float32) / 32768.0
