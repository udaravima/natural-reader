"""The segmenter: Talk's turn rules over a stream of 32 ms window scores."""
import numpy as np

from server.phone.segmenter import Misfire, Segmenter, SpeechEnded, SpeechStarted
from server.speech import window

W = 512   # samples in a window: 32 ms at 16 kHz


def feed(probs, settings=None, **kw):
    """Run the scores through a segmenter; returns [(window index, event)]. Window i is filled with i + 1."""
    seg = Segmenter(settings or window.settings({}), **kw)
    got = []
    for i, p in enumerate(probs):
        got += [(i, e) for e in seg.step(p, np.full(W, i + 1, dtype=np.float32))]
    return got


def kinds(got):
    return [type(e) for _, e in got]


def test_started_needs_min_speech():
    assert kinds(feed([0.9] * 7 + [0.0] * 22)) == [Misfire]                    # 224 ms: a click
    assert kinds(feed([0.9] * 8 + [0.0] * 22)) == [SpeechStarted, SpeechEnded]  # 256 ms
    assert feed([0.9] * 8)[0][0] == 7                                          # started on the 8th window


def test_a_quiet_start_never_begins_a_run():
    assert feed([0.49] * 50) == []


def test_ended_after_silence_ms():
    assert kinds(feed([0.9] * 10 + [0.0] * 22)) == [SpeechStarted, SpeechEnded]   # 704 ms ends it
    assert kinds(feed([0.9] * 10 + [0.0] * 21)) == [SpeechStarted]                # 672 ms doesn't
    assert feed([0.9] * 10 + [0.0] * 22)[1][0] == 31


def test_hysteresis_keeps_speech_going():
    # 0.4 is under vad_start but at or over vad_stay: it holds a run open and resets the silence count.
    probs = [0.9] * 8 + [0.0] * 20 + [0.4] + [0.0] * 21
    assert kinds(feed(probs)) == [SpeechStarted]
    assert kinds(feed(probs + [0.0])) == [SpeechStarted, SpeechEnded]
    assert kinds(feed([0.9] + [0.4] * 7)) == [SpeechStarted]    # 0.4s are speech windows, they count to min_speech


def test_the_next_run_starts_afresh_after_one_ends():
    got = feed([0.9] * 8 + [0.0] * 22 + [0.9] * 8 + [0.0] * 22)
    assert kinds(got) == [SpeechStarted, SpeechEnded, SpeechStarted, SpeechEnded]


def test_clip_includes_pre_roll():
    probs = [0.0] * 20 + [0.9] * 8 + [0.0] * 22
    clip = feed(probs)[-1][1].clip
    assert clip.size == (10 + 8 + 22) * W                    # ceil(300 / 32) = 10 windows before, then the run
    assert clip[0] == 11 and clip[-1] == 50                  # windows 10..49 (filled with index + 1)


def test_pre_roll_is_only_what_exists():
    clip = feed([0.0] * 3 + [0.9] * 8 + [0.0] * 22)[-1][1].clip
    assert clip.size == (3 + 8 + 22) * W


def test_a_second_clip_never_repeats_the_first_ones_audio():
    got = feed([0.9] * 8 + [0.0] * 22 + [0.0] * 4 + [0.9] * 8 + [0.0] * 22)
    second = got[-1][1].clip
    assert second.size == (4 + 8 + 22) * W and second[0] == 31   # windows 30.. only


def test_the_knobs_are_the_settings():
    knobs = {**window.settings({}), "min_speech_ms": 64, "silence_ms": 100, "pre_roll_ms": 0}
    got = feed([0.9] * 2 + [0.0] * 4, knobs)                    # 2 windows of speech; 4 of silence = 128 >= 100
    assert kinds(got) == [SpeechStarted, SpeechEnded]
    assert got[-1][1].clip.size == 6 * W


# --- the cap on a run (R5.8): talk, a TV or hold music that never leaves silence_ms of quiet ---

def test_a_run_ends_at_the_cap_and_the_next_window_opens_a_new_one():
    got = feed([0.9] * 25, max_run_s=0.32)                     # ceil(320 / 32) = 10 windows a run
    assert kinds(got) == [SpeechStarted, SpeechEnded, SpeechStarted, SpeechEnded]
    assert [i for i, e in got if isinstance(e, SpeechEnded)] == [9, 19]
    first, second = (e.clip for _, e in got if isinstance(e, SpeechEnded))
    assert first.size == second.size == 10 * W                 # bounded, and no pre-roll repeats the first clip
    assert first[-1] == 10 and second[0] == 11


def test_a_cap_reached_before_min_speech_is_a_misfire():
    assert kinds(feed([0.9] * 12, max_run_s=0.16)) == [Misfire, Misfire]   # 5-window runs, 8 needed to start


def test_a_hum_under_vad_start_cannot_hold_a_run_past_the_cap():
    """0.4 keeps a run going (>= vad_stay) but can't open one (< vad_start): once capped, the hum is between runs."""
    hum = [0.9] * 8 + [0.4] * 100
    assert kinds(feed(hum)) == [SpeechStarted]                                # held open for good
    assert kinds(feed(hum, max_run_s=0.64)) == [SpeechStarted, SpeechEnded]   # ends at 20 windows, stays ended


def test_no_cap_keeps_a_run_open():
    assert kinds(feed([0.9] * 2000, max_run_s=None)) == [SpeechStarted]       # 64 s, one run
