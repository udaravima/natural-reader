"""The benchmark's report (spec 2026-11-17-call-simulation): stage statistics over every turn, the network share of
the cloud stages, hearing accuracy, scored calls, and plain notes on what the numbers point to. A turn is a dict
the runner (scripts/bench_call.py) writes; every time in it is in seconds."""
from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

from .scoring import misheard_words

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "host.containers.internal", "ollama"})

# The chain Talk runs before the caller hears anything, in order.
FIRST_SOUND_PARTS = [("silence_s", "End-of-turn silence (Talk waits for it)"), ("hearing_s", "Hearing"),
                     ("model_first_piece_s", "Model: first piece ready"), ("voice_first_s", "Voice: first piece")]


def stats(values: list[float | None]) -> dict[str, float] | None:
    """Median, 90th percentile (nearest rank) and worst of the values that exist; None when none do."""
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return None
    return {"median": statistics.median(xs), "p90": xs[min(len(xs) - 1, max(0, -(-9 * len(xs) // 10) - 1))],
            "worst": xs[-1], "n": len(xs)}


def _net_total(net: dict[str, float] | None, keys=("connect", "upload", "download")) -> float | None:
    return None if not net else sum(net.get(k, 0.0) for k in keys)


def stage_table(turns: list[dict]) -> list[tuple[str, dict | None]]:
    col = lambda key: [t.get(key) for t in turns]   # noqa: E731
    return [
        ("End-of-turn silence (fixed)", stats(col("silence_s"))),
        ("Hearing", stats(col("hearing_s"))),
        ("  of which network (connect, upload, download)", stats([_net_total(t.get("hearing_net")) for t in turns])),
        ("Model: first word", stats(col("model_first_word_s"))),
        ("Model: first piece ready", stats(col("model_first_piece_s"))),
        ("Model: whole reply", stats(col("model_whole_s"))),
        ("  of which tools (their own time)", stats(col("tool_s"))),
        ("  of which opening connections to the model", stats(col("model_connect_s"))),
        ("Voice: first piece", stats(col("voice_first_s"))),
        ("  of which network (connect, upload, download)", stats([_net_total(t.get("voice_first_net")) for t in turns])),
        ("**First sound after the caller stopped**", stats(col("first_sound_s"))),
        ("Silence between sentences, per turn", stats(col("stalls_s"))),
        ("Words held back before a tool, per turn", stats([t.get("held_s") or None for t in turns])),
    ]


def network_share(turns: list[dict]) -> dict[str, Any]:
    """Of the cloud stages' time (hearing, model requests, voice), how much went to opening connections and moving
    bytes, and how many requests opened a new connection."""
    spent = net = 0.0
    new = requests = 0
    for t in turns:
        for key in ("hearing", "voice_first"):
            split = t.get(f"{key}_net")
            if split:
                spent += sum(split.values())
                net += _net_total(split) or 0.0
                requests += 1
                new += 1 if t.get(f"{key}_new_connection") else 0
        cloud = [r for r in t.get("model_requests") or () if r.get("host") not in LOCAL_HOSTS]
        for r in cloud:
            requests += 1
            new += 1 if r.get("new_connection") else 0
            net += r.get("connect") or 0.0
        if cloud:
            spent += t.get("model_whole_s") or 0.0
    return {"seconds": net, "of_seconds": spent, "share": (net / spent) if spent else None,
            "new_connections": new, "requests": requests}


def misheard(turns: list[dict], limit: int = 12) -> list[tuple[str, int]]:
    """Words of the script the hearing didn't return, most often first."""
    return misheard_words([(t["said"], t["heard"]) for t in turns if t.get("heard") is not None], limit)


def notes(turns: list[dict], calls: list[dict]) -> list[str]:
    """What the numbers point to, biggest first. Each note names a stage and the number behind it."""
    out: list[str] = []
    parts = [(label, (stats([t.get(key) for t in turns]) or {}).get("median")) for key, label in FIRST_SOUND_PARTS]
    parts = [(label, v) for label, v in parts if v]
    total = sum(v for _, v in parts)
    if total:
        label, v = max(parts, key=lambda p: p[1])
        out.append(f"The slowest link before the first sound is **{label}**: {v:.2f} s of a typical "
                   f"{total:.2f} s ({100 * v / total:.0f}%).")
    share = network_share(turns)
    if share["share"] is not None:
        out.append(f"Network (opening connections and moving bytes) took {share['seconds']:.2f} s of "
                   f"{share['of_seconds']:.2f} s in cloud stages ({100 * share['share']:.0f}%); "
                   f"{share['new_connections']} of {share['requests']} requests opened a new connection.")
    wers = [t["wer"] for t in turns if t.get("wer") is not None]
    missed = misheard(turns, 8)
    if wers and missed:
        words = ", ".join(w for w, _ in missed)
        out.append(f"Hearing got {100 * statistics.mean(wers):.0f}% of words wrong on average, and one misheard "
                   f"name or number fails a booking. The script's words it missed most: {words}. Those are what "
                   "phrase hints or a fine-tune would target.")
    invented = [c["id"] for c in calls if any(ch["name"] == "money" and not ch["ok"] for ch in c["checks"])]
    if invented:
        out.append(f"The model said amounts no tool returned in: {', '.join(invented)}. Tool discipline: a "
                   "stronger model, or fine-tuning on calls that look things up.")
    skipped = [c["id"] for c in calls if any(ch["name"] == "tools" and not ch["ok"] for ch in c["checks"])]
    if skipped:
        out.append(f"The model skipped a lookup it needed in: {', '.join(skipped)}.")
    silent = sum(1 for t in turns if t.get("empty_after_tool"))
    if silent:
        out.append(f"{silent} turn{'s' if silent > 1 else ''} ended in silence after a tool ran: the app should ask "
                   "the model again for the answer.")
    held = [t["held_s"] for t in turns if t.get("held_s")]
    if held:
        out.append(f"In {len(held)} turn{'s' if len(held) > 1 else ''} the model spoke before a tool, and Talk held "
                   f"those words until the answer began ({statistics.median(held):.2f} s typical): Talk should voice "
                   "a step's words when the step ends, or play the scenario's filler line.")
    stalls = stats([t.get("stalls_s") for t in turns])
    if stalls and stalls["median"] > 0.3:
        out.append(f"The voice is slower than speech: a typical reply has {stalls['median']:.2f} s of silence "
                   "between its sentences.")
    errors = Counter(e for t in turns for e in (t.get("model_error"), t.get("hearing_error"), *t.get("voice_errors", ()))
                     if e)
    for error, n in errors.most_common(3):
        out.append(f"Error {n}x: {error}")
    return out


def _fmt(v: float | None) -> str:
    return "–" if v is None else f"{v:.2f}"


def render(setup: dict[str, Any], calls: list[dict], turns: list[dict]) -> str:
    passed = sum(1 for c in calls if c["passed"])
    lines = [f"# Call benchmark: {setup['scenario_name']}", "",
             " · ".join(f"**{k}** {v}" for k, v in setup["shown"].items()), "",
             f"## Calls: {passed} of {len(calls)} passed", "", "| Call | Result | What failed |", "|---|---|---|"]
    for c in calls:
        failed = "; ".join(f"{ch['name']}: {ch['detail']}" for ch in c["checks"] if not ch["ok"])
        lines.append(f"| {c['id']}{' #' + str(c['repeat'] + 1) if setup.get('repeat', 1) > 1 else ''} | "
                     f"{'pass' if c['passed'] else '**fail**'} | {failed or '–'} |")
    lines += ["", f"## Where the time goes (seconds, over {len(turns)} turns)", "",
              "| Stage | median | 90% | worst |", "|---|---|---|---|"]
    for label, s in stage_table(turns):
        lines.append(f"| {label} | {_fmt(s and s['median'])} | {_fmt(s and s['p90'])} | {_fmt(s and s['worst'])} |")
    lines += ["", "## What this points to", ""] + [f"- {n}" for n in notes(turns, calls)]
    wers = [t["wer"] for t in turns if t.get("wer") is not None]
    if wers:
        lines += ["", "## Hearing", "", f"Word error rate: median {100 * statistics.median(wers):.0f}%, mean "
                  f"{100 * statistics.mean(wers):.0f}%, worst {100 * max(wers):.0f}%.", "",
                  "| Call, turn | Said | Heard | WER |", "|---|---|---|---|"]
        for t in sorted(turns, key=lambda t: -(t.get("wer") or 0))[:8]:
            if t.get("wer"):
                lines.append(f"| {t['call']} {t['turn']} | {t['said']} | {t['heard']} | {100 * t['wer']:.0f}% |")
    lines += ["", "## Turns", ""]
    for c in calls:
        lines += [f"### {c['id']}{' #' + str(c['repeat'] + 1) if setup.get('repeat', 1) > 1 else ''}: {c['title']}",
                  "", "| # | Caller (as heard) | Agent | Tools | First sound |", "|---|---|---|---|---|"]
        for t in (t for t in turns if t["call"] == c["id"] and t["repeat"] == c["repeat"]):
            tools = ", ".join(f"{r['name']}{'' if r['ok'] else ' (error)'}" for r in t.get("tool_runs", ()))
            reply = (t.get("reply") or "").replace("\n", " ").replace("|", "/") or f"*({t.get('model_error') or 'silence'})*"
            lines.append(f"| {t['turn']} | {(t.get('heard') or '').replace('|', '/')} | {reply} | {tools or '–'} | "
                         f"{_fmt(t.get('first_sound_s'))} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def compare(runs: list[tuple[str, dict]]) -> str:
    """Several results.json side by side: calls passed and each stage's median."""
    head = "| Stage | " + " | ".join(name for name, _ in runs) + " |"
    lines = [head, "|---|" + "---|" * len(runs)]
    passed = [f"{sum(c['passed'] for c in r['calls'])}/{len(r['calls'])}" for _, r in runs]
    lines.append("| Calls passed | " + " | ".join(passed) + " |")
    for key, label in [("hearing", "Hearing"), ("model", "Model"), ("voice", "Voice")]:
        lines.append(f"| {label} engine | " + " | ".join(str(r["setup"]["shown"].get(label, "")) for _, r in runs) + " |")
    tables = [dict(stage_table(r["turns"])) for _, r in runs]
    for label, _ in stage_table(runs[0][1]["turns"]):
        lines.append(f"| {label} | " + " | ".join(_fmt((t.get(label) or {}).get("median")) for t in tables) + " |")
    return "\n".join(lines) + "\n"
