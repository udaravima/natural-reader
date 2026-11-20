"""The transport-neutral side of a phone call (spec §5.6).

A call session talks to a `CallMedia` and never to a socket, so another way in (a WebSocket channel, a SIP
account, a browser test call) only has to implement this protocol."""
from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol


@dataclass(frozen=True)
class Audio:
    """What the caller said: s16le mono PCM at the media's `sample_rate`."""
    pcm: bytes


@dataclass(frozen=True)
class Digit:
    """A key the caller pressed ("0"-"9", "*", "#")."""
    key: str


@dataclass(frozen=True)
class HungUp:
    """The call is over, and the last event. `reason` is "caller" (they hung up or the link dropped) or "error"."""
    reason: str


class CallMedia(Protocol):
    sample_rate: int                       # 8000 for AudioSocket
    line_id: str                           # from the first frame

    def events(self) -> AsyncIterator[Audio | Digit | HungUp]: ...
    async def send(self, pcm: bytes) -> None: ...   # one frame; the pacer calls it every 20 ms
    async def hang_up(self) -> None: ...
