"""The pacer: one 320-byte frame every 20 ms for the whole call, whatever else is happening.

Asterisk hangs up a call after 2 s without audio from the app, so the app can never go quiet: the pacer sends the
next queued audio, or silence when there's none. It is also where "what did the caller actually hear" is known,
because only it knows which bytes really went out.

- Audio is one continuous stream: sentences queued back to back play with no gap, and the last frame of the stream
  is padded with silence.
- `pause()` sends silence and keeps the queue; `resume()` carries on from the next unsent byte.
- `stop_reply()` drops everything queued and freezes `heard()` until `new_reply()`.
- `heard()` follows Talk's rule (`spokenReply.js`, `HEARD_SHARE`): a sentence more than half sent counts as heard.

Timing: frame n is due at `start + n * FRAME_S` on the injected clock, never "the last send plus 20 ms", so sleeps
that run long don't add up to drift. A frame that is late (a slow `send`) doesn't make the next ones burst: the
missed deadlines are skipped and the next frame goes out on the next deadline still ahead. Audio isn't lost by that,
only taken later, because bytes leave the queue when a frame is built, not when its deadline passes."""
from __future__ import annotations

import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

FRAME = 320                      # bytes: 20 ms of 8 kHz s16le mono
FRAME_S = 0.020                  # seconds between frames
HEARD_SHARE = 0.5                # of a sentence sent before it counts as heard (Talk's rule)
SILENCE = b"\0" * FRAME


@dataclass
class _Item:
    pcm: bytes
    end: int | None              # offset in the raw reply where the sentence ends; None for a clip
    sent: int = 0                # bytes of `pcm` already put into frames


class Pacer:
    def __init__(
        self,
        send: Callable[[bytes], Awaitable[None]],
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        self._send = send
        self._clock = clock
        self._sleep = sleep
        self._queue: deque[_Item] = deque()
        self._paused = False
        self._heard = 0                  # end of the last finished sentence of the current reply
        self._frozen: int | None = None  # heard() as it was when the reply was stopped
        self._drained: Callable[[], None] | None = None
        self._played = False             # audio went out since the queue last emptied

    # --- what a call does to it ---

    def play(self, pcm: bytes, *, end: int | None = None) -> None:
        """Queue one sentence (`end` is its offset in the raw reply) or one clip (`end=None`: never counts as heard)."""
        if len(pcm) % 2:
            pcm += b"\0"                 # keep every sample whole
        if pcm:
            self._queue.append(_Item(pcm, end))

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def stop_reply(self) -> None:
        """The caller really interrupted: drop what's queued (and the rest of what's playing). `heard()` keeps the
        value it has now until `new_reply()`. It also clears a pause: the pause belonged to the reply just dropped, so
        the next audio isn't held. A caller whose speech run is still open calls `pause()` again."""
        self._frozen = self.heard()
        self._paused = False
        self._queue.clear()
        self._played = False             # an interruption isn't "the line finished speaking"

    def new_reply(self) -> None:
        """A new reply starts: `heard()` is 0 until one of its sentences counts. Like Talk's fresh reply, it starts
        unpaused; a caller whose speech run is still open calls `pause()` again."""
        self._heard = 0
        self._paused = False
        self._frozen = None

    def heard(self) -> int:
        """The end of the last sentence finished, or of the one being played once more than half of it has gone."""
        if self._frozen is not None:
            return self._frozen
        head = self._queue[0] if self._queue else None
        if head and head.end is not None and head.sent / len(head.pcm) > HEARD_SHARE:
            return head.end
        return self._heard

    def busy(self) -> bool:
        return bool(self._queue)

    def on_drained(self, cb: Callable[[], None]) -> None:
        """`cb()` runs once each time the queue empties after audio went out (not on every silent frame, and not
        after `stop_reply()`). The idle timer starts from it."""
        self._drained = cb

    # --- the clock ---

    async def run(self) -> None:
        """Send frames until cancelled. If `send` raises (the socket closed), the exception ends `run()`: the call
        session awaits this task to notice a hang-up."""
        start = self._clock()
        n = 0
        while True:
            await self._send(self._next_frame())
            self._notify_drained()
            n += 1
            now = self._clock()
            if now >= start + n * FRAME_S:           # late: skip the deadlines already gone, never burst
                n = int((now - start) / FRAME_S) + 1
            await self._sleep(start + n * FRAME_S - now)

    # --- frames ---

    def _next_frame(self) -> bytes:
        if self._paused or not self._queue:
            return SILENCE
        frame = bytearray()
        while self._queue and len(frame) < FRAME:
            item = self._queue[0]
            take = item.pcm[item.sent:item.sent + FRAME - len(frame)]
            frame += take
            item.sent += len(take)
            if item.sent >= len(item.pcm):
                self._queue.popleft()
                if item.end is not None:
                    self._heard = item.end
        self._played = True
        return bytes(frame).ljust(FRAME, b"\0")

    def _notify_drained(self) -> None:
        if self._queue or not self._played:
            return
        self._played = False
        if self._drained:
            try:
                self._drained()
            except Exception:
                log.exception("pacer on_drained callback failed")
