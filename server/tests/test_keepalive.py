"""Kept-open connections to cloud services (the 2026-11-17 call benchmark: 72 of 73 requests in an all-Google call
opened a new connection, about 0.1 s each plus a slow start). Three causes, each pinned here: httpx drops an idle
connection after 5 s by default; Google's speech and voice built a client per request; every chat provider stopped
reading a stream at its end marker, so the connection was closed with the body unfinished."""
import asyncio
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import numpy as np
import pytest
import soundfile as sf

from server import chat_voice, google_cloud, keepalive
from server.calls.nettrace import RequestTrace, TracingTransport
from server.llm.providers.base import ProviderConfig
from server.llm.providers.gemini import GeminiProvider
from server.llm.providers.ollama import OllamaProvider
from server.llm.providers.openai_compat import OpenAICompatProvider
from server.llm.types import CallSettings, Message, TextDelta
from server.speech.google_engine import GoogleSpeech

# ---- idle connections live long enough for a caller to speak ----


def test_a_new_client_keeps_idle_connections_for_a_turn_of_speech():
    client = keepalive.new_client()
    assert keepalive.KEEPALIVE_S >= 60
    assert client._transport._pool._keepalive_expiry == keepalive.KEEPALIVE_S   # httpx's default is 5 s


def test_one_client_per_event_loop():
    holder = keepalive.PerLoopClient()

    async def twice():
        return holder.get(), holder.get()

    a1, a2 = asyncio.run(twice())
    b1, _ = asyncio.run(twice())
    assert a1 is a2
    assert b1 is not a1      # a client is tied to the loop it was made on (each test has its own loop)


async def test_a_closed_client_is_replaced():
    holder = keepalive.PerLoopClient()
    first = holder.get()
    await first.aclose()
    assert holder.get() is not first


# ---- chat providers finish reading a stream, so its connection is reused ----

SSE_OPENAI = [b'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}\n\n',
              b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n', b"data: [DONE]\n\n"]
SSE_GEMINI = [b'data: {"event_type":"step.start","index":0,"step":{"type":"text"}}\n\n',
              b'data: {"event_type":"step.delta","index":0,"delta":{"type":"text","text":"Hello"}}\n\n',
              b'data: {"event_type":"step.stop","index":0}\n\n',
              b'data: {"event_type":"interaction.completed","interaction":{"status":"completed"}}\n\n',
              b"data: [DONE]\n\n"]
NDJSON_OLLAMA = [b'{"message":{"role":"assistant","content":"Hello"},"done":false}\n',
                 b'{"message":{"role":"assistant","content":""},"done":true,"done_reason":"stop",'
                 b'"prompt_eval_count":3,"eval_count":1}\n']
STREAMS = {"/chat/completions": SSE_OPENAI, "/interactions": SSE_GEMINI, "/api/chat": NDJSON_OLLAMA}


class Fake(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):   # model listings, for capabilities
        self._json({"data": [{"id": "m"}], "models": [{"name": "models/m"}]})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path.endswith("/api/show"):
            return self._json({"capabilities": ["completion"], "model_info": {}})
        chunks = next((v for k, v in STREAMS.items() if self.path.endswith(k)), None)
        if chunks is None:
            return self._json({})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for c in chunks:
            self.wfile.write(f"{len(c):X}\r\n".encode() + c + b"\r\n")
            self.wfile.flush()
        time.sleep(0.02)   # the end of the body comes a moment after the end marker, as from a real server
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    s = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


@pytest.mark.parametrize("kind, cls, path", [("openai", OpenAICompatProvider, "/chat/completions"),
                                              ("gemini", GeminiProvider, "/interactions"),
                                              ("ollama", OllamaProvider, "/api/chat")])
async def test_a_providers_next_stream_reuses_the_connection(server, kind, cls, path):
    sink: list[RequestTrace] = []
    client = keepalive.new_client(transport=TracingTransport(sink, limits=keepalive.LIMITS))
    provider = cls(ProviderConfig(kind, kind, server, "key"), client)
    for _ in range(2):
        text = ""
        async for chunk in provider.stream_chat("m", [Message("user", "hi")], [], CallSettings()):
            if isinstance(chunk, TextDelta):
                text += chunk.text
        assert text == "Hello"
    chats = [t for t in sink if t.path.endswith(path)]
    assert len(chats) == 2
    assert chats[1].connect_s is None, "the second stream opened a new connection"
    await client.aclose()


# ---- Google's speech and voice share one kept-open client ----

def _wav() -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.zeros(1600, dtype=np.float32), 16000, format="WAV")
    return buf.getvalue()


async def _token():
    return "t", None


async def test_speech_and_voice_go_through_the_shared_google_client_and_leave_it_open():
    hosts = []

    def answer(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == "texttospeech.googleapis.com":
            buf = io.BytesIO()
            sf.write(buf, np.zeros(2400, dtype=np.float32), 24000, format="WAV")
            import base64
            return httpx.Response(200, json={"audioContent": base64.b64encode(buf.getvalue()).decode()})
        return httpx.Response(200, json={"results": [{"alternatives": [{"transcript": "hello"}]}]})

    shared = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    google_cloud.use_http(shared)
    try:
        engine = GoogleSpeech("projects/p/locations/asia-southeast1/recognizers/r", token=_token)
        for _ in range(2):
            assert (await engine.transcribe(_wav(), "audio/wav", None)).text == "hello"
            await chat_voice.google_speak("Hello.", "Kore", 1.0, {"GOOGLE_TTS_MODEL": "m"}, token=_token)
        assert hosts == ["asia-southeast1-speech.googleapis.com", "texttospeech.googleapis.com"] * 2
        assert not shared.is_closed
        assert google_cloud.http() is shared
    finally:
        google_cloud.reset_for_tests()
        await shared.aclose()


async def test_without_an_installed_client_google_gets_a_kept_open_one():
    google_cloud.reset_for_tests()
    client = google_cloud.http()
    assert client is google_cloud.http()
    assert client._transport._pool._keepalive_expiry == keepalive.KEEPALIVE_S
    await google_cloud.close_http()
    assert client.is_closed


def test_the_model_router_is_built_on_a_kept_open_client(monkeypatch):
    from server.llm import router
    made = []
    real = keepalive.new_client
    monkeypatch.setattr(keepalive, "new_client", lambda **kw: made.append(kw) or real(**kw))

    async def start_and_stop():
        await router.start_router()
        await router.stop_router()

    asyncio.run(start_and_stop())
    assert len(made) == 1
