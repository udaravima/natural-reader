# The speech window

**Date:** 2026-11-02. **Status:** approved by the owner on 2026-11-03, English first; being built on `feat/speech-window`.

## Why

The owner wants a full speech-to-speech flow built from separate parts, not one direct speech-to-speech model, and wants to try it in this app first. You talk, it answers aloud, and you can cut in the way you would with a person.

Today the app has the parts but not the conversation. The mic records until you press Stop and leaves text in the box for you to send. Replies can be read aloud, but nothing listens while they play.

## What a person does

1. A person with chat and read-aloud access opens **Talk** in the view switcher and presses **Start**. The browser asks for the microphone.
2. They speak, with no buttons. The window shows **Listening**, **Hearing you**, **Thinking** and **Speaking**, and the words of each turn.
3. While it is thinking or speaking, they speak again. The reply pauses at once. If it was real speech, the reply stops and what they said joins the conversation. If it was a cough, the reply carries on from the same word.
4. **Mute** stops listening. **End** stops everything and turns the microphone off.
5. The conversation is an ordinary chat: it appears in **Chat** afterwards, with the same model, document or project scope.

Separately, an admin chooses which speech models and voices are offered, and a user picks among them in **Settings**.

## Goals

- A spoken conversation made of VAD, ASR, LLM and TTS as separate parts, each replaceable.
- Interruption that responds to speech and ignores coughs, sneezes, knocks and other noise.
- After an interruption, the LLM knows what was said and only what the person actually heard.
- Admin-curated speech models and voices that users choose among, as with chat models.
- Numbers on screen, because this is an experiment: every clip shows what each check decided.

## Not now

- Language detection beyond what Whisper's Auto already does. A fine-tuned model is told its language, so routing by language is later.
- A smarter end-of-turn model such as Smart Turn v3. A silence timer treats every language alike; Smart Turn can replace it later.
- Streaming ASR or a WebSocket. Whisper works on whole clips, so one HTTP request per clip is enough.
- Automatic switching to a Sinhala or Tamil voice. The owner parked it after hearing the clips.
- Direct speech-to-speech models.

## Must never happen

- **Audio leaves the browser before speech starts.** "Always listening" happens on the person's device. The server only ever gets clips of detected speech.
- **A cough loses part of a reply.** A false alarm pauses and resumes; nothing is dropped.
- **A user's choice starts a download.** Only an admin's action can fetch or convert a model.
- **The existing mic and read-aloud change behaviour.** The window is added beside them.
- **A switched-off deployment pays for it.** With voice input off there is no window, and the 17 MB of browser files are never fetched.

## The flow

```
Listening → Hearing you → Transcribing → Thinking → Speaking → Listening
                 ↑__________________ speech during Thinking or Speaking ___|
```

The interruption has two checks, because neither works alone (see **What was measured**):

| Moment | What happens | Where |
|---|---|---|
| Speech lasts 250 ms | **Pause.** Playback pauses at once. The LLM and TTS keep working, so nothing is lost if it was noise. | Browser, `onSpeechRealStart` |
| Speech stopped short of 250 ms | Nothing changed, nothing was sent. | Browser, `onVADMisfire` |
| Speech ends (700 ms of silence) | The clip goes to Whisper. | Browser, `onSpeechEnd` |
| Whisper accepts it | **Stop.** Playback stops, the stream is aborted, and the text goes as the next turn with how much of the reply was heard. | Browser, from the server's scores |
| Whisper rejects it | **Resume** from the same word. If nothing was playing, nothing is sent and the window goes back to Listening. | Browser |

Whisper accepts a clip when it has text, `no_speech < 0.4` and `avg_logprob >= -1.0`. A clip with several segments counts as speech if any one segment passes both, so one quiet stretch can't reject a long sentence (my call: the measurements were almost all one segment). No text means noise. A remote engine gives no scores, so any text counts as speech and the window says so.

**Echo guard (my call).** On speakers the mic may hear the reply. A clip that began while a reply was playing is dropped when at least 60% of its words are in that reply. Afterwards the same words are the person's: a first version looked back 15 s, and threw away a repeated question.

## Settings

Every value is an environment variable with this default, and reaches the browser through `/v1/speech/status`.

