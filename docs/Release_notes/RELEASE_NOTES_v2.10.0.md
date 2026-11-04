# v2.10.0 — A recognizer for each language, and the time

**Google speech can now send each language to its own recognizer, picked in the admin
console from Google's live list.** Until now, Google speech went through exactly one
recognizer, named in `.env`. A recognizer holds one model, and no single Google model
hears Sinhala, Tamil and English well: Sinhala isn't on `chirp_3` at all, and `chirp_2`
takes one language per recognizer. So the only way to give every language a model that
hears it is one recognizer per language. This release lets an admin tick them.

**And the assistant can tell the time.** Ask "what time is it?" or "how long until 5 pm?"
and it answers in your own timezone.

Out of the box nothing changes: with nothing ticked, `GOOGLE_SPEECH_RECOGNIZER` is the one
recognizer, as in v2.9.0.

## Added

### 🎙️ Admin → Chat options → Google recognizers

- Shown once Google is set up (`GOOGLE_APPLICATION_CREDENTIALS` or
  `GOOGLE_SPEECH_RECOGNIZER`). It lists **every active recognizer in the key's project**,
  read live from all 17 of Google's regions and every page of their lists, in about 3 s:
  each one's id, model, languages (by name) and location.
- **Tick the ones to use, in order.** A clip goes to the **first ticked recognizer that
  lists its language**; Auto, and any language none lists, go to the first one, marked
  **Auto**. **Move up** reorders.
- Languages are spelled out, so a slip shows: `sl-SI` reads **Slovenian**, which is easy to
  pick for Sinhala in Google's console.
- A ticked recognizer Google no longer lists is shown, with **Untick**. A refusal (no
  permission to list, a region that didn't answer) is shown in Google's words.
- The **Speaking** chip and **What it heard** name the recognizer and its model for each
  language and clip, as they do Whisper's models.

### 🕰️ The assistant can tell the time

- A new `current_time` tool gives the time where the person is: their browser's timezone,
  else the deployment's (**Admin → Defaults → Timezone**), else UTC. Nothing leaves the
  server.
- The rules the model gets still carry only today's date, to the day, so the prompt cache
  lasts the day and a clock in every message isn't mistaken for part of the question. A
  question that needs the time of day asks the tool instead.
- Walked with `gemma4:31b-cloud`: "What time is it right now?" and "How long until 11 pm?"
  called it and answered right; "What's the date today?" didn't need it.

## Changed

- **Only a recognizer Google listed for this server's key can be saved.** A recognizer's
  location becomes the host the server sends its Google token to
  (`https://<location>-speech.googleapis.com`), so a location such as `evil.example#` could
  have sent the token elsewhere. Every name, `GOOGLE_SPEECH_RECOGNIZER` included, must now
  match a strict pattern before a request is built from it, and a ticked name must also be
  in a fresh listing from Google. Every real Google name fits.
- Google can be chosen as the engine once a recognizer is ticked, not only once one is set
  in `.env`.
- **A `chirp_3` recognizer with several languages no longer says "Auto hears only …"**:
  measured live, it picks between its languages by itself. The note stays for models that
  hear only their first, such as `long`.
- **A clip Google refuses is logged in Google's words**, with the recognizer's id.
- Every turn with tools now offers `current_time`, so a chat with web search off and no
  document sends one tool too. A model marked as not taking tools gets none, as before.

## Upgrading

- **No migrations.** Rebuild both halves: the backend image (`./startup.sh rebuild`) and
  the frontend (`npm run build`). The ticked recognizers are one more key in the chat
  options row.
- **To use a recognizer per language:** make them in Google's console (Speech-to-Text →
  Recognizers), one per language; then **Admin → Chat options**, set **Speech recognition**
  to **Google Cloud** and tick them, the one that should hear Auto first. The service
  account must be allowed to list recognizers; if it isn't, the console shows Google's
  refusal.
- **A trap:** Google's console still creates a `chirp_3` recognizer in `asia-southeast1`,
  and reading it works, so the console says it's ready, but Google refuses every clip sent
  to it ("no longer generally available in region asia-southeast1"). Make `chirp_3`
  recognizers in `us` or `eu`; `chirp_2` works in `asia-southeast1`.
- `GOOGLE_SPEECH_RECOGNIZER` is now only the fallback for when nothing is ticked. If it
  names a recognizer you deleted, point it at one that exists or clear it.
- Measured results per model, region and language:
  [DEPLOYMENT.md, "Google Cloud speech"](https://github.com/udaravima/natural-reader/blob/master/docs/DEPLOYMENT.md#google-cloud-speech).

## Known limits

- **Recognizers are made in Google's console**, not in the app. The app lists and ticks
  them only.
- **Not walked through the app:** an English recognizer of its own (`chirp_2`, `en-GB`,
  `asia-southeast1`). The same model, language and region answered English right through
  Google's built-in default recognizer. Sinhala and Tamil recognizers were walked through
  the app with real recordings (1.0–1.5 s and 1.6–2.2 s).
- **Mixed languages within one clip** are untested.
- The clock gives the time; it doesn't set reminders or count down.

## Tests

2997 backend tests pass (16 skipped), 1817 frontend tests in 151 files, and lint has no
errors. Walked against Google with a real key: the console listed the project's
recognizers, they were ticked and reordered at 1366 and 360 px, and real Sinhala and Tamil
recordings went to their own recognizers and came back in their own scripts.

## Full changelog

[CHANGELOG.md](../../CHANGELOG.md#2100---2026-11-04) · diff:
[v2.9.0…v2.10.0](https://github.com/udaravima/natural-reader/compare/v2.9.0...v2.10.0)
