# Call benchmark round 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add recorded fillers, keypad digits, an end-of-turn model, streaming hearing and per-stage cost to the
hearing x voice benchmark, then run round 2 on the same synthetic caller.

**Architecture:** Pure, tested modules in `server/calls/` (fillers, keypad, timeline, endturn, streaming, cost)
and a scenario extension; `scripts/bench_matrix.py` orchestrates them. A turn's sound is composed from measured
stage times by `timeline.turn_timeline`, which adds fillers, still-checking lines, keypad prompts and keying time
to round 1's playback model. Talk is not changed.

**Tech Stack:** Python 3.13, numpy/scipy/soundfile, onnxruntime + transformers' WhisperFeatureExtractor (Smart Turn),
google-cloud-speech (gRPC, benchmark venv only), websockets (OpenAI realtime), openpyxl.

**Spec:** docs/superpowers/specs/2026-11-17-call-bench-round2-design.md

## Global Constraints

- Talk and the app's requirements are not changed; `google-cloud-speech` goes into `.venv` only.
- Tests run with the scratch databases: `source <scratchpad>/testenv.sh` before pytest. Never print DB URLs.
- `bench-out/` results hold the private Google project id: never commit or publish them; mask `projects/<id>` in
  anything shown.
- No commit without the owner's per-commit permission (CLAUDE.md overrides the plan's commit steps).
- Prices carry source URL, date checked (2026-11-17) and whether the page is the provider's own.
- Keying: 1.0 s to start plus 0.4 s a key including `#` (settable). Still-checking line after 1.5 s of silence.
  Smart Turn: 0.2 s of silence, then the model (threshold 0.5); otherwise end at 3 s of silence.

## Review Focus

1. A turn that ends silent after a tool (no pieces): timeline gives no first answer, and still lines stop at the
   turn's end instead of looping.
2. `collect_digits` for something the call has no keys for: an empty result the agent can say, not a crash.
3. A clip that is all silence, or has no pause: end-of-turn analysis returns a result, never raises.
4. A streaming engine that errors mid-stream (websocket `error` event, gRPC error): `Heard.error`, empty text,
   and the run goes on.
5. An engine with no price: cost shows as unknown with a note; local engines cost 0.

---

### Task 1: Scenario fields for fillers and keypad

**Files:** Modify `server/calls/scenario.py`, `server/calls/scenarios/hotel/scenario.toml`;
Test `server/tests/test_calls_fillers.py`

**Interfaces — Produces:**
- `Agent.fillers: dict[str, tuple[str, ...]]` (tool name → lines; `""` → `(agent.filler,)` when set),
  `Agent.still_checking: tuple[str, ...]`, `Agent.keypad: dict[str, str]` (what → instruction line).
- `CallScript.caller_keypad: tuple[str, ...] | None`, `CallScript.keys: dict[str, str]`,
  `CallScript.lines(keypad: bool) -> tuple[str, ...]`.
- TOML: `[agent] still_checking = [...]`, `[agent.fillers] <tool> = "line" | [lines]`,
  `[agent.keypad] phone = "...", reference = "..."`; per call `caller_keypad = [...]`, `[calls.keys]`.

- [ ] Tests: hotel loads with a filler for every tool and two still lines; `book-deluxe.lines(True)` has no
  digits and `keys == {"phone": "0771234567"}`; `lines(False)` is round 1's; a filler for an unknown tool, a
  non-digit key, a key whose `what` has no `[agent.keypad]` line are each a `ScenarioError`.
- [ ] Run, see them fail; implement; run, see them pass.
- [ ] Hotel keypad calls: book-deluxe turn 3 "That's fine, please book it. My name is Kasun Perera."; spelled-name
  without its number turn (3 turns); cancel-booking turn 1 "Hi, I need to cancel my reservation. I have the
  booking reference." with `keys.reference = "10421"`.

### Task 2: Filler book and the keypad tool

**Files:** Create `server/calls/fillers.py`, `server/calls/keypad.py`; Modify `server/calls/agent.py`,
`server/calls/scenario.py` (`Scenario.toolset(extra=())`); Test `server/tests/test_calls_fillers.py`,
`server/tests/test_calls_keypad.py`, `server/tests/test_calls_agent.py`

**Interfaces — Produces:**
- `FillerBook(agent)`: `.lines() -> list[str]` (every distinct line), `.for_tool(name) -> str`, `.still() -> str`;
  never returns the line it returned last (unless a pool has one line and nothing else fits).
