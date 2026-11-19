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

Round 2 (spec docs/superpowers/specs/2026-11-17-call-bench-round2-design.md) adds: recorded fillers when the model
decides on a lookup and "still checking" lines (server/calls/timeline.py), keypad digits (--keypad), an end-of-turn
model on every clip (Smart Turn v3.2) beside the fixed silence, streaming hearing (google-stream:, openai-rt:), and
what each stage costs (server/calls/prices.toml).

Writes report.md, results.xlsx, one CSV per table, and recordings: the caller clips, every voiced piece and recorded
line, and each call as it would sound with each voice, the measured waits, fillers and keypad tones included.

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

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RATE = 24000   # recordings are written at Kokoro's and Google's rate


def parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--scenario", default="hotel")
    p.add_argument("--model", default="gemini:gemini-3.1-flash-lite", help="the chat model, provider:name")
    p.add_argument("--model-rpm", type=int, default=0, help="model requests a minute (Gemini's free tier: 15)")
    p.add_argument("--hear", required=True, help="comma-separated: whisper:<id>, google:<recognizer>, "
                                                 "openai:<model>[@<url>], google-stream:<recognizer>[@<model>], "
                                                 "openai-rt:<model>")
    p.add_argument("--voice", required=True, help="comma-separated: kokoro[:<voice>], google[:<model>[:<voice>]], "
                                                  "chirp:<voice name>, chirp-stream:<voice name>")
    p.add_argument("--voice-rpm", type=int, default=0, help="Gemini-TTS requests a minute, per model (often 10)")
    p.add_argument("--caller-voice", default="kokoro:bm_george")
    p.add_argument("--caller-audio", type=Path, help="recordings named <call id>-<turn>.wav")
    p.add_argument("--reference", help="the hearing whose conversation is voiced (default: the lowest word errors)")
    p.add_argument("--calls", help="comma-separated call ids (default: every call)")
    p.add_argument("--silence-ms", type=float, default=float(os.environ.get("SPEECH_WINDOW_SILENCE_MS") or 700))
    p.add_argument("--first-clause", type=int, default=int(os.environ.get("SPEECH_WINDOW_FIRST_CLAUSE_CHARS") or 30))
    p.add_argument("--max-steps", type=int, default=4)
    p.add_argument("--think", default="off")
    p.add_argument("--keypad", action="store_true", help="numbers are keyed on the keypad (the calls' caller_keypad "
                                                          "lines and keys)")
    p.add_argument("--facts-in-prompt", action="store_true",
                   help="tell the agent the pack's fixed facts in its instructions and leave out the tool that serves "
                        "them ([agent.facts]): a question about them then takes one model request, not two")
    p.add_argument("--keying", default="1.0,0.4", help="seconds before the first key, seconds a key (hash included)")
    p.add_argument("--still-after", type=float, default=1.5, help="silence after a filler before 'still checking'")
    p.add_argument("--no-smart-turn", action="store_true", help="skip the end-of-turn model")
    p.add_argument("--warm-model", action="store_true",
                   help="before the model stage, one turn ('Hello.') with the scenario's instructions and tools and "
                        "the same settings: a local model is loaded and has read them, as on a line already "
                        "answering calls")
    p.add_argument("--prices", type=Path, help="a price list (default server/calls/prices.toml)")
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

    import io

    import httpx
    import soundfile as sf

    from server import keepalive
    from server.calls import matrix
    from server.calls.agent import CallAgent
    from server.calls.cost import (hearing_usd, load_prices, model_shortfall, model_usd, per_1000_minutes, price_for,
                                   voice_usd)
    from server.calls.endturn import speech_bounds, to_16k
    from server.calls.fillers import FillerBook
    from server.calls.keypad import keypad_tool
    from server.calls.timeline import render, turn_timeline
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
        if args.facts_in_prompt and scenario.facts is None:
            raise ScenarioError(f"{scenario.id} has no [agent.facts] table: --facts-in-prompt has nothing to tell")
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
    facts = scenario.facts if args.facts_in_prompt else None
    without = (facts.tool,) if facts else ()
    silence = args.silence_ms / 1000
    out = args.out or ROOT / "bench-out" / f"matrix-{scenario.id}-{datetime.now():%Y%m%d-%H%M%S}"
    recordings = out / "recordings"
    (recordings / "caller").mkdir(parents=True, exist_ok=True)
    say = lambda *a: print(*a, flush=True)   # noqa: E731

    key_start, per_key = (float(x) for x in args.keying.split(","))
    lines = {s.id: s.lines(args.keypad) for s in scripts}

    # ---- the caller's clips: a recording of the same words if there is one, else the caller's voice ----
    known = matrix.clip_sources(scenario.calls, args.caller_audio)
    clips: dict[tuple[str, int], bytes] = {}
    clip_info: dict[tuple[str, int], dict] = {}
    made_clips = 0
    for script in scripts:
        for n, said in enumerate(lines[script.id], 1):
            path = recordings / "caller" / f"{script.id}-{n}.wav"
            if said in known:
                path.write_bytes(known[said].read_bytes())
            else:
                spoken = await caller.speak(said)
                if spoken.wav is None:
                    print(f"Can't run: the caller's voice failed: {spoken.error}", file=sys.stderr)
                    return 2
                path.write_bytes(spoken.wav)
                made_clips += 1
            clips[(script.id, n)] = path.read_bytes()
            samples, rate = sf.read(io.BytesIO(clips[(script.id, n)]), dtype="float32", always_2d=True)
            mono = samples.mean(axis=1)
            clip_info[(script.id, n)] = {"samples": mono, "rate": rate, "clip_s": len(mono) / rate,
                                         "speech_end_s": speech_bounds(to_16k(mono, rate), 16000)[1]}
    say(f"{len(clips)} caller clips in {recordings / 'caller'} ({made_clips} newly spoken by {args.caller_voice})")

    # ---- end of turn: the fixed silence, and the end-of-turn model on every clip ----
    eot: dict[tuple[str, int], dict] = {}
    eot_rows: list[dict] = []
    if not args.no_smart_turn:
        from server.calls.endturn import SmartTurn, end_of_turn
        turn_model = SmartTurn()
        for key, info in clip_info.items():
            r = end_of_turn(info["samples"], info["rate"], turn_model, fixed_s=silence)
            eot[key] = r
            eot_rows.append({"call": key[0], "turn": key[1], "said": lines[key[0]][key[1] - 1],
                             "clip_s": info["clip_s"], "speech_end_s": r["speech_end_s"],
                             "smart_wait_s": r["wait_s"], "fixed_wait_s": silence, "probability": r["probability"],
                             "model_ms": 1000 * r["model_s"], "fallback": r["fallback"], "pauses": r["pauses"],
                             "smart_cutoffs": len(r["cutoffs"]), "fixed_cutoffs": len(r["fixed_cutoffs"]),
                             "smart_cutoff_at_s": ", ".join(f"{c:.2f}" for c in r["cutoffs"])})
        say(f"End of turn (Smart Turn v3.2): wait median {fmt(med(r['smart_wait_s'] for r in eot_rows))} s, "
            f"{sum(r['fallback'] for r in eot_rows)} fell back to 3 s, "
            f"{sum(r['smart_cutoffs'] for r in eot_rows)} cut-offs mid-turn "
            f"(fixed {silence:.2f} s: {sum(r['fixed_cutoffs'] for r in eot_rows)})")

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
            for n, said in enumerate(lines[script.id], 1):
                h = await ears.hear(clips[(script.id, n)], said, script.language)
                heard[(spec, script.id, n)] = h.text
                hearing_s[spec][(script.id, n)] = h.seconds
                asr_rows.append({"hearing": spec, "engine": ears.label, "where": ears.where, "call": script.id,
                                 "turn": n, "said": said, "heard": h.text, "wer": wer(said, h.text),
                                 "seconds": h.seconds, "audio_s": clip_info[(script.id, n)]["clip_s"],
                                 "billed_s": (h.billed_s if h.billed_s is not None
                                              else clip_info[(script.id, n)]["clip_s"]),
                                 **{f"net_{k}": v for k, v in (h.net or {}).items()},
                                 "new_connection": h.new_connection, "error": h.error})
        mine = [r for r in asr_rows if r.get("hearing") == spec and "wer" in r]
        say(f"  median {fmt(med(r['seconds'] for r in mine))} s · word errors "
            f"{100 * statistics.mean(r['wer'] for r in mine):.1f}%")
    if not ears_ok:
        print("Can't run: no hearing engine worked", file=sys.stderr)
        return 2

    # ---- 2. the model, through each hearing's transcripts ----
    if args.warm_model:   # same settings as the calls (no context size or keep-alive unless Talk sends one), or
        # Ollama would load the model again for the first call
        warm_tools = scenario.toolset((keypad_tool(scripts[0].keys),) if args.keypad else (), without)
        warm_agent = CallAgent(paced, args.model, scenario, warm_tools, settings=CallSettings(think=args.think),
                               max_steps=args.max_steps, first_clause_chars=args.first_clause, clock=clock,
                               keypad=args.keypad, facts=facts)
        warm = await warm_agent.turn("Hello.")
        args.warmed_s = warm.whole_s
        say(f"\nModel warmed: {warm.whole_s:.1f} s for one turn{f' ({warm.error})' if warm.error else ''}")
    llm_rows, call_rows = [], []
    turns_by: dict[str, list[dict]] = {}
    for spec in ears_ok:
        say(f"\nConversations heard by {spec}")
        turns_by[spec] = []
        for script in scripts:
            tools = scenario.toolset((keypad_tool(script.keys),) if args.keypad else (), without)
            agent = CallAgent(paced, args.model, scenario, tools, settings=CallSettings(think=args.think),
                              max_steps=args.max_steps, first_clause_chars=args.first_clause, clock=clock,
                              keypad=args.keypad, facts=facts)
            replies, silent = [], 0
            for n, said in enumerate(lines[script.id], 1):
                text = heard.get((spec, script.id, n), "")
                if not text:
                    replies.append("")
                    turns_by[spec].append({"call": script.id, "turn": n, "pieces": [], "steps": []})
                    continue
                mark = len(llm_sink)
                turn = await agent.turn(text)
                connects = [t.connect_s for t in llm_sink[mark:] if t.connect_s is not None]
                pieces = [{"text": p, "ready_s": ready} for p, ready in turn.pieces]
                steps = [{"end_s": st.end_s, "tools": list(st.tools), "words": st.words,
                          "keyed": [list(k) for k in st.keyed]} for st in turn.step_log]
                turns_by[spec].append({"call": script.id, "turn": n, "pieces": pieces, "steps": steps})
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
                                 "steps": " | ".join(f"{st['end_s']:.2f} {'+'.join(st['tools']) or ('words' if st['words'] else 'silent')}"
                                                     for st in steps),
                                 "keyed": ", ".join(f"{w} {d}" for st in steps for w, d in st["keyed"]),
                                 "error": turn.error})
            score = score_call(script, replies, tools.log, tools.state, scenario.data, unanswered=silent,
                               facts=facts)
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
    line_rows: list[dict] = []
    to_record = [("still" if t in scenario.agent.still_checking else "filler", t)
                 for t in FillerBook(scenario.agent).lines()]
    to_record += [("prompt", t) for t in scenario.agent.keypad.values()] if args.keypad else []
    line_audio: dict[str, dict[str, object]] = {}
    voiced: dict[str, dict[tuple[str, int, int], tuple[float, float]]] = {}
    pieces_audio: dict[str, dict[tuple[str, int, int], bytes | None]] = {}
    greetings: dict[str, bytes | None] = {}
    greeting_s: dict[str, float | None] = {}
    for spec, voice in voices.items():
        folder = recordings / slug(spec)
        folder.mkdir(parents=True, exist_ok=True)
        throttle = Throttle(args.voice_rpm if isinstance(voice, GoogleVoice) else 0)
        await throttle.wait()
        g = await voice.speak(scenario.agent.greeting)   # the greeting also warms the voice up
        greetings[spec] = g.wav
        greeting_s[spec] = g.audio_s if g.wav else None
        if g.wav:
            (folder / "greeting.wav").write_bytes(g.wav)
        line_audio[spec] = {}
        (folder / "lines").mkdir(exist_ok=True)
        for kind, text in to_record:   # made once, before any call: playing one costs no time on the call
            await throttle.wait()
            spoken = await voice.speak(text)
            line_audio[spec][text] = spoken
            if spoken.wav:
                (folder / "lines" / f"{slug(text)[:40]}.wav").write_bytes(spoken.wav)
            line_rows.append({"voice": spec, "kind": kind, "text": text, "chars": len(text),
                              "seconds": spoken.seconds, "audio_s": spoken.audio_s, "error": spoken.error})
        voiced[spec], pieces_audio[spec] = {}, {}
        errors = 0
        for t in ref_turns:
            for k, piece in enumerate(t["pieces"], 1):
                await throttle.wait()
                s = await voice.speak(piece["text"])
                voiced[spec][(t["call"], t["turn"], k)] = (s.playable_s, s.audio_s)
                pieces_audio[spec][(t["call"], t["turn"], k)] = s.wav
                if s.wav:
                    (folder / f"{t['call']}-{t['turn']}-{k}.wav").write_bytes(s.wav)
                errors += 1 if s.error else 0
                tts_rows.append({"voice": spec, "engine": voice.label, "where": voice.where, "call": t["call"],
                                 "turn": t["turn"], "piece": k, "text": piece["text"], "chars": len(piece["text"]),
                                 "seconds": s.seconds, "playable_s": s.playable_s, "audio_s": s.audio_s,
                                 "real_time_factor": s.seconds / s.audio_s if s.audio_s else None,
                                 **{f"net_{key}": v for key, v in (s.net or {}).items()},
                                 "new_connection": s.new_connection, "error": s.error})
        mine = [r for r in tts_rows if r["voice"] == spec]
        first = [r["playable_s"] for r in mine if r["piece"] == 1]
        say(f"  {spec}: first piece median {fmt(med(first))} s over {len(mine)} pieces"
            f"{f', {errors} failed' if errors else ''}")

    # ---- 4. pairs: each turn of the voiced conversation as the caller would hear it ----
    def line_s(v: str, text: str) -> float:
        spoken = line_audio[v].get(text)
        return spoken.audio_s if spoken is not None and spoken.wav else len(text.split()) * 0.4

    def timeline(h: str, v: str, t: dict, wait_s: float, book: FillerBook | None) -> dict:
        key = (t["call"], t["turn"])
        made = [voiced[v].get((*key, k), (0.0, 0.0)) for k in range(1, len(t["pieces"]) + 1)]

        def filler(tool: str):
            text = book.for_tool(tool)
            return text, line_s(v, text)

        def still():
            text = book.still()
            return text, line_s(v, text)

        def prompt(what: str):
            text = scenario.agent.keypad.get(what, "Please key in the number, then press the hash key.")
            return text, line_s(v, text)
        return turn_timeline(wait_s=wait_s, hearing_s=hearing_s[h].get(key, 0.0), steps=t.get("steps") or [],
                             pieces=t["pieces"], made=made, parallel=voices[v].parallel,
                             filler=filler if book else None, still=still if book else None,
                             prompt=prompt if args.keypad else None, still_after_s=args.still_after,
                             key_start_s=key_start, per_key_s=per_key)

    prices = load_prices(args.prices)
    model_price = price_for(prices, "model", args.model)
    pair_rows, summary, cost_rows = [], [], []
    heard_timelines: dict[str, dict[tuple[str, int], dict]] = {}
    call_s: dict[tuple[str, str], dict[str, float]] = {}   # (hearing, voice) -> call -> seconds, as recorded

    def caller_s(key: tuple[str, int]) -> float:   # the recorded call keeps the caller's clip to their last word
        end = clip_info[key]["speech_end_s"]
        return end if end > 0 else clip_info[key]["clip_s"]
    passed_by = {s: sum(r["passed"] for r in call_rows if r["hearing"] == s) for s in ears_ok}
    for h in ears_ok:
        for v, voice in voices.items():
            rows = []
            ends: dict[tuple[str, int], float] = {}
            books: dict[tuple[str, str], FillerBook] = {}
            for t in ref_turns:
                key = (t["call"], t["turn"])
                smart_wait = eot[key]["wait_s"] if key in eot else silence
                plain = timeline(h, v, t, silence, None)
                fixed = timeline(h, v, t, silence, books.setdefault(("fixed", t["call"]), FillerBook(scenario.agent)))
                smart = timeline(h, v, t, smart_wait, books.setdefault(("smart", t["call"]),
                                                                       FillerBook(scenario.agent)))
                ends[key] = smart["end_s"]
                if h == reference:
                    heard_timelines.setdefault(v, {})[key] = smart
                kinds = [x["kind"] for x in smart["sounds"]]
                rows.append({"hearing": h, "voice": v, "call": t["call"], "turn": t["turn"],
                             "hearing_s": hearing_s[h].get(key),
                             "model_first_piece_s": t["pieces"][0]["ready_s"] if t["pieces"] else None,
                             "voice_first_s": voiced[v].get((*key, 1), (None,))[0],
                             "wait_fixed_s": silence, "wait_smart_s": smart_wait,
                             "first_sound_no_fillers_s": plain["first_sound_s"],
                             "first_sound_fixed_s": fixed["first_sound_s"],
                             "first_answer_fixed_s": fixed["first_answer_s"], "dead_air_fixed_s": fixed["dead_air_s"],
                             "first_sound_smart_s": smart["first_sound_s"],
                             "first_answer_smart_s": smart["first_answer_s"], "dead_air_smart_s": smart["dead_air_s"],
                             "longest_silence_smart_s": smart["longest_silence_s"],
                             "fillers": kinds.count("filler"), "still_lines": kinds.count("still"),
                             "keypad_prompts": kinds.count("prompt"),
                             "keying_s": sum(k["end_s"] - k["start_s"] for k in smart["keying"]),
                             "band": matrix.band(smart["first_sound_s"])})
            pair_rows += rows

            # cost per call: this hearing's audio and conversation, this voice on the voiced conversation
            hearing_price, voice_price = price_for(prices, "hearing", h), price_for(prices, "voice", v)
            seconds = call_s[(h, v)] = {
                script.id: matrix.call_seconds(greeting_s.get(v), [
                    (caller_s((script.id, n)), ends.get((script.id, n), silence))
                    for n in range(1, len(lines[script.id]) + 1)]) for script in scripts}
            minutes = [x / 60 for x in seconds.values()]
            gap = model_shortfall([r for r in llm_rows if r["hearing"] == h], sum(len(lines[s.id]) for s in scripts))
            streams = bool(getattr(all_ears[h], "streams", False))
            whole = [hearing_usd(hearing_price, x) for x in seconds.values()] if streams else []
            per_call = []
            for script in scripts:
                hear = [hearing_usd(hearing_price, r["billed_s"]) for r in asr_rows
                        if r.get("hearing") == h and r.get("call") == script.id and "billed_s" in r]
                talk = [r for r in llm_rows if r["hearing"] == h and r["call"] == script.id]
                model = model_usd(model_price, sum(r["prompt_tokens"] for r in talk),
                                  sum(r["reply_tokens"] for r in talk))
                speech = [voice_usd(voice_price, len(p["text"]), voiced[v].get((t["call"], t["turn"], k),
                                                                              (0.0, 0.0))[1])
                          for t in ref_turns if t["call"] == script.id for k, p in enumerate(t["pieces"], 1)]
                parts = [None if any(x is None for x in hear) else sum(hear), model,
                         None if any(x is None for x in speech) else sum(speech)]
                per_call.append(parts)
            mean_part = [None if any(c[i] is None for c in per_call) else statistics.mean(c[i] for c in per_call)
                         for i in range(3)]
            total = None if None in mean_part else sum(mean_part)
            cost_rows.append({"hearing": h, "voice": v, "hearing_usd_per_call": mean_part[0],
                              "model_usd_per_call": mean_part[1], "voice_usd_per_call": mean_part[2],
                              "total_usd_per_call": total, "usd_per_1000_calls": None if total is None else 1000 * total,
                              "call_minutes": statistics.mean(minutes), "call_minutes_min": min(minutes),
                              "call_minutes_max": max(minutes),
                              "usd_per_1000_minutes": per_1000_minutes(total, statistics.mean(minutes)),
                              "model_turns": gap["turns"], "model_turns_failed": gap["failed"],
                              "model_first_error": gap["first_error"], "streams": streams,
                              "hearing_whole_call_usd_per_1000": (1000 * statistics.mean(whole)
                                                                  if whole and None not in whole else None),
                              "unpriced": ", ".join(name for name, part in zip(("hearing", "model", "voice"), mean_part)
                                                    if part is None)})

            first = [r["first_sound_smart_s"] for r in rows]
            m = med(first)
            summary.append({"hearing": h, "voice": v,
                            "first_sound_no_fillers_median_s": med(r["first_sound_no_fillers_s"] for r in rows),
                            "first_sound_fixed_median_s": med(r["first_sound_fixed_s"] for r in rows),
                            "first_sound_smart_median_s": m, "first_sound_smart_p90_s": p90(first),
                            "first_answer_fixed_median_s": med(r["first_answer_fixed_s"] for r in rows),
                            "first_answer_smart_median_s": med(r["first_answer_smart_s"] for r in rows),
                            "dead_air_smart_median_s": med(r["dead_air_smart_s"] for r in rows),
                            "longest_silence_smart_worst_s": max((r["longest_silence_smart_s"] for r in rows),
                                                                 default=None),
                            "band": matrix.band(m), "silence_s": silence,
                            "hearing_median_s": med(hearing_s[h].values()),
                            "model_first_piece_median_s": med(r["model_first_piece_s"] for r in rows),
                            "voice_first_piece_median_s": med(r["voice_first_s"] for r in rows),
                            "hearing_word_errors_pct": 100 * mean_wer[h],
                            "calls_passed": f"{passed_by[h]}/{len(scripts)}",
                            "usd_per_1000_calls": cost_rows[-1]["usd_per_1000_calls"],
                            "cost_lower_bound": cost_rows[-1]["model_turns_failed"] > 0})
    summary.sort(key=lambda r: (r["first_sound_smart_median_s"] is None, r["first_sound_smart_median_s"] or 0))
    ref_talk = [r for r in llm_rows if r["hearing"] == reference]

    def per_call_mean(of) -> float:
        return statistics.mean(of(script) for script in scripts)
    shape = {"reference": reference, "calls": len(scripts),
             "caller_turns": per_call_mean(lambda s: len(lines[s.id])),
             "caller_s": per_call_mean(lambda s: sum(clip_info[(s.id, n)]["clip_s"]
                                                     for n in range(1, len(lines[s.id]) + 1))),
             "agent_chars": per_call_mean(lambda s: sum(len(p["text"]) for t in ref_turns if t["call"] == s.id
                                                        for p in t["pieces"])),
             "prompt_tokens": per_call_mean(lambda s: sum(r["prompt_tokens"] for r in ref_talk if r["call"] == s.id)),
             "reply_tokens": per_call_mean(lambda s: sum(r["reply_tokens"] for r in ref_talk if r["call"] == s.id)),
             "voices": {v: {"agent_s": per_call_mean(lambda s, v=v: sum(
                                audio for (call, _, _), (_, audio) in voiced[v].items() if call == s.id)),
                            "minutes": statistics.mean(call_s[(reference, v)].values()) / 60,
                            "min_minutes": min(call_s[(reference, v)].values()) / 60,
                            "max_minutes": max(call_s[(reference, v)].values()) / 60} for v in voices}}
    price_rows = []
    for stage, specs in (("hearing", ears_ok), ("model", [args.model]), ("voice", list(voices))):
        for spec in specs:
            price = price_for(prices, stage, spec)
            price_rows.append({"stage": stage, "engine": spec.split("/recognizers/")[-1] if stage != "hearing"
                               else re.sub(r"projects/[^/]+", "projects/<p>", spec),
                               **({k: v for k, v in price.items() if k != "key"} if price else {"unpriced": True})})

    # ---- whole calls, as they would sound with each voice: the reference hearing's waits, the end-of-turn model,
    # fillers, "still checking" lines, keypad instructions and tones; the caller's clip ends at their last word ----
    calls_dir = recordings / "calls"
    for v in voices:
        (calls_dir / slug(v)).mkdir(parents=True, exist_ok=True)
        greeting = matrix.read_wav(greetings[v], RATE) if greetings.get(v) else None
        for script in scripts:
            turns = []
            for n in range(1, len(lines[script.id]) + 1):
                key = (script.id, n)
                caller_audio = matrix.read_wav(clips[key], RATE)
                end = clip_info[key]["speech_end_s"]
                if end > 0:
                    caller_audio = caller_audio[:int(round(end * RATE))]
                answers = iter(pieces_audio[v].get((script.id, n, k)) for k in range(1, 99))

                def audio_for(sound, answers=answers):
                    wav = next(answers, None) if sound["kind"] == "answer" else getattr(
                        line_audio[v].get(sound["text"]), "wav", None)
                    return matrix.read_wav(wav, RATE) if wav else None
                tl = heard_timelines.get(v, {}).get(key)
                reply = render(RATE, tl, audio_for, key_start_s=key_start, per_key_s=per_key) if tl else \
                    np.zeros(int(silence * RATE), dtype=np.float32)
                turns.append({"caller": caller_audio, "gap_s": 0.0, "pieces": [reply], "stalls_s": []})
            sf.write(calls_dir / slug(v) / f"{script.id}.wav", matrix.conversation(RATE, greeting, turns), RATE)
    await router.aclose()

    # ---- exports ----
    model_rows = llm_rows
    tables = {"Summary": summary, "Pairs by turn": pair_rows, "End of turn": eot_rows, "Hearing": asr_rows,
              "Model": model_rows, "Voice": tts_rows, "Lines": line_rows, "Calls": call_rows, "Cost": cost_rows,
              "Prices": price_rows,
              "References": [{**r} for r in matrix.REFERENCES] + [
                  {"what": f"Band '{label}': up to {limit:g} s ({matrix.BAND_NOTES[label]})", "seconds": limit,
                   "kind": "band"} for limit, label in matrix.BANDS if limit != float("inf")]}
    for name, rows in tables.items():
        matrix.write_csv(out / f"{slug(name)}.csv", rows)
    matrix.write_xlsx(out / "results.xlsx", tables)
    (out / "report.md").write_text(report(args, scenario, reference, summary, asr_rows, model_rows, tts_rows,
                                          call_rows, ears_ok, voices, silence, misheard_words, eot_rows=eot_rows,
                                          pair_rows=pair_rows, line_rows=line_rows, cost_rows=cost_rows,
                                          price_rows=price_rows, shape=shape), encoding="utf-8")
    say(f"\nReport: {out / 'report.md'}\nWorkbook: {out / 'results.xlsx'}\nRecordings: {recordings}")
    return 0


