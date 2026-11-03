# v2.7.0 — Google Cloud speech, as an option, for the chat and Talk

**Google can now hear you and speak back, in English, Sinhala and Tamil, if your server is
set up for it.** It's off unless a deployer configures it, and the app stays fully
self-contained without it. With it, an admin chooses who hears speech (Whisper on the
server, an OpenAI-compatible server, or Google Speech-to-Text) and who reads the chat and
Talk (Kokoro on the server, or Google Gemini-TTS). Both switch from the admin console, with
no restart.

It's there for the languages the local models handle badly, and for comparing. Gemini-TTS
is the first voice in the app that speaks Sinhala and Tamil. Documents, audiobooks, a
message's **Audio** download and the browser extension keep Kokoro whatever is chosen.

What it costs: recordings and every sentence read aloud go to Google, Google bills per
use, and Google's voice is slower to start. See [How fast it is](#how-fast-it-is).

## Added

### 🗣️ Google hears and speaks

- **Speech recognition: Google Cloud.** Under **Admin → Chat options**, beside Whisper and
  an OpenAI-compatible server. It uses a recognizer you create in Google Cloud, so its
  model and languages are yours to choose there. The microphone and Talk both use it. A
  recording stops at 55 s, Google's limit, and Talk's panel says **Heard by Google Cloud**.
- **Voice for the chat and Talk: Google Gemini-TTS.** One of 30 voices, the same for
  everyone, picked under the choice. Each sentence is spoken in its own language, judged by
  its letters: mostly Sinhala, mostly Tamil, or the default (English). The reading speed in
  **Settings** still applies. **Settings → Chat** tells each person that Google reads the
  replies.
- **Speech language follows the recognizer.** With Google, **Settings → Chat → Speech
  language** lists only the languages its recognizer knows. A recognizer that can't tell
  them apart says so: **Auto (Sinhala on this server)**, and "pick the language you speak".
- **Choices that say what they need.** An engine the deployer hasn't configured is greyed
  out, naming the server setting it needs. **Use the server setting** hands back control.

### 🩺 When Google says no

- **A refused sentence says why,** once for a run of them, and is skipped. There's no
  silent fallback to Kokoro or Whisper.
- **People are told to ask their admin, and the admin is told why.** **Admin → Overview**
  has a new **Chat voice** line, and the **Voice input** line covers Google too. Each names
  the problem: a key file that isn't a service-account key, a key Google refuses, a missing
  role, or an API that's switched off, with its project.

### ⚙️ For deployers

- **Eight settings,** all optional: `GOOGLE_APPLICATION_CREDENTIALS`,
  `GOOGLE_SPEECH_RECOGNIZER`, `GOOGLE_TTS_MODEL`, `GOOGLE_TTS_VOICE`,
  `GOOGLE_TTS_LANGUAGE`, `GOOGLE_CLOUD_PROJECT`, `CHAT_VOICE` and `GOOGLE_TIMEOUT_S`, plus
  `google` as a value of `SPEECH_ENGINE`. See [DEPLOYMENT.md § Google Cloud speech](../DEPLOYMENT.md#google-cloud-speech).
- **`startup.sh` mounts the key read-only** into the backend container when
  `GOOGLE_APPLICATION_CREDENTIALS` names a readable file on the host.

## How fast it is

Talk's time from the end of a question to the first sound of the answer. These are medians
of five English questions with a cloud chat model, measured on 2026-11-03 from one home
connection. Yours will differ with your distance to Google.

| Hears | Speaks | First sound |
|---|---|---|
| Whisper `base.en` on the server | Kokoro | 2.1 s |
| Google | Kokoro | 3.0 s |
| Google | Gemini-TTS | 7.1 s (Sinhala 6.3 s, Tamil 5.4 s) |

Gemini-TTS takes 2.3 s or more for any sentence, however short. For English, the quicker
mix is Google hearing and Kokoro speaking. For Sinhala and Tamil, Gemini-TTS is the only
voice the app has.

## Upgrade notes

1. **Rebuild the backend:** it has one new Python package, `google-auth`.
   `./startup.sh rebuild` rebuilds the container; on bare metal, run
   `pip install -r requirements.txt` and restart. Rebuild the frontend with
   `npm run build`; there are no new npm packages.
2. **Nothing changes until you configure Google.** No migrations, and no request reaches
   Google while the settings are unset.
3. **To turn it on,** follow [DEPLOYMENT.md § Google Cloud speech](../DEPLOYMENT.md#google-cloud-speech):
   turn on the APIs, create a recognizer and a service account with its JSON key, set the
   variables, then choose under **Admin → Chat options**. Two things that tripped the first
   setup:
   - **The key must be a service-account key,** with `"type": "service_account"` in it. An
     OAuth client ID file won't work, and the Overview says so.
   - **The recognizer's full name must use the project the recognizer lives in.** Copy it
     from the recognizer's page.
4. **Pick a recognizer for the languages you need.** `long` in `global` hears English,
   Sinhala and Tamil well when each person picks their language, but hears only the first
   of its languages for Auto: set **Admin → Defaults → Speech language** to the one most
   people speak. `chirp_3` with the language `auto` tells them apart on its own, about 1 s
   slower.
5. **Set a budget in Google Cloud.** The app has no spending cap.

Node.js 22.13 or 24 is still the minimum, as in v2.6.0.

## Known issues

- **Tested with synthetic Sinhala and Tamil.** Those clips were spoken by Gemini-TTS and
  heard by Google, so they prove the round trip, not accuracy on real voices or accents.
  The English questions were human recordings.
- **How Gemini-TTS sounds in Sinhala and Tamil is for you to judge:** listen before
  turning it on for people.
- **Long replies with Gemini-TTS may pause between sentences.** Each sentence takes about as
  long to make as to play, and gaps weren't measured.
- **Talk with a stored language the recognizer doesn't list** sends it anyway, and Google
  refuses it with its own words. Choose a listed language in **Settings → Chat** to clear
  it.
- **The known issues in [v2.6.0](RELEASE_NOTES_v2.6.0.md#known-issues) still apply.**

Not tested: a real room, real speakers, a real phone's microphone, and browsers other than
Chromium.

## Full changelog

[CHANGELOG.md](../../CHANGELOG.md#270---2026-11-03) · diff:
[v2.6.0…v2.7.0](https://github.com/udaravima/natural-reader/compare/v2.6.0...v2.7.0)
