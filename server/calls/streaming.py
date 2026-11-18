"""Hearing that streams the caller's audio while they speak (spec 2026-11-17-call-bench-round2).

Round 1's engines got the whole clip only after the caller stopped. These two get it as it is spoken, at
real-time pace, followed by `tail_s` of silence up to the end-of-turn decision; then the stream is closed (Google)
or the buffer committed (OpenAI), and `seconds` is from that moment to the final text: what streaming leaves of
the hearing wait.

- `google-stream:<recognizer>[@<model>]`: Speech-to-Text v2 StreamingRecognize, which exists only over gRPC
  (google-cloud-speech, installed for the benchmark, not in the app's requirements). The recognizer's region picks
  the endpoint; `_` with a model is the implicit recognizer. Measured 2026-11-17: chirp_2 streams in europe-west4
  and us-central1 but not asia-southeast1 (Private GA there: 404), chirp_3 in `us`.
- `openai-rt:<model>`: OpenAI's Realtime API in transcription mode over a websocket (gpt-live-transcribe is the
  model its docs start with), 24 kHz 16-bit PCM, its own turn detection off: we commit."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping

import numpy as np
import soundfile as sf

from .endturn import to_16k
from .voice import Heard, SetupError

REALTIME_URL = "wss://api.openai.com/v1/realtime?intent=transcription"


def _private(text: str) -> str:
    """An error as safe to print: Google names the project in its errors."""
    return re.sub(r"projects/[^/\s'\"]+", "projects/<project>", text)


def pcm16(wav: bytes, rate: int) -> bytes:
    """A recording as mono 16-bit little-endian PCM at `rate`."""
    samples, source = sf.read(io.BytesIO(wav), dtype="float32", always_2d=True)
    mono = samples.mean(axis=1)
    if source != rate:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(source, rate)
        mono = resample_poly(mono, rate // g, source // g)
    return (np.clip(mono, -1.0, 1.0) * 32767).astype("<i2").tobytes()


async def paced(pcm: bytes, bytes_per_s: int, *, chunk_s: float = 0.1, tail_s: float = 0.2,
                sleep: Callable[[float], Awaitable] = asyncio.sleep) -> AsyncIterator[bytes]:
    """The clip in `chunk_s` pieces, then `tail_s` of silence, one piece per `chunk_s`: as a phone line sends it."""
    size = max(2, int(bytes_per_s * chunk_s) // 2 * 2)
    tail = b"\0" * (int(bytes_per_s * tail_s) // 2 * 2)
    for data in (pcm, tail):
        for at in range(0, len(data), size):
            yield data[at:at + size]
            await sleep(chunk_s)


async def google_requests(recognizer: str, config, chunks: AsyncIterator[bytes]):
    """StreamingRecognize's requests: the recognizer and config first, then audio only (at most 15 KB each)."""
    from google.cloud.speech_v2.types import cloud_speech
    yield cloud_speech.StreamingRecognizeRequest(recognizer=recognizer, streaming_config=config)
    async for chunk in chunks:
        yield cloud_speech.StreamingRecognizeRequest(audio=chunk)


class GoogleStreamEars:
    needs_audio = True
    where = "Google Cloud"
    streams = True   # a phone line could stream the whole call to it (the Cost section says what that costs)

    def __init__(self, recognizer: str, model: str, env: Mapping[str, str], *, chunk_s: float = 0.1,
                 tail_s: float = 0.2) -> None:
        found = re.fullmatch(r"projects/[^/]+/locations/([^/]+)/recognizers/[^/]+", recognizer)
        if not found:
            raise SetupError("--hear google-stream needs projects/<p>/locations/<region>/recognizers/<id or _>")
        self.recognizer, self.model, self.region = recognizer, model, found.group(1)
        self.chunk_s, self.tail_s = chunk_s, tail_s
        self.label = f"google stream {model or 'recognizer'} {self.region}"
        self.client = None

    async def ready(self) -> None:
        try:
            from google.api_core.client_options import ClientOptions
            from google.cloud.speech_v2 import SpeechAsyncClient
        except ImportError:
            raise SetupError("google-stream needs google-cloud-speech in the benchmark's environment") from None
        key = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
        if key.startswith("~"):
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.expanduser(key)
        endpoint = "speech.googleapis.com" if self.region == "global" else f"{self.region}-speech.googleapis.com"
        self.client = SpeechAsyncClient(client_options=ClientOptions(api_endpoint=endpoint))

    def _config(self, language: str | None):
        from google.cloud.speech_v2.types import cloud_speech
        code = language or "en"
        code = "en-US" if code == "en" else code
        decoding = cloud_speech.ExplicitDecodingConfig(
            encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16, sample_rate_hertz=16000,
            audio_channel_count=1)
        config = cloud_speech.RecognitionConfig(explicit_decoding_config=decoding, language_codes=[code],
                                                **({"model": self.model} if self.model else {}))
        return cloud_speech.StreamingRecognitionConfig(
            config=config, streaming_features=cloud_speech.StreamingRecognitionFeatures(interim_results=True))

    async def hear(self, wav: bytes | None, said: str, language: str | None) -> Heard:
        if self.client is None:
            await self.ready()
        pcm = pcm16(wav or b"", 16000)
        closed: dict[str, float] = {}

        async def chunks():
            async for chunk in paced(pcm, 32000, chunk_s=self.chunk_s, tail_s=self.tail_s):
                yield chunk
            closed["at"] = time.perf_counter()

        finals, last = [], None
        try:
            stream = await self.client.streaming_recognize(
                requests=google_requests(self.recognizer, self._config(language), chunks()))
            async for response in stream:
                for result in response.results:
                    if result.is_final and result.alternatives:
                        finals.append(result.alternatives[0].transcript.strip())
                        last = time.perf_counter()
            error = None
        except Exception as e:
            error = _private(f"{type(e).__name__}: {e}")[:300]
        ended = time.perf_counter()
        seconds = max(0.0, (last or ended) - closed.get("at", ended))
        billed = (len(pcm) / 32000) + self.tail_s
        return Heard(" ".join(f for f in finals if f), seconds, error=error, billed_s=billed)


