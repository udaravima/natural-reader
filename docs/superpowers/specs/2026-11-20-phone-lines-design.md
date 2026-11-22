# Phone lines: call a profile

**Status:** design, nothing built. 2026-11-20. Phase 1 is planned in
[`docs/superpowers/plans/2026-11-20-phone-lines-phase1.md`](../plans/2026-11-20-phone-lines-phase1.md).
**Owner's request (their words):** "connecting this to a sip account. since we already have profiles. we can have sip
account for profile, so when dialed. will greet with a specified welcome message and profile will get a project or
project will get a profiles. we can have DTMF for selecting language later." Then: "go ahead. design specs and plan."

## 1. What this is and why

A person dials a number on a phone system. One of the app's assistant profiles picks up, says its own welcome
message, and holds a spoken conversation that answers from one project's documents. Later, the keypad picks the
language.

The app already has every part of this except a phone. It has the profiles (the assistant library), the projects
(shelves of indexed documents), the chat engine that answers from a project, Talk (a spoken conversation in the
browser) and server-side voices and recognizers. This feature puts a phone in front of them.

The first phone system is the owner's own Asterisk PBX (23.x), which already hands calls on one extension to an app
over AudioSocket. The PBX stays the owner's: this design asks it for nothing more than one dialplan entry per line.

Who it serves:
- **The admin** sets up lines and reads what was said on them.
- **The caller** is anonymous and hears the line.
- **The project's maintainers** see the line listed as a reader of their project.

## 2. Goals, non-goals, anti-goals

**Phase 1 goals**

- **G1.** An admin creates a line in the admin console and switches it on. A line has a name, the profile that answers, the project it knows, a model, a voice, a welcome message and limits.
- **G2.** The console shows the exact Asterisk lines to route an extension to that line, with the line's id and the backend's address filled in.
- **G3.** Calling the extension plays the welcome message as soon as the call connects; it is recorded in advance, so there's no voice wait. Then the line holds an English conversation:
  - it hears the caller and answers from the project through the existing chat engine;
  - the caller can interrupt with the same rules as Talk (pause on speech, decide on the whole clip, resume on noise or echo, stop on real speech, and remember only what was heard).
- **G4.** Calls are bounded: a cap on simultaneous calls, a longest call and a daily token budget per line. A busy or closed line says so in words, never with silence.
- **G5.** Every call keeps its transcript, listed under the line with how long each answer took. Admins can read and delete calls.
- **G6.** It works on the owner's PBX as it is today, over AudioSocket, with one dialplan entry per line.

**Not in phase 1, and what that means for people**

- **No keypad.** No language menu and no keyed digits. That's phase 2, and callers can only speak until then.
- **English only.** The Sinhala and Tamil voices are parked as not good enough ([reference_sinhala_tamil_tts]). A line has a `language` field from day one, fixed to `en` in the console.
- **No caller number.** AudioSocket carries only an id. Call logs therefore can't say who called, and limits can't be per caller.
- **Only Asterisk phone systems.** No way in for systems that aren't Asterisk (3CX, FreeSWITCH, a Fritz!Box, a SIP provider without a PBX). That's phase 3; until then they can't reach a line.
- **No audio kept.** Calls aren't recorded; only the transcript is kept.
- **No outbound calls, transfers to a person, or web search during a call.**

**Anti-goals: failures even if everything "works"**
- A line answering from documents that no one chose to make callable.
- Dead air. The app goes quiet for more than 2 s without a reason (Asterisk hangs up after 2 s of no audio from the app), or a caller hears nothing back when something fails.
- A call that keeps running, and spending tokens, after the caller has hung up.
- Anything copied from the owner's other projects. This is built fresh, as the call bench was.

## 3. Constraints and facts this rests on

Verified, with where each came from:

