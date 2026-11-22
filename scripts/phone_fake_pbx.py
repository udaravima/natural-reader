"""A fake PBX: calls a phone line over AudioSocket the way Asterisk would, so the phone service can be tried without
a PBX or a phone (docs/PHONE_LINES.md, docs/TESTING.md).

It connects, sends the line's UUID (frame 0x01), and then does what Asterisk does for the whole call: sends one 20 ms
audio frame (320 bytes of s16le, 8 kHz) every 20 ms, in real time, and never stops. While no WAV is playing the
frames are silence, because the app's voice-activity detector needs that silence to know a question is over. It waits
for the greeting to end (what it receives goes quiet after sound), then plays each WAV in turn: the time from the end
of the question to the first sound that comes back is what a caller waits, and it is printed per question. When the
last answer has ended it hangs up (0x00). Everything received is written to `phone-call-<time>.wav` (8 kHz, mono).

    python scripts/phone_fake_pbx.py <line id> question1.wav question2.wav [--address 192.0.2.10:9092] [--out DIR]

The line id is the one the console shows in its Asterisk snippet. Exit code: 0 when every question was answered,
1 when one was not (or the app hung up first), 2 when the call could not be made. Needs numpy, scipy and
soundfile (they are in requirements.txt); it imports nothing from the app."""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
import wave
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

RATE = 8000
FRAME_S = 0.02
FRAME_BYTES = 320                     # 20 ms of s16le mono at 8 kHz
KIND_HANGUP, KIND_UUID, KIND_AUDIO, KIND_ERROR = 0x00, 0x01, 0x10, 0xFF
DEFAULT_ADDRESS = "127.0.0.1:9092"


def frame(kind: int, payload: bytes = b"") -> bytes:
    return bytes([kind]) + len(payload).to_bytes(2, "big") + payload


