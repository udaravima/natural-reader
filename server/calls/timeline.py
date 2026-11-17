"""What a caller hears in one turn, and when (spec 2026-11-17-call-bench-round2).

Round 1 composed only the answer: end-of-turn wait + hearing + the model's first piece + voicing it. Round 2 adds
what fills the silence, from the same measured stage times (seconds; 0 is the caller's last word):

- the model is asked at `wait_s + hearing_s`; each step's end and each piece's ready time are measured from then;
- a step that ends with tool calls and no words plays a recorded filler for its first tool at that moment, if
  nothing is playing (recorded beforehand, so it takes no time to make);
- a step that calls `collect_digits` plays the keypad instruction, then the caller keys (start + a time per key,
  the hash included); the model's next request starts only then, so the rest of the turn moves later by that much;
- answer pieces are voiced as playback.py voices them (`parallel` at a time) and play one after another, after
  whatever is playing;
- if a filler or a "still checking" line ends and nothing has started `still_after_s` later while an answer is
  still to come, a "still checking" line plays.

Dead air is the silence from the caller's last word to the end of the turn, the time the caller spends keying
excluded."""
from __future__ import annotations

from collections.abc import Callable

import numpy as np

from .keypad import dtmf, keying_s

Line = tuple[str, float]   # (text, seconds of audio)


def turn_timeline(*, wait_s: float, hearing_s: float, steps: list[dict], pieces: list[dict],
                  made: list[tuple[float, float]], parallel: int, filler: Callable[[str], Line] | None = None,
                  still: Callable[[], Line] | None = None, prompt: Callable[[str], Line] | None = None,
                  still_after_s: float = 1.5, key_start_s: float = 1.0, per_key_s: float = 0.4) -> dict:
    """`steps`: {"end_s", "tools", "words", "keyed": [(what, digits)]}; `pieces`: {"ready_s"}; `made`: (seconds to
    voice, seconds of audio) per piece."""
    t0 = wait_s + hearing_s
    parallel = max(1, parallel)
    sounds: list[dict] = []
    keying: list[dict] = []
    state = {"busy": 0.0, "shift": 0.0, "fills": False}   # last sound's end; keypad delay so far; may a still follow
    voiced_at: list[float] = []   # when each piece's audio was made (voicing slots free up then)

    def play(start: float, length: float, kind: str, text: str) -> None:
        sounds.append({"start_s": start, "end_s": start + length, "kind": kind, "text": text})
        state["busy"] = start + length
        state["fills"] = kind in ("filler", "still")

    def answer(i: int) -> None:
        ready = t0 + pieces[i]["ready_s"] + state["shift"]
        slot_free = voiced_at[i - parallel] if i >= parallel else 0.0
        done = max(ready, slot_free) + made[i][0]
        voiced_at.append(done)
        while still is not None and state["fills"] and max(done, state["busy"]) - state["busy"] > still_after_s:
            text, length = still()
            play(state["busy"] + still_after_s, length, "still", text)
        play(max(done, state["busy"]), made[i][1], "answer", pieces[i].get("text", ""))

    next_piece = 0
    for s in steps:
        while next_piece < len(pieces) and pieces[next_piece]["ready_s"] <= s["end_s"]:
            answer(next_piece)
            next_piece += 1
        at = t0 + s["end_s"] + state["shift"]
        tools = list(s.get("tools") or ())
        if "collect_digits" in tools and prompt is not None and s.get("keyed"):
            for what, digits in s["keyed"]:
                text, length = prompt(what)
                play(max(at, state["busy"]), length, "prompt", text)
                ends = state["busy"] + keying_s(digits, key_start_s, per_key_s)
                keying.append({"start_s": state["busy"], "end_s": ends, "digits": digits})
                state["shift"] += ends - at
                state["busy"], at = ends, ends
        elif tools and not s.get("words") and filler is not None and at >= state["busy"]:
            text, length = filler(tools[0])
            play(at, length, "filler", text)
    while next_piece < len(pieces):
        answer(next_piece)
        next_piece += 1

    answers = [x for x in sounds if x["kind"] == "answer"]
    if sounds:
        end = state["busy"]
    else:
        end = t0 + (steps[-1]["end_s"] if steps else 0.0) + state["shift"]
    spans = sorted([(x["start_s"], x["end_s"]) for x in sounds] + [(k["start_s"], k["end_s"]) for k in keying])
    covered, longest, last = 0.0, 0.0, 0.0
    for start, stop in spans:
        longest = max(longest, start - last)
        covered += stop - start
        last = max(last, stop)
    longest = max(longest, end - last)
    return {"first_sound_s": sounds[0]["start_s"] if sounds else None,
            "first_answer_s": answers[0]["start_s"] if answers else None,
            "dead_air_s": end - covered, "longest_silence_s": longest, "end_s": end,
            "sounds": sounds, "keying": keying}


def render(rate: int, timeline: dict, audio_for: Callable[[dict], np.ndarray | None], *, key_start_s: float = 1.0,
           per_key_s: float = 0.4) -> np.ndarray:
    """The turn as it would sound from the caller's last word: each sound's audio (`audio_for(sound)`; None plays
    silence) at its start, and the keypad tones while the caller keys."""
    parts: list[tuple[int, np.ndarray]] = []
    for sound in timeline["sounds"]:
        clip = audio_for(sound)
        if clip is not None and len(clip):
            parts.append((int(round(sound["start_s"] * rate)), clip.astype(np.float32)))
    for k in timeline["keying"]:
        parts.append((int(round(k["start_s"] * rate)),
                      dtmf(k["digits"], rate, start_s=key_start_s, per_key_s=per_key_s)))
    length = max([int(round(timeline["end_s"] * rate))] + [at + len(clip) for at, clip in parts])
    out = np.zeros(length, dtype=np.float32)
    for at, clip in parts:
        out[at:at + len(clip)] += clip
    return out