- `keypad.keypad_tool(keys: dict[str, str]) -> SimTool` named `collect_digits`, args `{"what": "phone"|"reference"}`,
  result `{"what", "digits"}` (empty digits plus a `note` when the call has none); `KEYPAD_RULES: str`;
  `keying_s(digits, start_s=1.0, per_key_s=0.4) -> float` (= start + per_key × (len + 1));
  `dtmf(digits, rate, *, start_s=1.0, per_key_s=0.4, tone_s=0.12) -> np.ndarray` (len = keying_s × rate).
- `agent.Step(end_s: float, tools: tuple[str, ...], words: bool, keyed: tuple[tuple[str, str], ...])`;
  `TurnResult.step_log: list[Step]`; `system_prompt(scenario, keypad=False)`; `CallAgent(..., keypad=False)`.

- [ ] Tests: rotation never repeats back to back over 20 picks; unknown tool falls back to the default line;
  `keying_s("0771234567") == 1.0 + 0.4 * 11`; `dtmf("1#")` has energy at 697 and 1209 Hz in the first tone;
  `collect_digits` with no keys returns empty digits and a note; hotel `cancel_booking` accepts `10421`; a fake
  router turn with a tool step then a text step logs `[Step(.., ("check_availability",), False, ()), Step(.., (),
  True, ())]`; the keypad prompt mentions `collect_digits` only when keypad is on.
- [ ] Fail, implement, pass.

### Task 3: The turn timeline

**Files:** Create `server/calls/timeline.py`; Test `server/tests/test_calls_timeline.py`

**Interfaces — Produces:**
`turn_timeline(*, wait_s, hearing_s, steps: list[dict], pieces: list[dict], made: list[tuple[float, float]],
parallel: int, filler=None, still=None, prompt=None, still_after_s=1.5) -> dict` with `first_sound_s`,
`first_answer_s`, `dead_air_s`, `longest_silence_s`, `end_s`, `sounds` (list of `{start_s, end_s, kind, text}`,
kind ∈ filler/still/prompt/answer), `keying` (list of `{start_s, end_s, digits}`). Times from the caller's last
word. `steps`: `{"end_s", "tools", "words", "keyed"}` relative to the model being asked; `filler(tool) -> (text,
audio_s)`, `still() -> (text, audio_s)`, `prompt(what) -> (text, audio_s)`.
`render(rate, timeline, audio: dict[str, np.ndarray], tones) -> np.ndarray` places each sound and keypad tones.

Rules: the model is asked at `wait_s + hearing_s`. A step with tools, no words and no `collect_digits` plays a
filler at its end if nothing is playing. A `collect_digits` step plays the prompt (when free), then the keying;
everything later shifts by prompt + keying − (step end). Answer pieces voice as `playback.py` does and play
after whatever is playing. After a filler, still or prompt ends, a still line plays if nothing has started
within `still_after_s` and an answer is still to come. Dead air = silence from 0 to `end_s`, keying excluded.

- [ ] Tests: no tools → same first sound as `matrix.compose`; a tool step → first sound at the step end, first
  answer after the filler ends; a 4 s gap after a 1 s filler → one still line at +1.5 s; a silent turn after a
  tool → filler only, no still line, `first_answer_s is None`; keypad → later pieces shift by prompt + keying and
  keying isn't dead air; `render` length covers `end_s`.
- [ ] Fail, implement, pass.

### Task 4: End-of-turn model

**Files:** Create `server/calls/endturn.py`; Test `server/tests/test_calls_endturn.py`

**Interfaces — Produces:** `speech_bounds(samples, rate, *, frame_s=0.02, floor_db=-40.0) -> (pauses:
list[tuple[float, float]], speech_end_s: float)`; `SmartTurn(path=None, predict=None)` with
`.predict(samples16k) -> tuple[float, float]` (probability, seconds), downloading `smart-turn-v3.2-cpu.onnx` from
`pipecat-ai/smart-turn-v3` when no path; `end_of_turn(samples, rate, model, *, stop_s=0.2, fallback_s=3.0,
threshold=0.5, fixed_s=0.7) -> dict` with `speech_end_s`, `wait_s`, `probability`, `model_s`, `fallback`,
`cutoffs` (pause starts the model would have ended on), `fixed_cutoffs` (pauses ≥ fixed_s).
Preprocessing as the smart-turn repo's inference.py: 16 kHz, last 8 s, zeros padded at the start,
`WhisperFeatureExtractor(chunk_length=8)`, `input_features`, probability = `outputs[0][0]`.

