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
