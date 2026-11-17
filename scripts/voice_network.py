#!/usr/bin/env python3
"""How far the voice pipeline's cloud services are from this machine, and what keeping connections open would save
(spec docs/superpowers/specs/2026-11-17-call-simulation-design.md).

1. For every cloud host the configuration uses (Google speech-to-text for the recognizer, Gemini-TTS, Google's token
   service, each INFERENCE_*_URL that isn't this machine): name lookup, TCP, TLS and the first byte of a bare request,
   on a new connection each time. A new connection costs TCP plus TLS on every request that opens one.
2. With --live, real requests, first each on a new connection (what the app's speech and voice code does today) and
   then on one kept-open connection: a short Gemini-TTS sentence, a speech-to-text clip (--recognizer and --clip),
   and a chat model's first word (--model). These cost a few cloud requests and count against quotas.

    python scripts/voice_network.py
    python scripts/voice_network.py --live --model gemini:gemini-3.1-flash-lite \\
        --recognizer projects/P/locations/asia-southeast1/recognizers/R --clip question.wav

Reads the backend's environment and .env. Prints no credential. Times are milliseconds.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import os
import socket
import statistics
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TTS_HOST = "texttospeech.googleapis.com"
LOCAL = {"localhost", "127.0.0.1", "::1", "host.containers.internal"}
SENTENCE = "One moment while I check that for you."


def hosts() -> list[tuple[str, str]]:
    from server.speech.google_names import NAME, endpoint
    found = [(TTS_HOST, "Gemini-TTS"), ("oauth2.googleapis.com", "Google token (once an hour)")]
    recognizer = (os.environ.get("GOOGLE_SPEECH_RECOGNIZER") or "").strip()
    match = NAME.match(recognizer)
    if match:
        found.append((urlparse(endpoint(match.group(2))).hostname, f"speech-to-text ({match.group(2)})"))
    for name in (os.environ.get("INFERENCE_PROVIDERS") or "").split(","):
        url = os.environ.get(f"INFERENCE_{name.strip().upper()}_URL") or ""
        host = urlparse(url).hostname
        if host and host not in LOCAL:
            found.append((host, f"chat model ({name.strip()})"))
    return list(dict.fromkeys(found))


def ms(v: float | None) -> str:
    return "     –" if v is None else f"{v * 1000:6.0f}"


async def handshake(host: str, rounds: int) -> dict[str, float]:
    import httpx
    from server.calls.nettrace import RequestTrace, TracingTransport
    started = time.perf_counter()
    try:
        await asyncio.to_thread(socket.getaddrinfo, host, 443)
        lookup = time.perf_counter() - started
    except OSError:
        return {}
    rows = []
    for _ in range(rounds):
        sink: list[RequestTrace] = []
        async with httpx.AsyncClient(transport=TracingTransport(sink), timeout=20) as c:
            try:
                await c.get(f"https://{host}/")
            except httpx.HTTPError:
                continue
        t = sink[-1]
        tcp = t.connect_s or 0.0
        rows.append({"connect": tcp, "first_byte": (t.first_byte_s or 0) - (t.sent_s or 0)})
    if not rows:
        return {"lookup": lookup}
    return {"lookup": lookup, "connect": statistics.median(r["connect"] for r in rows),
            "first_byte": statistics.median(r["first_byte"] for r in rows)}


async def timed(make_request, rounds: int) -> tuple[list[dict], list[dict]]:
    """Each request on a new client, then `rounds` on one kept-open client (after one to open it)."""
    import httpx
    from server.calls.nettrace import RequestTrace, TracingTransport

    async def one(client, sink) -> dict:
        started = time.perf_counter()
        await make_request(client)
        trace = sink[-1]
        return trace.split(time.perf_counter() - trace.started_at) | {"total": time.perf_counter() - started}

    fresh, kept = [], []
    for _ in range(rounds):
        sink: list[RequestTrace] = []
        async with httpx.AsyncClient(transport=TracingTransport(sink), timeout=60) as c:
            fresh.append(await one(c, sink))
    sink = []
    async with httpx.AsyncClient(transport=TracingTransport(sink), timeout=60) as c:
        await make_request(c)
        for _ in range(rounds):
            kept.append(await one(c, sink))
    return fresh, kept


def show(label: str, fresh: list[dict], kept: list[dict]) -> None:
    med = lambda rows, k: statistics.median(r[k] for r in rows)   # noqa: E731
    print(f"\n{label}")
    print(f"  {'':24}{'connect':>8}{'upload':>8}{'wait':>8}{'download':>9}{'total':>8}")
    for name, rows in (("new connection (today)", fresh), ("kept-open connection", kept)):
        print(f"  {name:24}" + "".join(f"{ms(med(rows, k)):>8}" for k in ("connect", "upload", "wait"))
              + f"{ms(med(rows, 'download')):>9}{ms(med(rows, 'total')):>8}")
    net = lambda rows: statistics.median(r["connect"] + r["upload"] + r["download"] for r in rows)   # noqa: E731
    waits = sorted(r["wait"] for r in fresh + kept)
    print(f"  Network on a new connection: {net(fresh) * 1000:.0f} ms; kept open: {net(kept) * 1000:.0f} ms, so "
          f"keeping it open saves about {(net(fresh) - net(kept)) * 1000:.0f} ms a request.")
    print(f"  The service's own time (`wait`, plus one round trip) ran from {waits[0] * 1000:.0f} to "
          f"{waits[-1] * 1000:.0f} ms over these {len(waits)} requests.")


async def live(args) -> None:
    from server import google_cloud
    token, project = await google_cloud.access_token()
    headers = {"Authorization": f"Bearer {token}", **({"x-goog-user-project": project} if project else {})}
    model = (os.environ.get("GOOGLE_TTS_MODEL") or "").strip()
    if model:
        body = {"input": {"text": SENTENCE}, "voice": {"languageCode": "en-US", "name": "Kore", "modelName": model},
                "audioConfig": {"audioEncoding": "LINEAR16"}}

        async def speak(c):
            r = await c.post(f"https://{TTS_HOST}/v1/text:synthesize", headers=headers, json=body)
            r.raise_for_status()
        show(f"Gemini-TTS {model}, {len(SENTENCE)} characters", *await timed(speak, args.rounds))
    if args.recognizer and args.clip:
        from server.speech.google_names import NAME, endpoint
        match = NAME.match(args.recognizer)
        if not match:
            raise SystemExit("--recognizer must be projects/<p>/locations/<l>/recognizers/<id>")
        clip = {"config": {"autoDecodingConfig": {}}, "configMask": "autoDecodingConfig",
                "content": base64.b64encode(args.clip.read_bytes()).decode()}

        async def hear(c):
            r = await c.post(f"{endpoint(match.group(2))}/v2/{args.recognizer}:recognize", headers=headers, json=clip)
            r.raise_for_status()
        show(f"Speech-to-text {match.group(3)} ({match.group(2)}), {len(clip['content']) // 1024} KB",
             *await timed(hear, args.rounds))
    if args.model:
        await chat(args.model, args.rounds)


async def chat(model_id: str, rounds: int) -> None:
    import httpx
    from server.calls.nettrace import RequestTrace, TracingTransport
    from server.llm.router import Router, load_provider_configs
    from server.llm.types import CallSettings, Message, TextDelta

    async def first_word(router) -> float:
        started = time.perf_counter()
        async for chunk in router.stream_chat(model_id, [Message("user", "Say hello to a caller in five words.")],
                                              [], CallSettings()):
            if isinstance(chunk, TextDelta) and chunk.text.strip():
                return time.perf_counter() - started
        return time.perf_counter() - started

    rows = []
    for _ in range(rounds):
        sink: list[RequestTrace] = []
        router = Router(load_provider_configs(os.environ), httpx.AsyncClient(transport=TracingTransport(sink)))
        rows.append(("new", await first_word(router), sink[-1].connect_s))
        await router.aclose()
    sink = []
    router = Router(load_provider_configs(os.environ), httpx.AsyncClient(transport=TracingTransport(sink)))
    await first_word(router)
    for _ in range(rounds):
        mark = len(sink)
        rows.append(("kept", await first_word(router), sink[-1].connect_s if len(sink) > mark else None))
    await router.aclose()
    print(f"\nChat model {model_id}: first word")
    for kind, label in (("new", "new connection"), ("kept", "kept-open client")):
        mine = [r for r in rows if r[0] == kind]
        opened = sum(1 for r in mine if r[2] is not None)
        print(f"  {label:24}first word {ms(statistics.median(r[1] for r in mine))} ms; "
              f"{opened} of {len(mine)} requests opened a new connection")


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--live", action="store_true", help="also send real requests (a few, counted by quotas)")
    p.add_argument("--model", help="chat model for --live, provider:name")
    p.add_argument("--recognizer", default=os.environ.get("GOOGLE_SPEECH_RECOGNIZER"))
    p.add_argument("--clip", type=Path, help="a short WAV for the speech-to-text test")
    args = p.parse_args()
    print("Bare requests, a new connection each (median of", args.rounds, "):")
    print(f"  {'host':42}{'lookup':>7}{'connect':>8}{'1st byte':>9}  used for")
    for host, use in hosts():
        h = await handshake(host, args.rounds)
        print(f"  {host:42}{ms(h.get('lookup')):>7}{ms(h.get('connect')):>8}{ms(h.get('first_byte')):>9}  {use}")
    print("  connect = TCP + TLS: what every request that opens a new connection pays before it is sent.")
    if args.live:
        await live(args)
    return 0


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    raise SystemExit(asyncio.run(main()))
