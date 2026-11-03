# Google speech for the chat and Talk

**Date:** 2026-11-03. **Status:** approved by the owner 2026-11-03 ("go ahead"); built on `feat/google-speech`. Builds on the speech window
(`2026-11-02-speech-window-design.md`) and v2.6.0.

## Why

The owner wants Google Cloud as an alternative to Whisper for hearing speech, and to Kokoro
for speaking replies. It is for testing, and for languages the local models do badly: the
owner judged none of the Sinhala or Tamil voices good enough (`reference_sinhala_tamil_tts`).
Gemini-TTS lists Tamil as generally available and Sinhala in Preview. The owner already has a
Google billing account and a recognizer.

This is a community project, so Google stays a choice each deployer makes. The app stays
standalone by default. Nothing reaches Google until an admin configures it and switches it on.

## What a person does

**A deployer** creates a Google service account, puts its key file on the server, and sets
two or three environment variables (below). Nothing changes until an admin switches
something on.

**An admin**, in **Admin → Chat options**:
- **Speech recognition**: Whisper on this server, the OpenAI-compatible server (when
  `SPEECH_URL` is set), or Google Cloud (when a recognizer is set). An engine that isn't
  configured is shown greyed out, with what it needs.
- **Voice for chat and Talk**: Kokoro on this server, or Google Gemini-TTS. With Google,
  the admin also picks one of its 30 voices.
- **Admin → Overview**, on its Voice input line and a new Chat voice line, says which
  engine is in use and where audio goes.

**A person** uses the microphone, Talk and **Read aloud** in the chat exactly as before.
With Google on, the words are heard and spoken by Google. Documents, audiobook export and
the browser extension still read with Kokoro and the person's own voice. While Talk listens
through Google, its panel says "Heard by Google Cloud", so nobody is surprised that their
voice leaves the server.

## Goals, non-goals, anti-goals

- **Goal:** Sinhala, Tamil and English, heard and spoken by Google in the chat and in Talk,
  switchable by an admin without a restart.
- **Goal:** a deployer configures their own Google project. Nothing in the app names the
  owner's project, recognizer or region.