- [ ] Tests with a fake predictor: complete at the end → wait 0.2 + model time; incomplete → 3.0, `fallback`;
  a 0.4 s mid-clip pause the fake calls complete → one cut-off; all-silence clip → no raise. A real-model test
  skipped unless the file is in the HF cache: a synthetic tone burst returns a probability in [0, 1].
- [ ] Fail, implement, pass.

### Task 5: Streaming hearing

**Files:** Create `server/calls/streaming.py`; Modify `server/calls/voice.py` (`make_ears`); Install
`google-cloud-speech` into `.venv`; Test `server/tests/test_calls_streaming.py`

**Interfaces — Produces:** `paced(pcm, bytes_per_s, *, chunk_s=0.1, tail_s=0.2, sleep=asyncio.sleep)` async
iterator; `OpenAIRealtimeEars(model, env, url=...)` (`openai-rt:<model>`): `session.update` type
`transcription`, `audio/pcm` 24 kHz, `turn_detection: null`, append at real-time pace, `commit`, wait for
`conversation.item.input_audio_transcription.completed`; `GoogleStreamEars(recognizer, model, env)`
(`google-stream:<recognizer>[@<model>]`): `SpeechAsyncClient` on `<region>-speech.googleapis.com`, first request
recognizer + streaming config (LINEAR16 16 kHz, `language_codes`), audio chunks ≤ 15 KB, close after the tail.
Both return `Heard` with `seconds` = end of stream (the turn decision) → final text.

- [ ] Tests: `paced` yields chunk sizes summing to clip + tail and sleeps chunk_s each; OpenAI ears against a
  local `websockets` server that checks the session message and answers `completed` → text and seconds; the same
  server sending `error` → `Heard.error`, empty text; Google request builder: first request has no audio, later
  ones ≤ 15 KB.
- [ ] Fail, implement, pass.

### Task 6: Cost

**Files:** Create `server/calls/prices.toml`, `server/calls/cost.py`; Test `server/tests/test_calls_cost.py`

**Interfaces — Produces:** `load_prices(path=None) -> dict`; `price_for(prices, stage, spec) -> dict | None`
(exact spec, then the spec's kind); `hearing_usd(price, audio_s)`, `model_usd(price, prompt_tokens,
reply_tokens)`, `voice_usd(price, chars, audio_s)` (per-character, or text tokens ≈ chars/4 plus 25 audio tokens
a second); local entries cost 0.

- [ ] Tests: whisper is 0 and local; 60 s on a $0.016/min engine is $0.016; Gemini flash-lite 1M in + 1M out is
  $1.75; Chirp 1,000 chars is $0.03; Gemini-TTS 400 chars and 10 s is (100 × 0.5 + 250 × 10)/1e6; an unknown
  engine gives None; every entry has source, checked and official.
- [ ] Fail, implement, pass.

### Task 7: The benchmark, report and docs

**Files:** Modify `scripts/bench_matrix.py`, `docs/CALL_BENCH.md`, `CHANGELOG.md`, `docs/TESTING.md`; Test
`server/tests/test_calls_matrix.py` (caller-clip reuse by text)

- [ ] `--keypad`, `--keying 1.0,0.4`, `--still-after 1.5`, `--no-smart-turn`, `--prices`. Caller clips found by
  text among `--caller-audio` (keypad lines that match a round-1 line reuse its clip), else synthesized.
- [ ] Smart Turn on every clip; streaming ears in the hearing stage; `step_log` and keyed digits exported in the
  Model sheet; fillers, still lines and keypad prompts made once per voice (sheet "Lines").
- [ ] Pairs: round 1's first sound, and timeline first sound / first answer / dead air with the fixed wait and
  with Smart Turn; whole-call recordings from the Smart Turn timeline with fillers and tones; Cost sheet per
  engine and per pair (per call and per 1,000 calls); End of turn sheet.
- [ ] Report sections: pairs, end of turn, fillers and keypad, cost with sources. Docs updated.
- [ ] Full suite green.

### Task 8: Run round 2 and report

- [ ] Smoke run on one call; then the full lineup into `bench-out/matrix-hotel-r2`.
- [ ] Compare with `bench-out/matrix-hotel`; report to the owner with recordings, timings, costs, open questions.
