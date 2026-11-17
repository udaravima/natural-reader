"""Where a cloud call's time goes (spec 2026-11-17-call-simulation): opening the connection, sending the request,
waiting for the service, receiving the answer.

TracingTransport is an httpx transport that hooks httpcore's trace events for every request it carries and appends
one RequestTrace per request to a list. `connect_s` is the TCP and TLS set-up of a new connection (None when a
kept-open connection was reused: that is the saving a shared client buys). Seconds are counted from the moment the
request entered the transport; the caller adds the total after reading the body, and `split` turns the marks into
stages. `wait` is the service's own time plus one network round trip; it can't be split further from here."""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx


@dataclass
class RequestTrace:
    host: str
    path: str = ""
    connect_s: float | None = None
    sent_s: float | None = None
    first_byte_s: float | None = None
    started_at: float = 0.0   # time.perf_counter() when the request entered the transport
    body_done_s: float | None = None   # the whole answer read (for a stream: its last byte)

    @property
    def new_connection(self) -> bool:
        return self.connect_s is not None

    def split(self, total_s: float) -> dict[str, float]:
        connect = self.connect_s or 0.0
        sent = self.sent_s if self.sent_s is not None else connect
        first = self.first_byte_s if self.first_byte_s is not None else total_s
        if self.sent_s is None and self.first_byte_s is None:
            return {"connect": connect, "upload": 0.0, "wait": total_s - connect, "download": 0.0}
        return {"connect": connect, "upload": max(0.0, sent - connect), "wait": max(0.0, first - sent),
                "download": max(0.0, total_s - first)}


class TracingTransport(httpx.AsyncHTTPTransport):
    def __init__(self, sink: list[RequestTrace], **kwargs) -> None:
        super().__init__(**kwargs)
        self.sink = sink

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        started = time.perf_counter()
        trace = RequestTrace(request.url.host, request.url.path, started_at=started)
        self.sink.append(trace)

        async def hook(name: str, info: dict) -> None:
            now = time.perf_counter() - started
            if name == "connection.connect_tcp.complete" and trace.connect_s is None:
                trace.connect_s = now
            elif name == "connection.start_tls.complete":
                trace.connect_s = now
            elif name.endswith(".send_request_body.complete"):
                trace.sent_s = now
            elif name.endswith(".receive_response_headers.complete"):
                trace.first_byte_s = now
            elif name.endswith(".receive_response_body.complete"):
                trace.body_done_s = now

        request.extensions = {**request.extensions, "trace": hook}
        return await super().handle_async_request(request)