- **Non-goal:** documents, audiobooks and the extension (the owner: "just chat and talk
  only"). Batch synthesis and `/v1/synthesize` are untouched.
- **Non-goal:** people choosing among several engines or voices. That comes later, with the
  admin-curated lists still owed from 2026-11-02. For now the admin picks one Google voice.
- **Non-goal:** a spending cap in the app. Google bills per minute heard and per
  character spoken; deployers set budgets and alerts in Google's console, and the docs say
  so.
- **Non-goal:** streaming recognition or streaming synthesis. One request per clip and one
  per sentence, as today. Streaming is a later speed lever.
- **Anti-goal:** Google on by default, or reached by any request while switched off.
- **Anti-goal:** a credential in the database, the admin screen, a log or an error message.
- **Anti-goal:** silently falling back from Google to Kokoro mid-reply. Two voices in one
  answer is worse than a clear "Google's voice didn't answer".

## Constraints

- Every Google value is an environment variable with a safe default (off). Docs use example
  values only.
- Credentials are Google's standard ones: a service-account key file named by
  `GOOGLE_APPLICATION_CREDENTIALS`, or whatever `google.auth.default()` finds. The key file is
  read only by the backend.
- Transcripts and reply text are never logged, as now.
- No migration. Both choices are new keys in the existing `chat_options` row.

## Design

### Hearing: a `google` speech engine

`server/speech/google_engine.py` is a third `Transcriber`, beside `local` and `openai`.

- **Configured** when `GOOGLE_SPEECH_RECOGNIZER` is set to a full name,
  `projects/<project>/locations/<location>/recognizers/<id>`. The location in that name picks
  the endpoint: `global` is `speech.googleapis.com`, and anything else is
  `<location>-speech.googleapis.com`.
- **One call per clip:** `POST https://<endpoint>/v2/<recognizer>:recognize` with the audio
  inline. The audio is always decoded first, by the decoder Whisper already uses, and sent as
  16 kHz mono LINEAR16 WAV, whatever the browser recorded. That avoids depending on Google's
  automatic decoding of WebM.
- **Language:** when the person (or Talk) names a language, the request overrides the
  recognizer's languages with that one (`config.languageCodes` and
  `configMask: "languageCodes"`). The app's two-letter code maps to the recognizer's own
  code with the same first part (`en` → `en-GB` on the owner's recognizer). With no
  recognizer match it falls back to `en-US`, `si-LK` or `ta-IN`. With **Auto**, the
  recognizer's own languages apply. The app never overrides any other recognizer feature
  (punctuation, word confidence and the rest). Those are the deployer's choice in Google's
  console.
- **Result:** the text of every result, joined. `language` is the result's `languageCode`.
  `no_speech` and `avg_logprob` stay `None`. No results means empty text.
- **Length:** Google's synchronous call takes at most 1 minute or 10 MB. With Google, the
  status reports `max_seconds: 55`, so the microphone stops at 55 s instead of 120.
- **Status:** `GET` the recognizer once. Its model and languages feed the Speech language
  list and the Overview line. A 403 or 404 there is a clear `EngineUnavailable`: "The
  service account can't use this recognizer", or "No such recognizer".
- **Errors:** 401 or 403 means the key is refused; 400 means Google refused the audio or
  language, and passes on its message, which carries no secrets; 429 means quota; a timeout
  means "Google didn't answer". All use the existing error types and toasts.
- **Talk with Google:** without Whisper's scores, a clip counts as speech when Google
  returned text. Empty results, which is what a cough or a knock usually gets, count as
  noise. **What it heard** shows "no scores" for these clips. This is unmeasured, and
  measuring it is part of the build (below).
- **Switching:** `chat_options.speech_engine` (`local` | `openai` | `google`). An unset key
  means `SPEECH_ENGINE` applies, as now. Switching rebuilds the engine the way switching the
  model does today.

### Speaking: a chat voice route

- **`POST /v1/chat/voice`**, gated by `chat`, with the same body and response as
  `/v1/synthesize`: `{text, voice, speed}`, returning the audio. The admin's
  `chat_options.chat_voice` (`kokoro` | `google`) decides who speaks. Kokoro uses the
  person's voice and speed, exactly as now.
- **Google:** `POST https://texttospeech.googleapis.com/v1/text:synthesize` with
  `voice.modelName` = `GOOGLE_TTS_MODEL`, `voice.name` = the admin's voice (default
  `GOOGLE_TTS_VOICE`), `audioConfig.audioEncoding: LINEAR16`, and the project in
  `x-goog-user-project`.
- **The language comes from the text's script:** mostly Sinhala letters (U+0D80–U+0DFF) is
  `si-LK`, mostly Tamil letters (U+0B80–U+0BFF) is `ta-IN`, and anything else is
  `GOOGLE_TTS_LANGUAGE` (default `en-US`). Speed is sent as `audioConfig.speakingRate`, if
  Gemini-TTS takes it; otherwise it's ignored, and the docs say so.
- **Limits:** Google takes at most 4,000 bytes of text per call. Talk sends single
  sentences, and the chat's read-aloud already splits replies, so one sentence over the
  limit is refused with a 413, as `/v1/synthesize` does.
- **Frontend:** Talk's synthesis and the chat's **Read aloud** call `/v1/chat/voice`.
  (Built differently, 2026-11-03: a chat message's **Audio** download stays on Kokoro. It
  builds one file from many sentences through the batch route, like the audiobook. A
  refused sentence shows the server's reason as a toast, at most once in 10 seconds, so a
  Google failure is never silent.) The reader, the audiobook and the extension keep
  `/v1/synthesize`. `GET /v1/chat/voice` answers `{engine, voice, where}`, so
  **Settings → Chat** can say "Replies and Talk are read by Google's voice Kore…; your voice
  in Voice & reading still reads documents."
