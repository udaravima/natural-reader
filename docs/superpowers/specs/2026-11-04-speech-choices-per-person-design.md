# Speech each person chooses: the language they speak, and the voice that answers — design

2026-11-04. Status: **approved by the owner at 15:11 ("Yes your recommendations are
good"), with one addition: the language switch must sit where a person looks for it,
one tap away, never only in Settings.** Being built on `feat/speech-choices`.

## Why

The owner, 2026-11-04:

> currently If I want to speak in sinhala I have to goto the admin panel and choose
> sinhala there. and If I want to change it to english or tamil same. so what we can do
> is we can have select three different models on run time while we are having this
> conversation. because google stt worked only on english for some reason not others.
> but its tts model is good in sinhala for now. since that is cost related that should be
> there on admin panel. but if we selected, use might want to select a proper voice and
> also hear small part before selecting it.

And earlier the same day: people choose speech models "from admins allowed list, same as
in models".

So, in one conversation, someone speaks Sinhala, then English, then Tamil, and each is
heard by a model that knows it. The voice that answers can be Google's, which speaks
Sinhala well; the admin decides whether Google is used, because Google bills per use,
and each person picks which of Google's voices answers them, after hearing it.

## What the code does today, and why it matters here

Read 2026-11-04 (`server/routers/speech.py`, `server/speech/service.py`,
`server/speech/local_whisper.py`, `src/hooks/useSpeechWindow.js`,
`server/routers/chat_voice.py`):

- **One speech model for the whole deployment.** The admin picks it under **Admin →
  Chat options → Speech model**, and each worker keeps exactly that one loaded. Switching
  language means the admin switching the model, as the owner describes.
- **Talk always tells the engine a language.** Each clip goes with the person's own
  **Speech language** if they chose one, else the deployment's Talk language,
  `SPEECH_WINDOW_LANGUAGE`, which is `en`. So with Speech language on Auto, **every Talk
  clip is sent as English**: Google transcribes Sinhala as if it were English, and a
  Sinhala-only Whisper model would be told English too. That is very likely why Google
  "worked only on English" for the owner (read from the code; not checked on their
  deployment, where the recognizer lists si-LK, ta-IN and en-GB). The microphone button
  sends nothing on Auto, so there Google hears its recognizer's first language,
  Sinhala. **Today's workaround:** set **Settings → Chat → Speech language** to Sinhala
  or Tamil, and Talk sends that.
- **A model swap can hear the wrong person's clip.** The transcription route replaces the
  worker's one engine when the wanted model differs, then reads it back again inside
  `transcribe_for`. With one model for everyone that's safe; with two people on two
  models, a request arriving in between would swap it, and a clip would be heard by the
  other person's model.
- **A model that isn't loaded refuses the recording**: `EngineNotReady`, a 503 "still
  getting ready". In Talk that turn is lost.
- **Google's voice is the admin's alone.** `POST /v1/chat/voice` ignores the person's
  voice for Google and reads with the admin's (one of 30 Gemini-TTS voices).

## Goals

1. **A person switches the language they speak during a conversation**, in Talk and at the
   message box's microphone, with one tap, and the next clip is heard in that language.
2. **Each language is heard by a model made for it**, from the models the admin offers,
   the way **Admin → Models** decides which chat models people see. With Google hearing
   speech, the switch sends Google the language.
3. **When the admin has Google read the replies, each person picks their own Google
   voice**, and hears a short sample before choosing.
4. **A person who changes nothing sees no change**, and neither does a deployment whose
   admin offers nothing.
5. **No recording is lost** to a model that is loading, one the admin withdrew, or two
   people speaking at once on two models.

## Non-goals

- Choosing the **engine** (Whisper here, an OpenAI-compatible server, Google), for
  hearing or for speaking. It stays the admin's, because Google costs money.
- Two models offered for the same language, with the person choosing between them (say,
  `base.en` for speed and `small.en` for accuracy). The first offered model for a
  language hears it. A later step if wanted.
- Detecting the language from the speech itself. Whisper's detection is unreliable on
  short clips, which is why Talk sends one.
- Kokoro voices: already each person's own, with a preview (**Settings → Voice &
  reading**).
- A GPU. The machine is CPU-only, and so are the measurements here.

## Anti-goals (a failure even if it works)

- One person's switch makes everyone's recordings slow: a multi-gigabyte download on
  first use, or models reloading on every request.
- The server runs out of memory, or a dropped model's memory never goes back.
- A recording lost, or heard by the wrong model or in the wrong language.
- A preview that bills Google again every time someone taps it.

## Decided by the owner

- An admin-curated list, as with chat models.
- Ordinary users can switch, during a conversation.
- Google stays the admin's choice (cost); the voice is the person's.

## Part 1: switching the language you speak

### What the person sees

- **A "Speaking" switch in the Talk panel and beside the microphone**: *Speaking:
  English ▾*. It lists the languages this deployment can hear, each with the model that
  hears it ("සිංහල · Whisper small, Sinhala"), plus **Auto**. One tap changes it, and it
  applies from the next clip, without stopping Talk. *My call* on where it sits.
