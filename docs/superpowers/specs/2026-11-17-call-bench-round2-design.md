# Call benchmark, round 2: fillers, keypad digits, streaming hearing, an end-of-turn model, and cost

Status: proposed 2026-11-17, for the owner's approval. Builds on
[the call simulation](2026-11-17-call-simulation-design.md) and the hearing x voice matrix (`scripts/bench_matrix.py`).

## Why

Round 1's recordings showed the problem a caller feels: dead air. With kept-open connections and a Chirp-speed
voice, a turn with a lookup waited 4.8 s (median) before any sound and a turn without one 3.4 s. The 3 s at the
start of every turn is 0.7 s of Talk's fixed end-of-turn wait, about 1.1 s of hearing (the whole clip is sent after
the caller stops) and about 1.2 s for the model's first step. A simulated recorded filler at the moment the model
decides on a lookup cut lookup turns to 3.0 s, with the answer ready by the end of the filler in 10 of 12 turns.
Names and spoken digits were also heard wrong.

The owner chose for round 2: fillers, keypad digits, streaming hearing with an end-of-turn model, the same
synthetic caller (Kokoro, the same clips as round 1), and the cost of each stage. Out of scope for now: a model that
picks the backchannel ("jev"), the caller's mood, and name lists for the recognizers.

## Goals

1. **Fillers.** A short line per tool (`check_availability`: "Let me check those dates."), made once per voice
   before the calls, so it costs no time on the call. It plays the moment the model's step ends with a tool call
   and no words. If nothing has sounded for 1.5 s after a filler ends, a "still checking" line plays. Lines rotate
   so the same one never plays twice in a row. Configured in the scenario (`[agent.fillers]`); the old single
   `filler` line becomes the default for a tool without its own.
2. **Keypad digits** (`--keypad`). A `collect_digits` tool (`what`: `phone` or `reference`) whose recorded line is
   the instruction ("Please key in your phone number, then press the hash key."). The scripted caller keys the
   digits in the call's `[calls.keys]`. Calls that give a number get `caller_keypad` lines that don't say it, so
   round 1's spoken calls still run unchanged without `--keypad`. Keying takes 1.0 s to start plus 0.4 s a key
   including `#` (an assumption, settable). The time the caller spends keying is not counted as the agent's silence.
   Recordings play real keypad tones. Booking references are keyed as their digits; the hotel's tools accept
   `10421` for `LB-10421`.
3. **End-of-turn model.** Pipecat's Smart Turn v3.2 (CPU ONNX, BSD-2-Clause, about 8 MB, from Hugging Face
   `pipecat-ai/smart-turn-v3`) on each caller clip. Policy, as Pipecat uses it: after 0.2 s of silence ask the model;
   if it says the turn is complete, the turn ends; if not, wait for more speech or a 3 s fallback. Reported per clip:
   the end-of-turn wait (silence plus the model's own time on this CPU) and **false cut-offs**, pauses inside a clip
   where the model would have ended the turn. Pauses are found by energy, which is fine for clean synthetic clips;
   real calls would need a real voice-activity detector. LiveKit's turn detector is not used: its model licence
   allows use only inside LiveKit Agents.
4. **Streaming hearing.** Two streaming engines beside round 1's batch ones:
   - Google `chirp_2` through `StreamingRecognize`, which exists only over gRPC. It needs the `google-cloud-speech`
     package in the benchmark's environment; it is not added to the app's requirements.
   - OpenAI's realtime transcription over a websocket (the model the current docs name for it).
   The clip is sent at real-time pace, with the silence up to the end-of-turn decision, then the stream is closed.
   Hearing time is from that decision to the final transcript. Local Whisper stays batch: it cannot stream.
5. **Cost.** Per call and per 1,000 calls, by stage: hearing = billed audio minutes x price per minute; model =
   prompt and reply tokens x price per million; voice = characters (Chirp 3 HD) or text and audio tokens
   (Gemini-TTS) x price. Local engines (Whisper, Kokoro, Smart Turn) have no fee; the report says they use the
   server's CPU instead. Prices live in a data file with each price's source and the date it was checked, and the
   report says which come from the provider's own page and which from third-party pages. Costs assume the paid
   tier; the timings were measured on the free tier.
6. **Report and recordings.** Beside "first sound" the report gives "first answer" and the dead air in each turn,
   both with round 1's fixed 0.7 s wait and with Smart Turn, composed from the same measurements. A Cost sheet joins
   the Excel workbook. Each whole-call recording has the fillers, the keypad tones and the new waits, so the two
   rounds can be heard side by side.

## The run

Same caller clips as round 1 (`bench-out/caller-en`), `--keypad` on, model `gemini:gemini-3.1-flash-lite` (free tier,
paced). Hearing: Whisper `small.en`, Google batch and streaming, OpenAI `gpt-4o-mini-transcribe` batch and the
realtime streaming model. Voices: Kokoro `af_heart`, Chirp 3 HD `en-IN` and `en-US`, Gemini-TTS Flash-Lite.
`gemini-2.5-flash-tts` is left out: it timed out on 33 of 57 requests in round 1.

## Approaches considered

- **Fillers written by the model** (a short line before each tool call, as OpenAI's realtime models do): not chosen.
  It costs a model round before any sound, and Talk holds words said before a tool until the answer arrives.
- **A filler on a timer from the caller's last word:** not in this round. It would fire on most turns (the median
  wait without a lookup is 3.4 s) just before the answer, delaying it; choosing a fitting backchannel is the "jev"
  idea, deferred.
- **Keypad replacing spoken digits everywhere:** not chosen; a variant keeps round 1 reproducible.

## Not changed

Talk. Everything here is in the benchmark; what proves itself is built into Talk in a later round.

## Left to the builder

File layout inside `server/calls/`, the exact filler wording, the DTMF tone shape, and the realtime API's message
details (checked against the provider's current docs at build time).
