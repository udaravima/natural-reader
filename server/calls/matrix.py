"""The hearing x voice benchmark (scripts/bench_matrix.py): composing a caller's wait from measured stage times,
rating it against reference points for conversation timing, building whole-call recordings that keep the real
waits, and exporting CSV and Excel (spec 2026-11-17-call-simulation).

Why compose instead of running every pair: Talk's stages run one after another (the clip is heard, then the model
answers, and each piece is voiced once its text is ready), so a pair's first sound is the sum of its stages,
measured once each. Running every pair end to end would multiply the model's requests by the number of pairs on a
15-requests-a-minute quota and add nothing but noise."""
from __future__ import annotations

import csv
import io
import math
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from .playback import playback

# What the caller's wait (last word to first sound) is compared with. `kind`: study (measured in research),
# standard (ITU-T), practice (voice-agent and usability guidance; vendor sources are directional).
REFERENCES: list[dict[str, Any]] = [
    {"what": "Gap between turns in human conversation: about 0.2 s on average; most turn changes fall between "
             "-0.1 and 0.5 s", "seconds": 0.2, "kind": "study",
     "source": "Levinson & Torreira 2015, Frontiers in Psychology 6:731",
     "url": "https://www.frontiersin.org/articles/10.3389/fpsyg.2015.00731/full"},
    {"what": "Answers to yes/no questions in 10 languages: every language avoids both overlap and long silence; "
             "average start from +7 ms (Japanese) to +469 ms (Danish), as quoted by secondary sources",
     "seconds": 0.47, "kind": "study", "source": "Stivers et al. 2009, PNAS 106(26)",
     "url": "https://pubmed.ncbi.nlm.nih.gov/19553212/"},
    {"what": "Voice-agent target: under 0.8 s from the caller's last word to the agent's first sound, at the "
             "median (an industry rule of thumb from vendors, not a standard)", "seconds": 0.8, "kind": "practice",
     "source": "Voice-agent latency guides, e.g. twig.so",
     "url": "https://www.twig.so/blog/voice-ai-agents-latency-budget-800ms"},
    {"what": "About 1 s is the limit for a person's flow of thought to stay uninterrupted", "seconds": 1.0,
     "kind": "practice", "source": "Nielsen, Response Times: The 3 Important Limits (1993)",
     "url": "https://www.nngroup.com/articles/response-times-3-important-limits/"},
    {"what": "Phone network only (one-way mouth-to-ear delay): up to 0.15 s is transparent, 0.4 s is the upper "
             "limit for network planning. Not about the agent's thinking time", "seconds": 0.15, "kind": "standard",
     "source": "ITU-T G.114", "url": "https://www.itu.int/rec/T-REC-G.114"},
]

# Bands for the caller's wait. 0.5 and 0.8 come from the references; 1.2 and 2.0 are rules of thumb.
BANDS: list[tuple[float, str]] = [(0.5, "human-like"), (0.8, "natural"), (1.2, "acceptable"),
                                  (2.0, "noticeable"), (math.inf, "slow")]
BAND_NOTES = {"human-like": "within human turn gaps (most under 0.5 s)",
              "natural": "under the usual voice-agent target of 0.8 s",
              "acceptable": "a pause people notice but accept (rule of thumb)",
              "noticeable": "callers start to talk over the agent (rule of thumb)",
              "slow": "callers wonder whether the line dropped (rule of thumb)"}