| Variable | Default | Meaning |
|---|---|---|
| `SPEECH_WINDOW_VAD_START` | `0.5` | Silero score that starts speech (0 to 1) |
| `SPEECH_WINDOW_VAD_STAY` | `0.35` | Score that keeps it going |
| `SPEECH_WINDOW_MIN_SPEECH_MS` | `250` | Milliseconds of speech before the reply pauses |
| `SPEECH_WINDOW_PRE_ROLL_MS` | `300` | Milliseconds kept from before the start, so the first word isn't clipped |
| `SPEECH_WINDOW_SILENCE_MS` | `700` | Milliseconds of silence that end a turn. The library's default of 1400 adds 0.7 s to every reply |
| `SPEECH_WINDOW_MAX_NO_SPEECH` | `0.4` | Whisper's `no_speech` score above which a clip is noise |
| `SPEECH_WINDOW_MIN_LOGPROB` | `-1.0` | Whisper's `avg_logprob` below which a clip is noise |

## Changes by part

**Browser VAD.** `@ricky0123/vad-web` (ISC) with the Silero v5 model on `onnxruntime-web` (MIT). Imported lazily when the window starts. The model, worklet and wasm are served from this app, never a CDN (`baseAssetPath`, `onnxWASMBasePath`). One thread is enough, so cross-origin isolation isn't needed. `getUserMedia` asks for `echoCancellation: true`.

**Speech route.** `/v1/speech/transcriptions` is reused. `Transcript` and the response gain `no_speech` and `avg_logprob`, both null for a remote engine, and the form gains an optional `model`. A clip is WAV at 16 kHz, built in the browser. Clips go one at a time, because a second clip from one person is refused with 409 today: the browser queues them, and two clips waiting together become one message.

**Chat.** The window uses `useChatEngine`, whose `sendMessage` refuses while a reply streams. So the order is `stopStream`, then send. The server may answer `409 turn_in_progress` until the aborted turn has finished saving, so the browser retries for up to 3 s.

**What was heard.** A reply is saved with everything generated, and goes into the next turn's context, so the LLM would believe the person heard words that never played. It usually finishes writing long before it finishes playing, so most interruptions hit a reply already saved as `complete`, not an `aborted` one. `TurnIn` gains an optional `heard: {message_id, chars}`. Inside `begin_turn`, in the same transaction, the server cuts that message to `chars`, if it is an assistant message of this chat that isn't streaming, and marks it `finish_reason = 'interrupted'`. Sending it with the next turn avoids racing the save. Playback items record where their sentence ends in the reply's text. A sentence counts as heard once more than half of it has played, by `currentTime / duration` from the real audio element, never wall-clock time (my call). If nobody sends a next turn, the reply stays whole.

**Playback.** The window has its own queue and `<audio>` element (`lib/speech/spokenReply.js`), not the chat's, so read-aloud can't be affected. Pause keeps the position, and synthesis runs on behind it, two sentences ahead.

**The window.** A new `viewMode`, `talk`, in `ViewSwitcher`, shown only when voice input is on and the person has `chat` and `read_aloud`. It works at phone width. It carries **Mute**, **End**, the chat model, and a collapsed **What it heard** list: for each clip, how long the VAD held it, both scores, and the decision.

## Admin lists

**Speech models.** `chat_options` gains `speech_models_offered`, a list of catalogue ids. **Admin → Models** gets a list of checkboxes in the existing `SearchableList`. The current model is always offered.

- Empty means only the deployment's model, with no chooser. This differs from `chat_models`, where empty means all, because offering everything would let any user reach a multi-gigabyte model.
- Ticking a model starts its download or conversion, as choosing one does today, and the row shows its state. Unticking frees it at the next swap.
- The user's choice lives in **Settings → Chat → Speech model**, persisted like `selectedModel`, and is sent as `model`. A model not on the list is refused with 422 `model_not_offered`, and a saved choice that was removed falls back to the deployment's model with a note.
- A worker keeps up to `SPEECH_MODELS_LOADED` models (default `2`) and evicts the least recently used. Without this, two people on two models reload on every clip (about 0.8 s for `small`). Loading a third answers the existing 503 `not_ready`.
- Adding a model stays admin-only and unchanged: the add form and `SPEECH_MODELS_FILE`. With a remote engine there is no choice.

**Voices.** `chat_options` gains `voices_offered`, a list of Kokoro voice ids, where empty means all 50. **Admin → Models** gets a second checkbox list, grouped by language.

- A new `GET /v1/tts/voices`, behind `require_read_aloud` like the TTS routes, returns the offered ids. It stands alone because `/v1/speech/status` needs voice input on and the `chat` capability, and read-aloud needs neither.
- `/v1/synthesize` and `/v1/batch_synthesize` refuse a voice not offered with 422 `voice_not_offered`, which covers the extension too.
- **Settings** and **Admin → Defaults for users** show only offered voices. A saved voice that was removed falls back to the deployment's default with a note. The read-aloud extension narrows its built-in list with the same route, so it can't offer a voice the server would refuse.

