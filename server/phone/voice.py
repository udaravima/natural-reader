"""A line's voice at the phone's rate: s16le mono 8 kHz, from Kokoro, Chirp 3 HD or Gemini-TTS (spec 5.3).

Lines speak at speed 1.0. A failure surfaces as the engine raised it (Google's `GoogleVoiceError` carries a message
the console can show), so the caller decides: a call skips the sentence, a save answers 502."""
from __future__ import annotations

import io
from math import gcd

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from server import chat_voice
from server.phone.store import LineVoice

RATE = 8000
SPEED = 1.0


def voice_key(v: LineVoice) -> str:
    """What a clip was made with: "engine:voice:accent" (an empty accent when there is none)."""
    return f"{v.engine}:{v.voice}:{v.accent or ''}"


def _s16(audio: np.ndarray, rate: int) -> bytes:
    """Mono float audio at `rate` as s16le at 8 kHz."""
    audio = np.asarray(audio, dtype=np.float32)
    if rate != RATE:
        g = gcd(RATE, rate)
        audio = resample_poly(audio, RATE // g, rate // g)   # Kokoro's 24 kHz is (1, 3)
    return (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


async def render(text: str, v: LineVoice) -> bytes:
    """`text` spoken in the line's voice, as s16le mono 8 kHz with no header."""
    if v.engine == "kokoro":
        from server import endpoints   # imported here: importing it loads Kokoro
        audio, rate = await endpoints.kokoro_pcm(text, v.voice, SPEED)
        return _s16(np.asarray(audio).reshape(-1), rate)
    if v.engine == "chirp":
        wav, _ = await chat_voice.chirp_speak(text, v.voice, SPEED, accent=v.accent, sample_rate_hz=RATE)
    elif v.engine == "google":
        wav, _ = await chat_voice.google_speak(text, v.voice, SPEED, accent=v.accent, sample_rate_hz=RATE)
    else:
        raise ValueError(f"unknown voice engine: {v.engine}")
    audio, rate = sf.read(io.BytesIO(wav), dtype="float32", always_2d=True)
    return _s16(audio.mean(axis=1), rate)   # Google honours 8000; any other rate it sends is converted
