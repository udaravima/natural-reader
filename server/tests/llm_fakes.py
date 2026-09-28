"""Programmable provider HTTP for adapter tests (httpx.MockTransport).

A route's responses are consumed in order; the last one repeats. Give
factories (lambdas), not Response objects: a streamed body can be read once.
An Exception instead of a factory is raised by the transport.
"""
from __future__ import annotations

import json

import httpx


def streamed(body: bytes, status: int = 200,
             content_type: str = "application/x-ndjson") -> httpx.Response:
    # An async-iterator body keeps the response unconsumed, like a real
    # streamed reply (Response(content=bytes) is eagerly read).
    async def gen():
        yield body
    return httpx.Response(status, content=gen(), headers={"content-type": content_type})


def ndjson(*payloads) -> httpx.Response:
    return streamed(b"".join(json.dumps(p).encode() + b"\n" for p in payloads))


def sse(*payloads, done: bool = True) -> httpx.Response:
    parts = [b"data: " + json.dumps(p).encode() + b"\n\n" for p in payloads]
    if done:
        parts.append(b"data: [DONE]\n\n")
    return streamed(b"".join(parts), content_type="text/event-stream")


class FakeUpstream:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], list] = {}

    def on(self, method: str, path: str, *responses) -> "FakeUpstream":
        self.routes.setdefault((method, path), []).extend(responses)
        return self

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.routes.get((request.method, request.url.path))
        if not queue:
            return httpx.Response(404, json={"error": f"no route {request.url.path}"})
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item()

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def bodies(self, path: str) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.url.path == path]
