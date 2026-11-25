# Phone lines, phase 1: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dial an extension on an Asterisk phone system and have an assistant profile answer with its welcome message,
then hold an English spoken conversation about one project's documents.

**Architecture:**
- Asterisk hands each call to the backend over AudioSocket (TCP, 8 kHz).
- A server-side `CallSession` runs Talk's conversation rules in Python: Silero VAD, then the deployment's recognizer, then `run_turn` (driven directly, with no HTTP), then the sentence splitter, then the voice, then paced 20 ms frames.
- A **line** pairs a profile with a project, and acts through its own service user (`users.kind = 'line'`), which is a reader member of that project.

**Tech Stack:**
- Python 3.13, FastAPI, psycopg 3, asyncio, onnxruntime (Silero v6 from faster-whisper's assets), scipy (`resample_poly`), soundfile;
- React and Vitest;
- PostgreSQL migration 033.

**Spec:** [`docs/superpowers/specs/2026-11-20-phone-lines-design.md`](../specs/2026-11-20-phone-lines-design.md).
Read it first: its §3 table holds the protocol facts these tasks rely on.

## Global Constraints

- **Workspace.** Work in a git worktree on branch `feat/phone-lines` off `development` (superpowers:using-git-worktrees). Another session may be using the main working tree, so never switch its branch.
- **Never commit or push without the owner's explicit word for that commit.** Every task's last step asks first.
- **Backend tests run on the scratch database only.**
  - Export `TEST_DATABASE_URL` and `TEST_ADMIN_DATABASE_URL` for the scratch Postgres on `:5434`; the URLs are in `.superpowers/sdd/2026-10-25-admin-console-v2-phase1/constraints.md`, line 16.
  - If container `nr-c21-testpg` isn't running, start it.
  - A bare `pytest` hits the LIVE database on :5433.
- **Commands:**
  - backend: `.venv/bin/python -m pytest server/tests/<file> -q`
  - frontend: `npx vitest run <file>`
  - all gates: full pytest, `npx vitest run`, `npx eslint .`, `npm run build`
- **Configuration.** Every address, port and limit is an env var or a console setting with a safe default. Docs use example addresses (`192.0.2.10`, `example.com`), never the owner's.
- **Build fresh.** Nothing from the owner's other projects.
- **The PBX is the owner's.** No task touches it; Task 14 hands the owner a snippet.
- **AudioSocket facts** (spec §3):
  - frame = kind (1 byte) | length (2 bytes, big endian) | payload;
  - `0x01` UUID (16 bytes) comes first; `0x10` audio is s16le mono 8 kHz, 320 bytes per 20 ms; `0x03` is a digit (in only, never send one back); `0x00` hang up; `0xFF` error;
  - Asterisk drops a call after 2 s with no audio from the app.
- **Speech settings** come from `server/speech/window.settings()`: `vad_start` 0.5, `vad_stay` 0.35, `min_speech_ms` 250, `pre_roll_ms` 300, `silence_ms` 700, `first_clause_chars` 30, `max_no_speech` 0.25, `min_logprob` −1.0.
- **Defaults:**
  - `max_call_s` 600 (60–3600) and `max_calls` 2 (1–20) per line;
  - `PHONE_MAX_CALLS` 2, `PHONE_IDLE_S` 30, `PHONE_CHECKING_S` 4;
  - the listener is off unless `PHONE_AUDIOSOCKET_LISTEN` is set.
- **Logs** never contain transcripts, audio or document text; only ids, durations and end reasons.

## Review Focus

These are the failure modes most likely to bite a caller, each pinned by a test in the task named.

1. **A caller who says nothing, or line hiss only.** The app keeps sending frames (silence counts) and asks "Are you still there?" after `PHONE_IDLE_S`; a second idle period gets `goodbye` and a hang-up. Tasks 7 and 9.
2. **The caller hangs up while the model is still writing.** The turn's generator is closed (`_finalize` runs and the claim is released), no further model or voice calls happen, and the call ends `caller`. Tasks 9 and 10.
3. **Two calls on one line at the same time, with Talk in use.** Both are heard: the per-call transcription key prevents `AlreadyRunning`, Kokoro is shared under `inference_lock`, and the third call gets `busy`. Task 10.
4. **The project was taken away** (a maintainer removed the line's membership, or the project was deleted). The next call hears `closed`, not an exception or silence. Task 10.
5. **An interruption deep into a long answer.** Only the characters actually played are kept as `heard`, and the next turn's history shows the cut reply. Tasks 7 and 9.

---

### Task 1: Migration 033 and keeping line users out of people's paths

**Files:**
- Create: `server/sql/033_phone_lines.sql`
- Modify:
  - `server/auth/users.py`: `list_users`, `search_users`, `resolve_or_provision_user` (the by-email fetch and the link `UPDATE`), `delete_user`
  - `server/services/people.py`: `lookup`, and `assert_addable(conn, user_id, *, allow_line: bool = False)`
  - `server/services/project_members.py`: `put_member(..., line: bool = False)` passes `allow_line=line`
- Test: `server/tests/test_migration_033.py`, `server/tests/test_phone_line_users.py`

**Interfaces:**
- **Produces:**
  - the tables and columns of spec §5.5, verbatim, ending `INSERT INTO schema_migrations(version) VALUES (33) ON CONFLICT DO NOTHING;`
  - `users.kind` (`'person'` | `'line'`)
  - `put_member(conn, principal, project_id, user_id, role, *, line=False)`

- [ ] **Step 1: Write the failing tests**

```python
# test_migration_033.py
async def test_users_kind_defaults_to_person(db_conn): ...      # insert a user without kind -> 'person'
async def test_kind_rejects_other_values(db_conn): ...          # 'robot' -> CheckViolation
async def test_phone_lines_language_is_en_only(db_conn): ...    # language 'si' -> CheckViolation
async def test_clip_kinds_are_the_eight(db_conn): ...           # kind 'music' -> CheckViolation
async def test_service_user_delete_is_restricted(db_conn): ...  # DELETE the user of a line -> ForeignKeyViolation

# test_phone_line_users.py
async def test_login_never_links_a_line_row(db_conn):
    # a line row has email line-x@phone-line.invalid; a verified sign-in with that email
    # must not return the line's id (it fails, or makes a new person: never the line)
async def test_people_lists_skip_lines(db_conn): ...   # list_users, search_users, people.lookup omit kind='line'
async def test_assert_addable_refuses_lines(db_conn):  # 404 by default; passes with allow_line=True
async def test_delete_user_refuses_a_line(db_conn): ...  # raises; lines are deleted with their line
```

- [ ] **Step 2: Run them to make sure they fail.** `.venv/bin/python -m pytest server/tests/test_migration_033.py server/tests/test_phone_line_users.py -q`: expect failures (no column `kind`).
- [ ] **Step 3: Write the migration and the filters.** Every query listed under Files gains `kind = 'person'`, or a refusal for lines. The link `UPDATE` at `users.py:201` adds `AND kind = 'person'`.
- [ ] **Step 4: Run the two files, then the auth, people and project-member test files.** Expect them all to pass.
- [ ] **Step 5: Ask the owner before committing.** On their OK: `git add server/sql/033_phone_lines.sql server/auth/users.py server/services/people.py server/services/project_members.py server/tests/test_migration_033.py server/tests/test_phone_line_users.py && git commit -m "feat(phone): migration 033: lines, clips, calls; line users stay out of people's paths"`

### Task 2: The phone store (lines, the line's user, calls)

**Files:**
- Create: `server/phone/__init__.py`, `server/phone/store.py`
- Test: `server/tests/test_phone_store.py`

**Interfaces:**
- **Consumes:**
  - Task 1's tables and `put_member(..., line=True)`;
  - `authz.project_role(conn, user_id, project_id)`;
  - `chat.store.new_message_id(prefix)`.
- **Produces** (frozen dataclasses):
  - `LineVoice(engine: str, voice: str, accent: str | None)`;
  - `Line(id, name, enabled, profile_id, project_id, model_id, voice: LineVoice, language, greeting, max_call_s, max_calls, service_user_id, daily_tokens: int | None)`;
  - `Clip(kind: str, ordinal: int, text: str, voice_key: str, pcm: bytes, rendered_at: datetime)`.
- **Produces** (functions):
  - `async def create_line(conn, principal, *, name, profile_id, project_id, model_id, voice, greeting, max_call_s=600, max_calls=2, daily_tokens=None) -> Line`. It creates the service user (`kind='line'`, `email=f"line-{id}@phone-line.invalid"`, `display_name=f"Phone line: {name}"`, `status='active'`, `capabilities='{chat}'`) and its budget, then `put_member(conn, principal, project_id, user, "reader", line=True)`.
  - `async def update_line(conn, principal, line_id, **changes) -> Line`. A project change removes the old membership and puts the new one; a name change renames the service user.
  - `async def delete_line(conn, principal, line_id) -> int`. It returns how many call transcripts went: deletes the line, then the user, in one transaction.
  - `async def get_line(conn, line_id) -> Line | None` and `async def list_lines(conn) -> list[Line]`.
  - `async def save_clips(conn, line_id, clips: list[Clip])` and `async def load_clips(conn, line_id) -> list[Clip]`.
  - `async def open_call(conn, line, *, session_id: str, now_ms: int) -> str`. It inserts the call's chat (owner = the service user; title `Call YYYY-MM-DD HH:MM` in the deployment timezone; model = the line's), the greeting as an `assistant` message with `status='complete'`, and the `phone_calls` row. It returns the call id.
  - `async def close_call(conn, call_id, *, end_reason: str, turns: int, stats: list[dict])`.
  - `async def close_stale_calls(conn, older_than_s: int) -> int`.
  - `async def list_calls(conn, line_id, limit=50) -> list[dict]` and `async def get_call(conn, call_id) -> dict | None` (with the transcript messages).
  - `async def delete_call(conn, call_id) -> None`: deletes the call's chat, which cascades to its messages, then the `phone_calls` row.

- [ ] **Step 1: Write the failing tests**

```python
async def test_create_line_makes_a_reader_service_user(db_conn, admin_maintainer, project):
    line = await create_line(db_conn, admin_maintainer, name="Front desk", project_id=project, ...)
    assert await project_role(db_conn, line.service_user_id, project) == "reader"
    user = await get_user(db_conn, line.service_user_id)
    assert user["kind"] == "line" and user["display_name"] == "Phone line: Front desk"
async def test_only_a_maintainer_can_attach_a_project(db_conn, admin_not_member, project): ...  # same error put_member raises
async def test_changing_the_project_moves_the_membership(db_conn, ...): ...
async def test_delete_line_removes_user_chats_and_membership(db_conn, ...): ...  # returns the transcript count
async def test_open_call_writes_the_greeting_first(db_conn, line):
    # the chat's first message is role 'assistant', content == line.greeting, status 'complete'
async def test_close_stale_calls_ends_only_old_open_calls(db_conn, line): ...
```

- [ ] **Step 2: Run them to make sure they fail.** `.venv/bin/python -m pytest server/tests/test_phone_store.py -q`
- [ ] **Step 3: Implement `server/phone/store.py`** with the signatures above. Use plain SQL and one transaction per function.
- [ ] **Step 4: Run the tests.** Expect a pass.
- [ ] **Step 5: Ask the owner before committing.** On their OK, commit `server/phone/__init__.py server/phone/store.py server/tests/test_phone_store.py` with message `feat(phone): line store with a reader service user, calls and clips`.

### Task 3: Phone turns: the channel, `PHONE_RULE`, no web

**Files:**
- Modify:
  - `server/chat/orchestrator.py`: `TurnRequest.channel: str = "chat"`, and `web_mode = "off"` when `req.channel == "phone"`, next to the spoken "ask"→"off" switch at `:305`
  - `server/chat/context.py`: `TurnInput.phone: bool = False`, passed to `system_rules`
  - `server/chat/prompt.py`: `PHONE_RULE`, and `system_rules(..., phone: bool = False)`
- Test: `server/tests/test_chat_prompt.py` and `server/tests/test_chat_orchestrator.py` (add cases)

**Interfaces:**
- **Produces:** `TurnRequest(..., channel="phone")`. `PHONE_RULE` is exactly the quote in spec §5.4, and it is appended after `SPOKEN_RULE`.

- [ ] **Step 1: Write the failing tests**

```python
def test_phone_rule_only_on_phone_turns():
    assert PHONE_RULE in system_rules(None, [], has_pins=False, spoken=True, phone=True)
    assert PHONE_RULE not in system_rules(None, [], has_pins=False, spoken=True)
async def test_phone_turn_offers_no_web_tools(...):  # web_search "auto" in options; channel="phone" -> no web_search/read_web_pages in the step's tools
```

- [ ] **Step 2: Run them to make sure they fail.**
- [ ] **Step 3: Implement the three changes.**
- [ ] **Step 4: Run** `test_chat_prompt.py`, `test_chat_orchestrator.py` and `test_chat_turns.py`. Expect them all to pass, with existing behaviour unchanged.
- [ ] **Step 5: Ask the owner before committing.** Message: `feat(chat): a phone channel for turns: PHONE_RULE, no web search`.

### Task 4: Talk's text rules in Python (the splitter and the verdict), with cases shared with the JS

**Files:**
- Create:
  - `server/phone/sentences.py`, ported from `git show feat/voice-matrix:server/calls/sentences.py` (our own bench code), plus `end_step()`
  - `server/phone/verdict.py`
  - `server/tests/fixtures/speech/sentences_cases.json` and `server/tests/fixtures/speech/verdict_cases.json`
  - `src/lib/speech/shared.cases.test.js`
- Test: `server/tests/test_phone_text.py`

**Interfaces:**
- **Produces:**
  - `class SentenceSplitter(first_clause_chars: int)` with:
    - `feed(delta: str) -> list[Piece]`;
    - `end_step() -> list[Piece]`: cuts a pending piece that ends in `.`, `!` or `?`, and keeps anything else waiting;
    - `flush() -> list[Piece]`.

    `Piece(text: str, end: int)`, where `end` is the offset in the raw reply, as in `sentences.js`.
  - `heard_speech(text: str, no_speech: float | None, avg_logprob: float | None, *, max_no_speech: float, min_logprob: float) -> bool`
  - `is_echo(text: str, said: list[str]) -> bool`, using `MIN_ECHO_WORDS = 3` and `ECHO_SHARE = 0.6`
  - `verdict(t: Transcript, said: list[str], settings: dict) -> Literal["noise", "echo", "speech"]`

- [ ] **Step 1: Write the shared cases and the failing tests.**
  - Each JSON case is `{"input": ..., "expect": ...}`.
  - The JS test runs `createSentenceSplitter`, `heardSpeech` and `isEcho` over the same files, so the two languages can't drift.
  - The cases are plain text, because `markdownToSpeech` is richer than the port.

```python
def test_cases_match_talk(): ...      # every case in both JSON files, through the Python port
def test_end_step_speaks_words_before_a_tool():
    s = SentenceSplitter(30); assert s.feed("Let me check that.") == []
    assert [p.text for p in s.end_step()] == ["Let me check that."]
def test_end_step_keeps_an_unfinished_clause(): ...   # "Looking at the" -> [] after end_step
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest server/tests/test_phone_text.py -q` and `npx vitest run src/lib/speech/shared.cases.test.js`. Expect the Python file to fail and the JS file to pass.
- [ ] **Step 3: Implement the two modules.**
- [ ] **Step 4: Run both test files again.** Expect them to pass.
- [ ] **Step 5: Ask the owner before committing.** Message: `feat(phone): Talk's sentence and noise/echo rules in Python, with cases shared with the JS`.

### Task 5: Hearing a call (streaming VAD and segmenter)

**Files:**
- Create:
  - `server/phone/vad.py`
  - `server/phone/segmenter.py`
  - `scripts/make_phone_fixtures.py`, a one-off: the app's Kokoro speaks three short questions, saved as 16 kHz WAV under 100 KB each
  - `server/tests/fixtures/phone/question1.wav`, `question2.wav`, `question3.wav`, and `silence_2s.wav`
- Test: `server/tests/test_phone_vad.py`, `server/tests/test_phone_segmenter.py`

**Interfaces:**
- **Produces:**
  - `class SileroStream`. Its model path is `faster_whisper.utils.get_assets_path()/silero_vad_v6.onnx`.
    - `push(samples_16k: np.ndarray) -> list[float]` returns one probability per 512 samples. Each window is fed as `[1, 576]`: the previous window's last 64 samples (zeros at first) followed by the 512. `h` and `c` are carried.
    - `reset()`
  - `class Segmenter(settings: dict, rate: int = 16000)`. `step(prob: float, window: np.ndarray) -> list[Event]`, where `Event` is one of:
    - `SpeechStarted()`: after `min_speech_ms` of probabilities ≥ `vad_stay`, once one has reached `vad_start`;
    - `Misfire()`: speech ended before `min_speech_ms`;
    - `SpeechEnded(clip: np.ndarray)`: after `silence_ms` below `vad_stay`; the clip holds `pre_roll_ms` from before the start.

- [ ] **Step 1: Make the fixtures.** Run `.venv/bin/python scripts/make_phone_fixtures.py` (this needs the Kokoro model), and listen to one file.
- [ ] **Step 2: Write the failing tests.**

```python
def test_speech_scores_high_and_silence_low():
    probs = SileroStream().push(load("question1.wav"))
    assert max(probs) > 0.5 and max(SileroStream().push(load("silence_2s.wav"))) < 0.35
def test_started_needs_min_speech():      # probs [0.9]*7 (224 ms) then 0 -> Misfire; [0.9]*8 (256 ms) -> SpeechStarted
def test_ended_after_silence_ms(): ...    # 22 windows below vad_stay (704 ms) end it; 21 don't
def test_hysteresis_keeps_speech_going(): ...  # 0.4 (>= vad_stay) after start keeps speech
def test_clip_includes_pre_roll(): ...    # clip length == speech + pre_roll (300 ms) windows
```

- [ ] **Step 3: Run them to make sure they fail.**
- [ ] **Step 4: Implement `vad.py` and `segmenter.py`.**
- [ ] **Step 5: Run the tests.** Expect a pass.
- [ ] **Step 6: Ask the owner before committing.** Message: `feat(phone): streaming Silero VAD and Talk's turn segmenter on the server`.

### Task 6: The line's voice and its clips

**Files:**
- Modify:
  - `server/chat_voice.py`: `google_speak` and `chirp_speak` gain `sample_rate_hz: int | None = None`, forwarded into `_audio_config` as `sampleRateHertz`
  - `server/endpoints.py`: `async def kokoro_pcm(text: str, voice: str, speed: float) -> tuple[np.ndarray, int]` takes `inference_lock` and runs `_speak` in the executor, as `synthesize` does
- Create: `server/phone/voice.py`, `server/phone/clips.py`
- Test: `server/tests/test_phone_voice.py`, plus a case in `server/tests/test_chat_voice.py`

**Interfaces:**
- **Consumes:** `LineVoice`, `Line` and `Clip` (Task 2).
- **Produces:**
  - `voice_key(v: LineVoice) -> str`, of the form `"engine:voice:accent"`
  - `async def render(text: str, v: LineVoice) -> bytes`, returning s16le 8 kHz:
    - Kokoro goes 24 kHz → 8 kHz with `resample_poly(x, 1, 3)`;
    - Google and Chirp ask for `sample_rate_hz=8000`;
    - `server.endpoints` is imported inside the function, because importing it loads Kokoro.
  - `DEFAULT_CLIP_TEXTS: dict[str, list[str]]`: `busy`, `closed`, `goodbye`, `still`, `sorry`, `filler` (two texts) and `checking`; `greeting` comes from the line
  - `async def render_clips(line: Line) -> list[Clip]`
  - `class ClipCache` with `async def get(conn, line) -> dict[str, list[bytes]]`, reloaded when `voice_key` or `rendered_at` changes

- [ ] **Step 1: Write the failing tests**

```python
async def test_google_asks_for_8000_hz(): ...   # MockTransport sees audioConfig.sampleRateHertz == 8000
async def test_render_kokoro_resamples_to_8k(monkeypatch):   # fake kokoro_pcm: 24000 samples @24k -> 16000 bytes
async def test_render_clips_has_every_kind(): ...  # greeting text == line.greeting; filler ordinals 0 and 1
async def test_cache_reloads_when_the_voice_changes(): ...
```

- [ ] **Step 2: Run them to make sure they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `test_phone_voice.py` and `test_chat_voice.py`. Expect a pass, and the Talk and chat voice tests unchanged.
- [ ] **Step 5: Ask the owner before committing.** Message: `feat(phone): line voices at 8 kHz and pre-rendered clips`.

### Task 7: The pacer (frames on time, pause, and what was heard)

**Files:**
- Create: `server/phone/pacer.py`
- Test: `server/tests/test_phone_pacer.py`

**Interfaces:**
- **Produces:** `class Pacer(send: Callable[[bytes], Awaitable[None]], clock: Callable[[], float], sleep: Callable[[float], Awaitable[None]])`. `FRAME = 320` (20 ms).
  - `async run()`: one frame every 20 ms, made up from the queue or from silence.
  - `play(pcm: bytes, *, end: int | None = None)`: queues audio. `end` is the reply offset of a sentence; clips pass `None`.
  - `pause()` and `resume()`: pausing sends silence and keeps the queue.
  - `stop_reply()`: drops the queued sentences and keeps nothing.
  - `heard() -> int`: the end of the last sentence finished, or of the current one once more than 50% of it has been sent (Talk's `HEARD_SHARE`).
  - `busy() -> bool`: there is queued or current audio.
  - `on_drained(cb)`: called once whenever the queue empties after audio (the idle timer starts from it).

- [ ] **Step 1: Write the failing tests** (fake clock, list-capturing `send`)

```python
async def test_sends_silence_when_idle(): ...       # 10 ticks -> 10 frames of b"\0"*320
async def test_never_gaps_over_20ms(): ...          # timestamps of sends are 20 ms apart, no drift over 500 frames
async def test_pause_keeps_the_queue(): ...         # pause mid-sentence -> silence; resume -> continues from the next frame
async def test_heard_counts_half_played(): ...      # sentence A (end 20) done, B (end 45) at 60% -> heard()==45; at 40% -> 20
async def test_stop_reply_drops_queue(): ...        # after stop_reply busy() is False and heard() unchanged
```

- [ ] **Step 2: Run them to make sure they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests.** Expect a pass.
- [ ] **Step 5: Ask the owner before committing.** Message: `feat(phone): a 20 ms pacer that never goes quiet and knows what was heard`.

### Task 8: AudioSocket adapter and phone config

**Files:**
- Create: `server/phone/media.py`, `server/phone/audiosocket.py`, `server/phone/config.py`
- Test: `server/tests/test_phone_audiosocket.py`, `server/tests/test_phone_config.py`

**Interfaces:**
- **Produces:**
  - `media.py`: `Audio(pcm: bytes)`, `Digit(key: str)`, `HungUp(reason: str)`, and `class CallMedia(Protocol)` exactly as in spec §5.6.
  - `encode(kind: int, payload: bytes) -> bytes` and `async def read_frame(reader) -> tuple[int, bytes]`.
  - `class AudioSocketCall(CallMedia)`: `sample_rate = 8000`; `line_id` comes from the `0x01` frame; `send()` writes `0x10` frames; `hang_up()` writes `0x00` and closes.
  - `async def serve(cfg: PhoneConfig, on_call: Callable[[CallMedia], Awaitable[None]]) -> asyncio.Server`. It uses `reuse_port=True`, and closes a peer whose IP isn't in `cfg.allow` before reading a byte.
  - `PhoneConfig(listen: str | None, allow: tuple[str, ...], pbx_address: str | None, max_calls: int = 2, idle_s: float = 30, checking_s: float = 4)`.
  - `load_phone_config(env) -> PhoneConfig`. A non-loopback `listen` with an empty `allow` logs a warning and gives `listen=None`. Bad numbers warn and fall back, as `load_chat_config` does.

- [ ] **Step 1: Write the failing tests** (a real local server on 127.0.0.1, port 0)

```python
def test_encode_round_trip(): ...                     # 0x10 + 320 bytes; 0x01 + 16-byte UUID
async def test_split_reads_reassemble(): ...           # a frame written in 3 chunks reads as one
async def test_disallowed_peer_is_dropped_unread(): ...  # allow=("192.0.2.1",) -> on_call never runs, socket closed
async def test_first_frame_must_be_uuid(): ...         # 0x10 first -> closed, on_call never runs
async def test_digit_and_hangup_events(): ...          # 0x03 b"5" -> Digit("5"); 0x00 -> HungUp("caller")
def test_public_listen_without_allow_is_off(): ...     # PHONE_AUDIOSOCKET_LISTEN=0.0.0.0:9092, no ALLOW -> listen None
```

- [ ] **Step 2: Run them to make sure they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests.** Expect a pass.
- [ ] **Step 5: Ask the owner before committing.** Message: `feat(phone): AudioSocket adapter, allow list and phone config`.

### Task 9: The call session (Talk's conversation, on the server)

**Files:**
- Create: `server/phone/call.py`
- Test: `server/tests/test_phone_call.py`

**Interfaces:**
- **Consumes:** Tasks 4–8, and `TurnRequest(channel="phone")` from Task 3.
- **Produces:**
  - `CallDeps` (dataclass), which tests replace with fakes:
    - `transcribe: Callable[[bytes, str], Awaitable[Transcript]]` takes `(wav_16k, key)`; the real one is `transcribe_for(key, wav, "audio/wav", "en")`;
    - `begin: Callable[..., Awaitable[TurnClaim]]` is `store.begin_turn`;
    - `run: Callable[[TurnRequest, TurnClaim], AsyncIterator[dict]]` is a lambda over `run_turn(req, claim, router=..., cfg=..., deployment_budget=...)`;
    - `render: Callable[[str], Awaitable[bytes]]`;
    - `clock`, `sleep`, `settings: dict` (`window.settings()`) and `cfg: PhoneConfig`.
  - `class CallSession(media: CallMedia, line: Line, clips: dict[str, list[bytes]], session_id: str, deps: CallDeps)` with:
    - `async run() -> CallOutcome`;
    - `CallOutcome(end_reason: str, turns: int, stats: list[dict])`;
    - each stats entry has `stopped_at`, `heard_at`, `first_text_at`, `first_sentence_at` and `first_audio_at`, in seconds since the call started.
  - Spec §5.3's rules, in order:
    1. play the greeting;
    2. pause on `SpeechStarted`;
    3. on `SpeechEnded`, transcribe, then apply the verdict: noise or echo resumes; speech stops the reply, `aclose()`s the turn and starts the next turn with `heard`;
    4. `text-end` calls `splitter.end_step()`;
    5. the first `tool-input-available` with nothing queued plays `filler[n % 2]`;
    6. no text for `checking_s` plays `checking` once;
    7. `error` or an empty turn plays `sorry`; `budget_exhausted` plays `closed` and ends with `budget`;
    8. idle plays `still`, and idle again plays `goodbye` and ends with `idle`;
    9. `max_call_s` plays `goodbye` and ends with `limit`;
    10. `HungUp` cancels everything and ends with `caller`.

- [ ] **Step 1: Write the failing tests.** Fake media takes scripted inbound events and records frames; turns are scripted event lists; the clock is fake.

```python
async def test_greeting_is_the_first_audio(): ...
async def test_question_gets_an_answer(): ...        # speech clip -> transcript "hours?" -> turn text "We open at nine." -> its pcm sent
async def test_words_before_a_tool_play_at_once():   # turn: text "Let me check." , text-end, tool-input-available (no more text for 3 s)
    # "Let me check." audio sent before the tool output arrives; no filler (something was queued)
async def test_filler_when_a_tool_starts_silently(): ...
async def test_noise_resumes_the_same_reply(): ...    # verdict noise mid-reply -> remaining frames continue, no new turn
async def test_interrupt_keeps_only_what_was_heard(): # 3 sentences queued, speech after 1.6 played -> next begin(heard.chars == end of sentence 1 or 2 per 50% rule)
async def test_caller_hangup_closes_the_turn():       # the scripted run's finally ran (aclose), outcome.end_reason == "caller"
async def test_idle_then_goodbye(): ...               # 30 s still, 30 s goodbye, end "idle"
async def test_call_limit_says_goodbye(): ...         # max_call_s 60 -> goodbye at 60 s, end "limit"
async def test_turn_error_says_sorry_and_continues(): ...
async def test_transcription_failure_says_sorry(): ...   # transcribe raises -> sorry clip, still listening
async def test_voice_failure_skips_the_sentence(): ...   # render raises for sentence 2 of 3 -> 1 and 3 sent, no sorry
async def test_budget_error_closes_the_call(): ...    # end "budget"
async def test_digits_are_ignored(): ...              # Digit("1") changes nothing
```

- [ ] **Step 2: Run them to make sure they fail.**
- [ ] **Step 3: Implement `CallSession`.**
  - **Tasks:** a receiver task (media → VAD → segmenter → event queue), the pacer task, and the conversation (a single consumer of that event queue). Every task is cancelled in a `finally`.
  - **Turn requests:** each one is `TurnRequest(user_id=line.service_user_id, session_id, model_id=line.model_id, text, attachments=(), settings=CallSettings(think="off"), doc_id=None, timezone=None, project_id=line.project_id, spoken=True, channel="phone", profile_id=line.profile_id)`.
  - **Claims:** each comes from `begin(session_id=..., user_id=line.service_user_id, model_id=line.model_id, text=..., attachments=[], new_session_pins=None, scope_project_id=line.project_id, heard=Heard(prev_message_id, chars) or None)`.
  - **Voice failures:** a sentence whose voice fails is skipped, as in Talk.
- [ ] **Step 4: Run the tests.** Expect a pass.
- [ ] **Step 5: Ask the owner before committing.** Message: `feat(phone): the call session: Talk's listen-hear-answer-interrupt loop on the server`.

### Task 10: The phone service (caps, lines and the app's start and stop)

**Files:**
- Create: `server/phone/service.py`
- Modify: `server/app.py`. In the lifespan:
  - `await phone_service.start()` after the DB is ready; it is a no-op when `listen` is `None`;
  - `await phone_service.stop()` before `chat_orchestrator.drain_background`.
- Test: `server/tests/test_phone_service.py`

**Interfaces:**
- **Consumes:** everything above.
- **Produces:**
  - `async def start() -> None`. It runs `store.close_stale_calls(conn, 3600 + 60)`, then `audiosocket.serve(cfg, handle)`.
  - `async def stop() -> None`. It stops listening, then ends every live call with `shutdown`.
  - `async def handle(media: CallMedia) -> None`:
    1. look up the line; an unknown line hangs up silently;
    2. check that the line is on, has a project, and its service user is still a reader of it; otherwise play `closed`;
    3. check the line's service user has budget left, with the same check `post_turn` makes (`_budget_state_if_over` in `server/routers/chat_turns.py`; move it to `server/services/inference_budget.py` if importing it from a router is awkward); otherwise play `closed`;
    4. check the per-line and global caps (in-process counters; with `WORKERS > 1` each worker counts its own, which `docs/PHONE_LINES.md` says); otherwise play `busy`;
    5. `open_call`, then `CallSession.run()`, then `close_call`.
  - `def status() -> dict`: `{listening: bool, pbx_address, allow: list, active: int}`

- [ ] **Step 1: Write the failing tests** (fake media and a fake `CallSession`, except where marked)

```python
async def test_unknown_line_hangs_up_silently(): ...
async def test_line_off_plays_closed(): ...
async def test_removed_membership_plays_closed(): ...       # Review Focus 4
async def test_third_call_hears_busy(): ...                 # max_calls 2 -> third gets busy; Review Focus 3
async def test_two_calls_transcribe_concurrently(): ...     # real CallSession, fake engines that sleep: both keys run, no AlreadyRunning
async def test_hangup_mid_turn_spends_nothing_more(): ...   # real CallSession; after HungUp no render()/run() calls; Review Focus 2
async def test_stop_ends_live_calls_as_shutdown(): ...
async def test_db_down_at_connect(): ...                    # pool raises: warm ClipCache -> busy clip then hang-up; cold -> hang-up; no exception escapes
```

- [ ] **Step 2: Run them to make sure they fail.**
- [ ] **Step 3: Implement, and wire the lifespan.**
- [ ] **Step 4: Run** `test_phone_service.py`, then the full backend suite on the scratch database. Expect everything to pass.
- [ ] **Step 5: Ask the owner before committing.** Message: `feat(phone): phone service: caps, closed lines, start and stop with the app`.

### Task 11: Admin API for lines and calls

**Files:**
- Create: `server/routers/phone_admin.py` (`APIRouter(prefix="/v1/admin/phone")`; every route `Depends(require_admin)`)
- Modify: `server/app.py`, to include the router
- Test: `server/tests/test_phone_admin_api.py`

**Interfaces:**
- **Produces:** the routes in spec §5.7, plus `GET /v1/admin/phone/voices` → `{engines: [{engine, voices: [...]}]}`. Kokoro's voices come from `kokoro.get_voices()`, Google's and Chirp's from `chat_voice.GOOGLE_VOICES`, and only engines `chat_voice.engines(env)` offers appear. Line JSON: `{id, name, enabled, profile: {id, name}, project: {id, name}, model, voice, language, greeting, maxCallS, maxCalls, dailyTokens, callsToday, lastCallAt, snippet}`. `snippet` is the three dialplan lines with the line's id and `status().pbx_address`; it is `null` when the listener is off.
- **Rules:**
  - The model must pass the same checks as `post_turn`: `llm.is_allowed`, `_console_allows` and `is_listed`.
  - The voice engine must be offered.
  - `enabled: true` needs a project and a greeting clip.
  - A save whose text or voice changed re-renders the clips first; a render failure returns 502 with the engine's message, and nothing is saved.
  - `DELETE` returns `{transcripts: n}`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_members_get_403(member_client): ...
async def test_create_line_renders_clips_and_shows_snippet(admin_client, fake_render, listening): ...
async def test_snippet_is_null_when_not_listening(admin_client): ...
async def test_unallowed_model_is_422(admin_client): ...
async def test_switch_on_needs_project_and_greeting(admin_client): ...
async def test_render_failure_saves_nothing(admin_client, failing_render): ...   # 502, line unchanged
async def test_calls_list_and_transcript(admin_client, finished_call): ...
async def test_delete_reports_transcripts(admin_client, line_with_two_calls): ...  # {"transcripts": 2}
async def test_delete_one_call_removes_its_transcript(admin_client, finished_call): ...
async def test_greeting_clip_is_a_wav(admin_client, line): ...   # audio/wav, 8000 Hz mono, frames == len(pcm)/2
```

- [ ] **Step 2: Run them to make sure they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests.** Expect a pass.
- [ ] **Step 5: Ask the owner before committing.** Message: `feat(phone): admin API for lines, voices, calls and transcripts`.

### Task 12: Admin console "Phone lines" section

**Files:**
- Create: `src/lib/phoneApi.js` (thin `apiFetch` wrappers, one per route), `src/components/admin/PhoneLinesSection.jsx`
- Modify: `src/components/admin/AdminConsole.jsx`: `{ id: 'phone', label: 'Phone lines' }` goes after `projects` in `SECTIONS`, and the section renders when `active === 'phone'`
- Test: `src/components/admin/PhoneLinesSection.test.jsx`

**Interfaces:**
- **Consumes:** Task 11's JSON.
- **Produces:** the screen in spec §5.8. It reuses the existing profile and project lists and `ChatVoicePicker`'s look.
- **Copy, verbatim:**
  - the warning: "Anyone who calls this line hears answers drawn from this project.";
  - the snippet hint: "Pick any free extension. The id is this line's.";
  - with the listener off: "The phone listener is off. Set PHONE_AUDIOSOCKET_LISTEN and PHONE_AUDIOSOCKET_ALLOW, then restart.";
  - a new line's welcome message, prefilled and editable: "Hello, you've reached {line name}. I'm an automated assistant. How can I help?"

- [ ] **Step 1: Write the failing tests**

```jsx
it('lists lines with profile, project and calls today', ...)
it('shows the project warning once a project is picked', ...)
it('shows the snippet with the line id and the PBX address', ...)
it('says how to turn the listener on when it is off', ...)
it('plays the welcome from the clip endpoint', ...)          // Audio src = object URL of GET .../clips/greeting
it('opens a transcript from the calls list', ...)
it('asks before delete and names the transcripts lost', ...)  // "Delete Front desk and its 2 call transcripts?"
```

- [ ] **Step 2: Run them to make sure they fail.** `npx vitest run src/components/admin/PhoneLinesSection.test.jsx`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** this test file, then `npx vitest run`, `npx eslint .` and `npm run build`. Expect them all green.
- [ ] **Step 5: Ask the owner before committing.** Message: `feat(phone): Phone lines in the admin console`.

### Task 13: Fake PBX, the end-to-end test and the docs

**Files:**
- Create:
  - `scripts/phone_fake_pbx.py`. It connects to the AudioSocket port as Asterisk would, sends the line's UUID, and streams the given WAVs (resampled to 8 kHz) in real time after the greeting ends. It records everything it receives to `phone-call-<time>.wav` and prints, per question, the first-sound time after the question ended.
  - `server/tests/test_phone_e2e.py`, which is skipped unless `PHONE_E2E_MODEL` is set.
  - `docs/PHONE_LINES.md`: what a line is, the Asterisk setup with the snippet, the env table, limits, privacy, troubleshooting (the 2 s hang-up and the allow list).
- Modify:
  - `docs/DEPLOYMENT.md`: env rows;
  - `docs/USER_GUIDE.md`: the admin "Phone lines" section;
  - `docs/TESTING.md`: the fake-PBX row;
  - `CHANGELOG.md`: an Unreleased entry.

- [ ] **Step 1: Write the e2e test.** It starts the service on `127.0.0.1:0` with a line whose project holds a fixture document, runs the fake PBX with `question1.wav`, and asserts:
  - the first audio received is the greeting;
  - answer audio follows the question;
  - the `phone_calls` row has `turns == 1` and `end_reason == "caller"`;
  - the transcript holds the question.
- [ ] **Step 2: Run it.** `PHONE_E2E_MODEL=ollama:gemma4:e4b .venv/bin/python -m pytest server/tests/test_phone_e2e.py -q`. Expect a pass on a warm local model.
- [ ] **Step 3: Write the docs.** `PHONE_LINES.md` uses example addresses only.
- [ ] **Step 4: Run all the gates.** Expect green.
- [ ] **Step 5: Ask the owner before committing.** Message: `feat(phone): fake PBX, end-to-end test and docs`.

### Task 14: The walk on the owner's phone (done = this passes)

No code. Each step names its control.

- [ ] **Step 1: Start the backend.** Run it with `PHONE_AUDIOSOCKET_LISTEN=0.0.0.0:9092`, and with `PHONE_AUDIOSOCKET_ALLOW` set to the PBX's LAN address (the owner supplies it).
- [ ] **Step 2: Create the line.** In Admin → Phone lines → New line: name, The Librarian, a project the owner maintains, a model, a voice and a welcome message. Click "Hear the welcome", switch the line on, and save.
- [ ] **Step 3: Hand over the snippet.** Give the owner the snippet. They add it to their PBX's dialplan under a free extension and reload the dialplan. We never touch the PBX.
- [ ] **Step 4: Call from an analogue handset and from a softphone.** Check four things:
  - the greeting plays at once;
  - a question about the project gets a correct spoken answer;
  - an interruption stops the reply and the next answer follows;
  - hanging up ends the call.
- [ ] **Step 5: Check the console.** The call is listed with its end reason, turns and timings, and the transcript opens.
- [ ] **Step 6: Report.** Give per-turn first-sound times (the call row's stats) against Talk's 2.09 s median and the bench. Note anything misheard: this is the first 8 kHz real-line data.
- [ ] **Step 7: Update the docs.** Add the walk's facts to `docs/TESTING.md`. Ask the owner before committing.
