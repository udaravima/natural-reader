# v2.5.0 — Speak a message, attach a file, and an admin console that runs the deployment

**You can now talk to the chat, and hand it a file.** A microphone beside the
paperclip records up to 2 minutes and puts the text in the message box for you to
edit and send. Nothing is sent for you. It runs on your own server by default, and
it's off until an admin turns it on.

**A small text file can join a conversation, with its secrets hidden first.** Drop
in an `.env` and ask how it should change. The browser masks the secret values
**before anything is uploaded**, and clicking the file shows exactly what will be
sent. If you do send real values, they stay with the model you agreed for: neither
the file nor the replies that read it go to another model.

**The admin console grew into the place you run a deployment from.** It's now split
into sections: an **Overview** that checks whether everything works, **Models**,
**Documents**, usage by model and person, a timezone, and defaults. Long lists scroll
and search, and Save stays on screen. **Speech models** can be chosen and added there
with no restart, including fine-tuned Whisper models for languages stock Whisper
handles badly.

Two migrations, `023` and `024`. **023 can't simply be rolled back, so back up your
database first.** Read the upgrade notes: this release changes what the reader model
and local context size default to.

## Added

### 🎙️ Voice input

- **The microphone** beside the paperclip: **Stop** to transcribe, **Cancel** or
  **Esc** to throw it away. The text lands at the cursor. Off by default: **Admin →
  Chat options → Voice input**.