- **Who may:** Read aloud in the chat needs `chat`, plus `reader` as it does now. Talk is
  unchanged: `talk`, `chat`, `reader`.
- **The person's browser voice still wins.** Someone who chose the browser's own speech
  instead of Kokoro (the `isLocalhost` switch in `useTtsEngine`) keeps it for **Read
  aloud**; the chat voice applies where Kokoro would have spoken.

### What Google's tables say about recognizers (checked 2026-11-03)

Sinhala (`si-LK`) is listed only for `chirp` and `chirp_2`, in `asia-southeast1`,
`europe-west4` and `us-central1`. It is not listed for `long`, `short` or `chirp_3`, in any
location. Tamil (`ta-IN`) and English are listed far more widely. So a recognizer for all
three is `chirp_2` in one of those three locations; `asia-southeast1` is the nearest to Sri
Lanka. The owner's `asr-si-ta` (`long`, `global`) should do English and Tamil but not
Sinhala; the live test confirms it. The deployment docs carry this table's gist and a link to
Google's own.

### Talk's language

Talk listens in the person's own **Speech language** setting when they have picked one, and
otherwise in `SPEECH_WINDOW_LANGUAGE`. Today it always uses the latter. This is what lets
someone test Sinhala Talk without a redeploy. It applies to Whisper too, where `small` or
`base` (not the `.en` models) can hear Sinhala.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | (unset) | Path to the service-account key file. Google's standard name |
| `GOOGLE_SPEECH_RECOGNIZER` | (unset) | Full recognizer name. Set: Google can be chosen for speech recognition |
| `GOOGLE_TTS_MODEL` | (unset) | For example `gemini-2.5-flash-tts`. Set: Google can be chosen for the chat voice |
| `GOOGLE_TTS_VOICE` | `Kore` | The voice until an admin picks one |
| `GOOGLE_TTS_LANGUAGE` | `en-US` | The language of text that isn't Sinhala or Tamil |
| `GOOGLE_TIMEOUT_S` | `30` | Seconds before "Google didn't answer" |

`google-auth` (Apache-2.0) joins `requirements.txt`, for the tokens. The calls themselves are
plain `httpx`, as in the OpenAI engine, so there is no gRPC and no large client library.

## Testing

- **Unit:** `respx` mocks both Google endpoints with the documented shapes: the recognize
  body (inline audio, the language override and its mask, nothing else overridden), the
  endpoint picked from the location, each error status mapped to its error, a
  language-to-code mapping per recognizer, script detection, and the 55 s cap. Also: no
  request at all while switched off, and the key never appearing in logs or errors.
- **Live, with the owner's key, on loopback:** the key file's contents are never printed.
  The walk reuses the simulated-microphone harness: English, Sinhala and Tamil clips heard by
  Google; Gemini-TTS replies in each; time to first sound against the 2.09 s baseline;
  coughs, claps and laughs against Google's empty-result rule. The Sinhala and Tamil test
  clips come from Gemini-TTS itself, so they test the plumbing, not accuracy on real voices.
- **Real voices, real rooms and real speakers** stay owed to the owner.
- **Risks the live test settles:** Google lists `chirp_2` as "Private GA", so a project may
  need access before it answers; and whether Gemini-TTS takes `speakingRate`.

## Left to the builder

The exact admin layout, how the Overview words it, the two-letter-to-BCP-47 table, retry
policy (none, beyond what the person does), and how the 30 voices are labelled.

## Calls made without the owner

- One Google voice for everyone, chosen by the admin. People choosing comes with the curated
  lists.
- No silent fallback to Kokoro or Whisper when Google fails.
- Talk follows the person's Speech language.
- "Heard by Google Cloud" shown in the Talk panel.
- `google-auth` plus `httpx`, not Google's client libraries.
