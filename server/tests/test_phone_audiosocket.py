"""The AudioSocket adapter against a real local server on 127.0.0.1, port 0."""
import asyncio
import contextlib
import uuid

import pytest

from server.phone.audiosocket import AudioSocketCall, encode, read_frame, serve
from server.phone.config import PhoneConfig
from server.phone.media import Audio, Digit, HungUp

UID = uuid.UUID("6f1c2a00-1111-4222-8333-444455556666")
PCM = bytes(range(256)) + bytes(64)          # 320 bytes


def cfg(allow=()):
    return PhoneConfig(listen="127.0.0.1:0", allow=tuple(allow), pbx_address=None)


class Rig:
    """A running server whose `on_call` records the calls it gets and runs `behaviour(call)`."""

    def __init__(self, allow=(), behaviour=None):
        self.calls: list[AudioSocketCall] = []
        self.finished = asyncio.Event()
        self.behaviour = behaviour
        self.allow = allow

    async def __aenter__(self):
        async def on_call(call):
            self.calls.append(call)
            try:
                if self.behaviour:
                    await self.behaviour(call)
            finally:
                self.finished.set()
        self.server = await serve(cfg(self.allow), on_call)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        with contextlib.suppress(asyncio.TimeoutError):       # a stuck call must fail its test, not hang the suite
            await asyncio.wait_for(self.server.wait_closed(), 3)

    async def connect(self):
        return await asyncio.open_connection("127.0.0.1", self.port)

    async def first_call(self):
        while not self.calls:
            await asyncio.sleep(0.01)


async def eof(reader):
    """True once the server has closed its side (a reset counts as closed too)."""
    try:
        return await asyncio.wait_for(reader.read(), 2) == b""
    except ConnectionError:
        return True


async def collect(call, into):
    async for ev in call.events():
        into.append(ev)


def test_encode_round_trip():
    assert encode(0x10, PCM) == b"\x10\x01\x40" + PCM
    assert encode(0x01, UID.bytes) == b"\x01\x00\x10" + UID.bytes


async def test_read_frame_decodes():
    r = asyncio.StreamReader()
    r.feed_data(encode(0x01, UID.bytes) + encode(0x00, b""))
    assert await read_frame(r) == (0x01, UID.bytes)
    assert await read_frame(r) == (0x00, b"")


async def test_split_reads_reassemble():
    r = asyncio.StreamReader()
    data = encode(0x10, PCM)
    task = asyncio.ensure_future(read_frame(r))
    for chunk in (data[:2], data[2:100], data[100:]):
        await asyncio.sleep(0.01)
        r.feed_data(chunk)
    assert await task == (0x10, PCM)


async def test_short_read_raises():
    r = asyncio.StreamReader()
    r.feed_data(encode(0x10, PCM)[:50])
    r.feed_eof()
    with pytest.raises(asyncio.IncompleteReadError):
        await read_frame(r)


async def test_disallowed_peer_is_dropped_unread():
    async with Rig(allow=("192.0.2.1",)) as rig:
        reader, writer = await rig.connect()
        writer.write(encode(0x01, UID.bytes))
        assert await eof(reader)
        await asyncio.sleep(0.05)
        assert rig.calls == []
        writer.close()


async def test_empty_allow_on_loopback_admits_loopback():
    async with Rig() as rig:
        _, writer = await rig.connect()
        writer.write(encode(0x01, UID.bytes))
        await asyncio.wait_for(rig.first_call(), 2)
        assert rig.calls[0].line_id == str(UID)
        writer.close()


async def test_listed_loopback_is_admitted():
    async with Rig(allow=("127.0.0.1",)) as rig:
        _, writer = await rig.connect()
        writer.write(encode(0x01, UID.bytes))
        await asyncio.wait_for(rig.first_call(), 2)
        writer.close()


async def test_first_frame_must_be_uuid():
    async with Rig() as rig:
        reader, writer = await rig.connect()
        writer.write(encode(0x10, PCM))
        assert await eof(reader)
        assert rig.calls == []
        writer.close()


async def test_first_frame_uuid_must_be_16_bytes():
    async with Rig() as rig:
        reader, writer = await rig.connect()
        writer.write(encode(0x01, b"short"))
        assert await eof(reader)
        assert rig.calls == []
        writer.close()


async def test_oversized_length_closes():
    async with Rig() as rig:
        reader, writer = await rig.connect()
        writer.write(b"\x01\xff\xff" + b"\0" * 100)
        assert await eof(reader)
        assert rig.calls == []
        writer.close()


async def test_short_first_frame_closes():
    async with Rig() as rig:
        reader, writer = await rig.connect()
        writer.write(b"\x01\x00\x10abc")
        await asyncio.sleep(0.05)
        writer.close()
        await asyncio.sleep(0.1)
        assert rig.calls == []


async def test_digit_audio_and_hangup_events():
    seen = []
    async with Rig(behaviour=lambda call: collect(call, seen)) as rig:
        _, writer = await rig.connect()
        writer.write(encode(0x01, UID.bytes) + encode(0x03, b"5") + encode(0x10, PCM)
                     + encode(0x7E, b"xx") + encode(0x00, b""))
        await asyncio.wait_for(rig.finished.wait(), 2)
        assert seen == [Digit("5"), Audio(PCM), HungUp("caller")]
        assert rig.calls[0].sample_rate == 8000
        writer.close()