def report(args, scenario, reference, summary, asr_rows, model_rows, tts_rows, call_rows, ears_ok, voices, silence,
           misheard_words, *, eot_rows, pair_rows, line_rows, cost_rows, price_rows, shape) -> str:
    from server.calls import matrix
    from server.calls.matrix import money, short
    smart = bool(eot_rows)
    lines = [f"# Hearing x voice benchmark: {scenario.name}", "",
             f"**Model** {args.model} · **When** {datetime.now():%Y-%m-%d %H:%M} · **Fixed end-of-turn silence** "
             f"{silence:.2f} s · **End-of-turn model** {'Smart Turn v3.2' if smart else 'off'} · **Keypad** "
             f"{'on' if args.keypad else 'off'} · **Fixed facts** "
             f"{'in the prompt' if args.facts_in_prompt else 'from a tool'} · **Model warmed first** "
             f"{f'yes ({args.warmed_s:.1f} s)' if getattr(args, 'warmed_s', None) is not None else 'no'} · "
             f"**Calls** {len({r['call'] for r in call_rows})} · voiced "
             f"conversation: the one heard by `{short(reference)}`", "",
             "The caller's wait is the time from their last word to the agent's first sound. It is composed from "
             "stages each measured once: the end-of-turn wait, hearing, the model's first speakable piece, voicing "
             "it. Round 2 adds what fills the silence: a recorded filler the moment the model decides on a lookup, "
             f"a \"still checking\" line after {args.still_after:g} s more of silence, and keypad instructions and "
             "keying. **First answer** is when the reply itself starts; **dead air** is all the silence in a turn "
             "(keying excluded).", "",
             "## The pairs, quickest first", "",
             "| Hearing | Voice | No fillers (round 1's way) | Fillers | Fillers + end-of-turn model | First answer "
             "| Dead air a turn | Band | Word errors | Calls passed | Per 1,000 calls |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in summary:
        lines.append(f"| {short(r['hearing'])} | {r['voice']} | {fmt(r['first_sound_no_fillers_median_s'])} s | "
                     f"{fmt(r['first_sound_fixed_median_s'])} s | {fmt(r['first_sound_smart_median_s'])} s | "
                     f"{fmt(r['first_answer_smart_median_s'])} s | {fmt(r['dead_air_smart_median_s'])} s | {r['band']} | "
                     f"{r['hearing_word_errors_pct']:.1f}% | {r['calls_passed']} | "
                     f"{money(r['usd_per_1000_calls'], at_least=r['cost_lower_bound'])} |")
    lines += ["", "Medians over the turns. The first three columns are the first sound; the last timing columns "
              "use the end-of-turn model" + ("" if smart else " (off in this run: the fixed silence)") + ". A call "
              "here is one scripted call, greeting to goodbye; Cost below says how long one lasts and what is "
              "billed.", ""]
    if smart:
        waits = [r["smart_wait_s"] for r in eot_rows]
        lines += ["## End of turn", "",
                  f"Smart Turn v3.2 (int8 ONNX on this CPU) hears each caller clip. After 0.2 s of silence it says "
                  "whether the caller has finished; if not, the turn ends after 3 s of silence. A **cut-off** is a "
                  "pause inside a clip where it would have ended the turn: the agent would talk over the caller.", "",
                  "| | Fixed silence | Smart Turn |", "|---|---|---|",
                  f"| Wait after the last word (median) | {silence:.2f} s | {fmt(med(waits))} s |",
                  f"| Turns that waited 3 s (model said not finished) | – | "
                  f"{sum(r['fallback'] for r in eot_rows)} of {len(eot_rows)} |",
                  f"| Cut-offs mid-turn | {sum(r['fixed_cutoffs'] for r in eot_rows)} | "
                  f"{sum(r['smart_cutoffs'] for r in eot_rows)} (in {sum(1 for r in eot_rows if r['smart_cutoffs'])}"
                  f" of {len(eot_rows)} clips) |",
                  f"| Model time a decision (median) | – | {fmt(med(r['model_ms'] for r in eot_rows), 0)} ms |", "",
                  "Pauses are found by loudness, right for clean synthetic clips; a real call needs a voice-activity "
                  "detector. Clips where the model waited 3 s or cut in:", "",
                  "| Call | Turn | What the caller said | Wait | Cut-offs at |", "|---|---|---|---|---|"]
        for r in eot_rows:
            if r["fallback"] or r["smart_cutoffs"]:
                lines.append(f"| {r['call']} | {r['turn']} | {r['said']} | {fmt(r['smart_wait_s'])} s | "
                             f"{r['smart_cutoff_at_s'] or '–'} |")
        lines.append("")
    voiced_rows = [r for r in pair_rows if r["hearing"] == reference]
    if voiced_rows:
        lines += ["## Fillers and the keypad", "",
                  f"Recorded lines, made once per voice before any call (sheet Lines). On the conversation heard by "
                  f"`{short(reference)}`, per voice, with the end-of-turn model:", "",
                  "| Voice | Turns with a filler | \"Still checking\" lines | Keypad instructions | Keying (total) |",
                  "|---|---|---|---|---|"]
        for v in voices:
            mine = [r for r in voiced_rows if r["voice"] == v]
            lines.append(f"| {v} | {sum(1 for r in mine if r['fillers'])} of {len(mine)} | "
                         f"{sum(r['still_lines'] for r in mine)} | {sum(r['keypad_prompts'] for r in mine)} | "
                         f"{sum(r['keying_s'] for r in mine):.1f} s |")
        lines.append("")
    lines += matrix.cost_section(cost_rows, shape)
    lines += ["", "Hearing is the audio sent, the model the tokens it reported for the conversation that hearing "
              "produced, the voice the voiced conversation's pieces. Local engines cost nothing but use the server's "
              "CPU.", "",
              "| Stage | Engine | Price | Source | Provider's own | Note |", "|---|---|---|---|---|---|"]
    for r in price_rows:
        if r.get("unpriced"):
            price = "no price: unknown"
        elif r.get("local"):
            price = "local: no fee"
        elif "per_minute" in r:
            price = f"${r['per_minute']} a minute"
        elif "input_per_m" in r:
            price = f"${r['input_per_m']} / ${r['output_per_m']} a million tokens in / out"
        elif "per_m_chars" in r:
            price = f"${r['per_m_chars']} a million characters"
        else:
            price = f"${r['text_per_m']} / ${r['audio_per_m']} a million text / audio tokens"
        lines.append(f"| {r['stage']} | {r['engine']} | {price} | {r.get('source', '–')} | "
                     f"{'yes' if r.get('official') else ('–' if r.get('local') or r.get('unpriced') else 'no')} | "
                     f"{r.get('note', '')} |")
    lines += ["", f"Prices checked {next((r['checked'] for r in price_rows if r.get('checked')), '–')}.", "",
              "## Reference points", "", "| Band | Up to | Meaning |", "|---|---|---|"]
    for limit, label in matrix.BANDS:
        lines.append(f"| {label} | {'–' if limit == float('inf') else f'{limit:g} s'} | {matrix.BAND_NOTES[label]} |")
    lines.append("")
    for ref in matrix.REFERENCES:
        lines.append(f"- **{ref['seconds']:g} s** ({ref['kind']}): {ref['what']}. [{ref['source']}]({ref['url']})")
    lines += ["", "## Hearing", "",
              "| Engine | Where | Median | 90% | Network (median) | Word errors | Missed most |", "|---|---|---|---|---|---|---|"]
    for spec in ears_ok:
        mine = [r for r in asr_rows if r.get("hearing") == spec and "wer" in r]
        nets = [net_total({"connect": r.get("net_connect", 0), "upload": r.get("net_upload", 0),
                           "download": r.get("net_download", 0)}) if "net_connect" in r else None for r in mine]
        missed = ", ".join(w for w, _ in misheard_words([(r["said"], r["heard"]) for r in mine], 5)) or "–"
        lines.append(f"| {short(spec)} | {mine[0]['where']} | {fmt(med(r['seconds'] for r in mine))} s"
                     f" | {fmt(p90(r['seconds'] for r in mine))} s | {fmt(med(nets))} s | "
                     f"{100 * statistics.mean(r['wer'] for r in mine):.1f}% | {missed} |")
    lines += ["", "Streaming engines (google-stream, openai-rt) get the clip as it is spoken; their time runs from the "
              "end-of-turn decision to the final text.", ""]
    plain = [r for r in model_rows if not r["tools"]]
    tooled = [r for r in model_rows if r["tools"]]
    lines += [f"## Model: {args.model}", "",
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
        first = [r["playable_s"] for r in mine if r["piece"] == 1]
        nets = [r["net_connect"] + r["net_upload"] + r["net_download"] for r in mine if "net_connect" in r]
        lines.append(f"| {spec} | {voice.where} | {fmt(med(first))} s | {fmt(p90(first))} s | "
                     f"{fmt(med(r['real_time_factor'] for r in mine))} | {fmt(med(nets))} s | "
                     f"{sum(1 for r in mine if r['error'])} |")
    lines += ["", "Make / speak time below 1 means the voice makes audio faster than it plays. A streamed voice's "
              "first piece counts from when its audio could start playing and run without a gap.", "",
              "## Calls", "", "| Hearing | Call | Result |", "|---|---|---|"]
    for r in call_rows:
        lines.append(f"| {short(r['hearing'])} | {r['call']} | {'pass' if r['passed'] else 'fail: ' + r['failed']} |")
    lines += ["", "## Recordings", "",
              "- `recordings/caller/`: what the caller said, one clip per turn.",
              "- `recordings/<voice>/`: every piece the agent said, its greeting, and `lines/` (fillers, \"still "
              "checking\", keypad instructions).",
              f"- `recordings/calls/<voice>/<call>.wav`: each call as it would sound: the waits heard by "
              f"`{short(reference)}`, the end-of-turn model, fillers and keypad tones.", "",
              "Every table is also in `results.xlsx` and in one CSV each."]
    return "\n".join(lines) + "\n"


def main() -> int:
    return asyncio.run(run(parse(sys.argv[1:])))


if __name__ == "__main__":
    raise SystemExit(main())
