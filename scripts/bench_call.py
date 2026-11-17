#!/usr/bin/env python3
"""Benchmark a voice agent on scripted phone calls (spec docs/superpowers/specs/2026-11-17-call-simulation-design.md).

Every caller turn of every scripted call in a scenario pack (server/calls/scenarios/) goes through the app's own
engines, the way Talk runs a round: the caller's words are spoken (or read from a recording), heard by the speech
engine, answered by the chat model with the scenario's pretend tools, and the answer's pieces are voiced. Each stage
is timed, cloud requests are split into connection set-up, upload, the service's own time and download, and each
call is scored. Writes report.md and results.json, plus every clip, to the output folder.

    python scripts/bench_call.py --model gemini:gemini-3.1-flash-lite
    python scripts/bench_call.py --model gemini:gemini-3.1-flash-lite --hear whisper:small.en --voice kokoro:af_heart
    python scripts/bench_call.py --model gemini:gemini-3.1-flash-lite \\
        --hear google:projects/P/locations/asia-southeast1/recognizers/R --voice google:gemini-2.5-flash-tts:Kore
    python scripts/bench_call.py compare bench-out/run-a bench-out/run-b

Reads the same environment and .env as the backend (provider keys, GOOGLE_APPLICATION_CREDENTIALS, speech models).
Needs no database and no running backend. Exit status: 0 every call passed, 1 a call failed, 2 it couldn't run.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def parse(argv: list[str]) -> argparse.Namespace:
    if argv[:1] == ["compare"]:
        p = argparse.ArgumentParser(prog="bench_call.py compare", description="Runs side by side")
        p.add_argument("runs", nargs="+", help="output folders (or their results.json)")
        args = p.parse_args(argv[1:])
        args.command = "compare"
        return args
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--scenario", default="hotel", help="a pack in server/calls/scenarios, or a folder (default hotel)")
    p.add_argument("--model", required=True, help="chat model, provider:name, e.g. gemini:gemini-3.1-flash-lite")
    p.add_argument("--hear", default="text", help="text | whisper:<model id> | google:<recognizer> (default text)")
    p.add_argument("--voice", default="none", help="none | kokoro[:<voice>] | google[:<model>[:<voice>]]")
    p.add_argument("--caller-voice", default="kokoro:bm_george",
                   help="who speaks the caller's lines when there's no recording (default kokoro:bm_george)")
    p.add_argument("--caller-audio", type=Path, help="a folder of recordings named <call id>-<turn>.wav")
    p.add_argument("--calls", help="comma-separated call ids (default: every call)")
    p.add_argument("--repeat", type=int, default=1, help="play every call this many times")
    p.add_argument("--speak", choices=["all", "first"], default="all",
                   help="voice every piece of a reply (default) or only the first (fewer cloud voice requests)")
    p.add_argument("--model-rpm", type=int, default=0,
                   help="at most this many model requests a minute (Gemini's free tier allows 15); 0 no limit. "
                        "Waiting for the quota is left out of every timing")
    p.add_argument("--voice-rpm", type=int, default=0,
                   help="at most this many voice requests a minute (Gemini-TTS projects often allow 10); 0 no limit")
    p.add_argument("--silence-ms", type=float, default=_env_number("SPEECH_WINDOW_SILENCE_MS", 700),
                   help="silence Talk waits for before a turn ends (default SPEECH_WINDOW_SILENCE_MS, else 700)")
    p.add_argument("--first-clause", type=int, default=int(_env_number("SPEECH_WINDOW_FIRST_CLAUSE_CHARS", 30)),
                   help="cut a long opening at a comma past this many characters (Talk's default 30; 0 never)")
    p.add_argument("--max-steps", type=int, default=4, help="model requests per turn, the last without tools")
    p.add_argument("--think", default="off", help="the model's thinking level: off | low | medium | high")
    p.add_argument("--out", type=Path, help="output folder (default bench-out/<scenario>-<time>)")
    args = p.parse_args(argv)
    args.command = "run"
    return args


async def run(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)

    import httpx

    from server import keepalive
    from server.calls.agent import CallAgent
    from server.calls.nettrace import RequestTrace, TracingTransport
    from server.calls.pacing import PacedRouter, PausableClock, Throttle
    from server.calls.playback import playback
    from server.calls.report import render
    from server.calls.scenario import ScenarioError, load_scenario
    from server.calls.scoring import score_call, wer
    from server.calls.voice import SetupError, make_ears, make_voice
    from server.llm.router import Router, UnknownModel, load_provider_configs
    from server.llm.types import CallSettings

    llm_sink: list[RequestTrace] = []
    try:
        scenario = load_scenario(args.scenario)
        scripts = [scenario.call(c.strip()) for c in args.calls.split(",")] if args.calls else list(scenario.calls)
        ears = make_ears(args.hear)
        voice = make_voice(args.voice)
        caller = make_voice(args.caller_voice) if ears.needs_audio else None
        # Built like the app's router client (server/llm/router.py), with a traced pool of the same limits.
        router = Router(load_provider_configs(os.environ), keepalive.new_client(
            transport=TracingTransport(llm_sink, limits=keepalive.LIMITS),
            timeout=httpx.Timeout(connect=5.0, read=_env_number("INFERENCE_TIMEOUT_S", 120), write=30.0, pool=5.0)))
        router.resolve(args.model)
        clock = PausableClock()
        router = PacedRouter(router, Throttle(args.model_rpm), clock)
        print(f"Getting {ears.label} ready…", flush=True)
        await ears.ready()
    except (ScenarioError, SetupError, UnknownModel) as e:
        print(f"Can't run: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    out = args.out or ROOT / "bench-out" / f"{scenario.id}-{datetime.now():%Y%m%d-%H%M%S}"
    (out / "audio").mkdir(parents=True, exist_ok=True)
    silence = args.silence_ms / 1000
    throttle = Throttle(args.voice_rpm)
    setup = {"scenario": scenario.id, "scenario_name": scenario.name, "repeat": args.repeat, "args": {
        k: str(v) for k, v in vars(args).items() if k != "command"},
        "shown": {"Model": args.model, "Hearing": f"{ears.label} ({ears.where})",
                  "Voice": f"{voice.label} ({voice.where})" + (", first piece only" if args.speak == "first" else ""),
                  "Caller": (f"recordings in {args.caller_audio}, else " if args.caller_audio else "")
                  + (caller.label if caller else "the script's text"),
                  "Silence": f"{silence:.2f} s", "When": f"{datetime.now():%Y-%m-%d %H:%M}"}}
    print(" · ".join(f"{k}: {v}" for k, v in setup["shown"].items()), flush=True)

    async def caller_audio(call_id: str, n: int, said: str) -> bytes:
        if args.caller_audio and (args.caller_audio / f"{call_id}-{n}.wav").is_file():
            return (args.caller_audio / f"{call_id}-{n}.wav").read_bytes()
        cached = out / "audio" / f"{call_id}-{n}-caller.wav"
        if cached.is_file():
            return cached.read_bytes()
        spoken = await caller.speak(said)
        if spoken.wav is None:
            raise SetupError(f"The caller's voice failed: {spoken.error}")
        cached.write_bytes(spoken.wav)
        return spoken.wav

    calls, turns = [], []
    for repeat in range(args.repeat):
        for script in scripts:
            tools = scenario.toolset()
            agent = CallAgent(router, args.model, scenario, tools, settings=CallSettings(think=args.think),
                              max_steps=args.max_steps, first_clause_chars=args.first_clause, clock=clock)
            replies: list[str] = []
            silent = 0
            print(f"\n{script.id}{f' #{repeat + 1}' if args.repeat > 1 else ''}: {script.title}", flush=True)
            for n, said in enumerate(script.caller, 1):
                row: dict = {"call": script.id, "repeat": repeat, "turn": n, "said": said, "silence_s": silence}
                try:
                    wav = await caller_audio(script.id, n, said) if ears.needs_audio else None
                except SetupError as e:
                    print(f"Can't run: {e}", file=sys.stderr)
                    return 2
                heard = await ears.hear(wav, said, script.language)
                row |= {"heard": heard.text, "hearing_s": heard.seconds if ears.needs_audio else None,
                        "hearing_net": heard.net, "hearing_new_connection": heard.new_connection,
                        "hearing_error": heard.error, "wer": wer(said, heard.text) if ears.needs_audio else None}
                if not heard.text:
                    row["voice_errors"] = []
                    turns.append(row)
                    replies.append("")
                    print(f"  {n}. heard nothing ({heard.error or 'empty'}); the turn is skipped", flush=True)
                    continue
                mark = len(llm_sink)
                turn = await agent.turn(heard.text)
                requests = [{"host": r.host, "new_connection": r.new_connection, "connect": r.connect_s or 0.0,
                             "first_byte": r.first_byte_s, "done": r.body_done_s} for r in llm_sink[mark:]]
                pieces = turn.pieces if args.speak == "all" else turn.pieces[:1]
                spoken = []
                for k, (text, _) in enumerate(pieces, 1):
                    await throttle.wait()
                    s = await voice.speak(text)
                    spoken.append(s)
                    if s.wav:
                        (out / "audio" / f"{script.id}-{n}-agent-{k}.wav").write_bytes(s.wav)
                play = playback([ready for _, ready in pieces], [s.seconds for s in spoken],
                                [s.audio_s for s in spoken], parallel=voice.parallel)
                first_sound = (silence + (heard.seconds if ears.needs_audio else 0.0) + play["first_audio_s"]
                               if pieces else None)
                row |= {"reply": turn.text, "model_first_word_s": turn.first_word_s,
                        "model_first_piece_s": pieces[0][1] if pieces else None, "model_whole_s": turn.whole_s,
                        "steps": turn.steps, "step_ends": turn.step_ends, "tool_s": turn.tool_s, "tokens": list(turn.tokens),
                        "tool_runs": [{"name": r.name, "arguments": r.arguments, "ok": r.ok,
                                       "error": r.result.get("error")} for r in turn.tool_runs],
                        "held_s": turn.held_s, "empty_after_tool": turn.empty_after_tool, "model_error": turn.error,
                        "model_requests": requests, "model_connect_s": sum(r["connect"] for r in requests),
                        "pieces": [{"text": t, "ready_s": ready, "voice_s": s.seconds, "audio_s": s.audio_s}
                                   for (t, ready), s in zip(pieces, spoken)],
                        "voice_first_s": spoken[0].seconds if spoken else None,
                        "voice_first_net": spoken[0].net if spoken else None,
                        "voice_first_new_connection": spoken[0].new_connection if spoken else None,
                        "voice_errors": [s.error for s in spoken if s.error],
                        "stalls_s": play["stalls_s"], "longest_stall_s": play["longest_stall_s"],
                        "first_sound_s": first_sound}
                turns.append(row)
                replies.append(turn.text)
                silent += 1 if turn.empty_after_tool else 0
                tools_used = ", ".join(r.name for r in turn.tool_runs)
                fmt = lambda v: "–" if v is None else f"{v:.2f}"   # noqa: E731
                print(f"  {n}. heard {fmt(row['hearing_s'])} · model first piece {fmt(row['model_first_piece_s'])} · "
                      f"voice {fmt(row['voice_first_s'])} · first sound {fmt(first_sound)} s"
                      f"{f' · tools: {tools_used}' if tools_used else ''}", flush=True)
                if ears.needs_audio and heard.text != said:
                    print(f"     heard: {heard.text}", flush=True)
                print(f"     agent: {turn.text.strip() or '(' + (turn.error or 'silence') + ')'}", flush=True)
            score = score_call(script, replies, tools.log, tools.state, scenario.data, unanswered=silent)
            calls.append({"id": script.id, "repeat": repeat, "title": script.title, "passed": score.passed,
                          "checks": [asdict(c) for c in score.checks]})
            print(f"  → {'PASS' if score.passed else 'FAIL'}"
                  + "".join(f"\n     {c.name}: {c.detail}" for c in score.checks if not c.ok), flush=True)

    await router.aclose()
    (out / "results.json").write_text(json.dumps({"setup": setup, "calls": calls, "turns": turns}, indent=1,
                                                 ensure_ascii=False))
    (out / "report.md").write_text(render(setup, calls, turns), encoding="utf-8")
    print(f"\n{sum(c['passed'] for c in calls)} of {len(calls)} calls passed. Report: {out / 'report.md'}")
    return 0 if all(c["passed"] for c in calls) else 1


def compare_runs(args: argparse.Namespace) -> int:
    from server.calls.report import compare
    runs = []
    for given in args.runs:
        path = Path(given)
        path = path / "results.json" if path.is_dir() else path
        runs.append((path.parent.name, json.loads(path.read_text(encoding="utf-8"))))
    print(compare(runs))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse(sys.argv[1:] if argv is None else argv)
    if args.command == "compare":
        return compare_runs(args)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