| Fact | Source |
|---|---|
| AudioSocket frame = kind (1 byte) \| length (2 bytes, big endian) \| payload. `0x01` call UUID (16 bytes, always first); `0x10` audio, 16-bit signed little-endian mono at 8 kHz, 20 ms = 320 bytes; `0x03` a keypad digit (1 ASCII byte), Asterisk → app only (sending one back makes Asterisk drop the call); `0x00` hang up; `0xFF` error | the Asterisk 23.1.0 AudioSocket source, and a test call on the owner's PBX |
| Asterisk hangs up if the app sends no audio for 2 s, so the app sends frames the whole call, silence included | same |
| `chan_audiosocket` answers as soon as its TCP connection opens | same |
| An analogue handset's keypresses reach an app only as tones in the audio, never as `0x03` | same |
| Speech from an analogue handset arrived at −28 to −36 dBFS RMS in the test call | same |
| The owner's PBX is LAN-only and has no room for another SIP module, so nothing in this design adds one there. Other apps already on it are left alone. | the owner's PBX notes (2026-11-17) |
| Turns check project access through `load_project_scope(conn, user_id, project_id)`, which is membership-based | `server/chat/orchestrator.py:740` |
| `run_turn` is a plain async generator. A server task can drive it without HTTP, and closing it runs `_finalize` (shielded save, claim released) as a disconnect does today. | `server/chat/orchestrator.py:239`, `:609` |
| `begin_turn` creates the chat if it's new, under `user_id`. `heard` cuts the previous reply to what was played. | `server/chat/store.py:179` |
| `transcribe_for` refuses a second transcription for the same user id while one runs (`AlreadyRunning`). Local Whisper runs on one slot. | `server/speech/service.py:420` |
| Kokoro voices one text at a time, under `inference_lock`. Google and Chirp return LINEAR16 WAV. | `server/endpoints.py:61`, `server/chat_voice.py:225` |
| A spoken turn gets `SPOKEN_RULE` (short, plain, no markdown) | `server/chat/prompt.py:321` |
| Silero VAD v6 ships with faster-whisper 1.2.1 (`assets/silero_vad_v6.onnx`): input `[n, 576]` (64 samples of context + 512 at 16 kHz), state `h`, `c` of `[1, 1, 128]`. onnxruntime 1.30 and scipy 1.18 are installed. | checked in `.venv`, 2026-11-20 |
| `users.email` is NOT NULL UNIQUE and `oidc_sub` is nullable. A first login links to an existing row with the same verified email. | `server/sql/0*`, `server/auth/users.py:175-201` |
| `chat_sessions.user_id` cascades on delete, so deleting a user deletes their chats | `server/sql/` |
| `WORKERS` defaults to 1 | `docker-compose.yml:134` |
| The CPU-only dev box takes about 0.85 s to hear a clip (Whisper small) and about 0.57 s to voice a sentence (Kokoro) | call bench, 2026-11-17/18 |

Rules from the project:
- Every address, port and limit is an env var or console setting with a safe default, and docs use example addresses ([feedback_oss_no_hardcoding]).
- Tests run against the scratch database ([feedback_test_db_env]).
- No commit or push without the owner's word.

## 4. Approaches considered

### 4.1 How a call reaches the app

| | How | Gives | Costs | Verdict |
|---|---|---|---|---|
| **A** | The phone system's dialplan hands the call to the backend over **AudioSocket** | No SIP code at all. Already installed and proven on the owner's PBX. Any Asterisk with AudioSocket. | 8 kHz only. No caller number. No authentication on the socket, so the address has to be locked down. Asterisk only. | **Phase 1** |
| B | A bundled Asterisk container registers one SIP account per line with the phone system or a provider | Literally "a SIP account per profile". Works with a bare SIP provider. | One more container. RTP port ranges through podman. A second Asterisk hop on the owner's LAN. Configuration has to be generated and reloaded. | Later, for deployers with no PBX |
| C | The backend registers as a SIP account itself (its own SIP + RTP) | Works with any PBX | We'd own SIP correctness: digest auth, retransmission timers, SDP, codecs, NAT, DTMF variants. SIP passwords to store. | Phase 3 adapter |
| D | Asterisk's `chan_websocket` (20.16 / 21.11 / 22.6 / 23.0 and later) | 16 kHz, TLS, choice of codec, the channel's variables (caller number) | Not yet known to be packaged for the owner's PBX. It can't say what has been played (we pace the audio anyway). | Phase 3 adapter, for better audio |

