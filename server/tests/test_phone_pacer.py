"""The pacer (server/phone/pacer.py): a frame every 20 ms whatever happens, pause, stop, and what was heard.

A fake clock stands in for time: `sleep(d)` advances it by `d` plus a small overshoot (a real sleep always runs a
little long), so a pacer that adds up its sleeps would drift and these tests would see it."""
import asyncio

import pytest

from server.phone.pacer import FRAME, Pacer

SILENCE = b"\0" * FRAME


class Hangup(Exception):
    """What a closed socket raises from `send`."""


class Rig:
    """A pacer on a fake clock. `send` records (time, frame) and runs `hooks[i]` after frame i is recorded; the
    run ends with `Hangup` once `frames` have gone."""

    def __init__(self, frames: int, overshoot: float = 0.001, send_cost: dict | None = None) -> None:
        self.now = 0.0
        self.frames: list[bytes] = []
        self.times: list[float] = []
        self.hooks: dict = {}
        self.limit = frames
        self.overshoot = overshoot
        self.send_cost = send_cost or {}      # frame index -> seconds `send` itself takes
        self.pacer = Pacer(self._send, lambda: self.now, self._sleep)

    async def _sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0) + self.overshoot
        await asyncio.sleep(0)

    async def _send(self, frame: bytes) -> None:
        assert len(frame) == FRAME
        i = len(self.frames)
        self.frames.append(frame)
        self.times.append(self.now)
        if i in self.hooks:
            self.hooks[i]()
        self.now += self.send_cost.get(i, 0)
        if len(self.frames) >= self.limit:
            raise Hangup

    async def go(self) -> None:
        with pytest.raises(Hangup):
            await self.pacer.run()


def sentence(frames: int, fill: int = 1) -> bytes:
    return bytes([fill]) * (FRAME * frames)


async def test_sends_silence_when_idle():
    rig = Rig(10)
    await rig.go()
    assert rig.frames == [SILENCE] * 10


async def test_never_gaps_over_20ms_and_never_drifts():
    rig = Rig(500)
    await rig.go()
    # each frame is on its own deadline (i * 20 ms) plus at most the one sleep's overshoot: no growth over 500 frames
    for i, t in enumerate(rig.times):
        assert abs(t - i * 0.020) <= 0.0011
    gaps = [b - a for a, b in zip(rig.times, rig.times[1:])]
    assert max(gaps) < 0.0215 and min(gaps) > 0.0185


async def test_a_slow_send_does_not_make_the_next_frames_burst():
    rig = Rig(30, send_cost={5: 0.075})               # frame 5's send takes 75 ms: three deadlines missed
    await rig.go()
    gaps = [b - a for a, b in zip(rig.times, rig.times[1:])]
    assert min(gaps) >= 0.0185                        # never two frames back to back
    assert len(rig.frames) == 30
    # and it is back on the 20 ms grid straight after
    late = rig.times[6:]
    assert all(abs((t % 0.020) - 0.001) < 0.0011 or abs((t % 0.020) - 0.021) < 0.0011 for t in late)


async def test_plays_a_sentence_in_frames_then_goes_back_to_silence():
    rig = Rig(6)
    rig.pacer.play(sentence(2, 7), end=10)
    await rig.go()
    assert rig.frames[:2] == [bytes([7]) * FRAME] * 2
    assert rig.frames[2:] == [SILENCE] * 4


async def test_a_last_partial_frame_is_padded_with_silence():
    rig = Rig(3)
    rig.pacer.play(b"\x05" * 100, end=1)
    await rig.go()
    assert rig.frames[0] == b"\x05" * 100 + b"\0" * (FRAME - 100)
    assert rig.frames[1] == SILENCE


async def test_two_sentences_back_to_back_play_with_no_gap():
    rig = Rig(4)
    rig.pacer.play(b"\x01" * 400, end=10)
    rig.pacer.play(b"\x02" * 400, end=20)
    await rig.go()
    # the second sentence starts inside the frame where the first ends
    assert rig.frames[0] == b"\x01" * FRAME
    assert rig.frames[1] == b"\x01" * 80 + b"\x02" * 240
    assert rig.frames[2] == b"\x02" * 160 + b"\0" * 160
    assert rig.frames[3] == SILENCE


async def test_pause_sends_silence_and_resume_continues_where_it_stopped():
    rig = Rig(9)
    rig.pacer.play(b"".join(bytes([k]) * FRAME for k in (1, 2, 3, 4, 5)), end=50)
    rig.hooks[1] = rig.pacer.pause                    # frames 0 and 1 carry audio, then the pause
    rig.hooks[4] = rig.pacer.resume                   # frames 2, 3, 4 are silence
    await rig.go()
    assert rig.frames == [bytes([1]) * FRAME, bytes([2]) * FRAME, SILENCE, SILENCE, SILENCE,
                          bytes([3]) * FRAME, bytes([4]) * FRAME, bytes([5]) * FRAME, SILENCE]


async def test_busy_covers_queued_and_current_audio():
    rig = Rig(5)
    assert not rig.pacer.busy()
    rig.pacer.play(sentence(2), end=5)
    assert rig.pacer.busy()
    seen = []
    for i in range(5):
        rig.hooks[i] = lambda: seen.append(rig.pacer.busy())
    await rig.go()
    assert seen == [True, False, False, False, False]


