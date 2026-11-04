# v2.9.0 — Speak the language you speak

**Each person now switches the language they speak in one tap, and each language is heard
by a model made for it.** The **Speaking** chip sits beside the microphone and in the Talk
panel: pick Sinhala, English or Tamil, and the next thing you say goes in that language,
without stopping Talk. Before, changing language meant an admin switching the whole
server's one model.

Out of the box nothing changes: until an admin offers a model for a language, everyone is
heard by the default model, as in v2.8.0.

## Added

### 🗣️ The Speaking chip

- **Beside the microphone**, and on a phone just above the message box, so it doesn't
  squeeze the text box; and **in the Talk panel**, beside its buttons.
- It lists **Auto**, the Talk language, and each language an admin offered a model for,
  each with the model that hears it. **All languages…** opens Settings for the rest.
- It sets the person's **Speech language**, the same value as Settings → Chat, so the
  choice sticks. The server picks the model from the language; the browser never names a
  model.
- **On Auto, Talk says "Auto (English)"**: Talk sends its window language
  (`SPEECH_WINDOW_LANGUAGE`, English by default) with every clip when the Speech language
  is Auto. That is very likely why Google speech seemed to hear only English: it was told
  English. Picking the language on the chip fixes it.
- **What it heard** names each clip's language and the model that heard it.

### 🧩 Admin → Chat options → Speech model → Offered to people

- A tick beside each model made for particular languages, such as a Sinhala or a Tamil
  fine-tune. A ticked model hears its languages; the default model above it hears the rest.
- **Ticking downloads it.** People see it on the chip only once it's on the server, so
  nobody waits on a download mid-sentence; the console says **Downloading** until then.
- A model that hears every language can't be ticked: make it the default instead.

### 🧠 Several speech models per worker

- Up to `SPEECH_MODELS_LOADED` models (default 3, from 1 to 4) stay loaded in each worker.
  A request keeps the model it was sent to, so two people on two models are each heard by
  their own.
- A clip for a model that's still loading waits for it instead of failing. One that can't
  load is heard by the default model.
- A model dropped to make room hands its memory back to the system.
- `/v1/speech/transcriptions` answers which model heard the clip, and the log line says so.

### 🔊 Settings → Chat → Reply voice

When an admin has Google read the replies, each person picks Google's voice for them, with
the admin's voice as the default. The **Hear** button beside it plays a short sample in English, Sinhala and
Tamil, fetched once per voice and speed per session so it isn't billed twice.

## Changed

- **Reading speed is a slider** from 0.5× to 2×, instead of six fixed speeds. It drags
  smoothly but is pulled onto round values every 0.05, and harder onto 1×. In a drag across
  it, about 80% of positions land on a round value; the values between are a little extra
  push away. Arrow keys jump to the next round value, and Shift with an arrow moves by
  0.01. A saved speed carries over.
- **Volume and the Talk settings' sliders** pull onto round values the same way (5%,
  100 ms, 50 ms, 0.05), with finer values between. No marks under any of them.
- **Admin → Defaults → Reading speed** takes 0.05 steps.

## Fixed

- **On a phone, Settings was cut off on the right for an admin.** The header's row of views
  and the reader's buttons were wider than a 360 px screen, so the whole page widened to
  422 px. Where the row doesn't fit, one button now shows the open view and opens a menu
  of the others: on a phone, and in the reader below 1280 px, where a 768 px tablet
  overflowed by 164 px. Checked from 320 to 1440 px in every view.

## Upgrading

- **No migrations.** Rebuild both halves: the backend image and the frontend (`npm run
  build`). The offered list is one more key in the chat options row.
- **New:** `SPEECH_MODELS_LOADED` (default 3). Budget the memory: in a walk, three models
  (`small` and the Sinhala and Tamil `small` fine-tunes) took the backend from 1,238 MB to
  2,854 MB. Set it to 1 on a small server: everyone is still heard, but each switch of
  language loads that language's model again.
- **Disk:** a fine-tune that needs converting downloads its original checkpoint (about
  1 GB for a `small`) and keeps a 242 MB converted copy. The original stays in the
  Hugging Face cache afterwards; this release doesn't remove it.
- Details and measurements: [DEPLOYMENT.md, "Offering models to people"](https://github.com/udaravima/natural-reader/blob/master/docs/DEPLOYMENT.md#offering-models-to-people).

## Known limits

- **The Sinhala and Tamil fine-tunes don't score silence.** They report a no-speech
  probability of 0.00 for every voice tried, so Talk's noise limit never fires on them; the voice
  detector, the empty-text check and the echo check still filter.
- **Google speech is one recognizer** for every language. Per-language recognizers are
  not in this release.
- **Untested:** a member without the admin role through a real identity provider (an admin
  walked every screen), a real Google reply voice (the walk simulated Google's answers),
  and real Sinhala and Tamil speakers in Talk (the check used synthetic voices).

## Tests

2946 backend tests pass (16 skipped), 1806 frontend tests in 150 files, and lint has no
errors. Walked in Chromium: one Talk conversation went Sinhala → English → Tamil, heard by
the Sinhala fine-tune, `small` and the Tamil fine-tune with the right text, first clips in
1.8, 0.8 and 1.6 s while each model loaded. The sliders were swept one pixel at a time and
the header checked at every width from 320 to 1440 px.

## Full changelog

[CHANGELOG.md](../../CHANGELOG.md#290---2026-11-04) · diff:
[v2.8.0…v2.9.0](https://github.com/udaravima/natural-reader/compare/v2.8.0...v2.9.0)