- **It is the Speech language setting**, the same value as **Settings → Chat → Speech
  language**, stored per device as now (`usePersistedState('speechLanguage')`). One
  setting, reachable from three places. *My call*: two settings for "the language I
  speak" would disagree.
- Talk's **What it heard** shows the language and the model each clip was heard by, and
  **Copy numbers** includes them.

### Which languages are offered

- **Local Whisper**: the languages of the models the admin offers (below), each heard by
  the first offered model made for exactly it; then the default model's languages,
  heard by the default. The default `small` knows every language, so without anything
  offered the list is today's, every Whisper language, all heard by `small`.
- **Google**: the recognizer's languages, as Settings shows them today.
- **An OpenAI-compatible server**: every language, as today; its model is the admin's.

### What is sent

- The language, as now: the microphone and Talk both send **the language on the
  switch**. On Auto, Talk keeps sending `SPEECH_WINDOW_LANGUAGE`, and says so on the
  switch (*Speaking: Auto (English)*), so nothing is silently English any more.
- **No model id travels from the browser.** The server picks the model from the language,
  so a person can't make the server load anything the admin didn't offer.
- The response gains `model`, the model that heard it, and the log line gains `model=`.

### Admin

- Each model in the console's speech-model list gains an **Offer to people** tick; the
  order of the list is the order for picking the first model for a language. The
  admin's own choice is the default and always applies to the languages nothing else
  covers.
- Saved as `chat_options.offered_speech_models`, a list of ids beside
  `added_speech_models`, through `PUT /v1/admin/speech-models/offered` with `{ids}` (an
  unknown id, or one that can't run here, is refused with 422). Removing an admin-added
  model also takes it off the list. **Empty means nothing extra, as today.** *My call*,
  and the opposite of chat models, where empty means everything: offering every speech
  model would let anyone load `large-v3` (about six times `small`'s memory, an
  estimate).
- **Ticking a model starts its download** without loading it into memory:
  faster-whisper's `download_model(repo, revision=…)`, or `convert.ensure_converted` (its
  own process, as today) for one that is converted. Until it is on disk the console says
  **Downloading: people see it once it's here**, and people don't. Ticking again retries.
  Sizes: `base` ~145 MB, `small` ~480 MB, `medium` ~1.5 GB, `large-v3` ~3 GB.

### The owner's case, worked through

The admin ticks **Whisper small, Sinhala (run12)** and **Whisper small, Tamil**, with
`small` as the default. The switch offers Auto, සිංහල (run12), தமிழ் (Tamil), and every
other language through `small`; English is heard by `small`. In Talk the owner taps
සිංහල, speaks, then taps English: each clip goes with its language to its model. With
the limit below at 3, all three stay loaded on that worker after first use.

## Part 2: the server, several models per worker

- **A pool per worker** instead of one transcriber, keyed by model id, holding up to
  `SPEECH_MODELS_LOADED` models (**default 3**, 1 to 4). *My call*: the owner's case is
  three, and it is a ceiling: a model loads only when someone speaks its language.
- **A request resolves its model once and holds that engine to the end** of its
  transcription; a model in use is never dropped. This fixes the swap described above.
- **A request for a model that isn't loaded waits for its load** (about 1 s from disk,
  measured below), inside the same bounded wait as the slot (`LOCAL_WAIT_S`, 60 s),
  instead of answering 503. A model that fails to load: the default hears the clip, in
  the language asked for, and `model` says so.
- **The default keeps a place** when there are 2 or more; the others share the rest, the
  least recently used dropped first. With 1 place, models take turns in it.
- **Dropping a model releases its memory explicitly**: drop the reference, collect, then
  `malloc_trim(0)` (glibc; the container is `python:3.13-slim-trixie`). Without the trim
  about 250 MB of each dropped model stays with the process.
- **Status** (`GET /v1/speech/status`) gains `hears`: each offered language with the
  model that hears it, and whether it is on disk. It never loads anything but the
  default (it is called on every page load). A worker that has found a model on disk
  remembers it.
- The one-recording-at-a-time slot stays shared across models: they share the CPU. The
  retry clock for a failed load (`heal`, `RETRY_AFTER_S`) becomes per model.
- An admin's switch of the default no longer replaces anything: the new default loads on
  next use, and the old one becomes an ordinary pool member.

## Part 3: the voice that answers

- **When the admin's chat voice is Google**, **Settings → Chat** gains **Reply voice**:
  Google's 30 voices, the admin's as the default ("Default (Kore)"), with a **▶ Hear
  it** button beside it. When the chat voice is Kokoro, the person's Kokoro voice
  (**Voice & reading**) reads the replies, as now, and this row isn't shown.
- **All 30 voices are offered.** *My call*: they cost the same, so the admin's cost
  decision is Google on or off, not which voice.