- **Runs on your server**, with faster-whisper on the CPU, or on any
  OpenAI-compatible transcription server. The app keeps a recording in memory and
  never writes it to disk, the database or a log. A reverse proxy in front of it can:
  see [DEPLOYMENT.md](../DEPLOYMENT.md#recordings-and-your-reverse-proxy).
- **Your language** under **Settings → Chat → Speech language**, or **Auto**. An admin
  sets the starting value under **Defaults for users**.

### 🧠 Choosing the speech model

- **Admin → Chat options → Voice input → Speech model** switches models with no
  restart. One model runs for the whole server.
- **Stock Whisper stays the default, and the right choice for English.** Nothing
  downloads until a model is chosen.
- **Fine-tuned Sinhala and Tamil models are in the list and just work.** A
  Transformers-format model is converted to CTranslate2 the first time it runs. On
  the same recordings, stock `small` gave gibberish in Sinhala where run12 gave
  readable text in about 2 s. The Tamil model's median character error was 0.14,
  against 0.35 for stock `small`.
- **Add any Whisper model from Hugging Face** as `owner/name`. It's checked at once
  and pinned to a commit, and **Added here** has a **Remove** for each one. Or list
  your own in `SPEECH_MODELS_FILE`, a path on the host that `startup.sh` mounts.
- **How it listens was set by measuring real recordings.** The voice activity detector
  runs at 0.4, not the default 0.5, which cut real speech. `temperature=0` stops the
  retries that took stock `small` 171 s on 8 Sinhala clips, now 27 s. English came
  through word for word the same. The reasoning is in
  [DEPLOYMENT.md](../DEPLOYMENT.md#how-it-listens).

### 📎 Text files in a chat

- **Paperclip, drag or paste** an `.env`, `.txt`, `.md`, `.json`, `.yaml` or source
  file: 64 KB each, 128 KB per message.
- **Masked in the browser.** In an env file, every value except ones like `true`, a
  port or a short number. In other files, whatever looks like a secret. **Masking is a
  safety net, not a guarantee:** the chip shows what will be sent, so look before you
  send.
- **Send real values** asks first, and names the model and whether it runs on a cloud
  service. Changing the model turns masking back on.
- **Real values stay with that model.** On another model, the file and the replies
  that read it are held back, and a note under the reply says so. Auto web search
  asks first on those turns.

### 🛠️ The admin console

- **Overview** checks models, the reader, context windows, web search, Docling, voice
  input and more, one line each.
- **Models:** the reader, the context size for local Ollama models, per-model sizes,
  declared windows, Local or Cloud for each provider, and which chat models people are
  offered.
- **Documents:** who can convert, which presets, OCR, picture descriptions and a page
  limit, with a new `docling` role.
- **Usage** by model, by person and by day, with Local and Cloud apart.
- **A timezone for the deployment**, which sets when each day's budget resets.
- **One look for every setting:** where its value comes from, and a way back to the
  server's own.
- **Long lists scroll and search,** and Save stays on screen, at any width. Past 12
  entries a list gets a search box, including long dropdowns such as the timezone and
  the model pickers.

### 🖼️ Documents

- **Convert → Images → Keep** keeps a PDF's pictures, shown in **MD** view. It used to
  crop each picture and throw it away.
- **Conversion shows its progress,** and a failed one offers **Retry convert** with
  the reason.

## Changed

- **The reader defaults to the chat model** when none is set. It used to be
  `llama3.2:3b`.
- **Local Ollama models get 16,384 tokens of context** unless someone sets a size,
  instead of Ollama's own 4,096.
- Every living document was checked against the code. DEPLOYMENT gained worked
  examples for vLLM, Unsloth, OpenAI and Claude.

## Fixed

- **A local Ollama model no longer silently loses the start of a long chat.**
- **The reader no longer makes Ollama reload the chat model** on every call.
- **History is trimmed for a model that reports no window.**
- **A file opened from a folder can be indexed.**
- **Project chat finds a document named with its number**, and says when a document
  isn't searchable yet.

The full list is in the [changelog](../../CHANGELOG.md#250---2026-11-02).

## Upgrade notes

1. **Back up the database.** Migration 023 changes the key of `inference_usage`, and
   rolling back needs the manual Down SQL in `server/sql/023_usage_by_model.sql`
   **before** starting an older image. Without it, the old image's usage writes fail
   quietly, so budgets stop counting.
2. **Rebuild the image** (`./startup.sh rebuild`), which is about 276 MB larger. On bare
   metal, run `pip install -r requirements.txt` again.
3. **Behind nginx,** `/v1/speech/transcriptions` needs `client_max_body_size` above 10 MB.
   The sample vhost's 100m is enough.
4. **To convert or run a fine-tuned speech model,** use the Docling build
   (`NR_WITH_DOCLING=true`), which has `torch` and `transformers`. On the plain image
   those models show greyed out with the reason, until one converted elsewhere is
   copied in: see [DEPLOYMENT.md](../DEPLOYMENT.md#choosing-a-model).

Behaviour that changes on its own:

- **The reader moves to the chat model** on a deployment that sets none of
  `READER_MODEL`, `SUMMARIZE_MODEL` or `WEB_SEARCH_SUMMARY_MODEL`. With a cloud chat
  model, reader calls then count against its quota. Choose a local reader under
  **Admin → Models → Reader** to avoid that.
- **Local Ollama models reserve more memory,** at 16,384 tokens. On a machine short of
  RAM, size them down under **Models** or with `INFERENCE_LOCAL_NUM_CTX`.
- **If your environment sets `TZ`,** the daily budget and usage days follow it. To keep
  UTC days, choose UTC under **Deployment**.
- **A running Keycloak realm needs the `docling` role created by hand** if you want
  "only people with the Docling role".
- **Voice input stays off,** and nothing downloads, until an admin turns it on.

## Known issues

- **A transcription slows a local model on a CPU-only machine.** On one such machine, a
  local model's reply took about 1.5 to 2 times as long during a transcription, and
  narration about 1.6 to 2.4 times. A cloud model wasn't slowed. `SPEECH_CPU_THREADS`
  limits how many cores it takes.
- **A short all-digit password in an env file isn't masked:** to a program it looks
  like a port.
- **What a person typed or pinned isn't held back** from another model, and a model
  holding real values could pass them on in a written request to the reader model.
- **A file larger than the model's context is sent, with a note.** The model may read
  only part of it.
- **Non-English Kokoro voices come out wrong** through the app, because
  `/v1/synthesize` sends `en-us`.
- **The page scrolls sideways at 375 px** because of the header's buttons.

## What you can't do yet

- **Talk straight to an audio model.** Speech is transcribed first. The `Transcriber`
  interface is where a native audio engine will plug in.
- **Run more than one speech model.** A server can't specialise in Sinhala and Tamil
  together, or pick a model per person or language.
- **Describe pictures with a vision model you choose,** or use the DeepSeek-OCR preset.
  **Describe** still uses Docling's built-in model.
- **Change the embedding model in the console,** or index documents without Ollama.

Not tested: real Safari or a real phone, Sri Lankan Tamil, a fine-tuned model for a
language Whisper doesn't list, and a real remote speech engine (it's tested against a
fake).

## Full changelog

[CHANGELOG.md](../../CHANGELOG.md#250---2026-11-02) · diff:
[v2.4.0…v2.5.0](https://github.com/udaravima/natural-reader/compare/v2.4.0...v2.5.0)