def band(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    return next(label for limit, label in BANDS if seconds <= limit)


def compose(*, hearing: dict[tuple[str, int], float], turns: list[dict], voiced: dict[tuple[str, int, int],
            tuple[float, float]], silence_s: float, parallel: int) -> list[dict]:
    """One row per turn of the reference conversation: `hearing` is this pair's hearing time for each caller clip,
    `turns` the reference run's turns (each piece's `ready_s` after the model was asked), `voiced` this voice's
    (seconds to make, seconds of audio) for each piece."""
    rows = []
    for t in turns:
        key = (t["call"], t["turn"])
        pieces = t.get("pieces") or []
        made = [voiced.get((*key, k), (0.0, 0.0)) for k in range(1, len(pieces) + 1)]
        play = playback([p["ready_s"] for p in pieces], [m[0] for m in made], [m[1] for m in made],
                        parallel=parallel)
        heard = hearing.get(key)
        first = (silence_s + (heard or 0.0) + play["first_audio_s"]) if pieces else None
        rows.append({"call": t["call"], "turn": t["turn"], "silence_s": silence_s, "hearing_s": heard,
                     "model_first_piece_s": pieces[0]["ready_s"] if pieces else None,
                     "voice_first_s": made[0][0] if made else None, "first_sound_s": first,
                     "stalls_s": play["stalls_s"], "gaps_s": play["gaps_s"], "band": band(first)})
    return rows


def clip_sources(calls, folder: Path | None) -> dict[str, Path]:
    """Recordings of the caller's lines in `folder` (named <call id>-<turn>.wav after the spoken script), by the
    words they say: a keypad version of a call reuses the clip of any line it shares with the spoken one."""
    found: dict[str, Path] = {}
    if folder is None:
        return found
    for script in calls:
        for n, said in enumerate(script.caller, 1):
            path = folder / f"{script.id}-{n}.wav"
            if path.is_file():
                found.setdefault(said, path)
    return found


def read_wav(data: bytes, rate: int) -> np.ndarray:
    """A recording as mono float32 at `rate`."""
    samples, source_rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    mono = samples.mean(axis=1)
    if source_rate != rate:
        from scipy.signal import resample_poly
        g = math.gcd(source_rate, rate)
        mono = resample_poly(mono, rate // g, source_rate // g).astype(np.float32)
    return mono


def conversation(rate: int, greeting: np.ndarray | None, turns: list[dict], *, caller_pause_s: float = 0.4
                 ) -> np.ndarray:
    """A whole call as it would sound: the greeting, then for each turn a short pause, the caller, the measured
    wait (`gap_s`: the caller's last word to the agent's first sound), and the agent's pieces with the measured
    silences between them (`stalls_s`)."""
    silence = lambda s: np.zeros(int(round(s * rate)), dtype=np.float32)   # noqa: E731
    parts = [greeting] if greeting is not None else []
    for t in turns:
        parts += [silence(caller_pause_s), t["caller"], silence(t["gap_s"])]
        for k, piece in enumerate(t["pieces"]):
            if k:
                parts.append(silence(t["stalls_s"][k - 1] if k - 1 < len(t["stalls_s"]) else 0.0))
            parts.append(piece)
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)



def call_seconds(greeting_s: float | None, turns: list[tuple[float, float]], *, caller_pause_s: float = 0.4) -> float:
    """How long a call lasts, as `conversation` records it: the greeting, then for each turn the pause, the caller
    up to their last word and the agent's turn to its end (turn_timeline's `end_s`). `turns`: (caller seconds,
    agent seconds)."""
    return (greeting_s or 0.0) + sum(caller_pause_s + caller + agent for caller, agent in turns)


def short(spec: str) -> str:
    """An engine as the report names it: a Google recognizer by its region and id, never by its project."""
    if "/recognizers/" not in spec:
        return spec
    return f"{spec.split(':')[0]}:{spec.split('/locations/')[1].split('/')[0]}/{spec.split('/recognizers/')[-1]}"


def money(v: float | None, digits: int = 2, *, at_least: bool = False) -> str:
    return "–" if v is None else f"{'≥ ' if at_least else ''}${v:,.{digits}f}"


def _span(values: list[float]) -> str:
    lo, hi = min(values), max(values)
    return money(lo) if f"{lo:.2f}" == f"{hi:.2f}" else f"{money(lo)} to {money(hi)}"


def _lower_bound(row: dict) -> bool:
    return bool(row.get("model_turns_failed"))


def cost_section(cost_rows: list[dict], shape: dict) -> list[str]:
    """The report's Cost section: what one call is, each pair's cost per call, per 1,000 calls and per 1,000
    minutes, and which figures are lower bounds. `shape`: the reference conversation's mean call (scripts/
    bench_matrix.py builds it)."""
    lines = ["## Cost", "",
             "**\"Per 1,000 calls\" means 1,000 of the scripted calls, not 1,000 minutes.** A call runs from the "
             "greeting to the goodbye. Its cost is the mean over the calls, at the paid tier, with no free allowance "
             "taken off; \"per 1,000 minutes\" spreads it over how long the call lasts in this run.", "",
             "### What one call is", "",
             f"The conversation heard by `{short(shape['reference'])}`, the mean of {shape['calls']} "
             f"call{'' if shape['calls'] == 1 else 's'}. Speech and "
             "text are billed; the waits between them are not.", "",
             "| | A call | Billed by |", "|---|---|---|",
             f"| Caller turns | {shape['caller_turns']:.1f} | – |",
             f"| Caller audio (the clips sent to hearing) | {shape['caller_s']:.1f} s | hearing, by the minute |",
             f"| Agent text, spoken by the voice | {shape['agent_chars']:,.0f} characters | the voice: Chirp by the "
             "character, Gemini-TTS by tokens |",
             f"| Model text | {shape['prompt_tokens']:,.0f} tokens read, {shape['reply_tokens']:,.0f} written | the "
             "model, by the million tokens |", "",
             "| Voice | Agent speech a call | Call length (mean, shortest to longest) |", "|---|---|---|"]
    for v, x in shape["voices"].items():
        lines.append(f"| {v} | {x['agent_s']:.0f} s | {x['minutes']:.2f} min ({x['min_minutes']:.2f} to "
                     f"{x['max_minutes']:.2f}) |")
    lines += ["", "### What each pair costs", "",
              "| Hearing | Voice | Call length | Hearing | Model | Voice | Per call | Per 1,000 calls | "
              "Per 1,000 minutes |", "|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(cost_rows, key=lambda r: (_lower_bound(r), r["total_usd_per_call"] is None,
                                              r["total_usd_per_call"] or 0)):
        low = _lower_bound(r)
        length = "–" if r.get("call_minutes") is None else f"{r['call_minutes']:.2f} min"
        lines.append(f"| {short(r['hearing'])} | {r['voice']} | {length} | {money(r['hearing_usd_per_call'], 4)} | "
                     f"{money(r['model_usd_per_call'], 4, at_least=low)} | {money(r['voice_usd_per_call'], 4)} | "
                     f"{money(r['total_usd_per_call'], 4, at_least=low)} | "
                     f"{money(r['usd_per_1000_calls'], at_least=low)} | "
                     f"{money(r.get('usd_per_1000_minutes'), at_least=low)} |")
    lows = [r for r in cost_rows if _lower_bound(r)]
    if lows:
        lines += ["", "**≥ marks a lower bound.** The model didn't answer some caller turns of that hearing's "
                  "conversation (it errored, or nothing was heard), so its cost counts only the turns it answered; a "
                  "whole call would cost more:", ""]
        for h in dict.fromkeys(r["hearing"] for r in lows):
            r = next(x for x in lows if x["hearing"] == h)
            error = " ".join((r.get("model_first_error") or "").split())
            why = f"first error: {error[:80]}{'…' if len(error) > 80 else ''}" if error else "nothing was heard"
            lines.append(f"- `{short(h)}`: {r['model_turns_failed']} of {r['model_turns']} turns ({why})")
        full = [1000 * r["model_usd_per_call"] for r in cost_rows
                if not _lower_bound(r) and r.get("model_usd_per_call") is not None]
        lines += ["", f"Where every turn was answered, the model cost {_span(full)} per 1,000 calls: use that to "
                  "compare these pairs." if full else
                  "No hearing had every turn answered: rerun before comparing what the model costs."]
    streaming = [r for r in cost_rows if r.get("streams") and r.get("hearing_whole_call_usd_per_1000") is not None]
    if streaming:
        lines += ["", "**Hearing here is sent only the caller's turns.** A phone line that streams the whole call to "
                  "a streaming engine, both sides and every wait, pays for all of it. Hearing alone would then cost:",
                  ""]
        for h in dict.fromkeys(r["hearing"] for r in streaming):
            mine = [r for r in streaming if r["hearing"] == h]
            turns = [1000 * r["hearing_usd_per_call"] for r in mine if r.get("hearing_usd_per_call") is not None]
            lines.append(f"- `{short(h)}`: {_span([r['hearing_whole_call_usd_per_1000'] for r in mine])} per 1,000 "
                         f"calls instead of {_span(turns) if turns else '–'}")
    lines += ["", "A call's length counts every wait as well as speech, so a slower model or voice makes calls "
              "longer and the price per 1,000 minutes smaller while a call costs the same. Compare pairs per call; "
              "per 1,000 minutes is for comparing with prices quoted by the minute.", "",
              "Not included: the phone line, the server's CPU for local engines, and the greeting, fillers and keypad "
              "lines, which are recorded once."]
    return lines

def _columns(rows: list[dict]) -> list[str]:
    return list(dict.fromkeys(k for r in rows for k in r))


def write_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_columns(rows))
        writer.writeheader()
        for r in rows:
            writer.writerow({k: "" if v is None else v for k, v in r.items()})


def write_xlsx(path: Path, sheets: dict[str, list[dict]]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    book = Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name[:31])
        columns = _columns(rows)
        if not columns:
            continue
        sheet.append(columns)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for r in rows:
            sheet.append([r.get(c) if not isinstance(r.get(c), (list, dict)) else str(r.get(c)) for c in columns])
        sheet.freeze_panes = "A2"
        for i, column in enumerate(columns, 1):
            width = max([len(str(column))] + [len(f"{r.get(column):.3f}" if isinstance(r.get(column), float)
                                                  else str(r.get(column) or "")) for r in rows[:200]])
            sheet.column_dimensions[sheet.cell(1, i).column_letter].width = min(60, width + 2)
            for row in sheet.iter_rows(min_row=2, min_col=i, max_col=i):
                if isinstance(row[0].value, float):
                    row[0].number_format = "0.000"
    book.save(path)