class OpenAIRealtimeEars:
    needs_audio = True
    where = "OpenAI"
    streams = True   # a phone line could stream the whole call to it (the Cost section says what that costs)

    def __init__(self, model: str, env: Mapping[str, str], *, url: str = REALTIME_URL, chunk_s: float = 0.1,
                 tail_s: float = 0.2, timeout_s: float = 30.0) -> None:
        self.key = (env.get("SPEECH_API_KEY") or env.get("INFERENCE_OPENAI_API_KEY") or "").strip()
        if not self.key:
            raise SetupError("openai-rt needs SPEECH_API_KEY or INFERENCE_OPENAI_API_KEY")
        self.model, self.url = model, url
        self.chunk_s, self.tail_s, self.timeout_s = chunk_s, tail_s, timeout_s
        self.label = f"openai realtime {model}"

    async def ready(self) -> None:
        return None

    def _session(self, language: str | None) -> dict:
        lang = (language or "en").split("-")[0]
        transcription = {"model": self.model}
        if self.model.startswith("gpt-live") or self.model == "gpt-transcribe":
            transcription["languages"] = [lang]
        else:
            transcription["language"] = lang
        return {"type": "session.update", "session": {"type": "transcription", "audio": {"input": {
            "format": {"type": "audio/pcm", "rate": 24000}, "transcription": transcription, "turn_detection": None}}}}

    @staticmethod
    async def _until(ws, wanted: set[str]) -> dict:
        async for raw in ws:
            event = json.loads(raw)
            kind = event.get("type", "")
            if kind == "error" or kind.endswith(".failed"):
                detail = (event.get("error") or {}).get("message") or json.dumps(event)[:200]
                raise RuntimeError(f"OpenAI: {detail}")
            if kind in wanted:
                return event
        raise RuntimeError("OpenAI closed the connection")

    async def hear(self, wav: bytes | None, said: str, language: str | None) -> Heard:
        import websockets
        pcm = pcm16(wav or b"", 24000)
        committed = None
        try:
            async with websockets.connect(self.url, additional_headers={"Authorization": f"Bearer {self.key}"},
                                          max_size=None) as ws:
                await ws.send(json.dumps(self._session(language)))
                await asyncio.wait_for(self._until(ws, {"session.updated"}), self.timeout_s)
                # Read while sending, as a live call would: the service streams partial text meanwhile, and left
                # unread it held the final text back by about 2 s (measured 2026-11-17).
                final = asyncio.create_task(self._until(ws, {"conversation.item.input_audio_transcription.completed"}))
                try:
                    async for chunk in paced(pcm, 48000, chunk_s=self.chunk_s, tail_s=self.tail_s):
                        if final.done():
                            break
                        await ws.send(json.dumps({"type": "input_audio_buffer.append",
                                                  "audio": base64.b64encode(chunk).decode()}))
                    if not final.done():
                        await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                    committed = time.perf_counter()
                    done = await asyncio.wait_for(final, self.timeout_s)
                    got = time.perf_counter()   # not after the closing handshake: OpenAI's side takes ~2 s
                finally:
                    final.cancel()
        except Exception as e:
            seconds = time.perf_counter() - committed if committed else 0.0
            return Heard("", seconds, error=f"{type(e).__name__}: {e}"[:300])
        usage = done.get("usage") or {}
        billed = usage.get("seconds") if usage.get("type") == "duration" else None
        return Heard((done.get("transcript") or "").strip(), got - committed, billed_s=billed)