async def test_heard_counts_a_sentence_once_more_than_half_is_played():
    # A (end 20) is 2 frames, B (end 45) is 10 frames; heard() is read after each frame has gone out
    rig = Rig(8)
    rig.pacer.play(sentence(2, 1), end=20)
    rig.pacer.play(sentence(10, 2), end=45)
    heard = []
    for i in range(8):
        rig.hooks[i] = lambda: heard.append(rig.pacer.heard())
    await rig.go()
    # frame 0 is half of A (not more than half: not heard); frame 1 finishes A; frames 2..6 are B's 1st..5th of 10
    # (40% and exactly 50% don't count); frame 7 is B's 6th: 60%, B's end
    assert heard == [0, 20, 20, 20, 20, 20, 20, 45]


async def test_clips_never_count_as_heard():
    rig = Rig(5)
    rig.pacer.play(sentence(3), end=None)
    await rig.go()
    assert rig.pacer.heard() == 0


async def test_stop_reply_drops_the_queue_and_keeps_heard_where_it_was():
    rig = Rig(8)
    rig.pacer.play(sentence(2, 1), end=20)
    rig.pacer.play(sentence(10, 2), end=45)
    after = {}

    def stop():
        rig.pacer.stop_reply()
        after["busy"] = rig.pacer.busy()
        after["heard"] = rig.pacer.heard()

    rig.hooks[5] = stop                               # frame 5 is B's 4th frame: 40% -> heard is A's end
    await rig.go()
    assert after == {"busy": False, "heard": 20}
    assert rig.frames[6:] == [SILENCE, SILENCE]
    assert rig.pacer.heard() == 20


async def test_stop_reply_past_half_freezes_the_current_sentence_as_heard():
    rig = Rig(8)
    rig.pacer.play(sentence(10, 2), end=45)
    rig.hooks[5] = rig.pacer.stop_reply               # 6 of 10 frames
    await rig.go()
    assert rig.pacer.heard() == 45


async def test_new_reply_starts_the_count_again_from_zero():
    rig = Rig(14)
    rig.pacer.play(sentence(2), end=20)
    stopped = {}

    def interrupt():
        rig.pacer.stop_reply()
        stopped["before"] = rig.pacer.heard()
        rig.pacer.new_reply()
        stopped["after"] = rig.pacer.heard()
        rig.pacer.play(sentence(4, 3), end=30)        # the next reply's first sentence

    rig.hooks[1] = interrupt
    seen = []
    for i in (2, 3, 4, 5):
        rig.hooks[i] = lambda: seen.append(rig.pacer.heard())
    await rig.go()
    assert stopped == {"before": 20, "after": 0}
    # frames 2..5 are the new sentence: 1 of 4, 2 of 4 (half), 3 of 4, then done
    assert seen == [0, 0, 30, 30]


async def test_on_drained_fires_once_each_time_the_queue_empties_after_audio():
    rig = Rig(12)
    calls = []
    rig.pacer.on_drained(lambda: calls.append(len(rig.frames)))
    rig.pacer.play(sentence(2), end=1)
    rig.hooks[5] = lambda: rig.pacer.play(sentence(1), end=2)
    await rig.go()
    assert calls == [2, 7]                            # after frame 1 and after frame 6; never on silent frames


async def test_on_drained_does_not_fire_while_paused_or_after_stop_reply():
    rig = Rig(8)
    calls = []
    rig.pacer.on_drained(lambda: calls.append(1))
    rig.pacer.play(sentence(4), end=1)
    rig.hooks[1] = rig.pacer.pause
    rig.hooks[3] = rig.pacer.stop_reply
    await rig.go()
    assert calls == []


async def test_a_failing_drained_callback_does_not_stop_the_frames(caplog):
    rig = Rig(5)

    def boom():
        raise RuntimeError("nope")

    rig.pacer.on_drained(boom)
    rig.pacer.play(sentence(1), end=1)
    await rig.go()
    assert len(rig.frames) == 5


async def test_run_ends_by_raising_what_send_raised():
    async def closed(frame: bytes) -> None:
        raise ConnectionResetError("socket closed")

    pacer = Pacer(closed, lambda: 0.0, lambda s: asyncio.sleep(0))
    with pytest.raises(ConnectionResetError):
        await pacer.run()


async def test_a_pause_does_not_outlive_the_reply_it_held():
    # Task 9 pauses on every speech start; on real speech the next calls are stop_reply, new_reply, play
    rig = Rig(8)
    rig.pacer.play(sentence(4, 1), end=10)

    def interrupt_and_answer():
        rig.pacer.stop_reply()
        rig.pacer.new_reply()
        rig.pacer.play(sentence(2, 9), end=5)

    rig.hooks[1] = rig.pacer.pause
    rig.hooks[3] = interrupt_and_answer
    await rig.go()
    assert rig.frames[4:6] == [bytes([9]) * FRAME] * 2
    assert not rig.pacer.busy()


async def test_a_pause_does_not_hold_a_clip_played_after_stop_reply():
    rig = Rig(6)
    rig.pacer.play(sentence(4, 1), end=10)
    rig.hooks[0] = rig.pacer.pause

    def stop_then_clip():
        rig.pacer.stop_reply()
        rig.pacer.play(sentence(1, 6))

    rig.hooks[2] = stop_then_clip
    await rig.go()
    assert rig.frames[3] == bytes([6]) * FRAME


async def test_resume_still_continues_a_paused_reply_from_where_it_stopped():
    rig = Rig(6)
    rig.pacer.play(sentence(3, 4), end=10)
    rig.hooks[0] = rig.pacer.pause
    rig.hooks[2] = rig.pacer.resume
    await rig.go()
    assert rig.frames == [bytes([4]) * FRAME, SILENCE, SILENCE] + [bytes([4]) * FRAME] * 2 + [SILENCE]
