"""When each spoken sentence of a reply would play (spec 2026-11-17-call-simulation).

Sentence i's text is ready at ready_s[i] (seconds after the reply started). Its voicing starts once the text is
ready and a voicing slot is free: `parallel` 1 for a voice that speaks one sentence at a time (Kokoro, behind the
server's inference lock), 2 for a cloud voice (Talk asks for two sentences ahead). It plays once its audio is made
and the sentence before has finished; any gap between two sentences is a stall the caller hears as silence."""
from __future__ import annotations


def playback(ready_s: list[float], synth_s: list[float], audio_s: list[float], *, parallel: int) -> dict:
    if not ready_s:
        return {"first_audio_s": None, "stalls_s": 0.0, "longest_stall_s": 0.0, "finished_s": None, "audio_s": 0.0,
                "gaps_s": []}
    parallel = max(1, parallel)
    made: list[float] = []
    for i, ready in enumerate(ready_s):
        slot_free = made[i - parallel] if i >= parallel else 0.0
        made.append(max(ready, slot_free) + synth_s[i])
    gaps: list[float] = []   # the silence before each sentence after the first
    stalls, longest, end = 0.0, 0.0, None
    for i, done in enumerate(made):
        start = done if end is None else max(done, end)
        if end is not None:
            gap = start - end
            gaps.append(gap)
            stalls += gap
            longest = max(longest, gap)
        end = start + audio_s[i]
    return {"first_audio_s": made[0], "stalls_s": stalls, "longest_stall_s": longest, "finished_s": end,
            "audio_s": sum(audio_s), "gaps_s": gaps}