**Adding a new voice (my call, awaiting the owner).** Kokoro's 50 voices are one file, so a new voice needs a second TTS engine behind a `Voice` catalogue like `SpeechModel`. The first version narrows the 50. Adding comes right after as its own piece, and the docs say so until then.

## When things go wrong

- **Microphone refused or unavailable:** the same reasons as `useVoiceInput`, in the window, with no retry loop.
- **The speech engine is loading or busy:** the window says so and keeps listening. Paused replies resume, so a broken engine can't freeze a reply.
- **TTS fails on a sentence:** the existing Web Speech fallback plays it. If that fails too, the text stays on screen and the window returns to Listening.
- **The LLM fails or the budget runs out:** the chat's own error shows, spoken text isn't invented, and the window returns to Listening.
- **The tab is closed or the person leaves the window:** the stream closes and the microphone track stops. The browser's recording indicator must go out.
- **A clip reaches 2 minutes:** it is sent as it stands and listening continues.
- **Two people at once:** Whisper and Kokoro each run one job per worker, so they queue. This is a known limit and goes in the docs.

## Testing

- **Unit:** the state machine as a pure reducer covering every row of the flow table; the echo guard; the heard-text calculation; `heard` refused for another person's or a non-aborted message; `model_not_offered`; `voice_not_offered`; eviction at `SPEECH_MODELS_LOADED`.
- **Real browser:** Chromium with `--use-file-for-fake-audio-capture`, on the real stack: a spoken question gets a spoken reply; a cough during a reply pauses then resumes at the same word; "stop" during a reply stops it and the next turn's context holds only the heard part, checked in the real outgoing request.
- **The owner, on real hardware:** speakers without headphones, a real room, and Sinhala and Tamil. These can't be faked, so **What it heard** exists to collect them.
- Test audio stays out of the repo: ESC-50 is non-commercial.

## Order of building

The owner chose English first, to get a baseline and make it fast before anything unknown:

1. **The window and interruption, in English.** The baseline, and speed work against it.
2. **Admin lists and the user's choice.** Useful alone, and can merge alone.
3. **The unknowns:** real rooms, speakers, Sinhala and Tamil.

## What was measured

On 2026-11-02, with the Silero v5 model from `vad-web` 0.0.31 and this app's Whisper settings, on a 32-thread CPU, using 160 ESC-50 noise clips (8 classes), 600 Speech Commands words (stop, no, yes, go) and 30 LibriSpeech sentences:

| Passes | Noise | Speech |
|---|---|---|
| VAD for 250 ms or more | 27 of 160 | 556 of 600 words, 30 of 30 sentences |
| Then Whisper's scores | 4 of 160 | all 190 tested |

- A 500 ms minimum keeps 9 of 150 real "stop"s, whose median is 320 ms.
- Whisper produced text for 20 of the 27 noises, so text alone is no check.
- `no_speech` at the 10th and 90th percentiles: real speech 0.02 and 0.17, noise 0.34 and 0.71.
- Whisper time for 1, 3 and 8 s of speech with 16 threads: `small` 0.8, 1.05 and 1.4 s; `base` 0.28, 0.38 and 0.59 s.
- Silero costs about 0.1 ms per 32 ms frame natively. The browser's cost is **not measured**.

Summing measured parts gives about 2.3 s from the end of speech to the first sound, plus the LLM's time to its first sentence: 0.7 s silence, 1.05 s Whisper, 0.5 s Kokoro. That sum has **not been measured end to end**.

These are clean English recordings and a small sample, and the thresholds came from the same data, so expect worse in a real room.

## What is unknown

- **Whether Chrome's echo cancelling covers this app's own playback.** Sources conflict, and only real speakers and a mic can settle it. If it doesn't, the echo guard and headphones are the first answer, and routing playback through a local WebRTC connection the next.
- **How the checks do in Sinhala and Tamil,** especially `no_speech` on fine-tuned models.
- **How it runs on a phone:** the VAD's cost, and whether the browser keeps listening with the screen locked.
- **CPU load.** Whisper, Kokoro and a local model share the machine, and the bucket list's CPU anti-goal already says a transcription slows a local reply 1.5 to 2 times. The window makes that happen every turn.

## Left to the builder

File layout, the look of the window, and how the WAV is written. Each worker's model pool stays inside `server/speech/service.py`.
