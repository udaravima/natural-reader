"""Pacing requests under a provider's per-minute quota without counting the wait as the agent's time."""
from server.calls.pacing import PausableClock, Throttle


class FakeTime:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


async def test_the_throttle_waits_only_when_the_last_minute_is_full():
    t = FakeTime()
    throttle = Throttle(2, monotonic=t.monotonic, sleep=t.sleep)
    assert await throttle.wait() == 0
    t.now += 10
    assert await throttle.wait() == 0
    waited = await throttle.wait()          # the first request was 10 s ago: wait until it is a minute old
    assert 50 <= waited <= 50.2
    assert Throttle(0, monotonic=t.monotonic, sleep=t.sleep).per_minute == 0


async def test_a_paused_clock_leaves_the_wait_out():
    t = FakeTime()
    clock = PausableClock(t.monotonic)
    start = clock()
    t.now += 3
    clock.pause(2.5)
    assert clock() - start == 0.5
