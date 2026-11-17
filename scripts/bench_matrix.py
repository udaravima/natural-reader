#!/usr/bin/env python3
"""Benchmark every hearing engine against every voice on the scenario's scripted calls, with one chat model, and
tell how each pair would feel to a caller (spec docs/superpowers/specs/2026-11-17-call-simulation-design.md).

1. Every caller clip goes through every hearing engine: text, word error rate, seconds, the network part.
2. Every hearing engine's transcripts are played through the chat model as whole calls (the scenario's tools,
   Talk's sentence cuts): the model's timings, and which calls each engine's hearing let succeed.
3. The replies of the conversation whose hearing was closest to the script are voiced by every voice: seconds to
   make each piece, its length, the network part, and the recording.
4. Each hearing x voice pair's wait (caller's last word to first sound) is composed from those stage times, as
   Talk runs them one after another, and rated against reference points for conversation timing.

Writes report.md, results.xlsx, one CSV per table, and recordings: the caller clips, every voiced piece, and each
call as it would sound with each voice, the measured waits included.

    python scripts/bench_matrix.py --model gemini:gemini-3.1-flash-lite --model-rpm 14 \\
        --hear whisper:small,whisper:small.en,google:projects/P/locations/L/recognizers/R,openai:whisper-1 \\
        --voice kokoro:af_heart,google:gemini-2.5-flash-tts:Kore,chirp:en-US-Chirp3-HD-Kore --voice-rpm 9

Reads the backend's environment and .env. Needs no database and no running backend. Exit status 0 when it ran
(the calls' results are in the report), 2 when it couldn't.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RATE = 24000   # recordings are written at Kokoro's and Google's rate


def parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--scenario", default="hotel")
    p.add_argument("--model", default="gemini:gemini-3.1-flash-lite", help="the chat model, provider:name")
    p.add_argument("--model-rpm", type=int, default=0, help="model requests a minute (Gemini's free tier: 15)")
    p.add_argument("--hear", required=True, help="comma-separated: whisper:<id>, google:<recognizer>, "
                                                 "openai:<model>[@<url>]")
    p.add_argument("--voice", required=True, help="comma-separated: kokoro[:<voice>], google[:<model>[:<voice>]], "
                                                  "chirp:<voice name>")
    p.add_argument("--voice-rpm", type=int, default=0, help="Gemini-TTS requests a minute, per model (often 10)")
    p.add_argument("--caller-voice", default="kokoro:bm_george")
    p.add_argument("--caller-audio", type=Path, help="recordings named <call id>-<turn>.wav")
    p.add_argument("--reference", help="the hearing whose conversation is voiced (default: the lowest word errors)")
    p.add_argument("--calls", help="comma-separated call ids (default: every call)")
    p.add_argument("--silence-ms", type=float, default=float(os.environ.get("SPEECH_WINDOW_SILENCE_MS") or 700))
    p.add_argument("--first-clause", type=int, default=int(os.environ.get("SPEECH_WINDOW_FIRST_CLAUSE_CHARS") or 30))
    p.add_argument("--max-steps", type=int, default=4)
    p.add_argument("--think", default="off")
    p.add_argument("--out", type=Path)
    return p.parse_args(argv)


def slug(spec: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", spec.lower().split("/recognizers/")[-1]).strip("-")[:60]


def med(values) -> float | None:
    xs = [v for v in values if v is not None]
    return statistics.median(xs) if xs else None


def p90(values) -> float | None:
    xs = sorted(v for v in values if v is not None)
    return xs[min(len(xs) - 1, max(0, -(-9 * len(xs) // 10) - 1))] if xs else None


def net_total(split: dict | None) -> float | None:
    return None if not split else split["connect"] + split["upload"] + split["download"]


def fmt(v: float | None, digits: int = 2) -> str:
    return "–" if v is None else f"{v:.{digits}f}"


async def run(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)

    import httpx
    import soundfile as sf

    from server import keepalive
    from server.calls import matrix
    from server.calls.agent import CallAgent
    from server.calls.nettrace import RequestTrace, TracingTransport
    from server.calls.pacing import PacedRouter, PausableClock, Throttle
    from server.calls.scenario import ScenarioError, load_scenario
    from server.calls.scoring import misheard_words, score_call, wer
    from server.calls.voice import GoogleVoice, SetupError, make_ears, make_voice
    from server.llm.router import Router, UnknownModel, load_provider_configs
    from server.llm.types import CallSettings

    hear_specs = [h.strip() for h in args.hear.split(",") if h.strip()]
    voice_specs = [v.strip() for v in args.voice.split(",") if v.strip()]
    llm_sink: list[RequestTrace] = []
    try:
        scenario = load_scenario(args.scenario)
        scripts = [scenario.call(c.strip()) for c in args.calls.split(",")] if args.calls else list(scenario.calls)
        all_ears = {h: make_ears(h) for h in hear_specs}
        voices = {v: make_voice(v) for v in voice_specs}
        caller = make_voice(args.caller_voice)
        router = Router(load_provider_configs(os.environ), keepalive.new_client(
            transport=TracingTransport(llm_sink, limits=keepalive.LIMITS),
            timeout=httpx.Timeout(connect=5.0, read=float(os.environ.get("INFERENCE_TIMEOUT_S") or 120), write=30.0,
                                  pool=5.0)))
        router.resolve(args.model)
    except (ScenarioError, SetupError, UnknownModel) as e:
        print(f"Can't run: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    clock = PausableClock()
    paced = PacedRouter(router, Throttle(args.model_rpm), clock)
    silence = args.silence_ms / 1000
    out = args.out or ROOT / "bench-out" / f"matrix-{scenario.id}-{datetime.now():%Y%m%d-%H%M%S}"
    recordings = out / "recordings"
    (recordings / "caller").mkdir(parents=True, exist_ok=True)
    say = lambda *a: print(*a, flush=True)   # noqa: E731

    # ---- the caller's clips ----
    clips: dict[tuple[str, int], bytes] = {}
    for script in scripts:
        for n, said in enumerate(script.caller, 1):
            path = recordings / "caller" / f"{script.id}-{n}.wav"
            given = args.caller_audio / f"{script.id}-{n}.wav" if args.caller_audio else None
            if given and given.is_file():
                path.write_bytes(given.read_bytes())
            elif not path.is_file():
                spoken = await caller.speak(said)
                if spoken.wav is None:
                    print(f"Can't run: the caller's voice failed: {spoken.error}", file=sys.stderr)
                    return 2
                path.write_bytes(spoken.wav)
            clips[(script.id, n)] = path.read_bytes()
    say(f"{len(clips)} caller clips in {recordings / 'caller'}")

    # ---- 1. hearing ----
    asr_rows: list[dict] = []
    heard: dict[tuple[str, str, int], str] = {}
    hearing_s: dict[str, dict[tuple[str, int], float]] = {}
    ears_ok = []
    for spec, ears in all_ears.items():
        say(f"\nHearing: {spec}")
        try:
            await ears.ready()
            first = next(iter(clips))
            await ears.hear(clips[first], "", None)   # warm: model loaded, connection open, as mid-call
        except SetupError as e:
            say(f"  skipped: {e}")
            asr_rows.append({"hearing": spec, "error": str(e)})
            continue
        ears_ok.append(spec)
        hearing_s[spec] = {}
        for script in scripts:
            for n, said in enumerate(script.caller, 1):
                h = await ears.hear(clips[(script.id, n)], said, script.language)
                heard[(spec, script.id, n)] = h.text
                hearing_s[spec][(script.id, n)] = h.seconds
                asr_rows.append({"hearing": spec, "engine": ears.label, "where": ears.where, "call": script.id,
                                 "turn": n, "said": said, "heard": h.text, "wer": wer(said, h.text),
                                 "seconds": h.seconds, **{f"net_{k}": v for k, v in (h.net or {}).items()},
                                 "new_connection": h.new_connection, "error": h.error})
        mine = [r for r in asr_rows if r.get("hearing") == spec and "wer" in r]
        say(f"  median {fmt(med(r['seconds'] for r in mine))} s · word errors "
            f"{100 * statistics.mean(r['wer'] for r in mine):.1f}%")
    if not ears_ok:
        print("Can't run: no hearing engine worked", file=sys.stderr)
        return 2

    # ---- 2. the model, through each hearing's transcripts ----
    llm_rows, call_rows = [], []
    turns_by: dict[str, list[dict]] = {}
    for spec in ears_ok:
        say(f"\nConversations heard by {spec}")
        turns_by[spec] = []
        for script in scripts:
            tools = scenario.toolset()
            agent = CallAgent(paced, args.model, scenario, tools, settings=CallSettings(think=args.think),
                              max_steps=args.max_steps, first_clause_chars=args.first_clause, clock=clock)
            replies, silent = [], 0
            for n, said in enumerate(script.caller, 1):
                text = heard.get((spec, script.id, n), "")
                if not text:
                    replies.append("")
                    turns_by[spec].append({"call": script.id, "turn": n, "pieces": []})
                    continue
                mark = len(llm_sink)
                turn = await agent.turn(text)
                connects = [t.connect_s for t in llm_sink[mark:] if t.connect_s is not None]
                pieces = [{"text": p, "ready_s": ready} for p, ready in turn.pieces]
                turns_by[spec].append({"call": script.id, "turn": n, "pieces": pieces})
                replies.append(turn.text)
                silent += 1 if turn.empty_after_tool else 0
                llm_rows.append({"hearing": spec, "call": script.id, "turn": n, "heard": text, "reply": turn.text,
                                 "first_word_s": turn.first_word_s,
                                 "first_piece_s": pieces[0]["ready_s"] if pieces else None,
                                 "whole_s": turn.whole_s, "requests": turn.steps,
                                 "tools": ", ".join(r.name for r in turn.tool_runs), "tool_s": turn.tool_s,
                                 "prompt_tokens": turn.tokens[0], "reply_tokens": turn.tokens[1],
                                 "new_connections": len(connects), "connect_s": sum(connects),
                                 "silent_after_tool": turn.empty_after_tool, "held_s": turn.held_s,
                                 "error": turn.error})
            score = score_call(script, replies, tools.log, tools.state, scenario.data, unanswered=silent)
            failed = "; ".join(f"{c.name}: {c.detail}" for c in score.checks if not c.ok)
            call_rows.append({"hearing": spec, "call": script.id, "title": script.title, "passed": score.passed,
                              "failed": failed})
            say(f"  {script.id}: {'pass' if score.passed else 'FAIL ' + failed}")

    # ---- 3. voices, on the reference conversation ----
    mean_wer = {s: statistics.mean(r["wer"] for r in asr_rows if r.get("hearing") == s and "wer" in r)
                for s in ears_ok}
    reference = args.reference if args.reference in ears_ok else min(ears_ok, key=lambda s: mean_wer[s])
    ref_turns = turns_by[reference]
    say(f"\nVoicing the conversation heard by {reference}")
    tts_rows: list[dict] = []
    voiced: dict[str, dict[tuple[str, int, int], tuple[float, float]]] = {}
    pieces_audio: dict[str, dict[tuple[str, int, int], bytes | None]] = {}
    greetings: dict[str, bytes | None] = {}
    for spec, voice in voices.items():
        folder = recordings / slug(spec)
        folder.mkdir(parents=True, exist_ok=True)
        throttle = Throttle(args.voice_rpm if isinstance(voice, GoogleVoice) else 0)
        await throttle.wait()
        g = await voice.speak(scenario.agent.greeting)   # the greeting also warms the voice up
        greetings[spec] = g.wav
        if g.wav:
            (folder / "greeting.wav").write_bytes(g.wav)
        voiced[spec], pieces_audio[spec] = {}, {}
        errors = 0
        for t in ref_turns:
            for k, piece in enumerate(t["pieces"], 1):
                await throttle.wait()
                s = await voice.speak(piece["text"])
                voiced[spec][(t["call"], t["turn"], k)] = (s.seconds, s.audio_s)
                pieces_audio[spec][(t["call"], t["turn"], k)] = s.wav
                if s.wav:
                    (folder / f"{t['call']}-{t['turn']}-{k}.wav").write_bytes(s.wav)
                errors += 1 if s.error else 0
                tts_rows.append({"voice": spec, "engine": voice.label, "where": voice.where, "call": t["call"],
                                 "turn": t["turn"], "piece": k, "text": piece["text"], "chars": len(piece["text"]),
                                 "seconds": s.seconds, "audio_s": s.audio_s,
                                 "real_time_factor": s.seconds / s.audio_s if s.audio_s else None,
                                 **{f"net_{key}": v for key, v in (s.net or {}).items()},
                                 "new_connection": s.new_connection, "error": s.error})
        mine = [r for r in tts_rows if r["voice"] == spec]
        first = [r["seconds"] for r in mine if r["piece"] == 1]
        say(f"  {spec}: first piece median {fmt(med(first))} s over {len(mine)} pieces"
            f"{f', {errors} failed' if errors else ''}")

    # ---- 4. pairs ----
    pair_rows, summary = [], []
    passed_by = {s: sum(r["passed"] for r in call_rows if r["hearing"] == s) for s in ears_ok}
    for h in ears_ok:
        for v, voice in voices.items():
            rows = matrix.compose(hearing=hearing_s[h], turns=ref_turns, voiced=voiced[v], silence_s=silence,
                                  parallel=voice.parallel)
            for r in rows:
                pair_rows.append({"hearing": h, "voice": v, **{k: val for k, val in r.items() if k != "gaps_s"}})
            firsts = [r["first_sound_s"] for r in rows]
            m = med(firsts)
            summary.append({"hearing": h, "voice": v, "first_sound_median_s": m, "first_sound_p90_s": p90(firsts),
                            "first_sound_worst_s": max((x for x in firsts if x is not None), default=None),
                            "band": matrix.band(m), "silence_s": silence,
                            "hearing_median_s": med(hearing_s[h].values()),
                            "model_first_piece_median_s": med(r["model_first_piece_s"] for r in rows),
                            "voice_first_piece_median_s": med(r["voice_first_s"] for r in rows),
                            "stalls_median_s": med(r["stalls_s"] for r in rows),
                            "hearing_word_errors_pct": 100 * mean_wer[h],
                            "calls_passed": f"{passed_by[h]}/{len(scripts)}"})
    summary.sort(key=lambda r: (r["first_sound_median_s"] is None, r["first_sound_median_s"] or 0))

    # ---- whole calls, as they would sound with each voice (the reference hearing's waits) ----
    calls_dir = recordings / "calls"
    for v in voices:
        rows = {(r["call"], r["turn"]): r for r in matrix.compose(hearing=hearing_s[reference], turns=ref_turns,
                                                                    voiced=voiced[v], silence_s=silence,
                                                                    parallel=voices[v].parallel)}
        (calls_dir / slug(v)).mkdir(parents=True, exist_ok=True)
        greeting = matrix.read_wav(greetings[v], RATE) if greetings.get(v) else None
        for script in scripts:
            turns = []
            for n in range(1, len(script.caller) + 1):
                r = rows.get((script.id, n))
                audio = [pieces_audio[v].get((script.id, n, k)) for k in range(1, 99)
                         if (script.id, n, k) in pieces_audio[v]]
                turns.append({"caller": matrix.read_wav(clips[(script.id, n)], RATE),
                              "gap_s": (r["first_sound_s"] if r and r["first_sound_s"] is not None else silence),
                              "pieces": [matrix.read_wav(a, RATE) for a in audio if a],
                              "stalls_s": r["gaps_s"] if r else []})
            sf.write(calls_dir / slug(v) / f"{script.id}.wav", matrix.conversation(RATE, greeting, turns), RATE)
    await router.aclose()

    # ---- exports ----
    model_rows = llm_rows
    tables = {"Summary": summary, "Pairs by turn": pair_rows, "Hearing": asr_rows, "Model": model_rows,
              "Voice": tts_rows, "Calls": call_rows,
              "References": [{**r} for r in matrix.REFERENCES] + [
                  {"what": f"Band '{label}': up to {limit:g} s ({matrix.BAND_NOTES[label]})", "seconds": limit,
                   "kind": "band"} for limit, label in matrix.BANDS if limit != float("inf")]}
    for name, rows in tables.items():
        matrix.write_csv(out / f"{slug(name)}.csv", rows)
    matrix.write_xlsx(out / "results.xlsx", tables)
    (out / "report.md").write_text(report(args, scenario, reference, summary, asr_rows, model_rows, tts_rows,
                                          call_rows, ears_ok, voices, silence, misheard_words), encoding="utf-8")
    say(f"\nReport: {out / 'report.md'}\nWorkbook: {out / 'results.xlsx'}\nRecordings: {recordings}")
    return 0


def report(args, scenario, reference, summary, asr_rows, model_rows, tts_rows, call_rows, ears_ok, voices,
           silence, misheard_words) -> str:
    from server.calls import matrix
    lines = [f"# Hearing x voice benchmark: {scenario.name}", "",
             f"**Model** {args.model} · **When** {datetime.now():%Y-%m-%d %H:%M} · **End-of-turn silence** "
             f"{silence:.2f} s · **Calls** {len({r['call'] for r in call_rows})} · voiced conversation: the one "
             f"heard by `{reference}`", "",
             "The caller's wait is the time from their last word to the agent's first sound: Talk's end-of-turn "
             "silence, then hearing, then the model's first speakable piece, then voicing that piece. Each pair's "
             "wait is composed from those stages, each measured once (stages run one after another).", "",
             "## The pairs, quickest first", "",
             "| Hearing | Voice | Wait (median) | 90% | Band | Word errors | Calls passed |",
             "|---|---|---|---|---|---|---|"]
    for r in summary:
        lines.append(f"| {r['hearing'].split('/recognizers/')[-1]} | {r['voice']} | {fmt(r['first_sound_median_s'])} s"
                     f" | {fmt(r['first_sound_p90_s'])} s | {r['band']} | {r['hearing_word_errors_pct']:.1f}% | "
                     f"{r['calls_passed']} |")
    lines += ["", "## Reference points", "", "| Band | Up to | Meaning |", "|---|---|---|"]
    for limit, label in matrix.BANDS:
        lines.append(f"| {label} | {'–' if limit == float('inf') else f'{limit:g} s'} | {matrix.BAND_NOTES[label]} |")
    lines += ["", f"Talk waits {silence:.2f} s of silence before it decides the caller has finished, so that "
              "alone uses most of the 0.8 s target before any engine starts.", ""]
    for ref in matrix.REFERENCES:
        lines.append(f"- **{ref['seconds']:g} s** ({ref['kind']}): {ref['what']}. [{ref['source']}]({ref['url']})")
    lines += ["", "## Hearing", "", "| Engine | Where | Median | 90% | Network (median) | Word errors | Missed most |",
              "|---|---|---|---|---|---|---|"]
    for spec in ears_ok:
        mine = [r for r in asr_rows if r.get("hearing") == spec and "wer" in r]
        nets = [net_total({"connect": r.get("net_connect", 0), "upload": r.get("net_upload", 0),
                           "download": r.get("net_download", 0)}) if "net_connect" in r else None for r in mine]
        missed = ", ".join(w for w, _ in misheard_words([(r["said"], r["heard"]) for r in mine], 5)) or "–"
        lines.append(f"| {spec.split('/recognizers/')[-1]} | {mine[0]['where']} | {fmt(med(r['seconds'] for r in mine))} s"
                     f" | {fmt(p90(r['seconds'] for r in mine))} s | {fmt(med(nets))} s | "
                     f"{100 * statistics.mean(r['wer'] for r in mine):.1f}% | {missed} |")
    plain = [r for r in model_rows if not r["tools"]]
    tooled = [r for r in model_rows if r["tools"]]
    lines += ["", f"## Model: {args.model}", "",
              "| Turns | First word | First piece | Whole reply | Requests | New connections |", "|---|---|---|---|---|---|"]
    for label, rows in (("without a tool", plain), ("with a tool", tooled), ("all", model_rows)):
        if rows:
            lines.append(f"| {label} ({len(rows)}) | {fmt(med(r['first_word_s'] for r in rows))} s | "
                         f"{fmt(med(r['first_piece_s'] for r in rows))} s | {fmt(med(r['whole_s'] for r in rows))} s"
                         f" | {sum(r['requests'] for r in rows)} | {sum(r['new_connections'] for r in rows)} |")
    lines += ["", "## Voices", "",
              "| Voice | Where | First piece (median) | 90% | Make / speak time | Network (median) | Failed |",
              "|---|---|---|---|---|---|---|"]
    for spec, voice in voices.items():
        mine = [r for r in tts_rows if r["voice"] == spec]
        first = [r["seconds"] for r in mine if r["piece"] == 1]
        nets = [r["net_connect"] + r["net_upload"] + r["net_download"] for r in mine if "net_connect" in r]
        lines.append(f"| {spec} | {voice.where} | {fmt(med(first))} s | {fmt(p90(first))} s | "
                     f"{fmt(med(r['real_time_factor'] for r in mine))} | {fmt(med(nets))} s | "
                     f"{sum(1 for r in mine if r['error'])} |")
    lines += ["", "Make / speak time below 1 means the voice makes audio faster than it plays.", "",
              "## Calls", "", "| Hearing | Call | Result |", "|---|---|---|"]
    for r in call_rows:
        lines.append(f"| {r['hearing'].split('/recognizers/')[-1]} | {r['call']} | "
                     f"{'pass' if r['passed'] else 'fail: ' + r['failed']} |")
    lines += ["", "## Recordings", "",
              "- `recordings/caller/`: what the caller said, one clip per turn.",
              "- `recordings/<voice>/`: every piece the agent said, and its greeting.",
              f"- `recordings/calls/<voice>/<call>.wav`: each call as it would sound, the waits included (hearing "
              f"by `{reference.split('/recognizers/')[-1]}`).", "",
              "Every table is also in `results.xlsx` and in one CSV each."]
    return "\n".join(lines) + "\n"


def main() -> int:
    return asyncio.run(run(parse(sys.argv[1:])))


if __name__ == "__main__":
    raise SystemExit(main())
