"""AudioSocket, Asterisk's small TCP protocol for handing a call's audio to an app (spec §5.6).

A frame is kind (1 byte) | length (2 bytes, big endian) | payload. The first frame from Asterisk is 0x01 with the
16-byte UUID the dialplan gave (this app's line id). After that: 0x10 audio (s16le mono 8 kHz, 320 bytes per
20 ms), 0x03 a key press (in only), 0x00 hang up, 0xFF error. Asterisk drops a call that gets no audio from the
app for 2 s, which is the pacer's job to prevent, not this module's.

The socket has no authentication. The defences are here and in config: a peer whose IP is not allowed is closed
before a byte is read, the listener is off unless configured, and it refuses a public bind without an allow list.
Logs carry the peer IP, the line id and frame kinds, never audio."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import AsyncIterator, Awaitable, Callable

from .config import PhoneConfig, is_loopback_host, normalize_ip, split_listen
from .media import Audio, CallMedia, Digit, HungUp

logger = logging.getLogger(__name__)

KIND_HANGUP, KIND_UUID, KIND_DIGIT, KIND_AUDIO, KIND_ERROR = 0x00, 0x01, 0x03, 0x10, 0xFF
MAX_PAYLOAD = 4096          # a real audio frame is 320 bytes; anything this large is not AudioSocket
FIRST_FRAME_S = 5.0         # a peer that connects and says nothing is not a call
HANGUP_WRITE_S = 2.0        # how long a close may take to finish
STALL_S = 1.0               # a peer that won't take audio for this long is gone
STALL_BYTES = 16_000        # one second of 8 kHz s16le audio: more than this unsent means the peer stopped reading


class FrameError(Exception):
    """The peer sent something that isn't AudioSocket."""


def encode(kind: int, payload: bytes = b"") -> bytes:
    return bytes([kind]) + len(payload).to_bytes(2, "big") + payload


async def read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """One frame, reassembled across however many reads it arrives in. Raises `asyncio.IncompleteReadError` when
    the stream ends inside a frame (or before one) and `FrameError` for an oversized length."""
    head = await reader.readexactly(3)
    length = int.from_bytes(head[1:], "big")
    if length > MAX_PAYLOAD:
        raise FrameError(f"frame length {length} is too large")
    return head[0], await reader.readexactly(length) if length else b""


class AudioSocketCall(CallMedia):
    sample_rate = 8000

    def __init__(self, line_id: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, peer: str) -> None:
        self.line_id = line_id
        self._reader = reader
        self._writer = writer
        self._peer = peer
        self._gone = False          # the peer ended the call, or we closed it
        self._hung_up = False       # hang_up() has run

    async def events(self) -> AsyncIterator[Audio | Digit | HungUp]:
        """Audio and digits as they come, then one `HungUp`: "caller" on 0x00, on EOF or a broken link, "error" on
        0xFF. An unknown kind is logged (kind and length only) and skipped."""
        reason = "caller"
        while True:
            try:
                kind, payload = await read_frame(self._reader)
            except (asyncio.IncompleteReadError, ConnectionError, FrameError, OSError) as exc:
                logger.info("audiosocket %s line %s: stream ended (%s)", self._peer, self.line_id,
                            type(exc).__name__)
                break
            if kind == KIND_AUDIO:
                yield Audio(payload)
            elif kind == KIND_DIGIT:
                yield Digit(payload.decode("ascii", "replace"))
            elif kind == KIND_HANGUP:
                break
            elif kind == KIND_ERROR:
                logger.warning("audiosocket %s line %s: error frame from the peer", self._peer, self.line_id)
                reason = "error"
                break
            else:
                logger.warning("audiosocket %s line %s: unknown frame kind 0x%02x, %d bytes skipped",
                               self._peer, self.line_id, kind, len(payload))
        # Only here has the call truly ended. A consumer that stops iterating (cancelled, or closing the
        # generator) leaves the call up: the goodbye clip still has to be sent and the hang-up written.
        self._gone = True
        yield HungUp(reason)

    async def send(self, pcm: bytes) -> None:
        """One 0x10 audio frame. Raises `ConnectionError` once the peer is gone, which is how the pacer learns to
        stop. A peer that stops reading is treated as gone: past one second of unsent audio, or a drain that takes
        longer than a second, the transport is aborted."""
        if self._gone or self._writer.is_closing():
            raise ConnectionError("the call has ended")
        transport = self._writer.transport
        if transport.get_write_buffer_size() > STALL_BYTES:
            self._abort("the peer stopped reading")
        self._writer.write(encode(KIND_AUDIO, pcm))
        try:
            await asyncio.wait_for(self._writer.drain(), STALL_S)
        except asyncio.TimeoutError:
            self._abort("the peer stopped reading")
        except OSError as exc:                  # includes ConnectionResetError from an aborted transport
            raise ConnectionError("the call has ended") from exc

    def _abort(self, why: str) -> None:
        """Drop the transport at once (nothing is flushed), which ends in connection_lost and so ends events()."""
        logger.warning("audiosocket %s line %s: %s; dropping the connection", self._peer, self.line_id, why)
        self._gone = True
        self._writer.transport.abort()
        raise ConnectionError(why)

    async def hang_up(self) -> None:
        """Tell Asterisk to hang up (0x00) and close. Safe to call twice and after the peer has gone. Whatever the
        peer does, the connection is gone when this returns: a graceful close if the 0x00 got out within a second,
        else an abort. That wakes a blocked `send()` and ends `events()`."""
        if self._hung_up:
            return
        self._hung_up = True
        transport = self._writer.transport
        if not self._gone and not self._writer.is_closing():
            with contextlib.suppress(OSError):
                self._writer.write(encode(KIND_HANGUP))
                deadline = asyncio.get_running_loop().time() + STALL_S
                while transport.get_write_buffer_size() and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.01)
        await self.close()

    async def close(self) -> None:
        self._gone = True
        transport = self._writer.transport
        if transport.get_write_buffer_size():
            transport.abort()
        else:
            self._writer.close()
        with contextlib.suppress(OSError, asyncio.TimeoutError):
            await asyncio.wait_for(self._writer.wait_closed(), HANGUP_WRITE_S)