def load_8k(path: Path) -> bytes:
    """A WAV as s16le mono 8 kHz, padded with silence to whole 20 ms frames."""
    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if rate != RATE:
        g = gcd(RATE, rate)
        audio = resample_poly(audio, RATE // g, rate // g)
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    return pcm + bytes(-len(pcm) % FRAME_BYTES)


def rms(pcm: bytes) -> float:
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    return float(np.sqrt(np.mean(samples ** 2))) if samples.size else 0.0


class Call:
    """The PBX's end of one call."""

    def __init__(self, args: argparse.Namespace, questions: list[tuple[str, bytes]]) -> None:
        self.args = args
        self.questions = questions
        self.received = bytearray()           # everything the app sent, silence included
        self.sound_at: list[float] = []       # receive time of every frame that carried sound
        self.last_sound = 0.0                 # receive time of the latest sound (0 = none yet)
        self.ended = ""                       # why the call is over from the app's side: "hung up" or "error"
        self.results: list[float | None] = []

    async def read_loop(self, reader: asyncio.StreamReader) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                head = await reader.readexactly(3)
                payload = await reader.readexactly(int.from_bytes(head[1:], "big"))
                if head[0] == KIND_AUDIO:
                    now = loop.time()
                    self.received += payload
                    if rms(payload) >= self.args.sound_rms:
                        self.last_sound = now
                        self.sound_at.append(now)
                elif head[0] == KIND_HANGUP:
                    self.ended = "hung up"
                    return
                elif head[0] == KIND_ERROR:
                    self.ended = "error"
                    return
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            self.ended = self.ended or "hung up"

    def quiet_for(self, now: float, since: float, seconds: float) -> bool:
        """Sound was heard after `since`, and nothing loud has arrived for `seconds`."""
        return self.last_sound > since and now - self.last_sound >= seconds

    async def run(self, writer: asyncio.StreamWriter) -> int:
        loop = asyncio.get_running_loop()
        args = self.args
        silence = bytes(FRAME_BYTES)
        playing = b""                    # what is left of the WAV being played
        queue = list(self.questions)
        state = "greeting"               # greeting -> (playing -> waiting -> listening) per question
        started = loop.time()
        mark = started                   # when the current state began
        question_end = 0.0
        next_tick = started
        while not self.ended:
            now = loop.time()
            if state == "greeting":
                if self.quiet_for(now, 0.0, args.quiet_s):
                    state, mark = "next", now
                elif now - started > args.greeting_timeout:
                    print(f"No greeting came within {args.greeting_timeout:g} s.")
                    return 1
            if state == "next":
                if not queue:
                    break
                name, playing = queue.pop(0)
                print(f"Question {len(self.results) + 1} ({name}): speaking")
                state = "playing"
            chunk = silence
            if state == "playing":
                chunk, playing = playing[:FRAME_BYTES], playing[FRAME_BYTES:]
                if not playing:
                    state, question_end = "waiting", now + FRAME_S
            elif state == "waiting":
                first = next((t for t in self.sound_at if t > question_end), None)
                if first is not None:
                    self.results.append(first - question_end)
                    print(f"Question {len(self.results)}: first sound {first - question_end:.2f} s after it ended")
                    state, mark = "listening", now
                elif now - question_end > args.answer_timeout:
                    self.results.append(None)
                    print(f"Question {len(self.results)}: no answer within {args.answer_timeout:g} s")
                    return self._finish(1)
            elif state == "listening" and self.quiet_for(now, question_end, args.quiet_s):
                sound = sum(1 for t in self.sound_at if t > question_end) * FRAME_S
                print(f"Question {len(self.results)}: the answer held {sound:.1f} s of sound")
                state = "next"
            try:
                writer.write(frame(KIND_AUDIO, chunk))
                await writer.drain()
            except (ConnectionError, OSError):
                self.ended = self.ended or "hung up"
                break
            next_tick += FRAME_S
            await asyncio.sleep(max(0.0, next_tick - loop.time()))
        return self._finish(0)

    def _finish(self, code: int) -> int:
        if self.ended:
            print(f"The app ended the call ({self.ended}).")
            code = max(code, 1) if len(self.results) < len(self.questions) else code
        return code


def save(received: bytes, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"phone-call-{time.strftime('%Y%m%d-%H%M%S')}.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(received))
    return path


async def main(args: argparse.Namespace) -> int:
    host, _, port = args.address.rpartition(":")
    host = host.removeprefix("[").removesuffix("]")
    try:
        line = uuid.UUID(args.line_id)
        questions = [(Path(p).name, load_8k(Path(p))) for p in args.wavs]
        reader, writer = await asyncio.open_connection(host, int(port))
    except (ValueError, OSError) as exc:
        print(f"Can't make the call: {exc}", file=sys.stderr)
        return 2
    call = Call(args, questions)
    writer.write(frame(KIND_UUID, line.bytes))
    reading = asyncio.ensure_future(call.read_loop(reader))
    try:
        code = await call.run(writer)
    finally:
        if not call.ended:
            try:
                writer.write(frame(KIND_HANGUP))      # the caller hangs up, as Asterisk tells the app
                await writer.drain()
            except (ConnectionError, OSError):
                pass
        await asyncio.sleep(0.1)                      # let the hang-up leave before the socket closes
        reading.cancel()
        writer.close()
        await asyncio.gather(reading, return_exceptions=True)
    path = save(bytes(call.received), Path(args.out))
    print(f"Recorded {len(call.received) / 2 / RATE:.1f} s of what came back: {path}")
    return code


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Call a phone line over AudioSocket as Asterisk would.")
    p.add_argument("line_id", help="the line's id (a UUID), as the console's Asterisk snippet shows it")
    p.add_argument("wavs", nargs="+", metavar="question.wav", help="the questions to ask, in order (any WAV)")
    p.add_argument("--address", default=DEFAULT_ADDRESS, metavar="HOST:PORT",
                   help=f"the app's AudioSocket listener (default {DEFAULT_ADDRESS})")
    p.add_argument("--out", default=".", metavar="DIR", help="where phone-call-<time>.wav goes (default: here)")
    p.add_argument("--sound-rms", type=float, default=100.0,
                   help="a received frame is sound when its RMS (of 32768) is at least this (default 100)")
    p.add_argument("--quiet-s", type=float, default=1.5,
                   help="seconds of quiet after sound that end the greeting or an answer (default 1.5; a slow "
                        "voice pauses between sentences, so raise it on a slow machine)")
    p.add_argument("--greeting-timeout", type=float, default=30.0, help="seconds to wait for the greeting")
    p.add_argument("--answer-timeout", type=float, default=120.0,
                   help="seconds to wait for the first sound of an answer (default 120)")
    return p


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parser().parse_args())))