**A first:** it's the shortest path to a real call on the owner's phone. The call loop (§5.3) sits behind a small
adapter interface, so B, C and D plug in later without touching it.

### 4.2 Profile and project: who owns whom?

The owner asked whether a profile gets a project or a project gets profiles. **Neither. A line pairs them.**
- A profile answers chats across the whole app, and a project is a members-only shelf.
- If either owned the other, the link would leak into the web app. With a separate line:
  - the Librarian can answer two numbers for two projects;
  - one project can have an English line and later a Sinhala line;
  - nothing changes what profiles or projects mean in chat.

### 4.3 Who the line is, to the rest of the app

A caller has no identity, and the whole app assumes a signed-in person. The options:
- **(a)** Calls run as the admin who made the line. That bills the admin's budget and needs the admin's own access, and the transcripts land in the admin's chats.
- **(b)** A special "caller" principal threaded through `run_turn`, budgets, usage and access checks. Every one of those paths would have to learn about it.
- **(c) Chosen:** each line gets its own **service user** (`users.kind = 'line'`). It is added to the project as a `reader`. Its own daily token budget is the line's budget, and its chats are the line's calls.

**Why (c):**
- Access, budgets, usage and the chat engine work unchanged.
- The project's member list shows exactly who can hear its documents ("Phone line: Front desk", reader).
- Removing that member, or switching the line off, is the revoke.

**The trap it creates:** a sign-in links to an existing row by verified email. A line's row must never be linkable, so linking, the People list, notifications and the Keycloak sync all skip `kind = 'line'`.

## 5. Design

### 5.1 Words used below
- **Line:** a phone entry point, made of profile + project + model + voice + language + clips + limits + service user.
- **Call:** one connection on a line. It is a `phone_calls` row plus one chat (the transcript), owned by the line's service user.
- **Clips:** recorded lines in the line's voice, at 8 kHz, rendered when the line is saved:
  - `greeting` (the welcome message);
  - `busy`: "Sorry, this line is busy. Please call again later.";
  - `closed`: for a line that's off or out of budget;
  - `goodbye`: before hanging up at the call limit;
  - `still`: "Are you still there?";
  - `sorry`: when an answer fails;
  - `filler` ×2: played when a tool starts and nothing has been said yet;
  - `checking`: once, if a tool is still running after 4 s.

### 5.2 The path of a call

```
caller ─phone─▶ Asterisk (owner's PBX)
                  exten 810: Answer → AudioSocket(<line id>, <backend>:9092)
                       │ TCP, from an allowed address only
                       ▼
backend  phone.audiosocket (adapter)  ── frames in/out, digits, hang-up
           │
           ▼
         phone.call.CallSession  (one task per call)
           listen:  8 kHz → 16 kHz → Silero VAD (32 ms windows) → clip
           hear:    clip → transcribe_for(key = call) → verdict (noise / echo / speech)
           answer:  begin_turn + run_turn(spoken, phone, line's profile, project, model)
           speak:   text → sentences → voice → 8 kHz → paced 20 ms frames (silence when idle)
           log:     phone_calls row; the chat is the transcript
```

### 5.3 The call loop (`server/phone/call.py`)

One `CallSession` per connection. It owns the adapter and runs three cooperating tasks: the receiver, the pacer and
the conversation.

