"""The streaming Silero VAD a call listens with: it must hear speech, ignore a quiet line, and agree with
faster-whisper's own use of the same model however the audio is cut into pushes."""
import os
from pathlib import Path

import numpy as np
import soundfile as sf
from faster_whisper.utils import get_assets_path
from faster_whisper.vad import SileroVADModel
from scipy.signal import resample_poly

from server.phone.vad import SileroStream

FIXTURES = Path(__file__).parent / "fixtures" / "phone"


def load(name: str) -> np.ndarray:
    samples, rate = sf.read(FIXTURES / name, dtype="float32")
    assert rate == 16000
    return samples


def test_speech_scores_high_and_silence_low():
    probs = SileroStream().push(load("question1.wav"))
    assert max(probs) > 0.5 and max(SileroStream().push(load("silence_2s.wav"))) < 0.35


def test_one_probability_per_window_and_the_remainder_waits():
    stream = SileroStream()
    assert len(stream.push(np.zeros(1000, dtype=np.float32))) == 1     # 488 left over
    assert len(stream.push(np.zeros(23, dtype=np.float32))) == 0       # 511
    assert len(stream.push(np.zeros(1, dtype=np.float32))) == 1        # the 512th completes a window
    assert stream.push(np.zeros(0, dtype=np.float32)) == []


def test_any_cutting_gives_the_same_scores():
    audio = load("question1.wav")
    whole = SileroStream().push(audio)
    stream, pieces, at = SileroStream(), [], 0
    for size in (1, 160, 321, 700, 5000, 33):    # odd sizes, as 8 kHz frames resampled would arrive
        while at < audio.size:
            pieces += stream.push(audio[at:at + size])
            at += size
            break
    pieces += stream.push(audio[at:])
    assert pieces == whole


def test_reset_forgets_the_call():
    stream = SileroStream()
    first = stream.push(load("question1.wav"))
    stream.reset()
    assert stream.push(load("question1.wav")) == first


def test_streaming_matches_faster_whispers_own_silero():
    """faster-whisper scores a whole file in one batch; the stream scores it window by window. The model carries its
    LSTM state either way, so the two must agree to rounding."""
    audio = load("question1.wav")
    audio = audio[:audio.size // 512 * 512]
    theirs = np.asarray(SileroVADModel(os.path.join(get_assets_path(), "silero_vad_v6.onnx"))(audio.copy())).reshape(-1)
    ours = SileroStream().push(audio)
    assert len(ours) == len(theirs)
    np.testing.assert_allclose(ours[:-1], theirs[:-1], atol=1e-6, rtol=0)
    # faster-whisper's `context[-1] = 0` writes through a view and blanks the last 64 samples of the file's final
    # window before scoring it. That is its quirk, not the model's: give the stream the same blanked tail and the
    # last window agrees too.
    blanked = audio.copy()
    blanked[-64:] = 0
    np.testing.assert_allclose(SileroStream().push(blanked), theirs, atol=1e-6, rtol=0)


def test_speech_survives_the_phone_line():
    """A call is 8 kHz: the caller's voice goes 16 -> 8 kHz on the PBX and back up to 16 kHz here."""
    phone = resample_poly(resample_poly(load("question1.wav"), 1, 2), 2, 1).astype(np.float32)
    assert max(SileroStream().push(phone)) > 0.5


def test_push_windows_pairs_each_score_with_its_samples():
    audio = load("question1.wav")
    whole = SileroStream().push(audio)
    stream, got, at = SileroStream(), [], 0
    for size in (1, 160, 321, 700, 33, 5000):
        got += stream.push_windows(audio[at:at + size])
        at += size
    got += stream.push_windows(audio[at:])
    assert [p for p, _ in got] == whole
    assert all(w.size == 512 for _, w in got)
    kept = np.concatenate([w for _, w in got])            # nothing lost or repeated across pushes
    np.testing.assert_array_equal(kept, audio[:kept.size])
    assert audio.size - kept.size < 512


def test_reset_clears_the_remainder():
    stream = SileroStream()
    stream.push(np.ones(300, dtype=np.float32))
    stream.reset()
    assert stream.push_windows(np.ones(300, dtype=np.float32)) == []     # 300 alone, not 600
