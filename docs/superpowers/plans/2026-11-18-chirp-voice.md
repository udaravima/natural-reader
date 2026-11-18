# Google Chirp 3 HD chat voice: implementation plan

> Executed natively in one session (owner: "go ahead"); no commits without the owner's word.

**Goal:** Chirp 3 HD as a third voice for the chat's replies and Talk.

**Spec:** [2026-11-18-chirp-voice-design.md](../specs/2026-11-18-chirp-voice-design.md)

## Global constraints

- Engine id `chirp`, label `Google Chirp 3 HD`; voices are `chat_voice.GOOGLE_VOICES`, sent as
  `<language>-Chirp3-HD-<voice>`, no `modelName`.
- Sinhala (`si-LK`) goes to `google_speak` (Gemini-TTS) when `GOOGLE_TTS_MODEL` is set, else `GoogleVoiceError(503)`.
- Tokens never in a log or an error message (as now). No request reaches Google from a test.
- Documents and audiobooks keep Kokoro.

## Review focus

1. A mixed English/Sinhala/Tamil sample: three requests, in order, audio joined, total length right.
2. Sinhala with no Gemini model: nothing is sent, the person's message says to ask the admin.
3. A Chirp refusal (403/429/400) reads like Gemini's, without Gemini-only advice (Vertex AI User).
4. The admin's stored `{"engine": "chirp"}` while Chirp can't run (no credentials): Kokoro applies.
5. Settings → Chat shows the Reply voice for Chirp as for Gemini-TTS.

## Tasks

### Task 1: chirp in `server/chat_voice.py`

Tests in `server/tests/test_chat_voice.py`:
- `test_chirp_can_be_chosen_once_google_is_set_up`: `needs("chirp", {})` is `"GOOGLE_APPLICATION_CREDENTIALS"`;
  empty with either that or `GOOGLE_TTS_MODEL`.
- `test_the_admins_chirp_choice_applies_while_it_can_run`: `current(_options({"engine": "chirp", "voice":
  "Charon"}), creds)` is `ChatVoice("chirp", "Charon", "admin")`; with `{}`, Kokoro. `CHAT_VOICE=chirp` from env.
- `test_a_sentence_goes_to_chirp_with_its_language_and_voice_name`: body voice `{"languageCode": "en-US", "name":
  "en-US-Chirp3-HD-Kore"}`, no `modelName`; Tamil gives `ta-IN-Chirp3-HD-Kore`; speed 1.25 is `speakingRate`.
- `test_a_sinhala_sentence_goes_to_gemini_tts_when_it_is_set_up`.
- `test_sinhala_without_gemini_tts_is_refused_before_sending`.
- `test_mixed_languages_are_spoken_in_turn_and_joined`.
- `test_language_runs_*` for `language_runs(text, env) -> list[tuple[str, str]]`.
- Chirp's refusals: 403 says Cloud Text-to-Speech, not Vertex AI User.

Implement `chirp_speak(text, voice, speed, env, *, transport=None, token=_default_token) -> (bytes, float)`,
`language_runs`, and a shared `_post` used by both engines.

### Task 2: routes

`server/routers/chat_voice.py`: `speak` dispatches `chirp` to `chat_voice.chirp_speak`; GET lists voices for
`chirp` too. `server/routers/admin.py`: `ChatVoiceIn.engine` admits `chirp`, checked like `google`.
Tests: route reads with the person's voice through Chirp and says `("chirp", "Google Cloud", s)`; admin can choose
Chirp and a voice; refused with the need named when it can't run.

### Task 3: Overview

`server/services/console_overview.py::_chat_voice`: Chirp's summary names the voice and the Sinhala route; `warn`
when Sinhala has no voice. Tests in `server/tests/test_console_overview.py`.

### Task 4: the console and Settings

`src/components/admin/ChatVoicePicker.jsx`: the voice list and help line for `chirp` too.
`src/components/settings/SettingsDialog.jsx::useChatVoice`: Chirp counts as Google's voice.
Tests in `ChatVoicePicker.test.jsx` and `SettingsDialog.test.jsx`.

### Task 5: docs

`.env.example` (`CHAT_VOICE`), `docs/DEPLOYMENT.md`, `docs/ARCHITECTURE.md`, `CHANGELOG.md` (Unreleased),
`server/services/chat_options.py` docstring.