- **The sample** is three short sentences, one each in English, Sinhala and Tamil, read
  as one clip (the route already speaks each sentence in its own language), at the
  person's reading speed. About 6 s, a few hundred characters of Google's quota.
  **Each sample is kept in the browser for the session**, so tapping again doesn't call
  Google again. *My call.*
- Stored per device (`usePersistedState('googleVoice')`, empty for Default), sent with
  each reply's speech as `google_voice` on `POST /v1/chat/voice`. The server reads with
  it if it is one of `GOOGLE_VOICES`, else with the admin's. Documents, audiobooks and
  the extension keep Kokoro, as now.
- **The reading speed** (now a slider, 0.5× to 2× in steps of 0.05) applies to Google's
  voice too, as now.

## What it costs

Measured 2026-11-04 on this machine: faster-whisper 1.2.1, int8, 4 threads, a 2 s clip,
resident memory of one process (scratch `pool/release.py`, `pool/both.py`).

| | Resident memory |
|---|---|
| The process before any model | 70 MB |
| `small` loaded / after its first clip | 584 MB / 756 MB |
| `small` and `base` both held, after a clip each | 886 MB |
| … then `base` dropped, collected and trimmed | 482 MB |
| A dropped `small`, collected but **not** trimmed | 411–463 MB |
| The same, then `malloc_trim(0)` | 177–215 MB |

- **Per worker**, so a deployment multiplies by `WORKERS` (default 1). The owner's three
  `small`-sized models would take about 1.8–2 GB per worker (an estimate: 515 MB of
  weights each, plus working memory; `small` with `base` measured 886 MB). `medium` is
  about three times `small` and `large-v3` about six, by parameter count (estimates).
- **Switching to a language whose model isn't loaded**: its load, on that clip. From
  disk, `small` about 1 s and `base` about 0.8 s including the 2 s clip, 4 threads, idle
  machine; `small` took 3.3 s with two threads on a busy one. A converted fine-tune is
  converted once, the first time, as today.
- **A slow model slows everyone on that worker**: recordings take turns in one slot.
- Six alternating loads drifted the trimmed floor from 177 to 245 MB. Too few to call a
  leak; a long run should watch it.

### Talk's speech-or-noise check across models

Talk sends a clip only if Whisper's scores say speech (`no_speech` below 0.25,
`avg_logprob` at least −1.0; `src/lib/speech/checks.js`), limits measured on `small`.
The same clips through three English-capable models with the app's decoding (scratch
`pool/verdicts.py`):

| | `small` | `base` | `base.en` |
|---|---|---|---|
| The person's 7 questions, 3 of them 6 dB quieter | 7 speech | 7 speech | 7 speech |
| Highest `no_speech` among them | 0.03 | 0.03 | 0.01 |
| 6 four-second pieces of a colleague's voice, ~13 dB below | 4 speech | 3 speech | 5 speech |
| Room noise | noise | noise | noise |

So the limits hold for these. **The Sinhala and Tamil fine-tunes are untried in Talk**:
the build must run the same check on the Sinhala and Tamil recordings the v2.5.0 model
measurements used, before calling Talk in those languages done.

## Left to the builder

- The look of the switch, and of **Downloading** in the console (today's
  `SpeechModelPicker` is a single select plus the add form).
- Whether the pool lives in `service.py` or its own module.
- The three sample sentences.

## Testing

- The admin offers two models; the switch lists their languages with those models, plus
  the default's, only once each is on disk. Nothing offered: today's list.
- **Talk with the switch on Sinhala sends `si`; on Auto it sends the Talk language and
  the switch says which.** The microphone sends the switch's language.
- The language picks the model: first offered model made for it, else the default; the
  response and the log name it.
- **Two people on two models, their requests interleaved so that one resolves its model
  while the other is mid-transcription: each clip is heard by its own model.** (Today's
  swap would fail this.)
- **A clip for a model that isn't loaded waits for the load and is transcribed, with no
  503.** A model whose load fails: the default hears it.
- With a limit of 2 and three models used in turn, the default stays, the least recently
  used other is dropped, `malloc_trim` is called, and a recording already running
  finishes on its model.
- Status lists `hears` and loads nothing but the default.
- Reply voice: shown only with Google; the sample plays, a second tap doesn't call Google;
  the chosen voice reaches `POST /v1/chat/voice`; a voice that isn't Google's is read
  with the admin's.
- The docs, as part of the work: DEPLOYMENT.md (the memory table, `WORKERS` × models,
  `SPEECH_MODELS_LOADED`, the Talk-sends-English trap), `.env.example`, USER_GUIDE.md,
  ARCHITECTURE.md, TESTING.md, CHANGELOG.md.
- Walk it in the app: the admin ticks the Sinhala and Tamil models and sees them
  download; a non-admin person with the `talk` role (a throwaway Keycloak) switches
  between Sinhala, English and Tamil in one Talk conversation; the log shows each clip's
  model and language; with Google's voice on, they hear two samples and pick one, and
  the reply uses it. Measure the worker's memory before and after.
