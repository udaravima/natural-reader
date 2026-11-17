"""Connection timing for cloud calls (spec 2026-11-17-call-simulation), against a real local server: the trace
hooks are httpcore's, so a fake transport can't show them."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from server.calls.nettrace import RequestTrace, TracingTransport


class Hello(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Length", "5")
        self.end_headers()
        self.wfile.write(b"hello")

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    s = ThreadingHTTPServer(("127.0.0.1", 0), Hello)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


async def test_a_new_connection_is_timed_and_a_kept_open_one_is_marked_reused(server):
    sink: list[RequestTrace] = []
    async with httpx.AsyncClient(transport=TracingTransport(sink)) as c:
        for _ in range(2):
            r = await c.post(server, content=b"x" * 1000)
            assert r.text == "hello"
    first, second = sink
    assert first.host == "127.0.0.1"
    assert first.connect_s is not None and first.connect_s >= 0
    assert second.connect_s is None
    for t in sink:
        assert t.sent_s is not None and t.first_byte_s is not None and t.first_byte_s >= t.sent_s
        assert t.body_done_s is not None and t.body_done_s >= t.first_byte_s


def test_split_turns_marks_into_stages():
    t = RequestTrace("example.com", connect_s=0.1, sent_s=0.25, first_byte_s=1.25)
    assert t.split(1.5) == pytest.approx({"connect": 0.1, "upload": 0.15, "wait": 1.0, "download": 0.25})
    reused = RequestTrace("example.com", connect_s=None, sent_s=0.01, first_byte_s=0.5)
    assert reused.split(0.6) == pytest.approx({"connect": 0.0, "upload": 0.01, "wait": 0.49, "download": 0.1})
    assert reused.new_connection is False


def test_split_without_marks_puts_everything_in_wait():
    assert RequestTrace("example.com").split(2.0) == {"connect": 0.0, "upload": 0.0, "wait": 2.0, "download": 0.0}
