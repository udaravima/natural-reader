"""Round 2 of the call benchmark: hearing that streams the caller's audio as it is spoken (spec
2026-11-17-call-bench-round2). The OpenAI realtime ears run against a local websocket server that plays the
service's part; nothing here reaches the internet."""
import asyncio
import base64
import io
import json

import numpy as np
import pytest
import soundfile as sf
import websockets

from server.calls.streaming import OpenAIRealtimeEars, paced


def wav(seconds=0.5, rate=24000):
    buf = io.BytesIO()
    sf.write(buf, 0.1 * np.sin(np.arange(int(seconds * rate)) / 10), rate, format="WAV")
    return buf.getvalue()


async def test_paced_sends_the_clip_then_the_tail_at_real_time_pace():
    slept = []

    async def sleep(s):
        slept.append(s)
    chunks = [c async for c in paced(b"\1" * 1000, 4000, chunk_s=0.1, tail_s=0.2, sleep=sleep)]
    assert [len(c) for c in chunks] == [400, 400, 200, 400, 400]
    assert b"".join(chunks)[1000:] == b"\0" * 800
    assert slept == [0.1] * 5


class FakeRealtime:
    """Plays the service: checks the session set-up, gathers the audio, answers the commit."""

    def __init__(self, answer):
        self.answer, self.session, self.audio = answer, None, b""

    async def handler(self, ws):
        self.session = json.loads(await ws.recv())
        await ws.send(json.dumps({"type": "session.updated", "session": self.session["session"]}))
        async for raw in ws:
            event = json.loads(raw)
            if event["type"] == "input_audio_buffer.append":
                self.audio += base64.b64decode(event["audio"])
            elif event["type"] == "input_audio_buffer.commit":
                await ws.send(json.dumps({"type": "input_audio_buffer.committed", "item_id": "item_1"}))
                await asyncio.sleep(0.05)
                await ws.send(json.dumps(self.answer))


async def hear_with(answer):
    fake = FakeRealtime(answer)
    async with websockets.serve(fake.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        ears = OpenAIRealtimeEars("gpt-live-transcribe", {"INFERENCE_OPENAI_API_KEY": "k"},
                                  url=f"ws://127.0.0.1:{port}", chunk_s=0.01)
        heard = await ears.hear(wav(), "said", "en")
    return fake, heard


async def test_openai_realtime_streams_the_clip_commits_and_takes_the_final_transcript():
    fake, heard = await hear_with({"type": "conversation.item.input_audio_transcription.completed",
                                   "item_id": "item_1", "transcript": " Book it, please. ",
                                   "usage": {"type": "duration", "seconds": 1}})
    audio = fake.session["session"]["audio"]["input"]
    assert fake.session["type"] == "session.update" and fake.session["session"]["type"] == "transcription"
    assert audio["format"] == {"type": "audio/pcm", "rate": 24000} and audio["turn_detection"] is None
    assert audio["transcription"] == {"model": "gpt-live-transcribe", "languages": ["en"]}
    assert len(fake.audio) == 2 * int(0.7 * 24000)   # the clip and 0.2 s of silence, 16-bit
    assert heard.text == "Book it, please." and heard.error is None
    assert 0.04 < heard.seconds < 1.0 and heard.billed_s == 1


async def test_hearing_time_stops_at_the_final_text_not_when_the_connection_closes(monkeypatch):
    """OpenAI's side took about 2 s over the closing handshake; that is not hearing time."""
    class SlowToClose:
        def __init__(self):
            self.events = asyncio.Queue()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            await asyncio.sleep(1.0)

        async def send(self, raw):
            kind = json.loads(raw)["type"]
            if kind == "session.update":
                await self.events.put({"type": "session.updated"})
            elif kind == "input_audio_buffer.commit":
                await self.events.put({"type": "conversation.item.input_audio_transcription.completed",
                                       "transcript": "Yes."})

        def __aiter__(self):
            return self

        async def __anext__(self):
            return json.dumps(await self.events.get())

    monkeypatch.setattr(websockets, "connect", lambda *a, **k: SlowToClose())
    ears = OpenAIRealtimeEars("gpt-live-transcribe", {"INFERENCE_OPENAI_API_KEY": "k"}, chunk_s=0.01)
    heard = await ears.hear(wav(), "said", "en")
    assert heard.text == "Yes." and heard.seconds < 0.5


async def test_an_error_from_the_service_is_an_empty_hearing_with_its_reason():
    _, heard = await hear_with({"type": "error", "error": {"message": "Invalid model"}})
    assert heard.text == "" and "Invalid model" in heard.error


async def test_the_google_stream_sends_its_config_first_then_audio_under_15_kb():
    speech = pytest.importorskip("google.cloud.speech_v2")
    from server.calls.streaming import google_requests
    config = speech.StreamingRecognitionConfig()

    async def chunks():
        for size in (3200, 3200, 1000):
            yield b"\0" * size
    requests = [r async for r in google_requests("projects/p/locations/l/recognizers/_", config, chunks())]
    assert requests[0].recognizer.endswith("/recognizers/_") and not requests[0].audio
    assert [len(r.audio) for r in requests[1:]] == [3200, 3200, 1000]


def test_the_streaming_hearing_engines_say_they_stream():
    from server.calls.voice import make_ears
    assert make_ears("openai-rt:gpt-live-transcribe", {"SPEECH_API_KEY": "k"}).streams is True
    assert make_ears("google-stream:projects/p/locations/us/recognizers/_@chirp_3", {}).streams is True
    assert getattr(make_ears("text"), "streams", False) is False   # sent only each finished turn
