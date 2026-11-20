"""One-off: make the phone tests' WAV fixtures (16 kHz mono, 16-bit) under server/tests/fixtures/phone/.

Kokoro (the app's own voice) speaks three short questions in a voice unlike the line default (af_heart), so a fixture
never reads as a line's own echo; silence_2s.wav is seeded low-level noise, because a phone line is never silent.
Run once from the repository root: PYTHONPATH=. python scripts/make_phone_fixtures.py (needs the Kokoro model files)."""
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from server.endpoints import _speak

OUT = Path(__file__).resolve().parent.parent / "server" / "tests" / "fixtures" / "phone"
VOICE = "am_michael"
LINES = {
    "question1": "What time does the library open on Saturday?",
    "question2": "Can you say that again, please?",
    "question3": "Thank you, goodbye.",
}


def _write(name: str, samples_16k: np.ndarray) -> None:
    sf.write(OUT / f"{name}.wav", samples_16k.astype(np.float32), 16000, subtype="PCM_16")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in LINES.items():
        audio, rate = _speak(text, VOICE, 1.0)
        _write(name, resample_poly(np.asarray(audio, dtype=np.float32), 16000, rate))
    rng = np.random.default_rng(7)
    _write("silence_2s", rng.normal(0.0, 10 ** (-50 / 20), 32000))   # RMS about -50 dBFS


if __name__ == "__main__":
    main()