**Connect.**
- Read the first `0x01` frame and look the line up.
- Unknown line: hang up.
- Line off, out of budget, or at its call cap or the global cap: play `closed` or `busy`, then hang up.
- Otherwise: create the `phone_calls` row and the call's chat, write the greeting text into the chat as the assistant's first message (so the model knows it has greeted), and play `greeting`.

**The pacer.** It sends exactly one 320-byte frame every 20 ms on a monotonic clock: the next audio if any is queued,
silence otherwise. That covers the 2 s rule while the model thinks. For each sentence it counts the samples actually
sent, so "heard" uses the same rule as Talk (`spokenReply.js`): a sentence half played counts as heard. Pausing means
sending silence while keeping the queue.

**The receiver.**
- Audio frames are resampled 8 → 16 kHz (`resample_poly`, 2/1) and fed to a streaming Silero VAD (v6, from faster-whisper's assets): 512-sample windows with 64 of context, carrying `h`/`c`.
- A segmenter turns probabilities into "speech started" and "speech ended, here's the clip". It uses the knobs Talk's browser gets, read from the same server function, `server/speech/window.settings()`:
  - `vad_start` (0.5) starts speech, and `vad_stay` (0.35) keeps it going;
  - `min_speech_ms` (250) is the speech that counts, and `pre_roll_ms` (300) is kept from before the start;
  - `silence_ms` (700) ends the turn;
  - the clip is judged with `max_no_speech` (0.25) and `min_logprob` (−1.0).
- Digits (`0x03`) are logged and ignored in phase 1. Hang-up (`0x00`), an error (`0xFF`) or EOF ends the call.

**The conversation** ports Talk's rules (`conversation.js`, `checks.js`) to Python:
- **Speech started:** pause the reply. The turn keeps generating, and audio keeps being prepared behind the pause.
- **Speech ended:** transcribe the clip as a 16 kHz WAV with `transcribe_for` on the deployment's current speech engine (as Talk does), then decide with Talk's verdict:
  - the key is `phone:<call id>`, not the line's user, because `transcribe_for` uses the id only as a one-at-a-time guard and in a log line, and two calls on one line must not block each other;
  - noise when the recognizer's own scores say so;
  - echo when ≥ 60% of a clip of three or more words was just spoken by the line;
  - otherwise speech.
- **Noise or echo:** resume from where it paused.
- **Speech:**
  - stop the reply, close the turn's generator, and wait for its `_finalize`;
  - start a new turn with the text and `heard` = the previous reply's message and the characters actually played;
  - clips accepted while stopping are joined, as Talk does.

**A turn** is `store.begin_turn` (line user, call chat, line model, text, `heard`) followed by iterating `run_turn` with
a `TurnRequest(spoken=True, channel="phone", profile_id=line.profile, project_id=line.project, settings with
think off)`.

Its events:
- `text-delta` feeds the sentence splitter (Talk's rules; port the bench's `server/calls/sentences.py`).
- **`text-end` ends the pending sentence.** This deliberately fixes the Talk trap: words written before a tool are spoken at once instead of waiting for the answer.
- `tool-input-available` with nothing queued yet for this turn plays one `filler`. If no new text arrives within 4 s, it plays `checking` once.
- `error`, or a turn that ends with nothing said, plays `sorry`.
- `finish` ends the turn.

**Voice per sentence.** Use the line's engine:
- Kokoro: through `inference_lock`, 24 kHz → 8 kHz.
- Chirp or Google: ask for 8000 Hz LINEAR16 directly.

At most two sentences are prepared ahead (Talk's `AHEAD = 2`).

**Ends:**
- The caller hangs up, or the socket closes: cancel the turn and stop at once.
- `max_call_s` reached: play `goodbye`, then hang up.
- No accepted speech for `PHONE_IDLE_S` (default 30 s) after the line last finished speaking: play `still`. Another `PHONE_IDLE_S` with nothing: `goodbye` and hang up.
- The daily budget runs out mid-call (the turn's `budget_exhausted` error): play `closed` and hang up.
- Server shutdown: hang up all calls with `end_reason='shutdown'` before the pool closes.

**Timings per turn**, stored in `phone_calls.stats`:
- when the caller stopped speaking;
- when the transcript arrived;
- the first text;
- the first sentence;
- the first audio frame sent.

The figure to compare with Talk and the bench is "first sound after the caller stopped".

### 5.4 What the model is told

`SPOKEN_RULE` already asks for short, plain speech. A phone turn also gets `PHONE_RULE`:

> "This is a phone call: the caller only hears you. Don't cite sources the way the steps above show: say no page numbers, document numbers, file names or file extensions (such as .md or .pdf), links, ids or symbols, and put nothing in brackets. If it helps to say where an answer comes from, describe the document in a few plain words, such as "the opening hours notice". Look things up yourself; never ask the caller whether you should. If the documents don't have the answer, say so. If you didn't catch something, ask the caller to repeat it."

The steps themselves stop teaching citations on a phone turn (final fix F2: the walk heard "Kettle facts, MD, page one", because step 4's own example said "(Guide.md, page 7)" and a later rule can't reliably undo an example):
- step 4 reads "4. Answer in plain spoken sentences, without page numbers or document names in brackets. When the documents don't cover part of the question, say so plainly; you may add what you know in general, introduced as not from the documents.";
- after a profile, the opener is "Whatever your role, an answer from the documents follows the steps below.", without "and cites its pages";
- the pin sentence ("Cite text the user pinned by its page") comes only with pins, which a call's new chat never has.

The chat's and Talk's rules are unchanged, byte for byte (`test_chat_prompt.py` pins a digest over every combination).

Implementation:
- A new `channel` field on `TurnRequest` and `TurnInput` (`"chat"` by default, `"phone"` for lines).
- `system_rules(..., phone=True)` appends the rule, and passes `phone` to the steps.
- Web search is off for phone turns: `web_mode = "off"`, the same switch spoken turns already use for "ask".

### 5.5 Data (migration 033)

```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'person'
    CHECK (kind IN ('person', 'line'));

CREATE TABLE IF NOT EXISTS phone_lines (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),   -- also the AudioSocket call UUID
    name            TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60),
    enabled         BOOLEAN NOT NULL DEFAULT FALSE,
    profile_id      UUID REFERENCES assistant_profiles(id) ON DELETE SET NULL,  -- NULL: the default profile
    project_id      UUID REFERENCES projects(id) ON DELETE SET NULL,            -- NULL: can't be switched on
    model_id        TEXT NOT NULL,
    voice           JSONB NOT NULL,              -- {"engine": "kokoro"|"chirp"|"google", "voice": "...", "accent": "..."}
    language        TEXT NOT NULL DEFAULT 'en' CHECK (language = 'en'),   -- phase 2 widens this
    greeting        TEXT NOT NULL CHECK (char_length(greeting) BETWEEN 1 AND 400),
    max_call_s      INT NOT NULL DEFAULT 600 CHECK (max_call_s BETWEEN 60 AND 3600),
    max_calls       INT NOT NULL DEFAULT 2 CHECK (max_calls BETWEEN 1 AND 20),
    service_user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE RESTRICT,
    created_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS phone_line_clips (
    line_id     UUID NOT NULL REFERENCES phone_lines(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('greeting','busy','closed','goodbye','still','sorry','filler','checking')),
    ordinal     INT NOT NULL DEFAULT 0,
    text        TEXT NOT NULL,
    voice_key   TEXT NOT NULL,          -- engine:voice:accent the audio was made with; stale if the line's differs
    pcm         BYTEA NOT NULL,         -- 16-bit LE mono, 8 kHz
    rendered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (line_id, kind, ordinal)
);

CREATE TABLE IF NOT EXISTS phone_calls (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    line_id     UUID NOT NULL REFERENCES phone_lines(id) ON DELETE CASCADE,
    session_id  TEXT REFERENCES chat_sessions(id) ON DELETE SET NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at    TIMESTAMPTZ,
    end_reason  TEXT CHECK (end_reason IN ('caller','limit','idle','budget','busy','closed','error','shutdown')),
    turns       INT NOT NULL DEFAULT 0,
    stats       JSONB NOT NULL DEFAULT '[]'   -- one object per turn: the timings in 5.3
);
CREATE INDEX IF NOT EXISTS phone_calls_line_idx ON phone_calls (line_id, started_at DESC);
```

**Deleting.**
- Switching a line off is the normal way to stop it.
- Deleting a line deletes its calls and clips, then its service user, which cascades to the call transcripts and the project membership. The console says how many transcripts go with it.
- `ON DELETE RESTRICT` on `service_user_id` makes the order explicit: the line goes first, in one transaction.

**Budget.** It is the service user's `inference_daily_token_budget`. NULL means the deployment default, as for anyone.

### 5.6 The adapter interface (`server/phone/media.py`)

```python
class CallMedia(Protocol):
    sample_rate: int                       # 8000 for AudioSocket
    line_id: str                           # from the first frame
    async def events(self) -> AsyncIterator[Audio | Digit | HungUp]: ...
    async def send(self, pcm: bytes) -> None: ...   # one frame; the pacer calls it every 20 ms
    async def hang_up(self) -> None: ...
```

`server/phone/audiosocket.py` implements it:
- It listens on `PHONE_AUDIOSOCKET_LISTEN` (default empty, which means off).
- Before reading a byte, it drops any peer whose address isn't in `PHONE_AUDIOSOCKET_ALLOW`.
- It refuses to start on a non-loopback address with an empty allow list.
- It binds with `reuse_port`, so more than one worker shares the port.

Phase 3 adapters (`chan_websocket`, a SIP account, a browser test call) implement the same protocol.

### 5.7 Admin API (admin capability)

| Method and path | Does |
|---|---|
| `GET /v1/admin/phone/status` | Listener on or off, its address for the PBX, the allow list, active calls |
| `GET /v1/admin/phone/lines` · `POST` | List lines, or create one. Creating also makes the service user and the clips. |
| `GET /v1/admin/phone/lines/{id}` · `PATCH` · `DELETE` | Read, edit (clips re-render when the text or voice changes), delete |
| `GET /v1/admin/phone/lines/{id}/clips/greeting` | The greeting as WAV, for "Hear it" |
| `GET /v1/admin/phone/lines/{id}/calls` | Calls, newest first: time, length, why it ended, turns, slowest first sound |
| `GET /v1/admin/phone/calls/{id}` · `DELETE` | One call with its transcript (the chat's messages), or delete it |

Rules:
- **Choosing a project** requires the admin to be its maintainer or owner, because adding a member is a maintainer's act.
- **A line can't be switched on** without a project, a model the console allows, and a greeting clip.
- **Saving waits for the clips to render.** A voice failure fails the save with the engine's message.

### 5.8 Admin console: "Phone lines" section

```
Phone lines                                              [ New line ]
 Listening for your phone system at 192.0.2.10:9092 · 0 calls now
 ┌──────────────────────────────────────────────────────────────────┐
 │ Front desk        ● On     The Librarian · Product docs          │
 │ 3 calls today · last 14:02                              [ Edit ] │
 └──────────────────────────────────────────────────────────────────┘

Edit line
 Name             [Front desk                    ]
 Answers as       [The Librarian            ▾]
 Knows            [Product docs             ▾]
                  ⚠ Anyone who calls this line hears answers drawn from this project.
 Model            [gemini-3.1-flash-lite    ▾]
 Voice            [Chirp 3 HD · Aoede       ▾]   [ ▶ Hear the welcome ]
 Welcome message  [Hello, you've reached the Product docs line. I'm an automated assistant. How can I help?]
 Longest call     [10 min]   Calls at once [2]   Tokens a day [ default ]
 [x] Line is on

 Connect your phone system (Asterisk)
   exten => 810,1,Answer()
    same => n,AudioSocket(6f1c2a…,192.0.2.10:9092)
    same => n,Hangup()
   Pick any free extension. The id is this line's.            [ Copy ]

 Calls
   14:02   2 m 31 s   caller hung up   4 turns   slowest 2.9 s   [ Transcript ]
```

Placement: a section in `AdminConsole` beside "Assistant profiles" and "Projects". It reuses the existing profile
picker, project picker, model list and `ChatVoicePicker`.

### 5.9 Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PHONE_AUDIOSOCKET_LISTEN` | *(empty: off)* | `host:port` the backend listens on for AudioSocket, e.g. `0.0.0.0:9092` |
| `PHONE_AUDIOSOCKET_ALLOW` | *(empty)* | Comma-separated peer IPs allowed to connect. Required unless listening on loopback. |
| `PHONE_PBX_ADDRESS` | the listen address | `host:port` written into the console's snippet: what the PBX must dial, which may differ inside a container |
| `PHONE_MAX_CALLS` | `2` | Calls at once across all lines (each line also has its own cap) |
| `PHONE_IDLE_S` | `30` | Seconds of caller silence before "Are you still there?", and again before hanging up |
| `PHONE_CHECKING_S` | `4` | Seconds a tool may run before the one "still checking" line |

Silence, minimum speech, pre-roll, the VAD thresholds and the noise limits are not repeated here. They come from
`server/speech/window.settings()` (`SPEECH_WINDOW_*`), so a line hears the way Talk does, and one change tunes both.

### 5.10 Security and privacy

- **The socket has no authentication.** Defences:
  - an allow list checked before reading;
  - off by default;
  - refusal to listen publicly without an allow list.

  The line id in the first frame is a routing key, not a secret. Anyone who can reach the port can already call the number.
- **Publishing a project is explicit:**
  - only a project maintainer or owner can attach it;
  - the console warns in words;
  - the project's member list shows the line;
  - removing that member revokes it on the next call.
- **Bounded spend:** per-line daily tokens, per-line and global call caps, and a longest call.
- **Kept:** transcripts only (the call's chat), as long as the line exists, and deletable per call. No audio, no caller number.
- **Logs** hold ids, durations and end reasons, never transcripts or audio.
- **Disclosure:** the default welcome message says the caller is talking to an automated assistant. Admins can change it.

### 5.11 Failure handling (a caller never hears silence for nothing)

| What happens | The caller hears | Recorded as |
|---|---|---|
| A model or provider error in a turn | `sorry`, then the call goes on | turn error in stats |
| The recognizer fails on a clip | `sorry` ("Sorry, I didn't catch that…") | turn error |
| A voice fails on a sentence | that sentence is skipped (as in Talk); if a whole turn says nothing, `sorry` | turn error |
| Daily budget gone | `closed`, hang up | `budget` |
| Database down at connect | the pre-loaded `busy` if clips are cached, else an immediate hang-up | logged only |
| The backend stops | hang up | `shutdown` |

Clips are cached in memory per line, keyed by `voice_key` and `rendered_at`, so a call never waits on the database
for its greeting.

## 6. Phases

1. **Phase 1 (this plan):** lines, service users, AudioSocket, the call loop in English, clips, the console section with snippet and calls, limits, tests, and a walk on the owner's PBX.
2. **Phase 2, the keypad:**
   - `0x03` digits, plus an in-app tone detector (Goertzel), because an analogue handset sends tones only;
   - a per-line language menu, with prompts recorded like the clips;
   - a `collect_digits` tool for numbers (the bench has this design).

   Sinhala and Tamil only once their voices pass a listening test.
3. **Phase 3, more ways in:**
   - `chan_websocket` (16 kHz and the caller's number);
   - a SIP-account adapter (C, or B for a bare provider);
   - a browser "test call" from the console.
4. **Later:** work that outlives a call (a job that texts a summary). This ties to the jobs idea in [project_async_tool_lanes].

## 7. Testing and the walk

All backend tests run against the scratch database (`TEST_DATABASE_URL` / `TEST_ADMIN_DATABASE_URL` on :5434).

| Layer | What |
|---|---|
| Protocol | Frame encode and decode; split reads; unknown kinds; the allow list refusing a peer before any read |
| VAD and segmenter | Silero v6 on committed speech clips: speech starts and ends at the right frames; silence and tones stay silent |
| Verdict | The Python port gives the same answers as `checks.js`, through shared JSON cases |
| Splitter | The bench port plus the `text-end` rule, through shared cases with `sentences.js` |
| Pacer | 20 ms cadence under a fake clock; silence fill; pause and resume; heard characters |
| Call loop | Fake media, fake transcriber, fake voice and a scripted `run_turn`: greeting first, filler on tool, barge-in cuts `heard`, noise resumes, idle and limit endings, busy and closed lines |
| Store and API | Line create, edit and delete (service user, membership, clips, cascade); the maintainer rule; switch-on rules; calls list |
| Login safety | A sign-in whose verified email equals a line row's never links to it; People and notifications skip lines |
| End to end | `scripts/phone_fake_pbx.py` connects as Asterisk would, streams a recorded question, records what comes back, and the test asserts greeting → answer audio → call row with timings. It runs on a real local model, and is skipped when none is configured. |
| UI | Vitest for the section: list, form validation, warning, snippet with real values, calls and transcript |

**The walk**, which makes the feature done:
1. The owner adds the console's snippet to their PBX's dialplan.
2. They point `PHONE_AUDIOSOCKET_ALLOW` at the PBX.
3. They call the extension from an analogue handset and from a softphone.
4. They check: the greeting plays; a question about the project is answered correctly; an interruption stops the reply; the call shows in the console with its transcript and timings.
5. They record first-sound times, to compare against Talk (2.09 s median, 2026-11-03) and the bench.

Changing the PBX is the owner's act, not ours.

## 8. Calls I made (the owner said "go ahead")

1. AudioSocket first, behind an adapter interface. SIP accounts, `chan_websocket` and a browser test call come later.
2. A line pairs profile and project; neither owns the other.
3. A line is a service user (`kind = 'line'`) that reads its project as a member, with its own budget.
4. The welcome message and the other fixed lines are recorded at save time, in the line's voice, at 8 kHz.
5. The conversation runs on the server, drives `run_turn` directly, and ports Talk's interruption rules. It also fixes the pre-tool-words trap.
6. English only in phase 1. `language` exists but is fixed to `en`.
7. No caller number and no audio kept in phase 1 (AudioSocket can't carry the number anyway).
8. No web search on lines in phase 1.
9. Defaults: 2 calls at once, 10-minute calls, 30 s idle prompt, 4 s before "still checking".
10. Only a project's maintainer or owner can put that project on a line.
11. A line hears with the deployment's current speech engine and Talk's speech settings. There is no per-line recognizer in phase 1.
12. **Phase 1 lines are public doors:** anyone who reaches the extension is answered. A members-only door (a PIN keyed on the phone) needs the keypad, so it comes with phase 2. This is the one call the owner should confirm.

## 9. Left to the builder

- Resampler details, provided 8 ↔ 16 ↔ 24 kHz stays clean (`scipy.signal.resample_poly`).
- Whether the VAD runs in a worker thread (it takes about 1 ms per window on this CPU), and how the segmenter turns probabilities into decisions, as long as it uses Talk's knobs and the tests pass.
- The exact sentences for `busy`, `closed`, `goodbye`, `still`, `sorry`, `filler` and `checking`: short, polite and editable later.
- Paging of the calls list (newest 50 is enough to start).