async def test_eof_is_caller_hangup():
    seen = []
    async with Rig(behaviour=lambda call: collect(call, seen)) as rig:
        _, writer = await rig.connect()
        writer.write(encode(0x01, UID.bytes))
        await asyncio.sleep(0.05)
        writer.close()
        await asyncio.wait_for(rig.finished.wait(), 2)
        assert seen == [HungUp("caller")]


async def test_error_frame_is_error_hangup():
    seen = []
    async with Rig(behaviour=lambda call: collect(call, seen)) as rig:
        _, writer = await rig.connect()
        writer.write(encode(0x01, UID.bytes) + encode(0xFF, b"\x01"))
        await asyncio.wait_for(rig.finished.wait(), 2)
        assert seen == [HungUp("error")]
        writer.close()


async def test_send_wraps_audio_and_hang_up_is_idempotent():
    result = {}

    async def behaviour(call):
        await call.send(PCM)
        await call.hang_up()
        await call.hang_up()
        with pytest.raises(Exception):
            await call.send(PCM)
        result["ok"] = True

    async with Rig(behaviour=behaviour) as rig:
        reader, writer = await rig.connect()
        writer.write(encode(0x01, UID.bytes))
        data = await asyncio.wait_for(reader.read(), 2)
        assert data == encode(0x10, PCM) + encode(0x00, b"")
        await asyncio.wait_for(rig.finished.wait(), 2)
        assert result == {"ok": True}
        writer.close()


async def test_send_after_peer_gone_raises_and_hang_up_is_safe():
    result = {}

    async def behaviour(call):
        async for _ in call.events():
            pass                                    # the caller hung up
        with pytest.raises(Exception):
            await call.send(PCM)
        await call.hang_up()
        await call.hang_up()
        result["ok"] = True

    async with Rig(behaviour=behaviour) as rig:
        _, writer = await rig.connect()
        writer.write(encode(0x01, UID.bytes))
        await asyncio.sleep(0.05)
        writer.close()
        await asyncio.wait_for(rig.finished.wait(), 2)
        assert result == {"ok": True}


async def test_connection_closes_after_on_call_returns():
    async with Rig() as rig:
        reader, writer = await rig.connect()
        writer.write(encode(0x01, UID.bytes))
        assert await asyncio.wait_for(reader.read(), 2) == encode(0x00)   # told to hang up, then closed
        writer.close()


async def test_on_call_exception_is_contained():
    async def behaviour(call):
        raise RuntimeError("boom")

    async with Rig(behaviour=behaviour) as rig:
        reader, writer = await rig.connect()
        writer.write(encode(0x01, UID.bytes))
        assert await asyncio.wait_for(reader.read(), 2) == encode(0x00)   # told to hang up, then closed
        writer.close()


async def test_serve_refuses_public_listen_without_allow_and_off():
    async def on_call(call): ...
    with pytest.raises(ValueError):
        await serve(PhoneConfig(listen="0.0.0.0:0", allow=(), pbx_address=None), on_call)
    with pytest.raises(ValueError):
        await serve(PhoneConfig(listen=None, allow=(), pbx_address=None), on_call)


# --- liveness: a consumer that stops, and a peer that stalls -------------------------------------------------

async def test_cancelled_events_consumer_leaves_the_call_up():
    """Task 9 stops its receiver before an ending clip; the clip and the 0x00 must still go out."""
    result = {}

    async def behaviour(call):
        consumer = asyncio.ensure_future(collect(call, []))
        await asyncio.sleep(0.05)
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        await call.send(PCM)
        await call.hang_up()
        result["ok"] = True

    async with Rig(behaviour=behaviour) as rig:
        reader, writer = await rig.connect()
        writer.write(encode(0x01, UID.bytes))
        data = await asyncio.wait_for(reader.read(), 3)
        assert data == encode(0x10, PCM) + encode(0x00)
        assert result == {"ok": True}
        writer.close()


def _stalled_peer(port):
    """A raw peer with a tiny receive buffer that sends its line id and then never reads."""
    import socket
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    sock.connect(("127.0.0.1", port))
    sock.sendall(encode(0x01, UID.bytes))
    return sock


async def test_stalled_peer_makes_send_raise_quickly_and_hang_up_frees_the_call():
    result = {}
    seen = []

    async def behaviour(call):
        events = asyncio.ensure_future(collect(call, seen))
        async def flood():
            for _ in range(200_000):
                await call.send(PCM)
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(flood(), 5)       # a stall must raise ConnectionError, not hang
        await call.hang_up()
        await call.hang_up()
        await asyncio.wait_for(events, 2)
        result["closed"] = call._writer.transport.is_closing()

    async with Rig(behaviour=behaviour) as rig:
        sock = _stalled_peer(rig.port)
        await asyncio.wait_for(rig.finished.wait(), 10)
        sock.close()
        assert result == {"closed": True}
        assert seen == [HungUp("caller")]


async def test_hang_up_wakes_a_blocked_send():
    result = {}

    async def behaviour(call):
        async def pump():
            while True:
                await call.send(PCM)
        pumping = asyncio.ensure_future(pump())
        await asyncio.sleep(0.3)
        await call.hang_up()
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(pumping, 2)
        result["ok"] = True

    async with Rig(behaviour=behaviour) as rig:
        sock = _stalled_peer(rig.port)
        await asyncio.wait_for(rig.finished.wait(), 10)
        sock.close()
        assert result == {"ok": True}