def peer_allowed(peer_ip: str | None, cfg: PhoneConfig) -> bool:
    """With an allow list, only its members. Without one, loopback peers only."""
    ip = normalize_ip(peer_ip) if peer_ip else None
    if ip is None:
        return False
    if cfg.allow:
        return ip in {normalize_ip(a) for a in cfg.allow}
    return is_loopback_host(ip)


async def serve(cfg: PhoneConfig, on_call: Callable[[CallMedia], Awaitable[None]]) -> asyncio.Server:
    """Listen on `cfg.listen` and hand each admitted call to `on_call`. The caller closes the returned server.

    Raises ValueError when the listener is off, or would be public without an allow list: `load_phone_config`
    already refuses both, and this refuses them again because it is the last gate before a socket opens."""
    parts = split_listen(cfg.listen) if cfg.listen else None
    if parts is None:
        raise ValueError("the phone listener is off")
    host, port = parts
    if not cfg.allow and not is_loopback_host(host):
        raise ValueError("refusing to listen on a non-loopback address without PHONE_AUDIOSOCKET_ALLOW")

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peername = writer.get_extra_info("peername")
        ip = peername[0] if peername else None
        if not peer_allowed(ip, cfg):
            logger.warning("audiosocket: dropped a peer %s that is not allowed", ip)
            writer.close()          # before any read: an unlisted peer gets no say
            return
        call: AudioSocketCall | None = None
        try:
            try:
                kind, payload = await asyncio.wait_for(read_frame(reader), FIRST_FRAME_S)
            except (asyncio.IncompleteReadError, ConnectionError, FrameError, OSError, asyncio.TimeoutError) as exc:
                logger.warning("audiosocket %s: no valid first frame (%s)", ip, type(exc).__name__)
                return
            if kind != KIND_UUID or len(payload) != 16:
                logger.warning("audiosocket %s: first frame was kind 0x%02x, %d bytes, not a line id",
                               ip, kind, len(payload))
                return
            call = AudioSocketCall(str(uuid.UUID(bytes=payload)), reader, writer, str(ip))
            logger.info("audiosocket %s: call for line %s", ip, call.line_id)
            await on_call(call)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("audiosocket %s: the call handler failed", ip)
        finally:
            if call is not None:
                await call.hang_up()
            else:
                writer.close()

    return await asyncio.start_server(handle, host, port, reuse_port=True)
