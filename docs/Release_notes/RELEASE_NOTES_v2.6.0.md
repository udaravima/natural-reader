# v2.6.0 — Talk: a spoken conversation in the chat

**You can now talk with the assistant, back and forth, the way you would in a voice
app.** Press **Talk** beside **Send**, speak, and it answers aloud. To cut in, just
speak: the reply pauses at once, a real interruption stops it, and a cough lets it carry
on from the same word. Everything said fills the chat like any other conversation, so
when you press **End** you're back to typing in the same chat.

It's built from separate parts rather than one speech-to-speech model. The browser
listens (Silero), Whisper turns speech into words, the chat model answers, and Kokoro
speaks. On a CPU with a cloud chat model, the first sound comes about **2.1 s** after
you stop speaking. It's English for now.

Talk is for admins and anyone given the new **`talk` role**. There are no database
migrations, but an existing Keycloak realm needs that role created once: see
[Upgrade notes](#upgrade-notes).

## Added

### 🎙️ Talk

- **A button beside Send,** in the chat and in the chat beside a page. It starts
  listening at once; the browser asks for the microphone the first time.
- **The typing box makes way for the conversation:** a circle whose halo grows with
  your voice, **Mute** and **End**. Beside it, one word says what it's doing:
  Listening, Hearing you, Checking, Thinking or Speaking.
- **Cut in by speaking, or by tapping the circle.** Speaking pauses the reply at once,
  and Whisper decides whether you really spoke. Real words stop the reply and join the
  conversation. A cough, a knock or a laugh lets it carry on from the same word.
  Tapping the circle stops the reply there and then.
- **The model remembers only what you heard.** Cut a reply short and the chat keeps it
  up to where you stopped it, so the next answer doesn't assume you heard the rest.
- **Your voice stays in your browser until you speak.** Nothing streams while the room
  is quiet. The listening model and its runtime, about 17 MB, are served by your own
  server and load the first time someone presses Talk.
- **Esc ends it,** and so does opening another chat. On a phone the screen stays on
  while you talk.
- **What it heard** lists every clip: what it heard, whether that counted as speech, and
  how long Whisper, the model and the voice each took. **Copy numbers** copies them,
  for reporting how it behaved.

### 🔑 The `talk` role

- **Who may use Talk.** Tick **talk** under **Admin → People**, or on the Enroll form,
  or give the `talk` realm role in Keycloak. An admin always may. Talk also needs chat
  and read-aloud access, and voice input on.
- **The server checks it too:** a spoken turn from someone without the role is refused
  with a 403, so hiding the button isn't the only lock.

### ⚙️ For deployers

- **English-only speech models, `base.en` and `small.en`,** under **Admin → Chat
  options → Speech model**. `base.en` takes about 0.4 s a clip where `small` takes
  1.0 s, at about 9% word errors against 7%.
- **`TTS_CPU_THREADS`** (default 6). Kokoro was slower using every core than using six.
- **`KOKORO_MODEL_PATH`,** to point at another Kokoro file. The `fp16` file is about
  15% faster, but its audio isn't identical, so listen first. Never use `int8` on a CPU:
  it's 4 times slower.
- **Nine `SPEECH_WINDOW_*` settings** for how Talk listens and decides. The defaults
  are measured; see [DEPLOYMENT.md § The speech window](../DEPLOYMENT.md#the-speech-window).

## Fixed

- **Someone holding only `talk` or `docling` sees "no access yet",** not an endless
  Loading screen. Both roles add to Reader or Chat and open nothing on their own.
- **An admin's header fits a phone.** It was 15 px too wide at 375 px and 30 px at
  360 px.

## Upgrade notes

1. **Rebuild both halves, installing packages first.** The frontend has two new npm
   packages (`@ricky0123/vad-web` and `onnxruntime-web`): run `npm install`, then
   `npm run build`, which also copies the listening files into `dist/vad/`. For the
   backend, `./startup.sh rebuild` rebuilds its container; on bare metal, restart it.
2. **Create the `talk` role in an existing realm, once.** The realm file only seeds a
   brand-new Keycloak database, so a running realm doesn't get the role. In the
   Keycloak admin console, open realm `natural-reader`, then **Realm roles → Create
   role**, name `talk`, and Save. Until then, ticking **talk** under **People** says
   which role is missing and changes nothing.
3. **Give people the role.** A tick under **People** applies at once. A role given in
   Keycloak applies at the person's **next login**, not on a reload.
4. **For Talk on a CPU, choose `base.en`** as the speech model, and a cloud chat model
   if you have one. The speech model serves the whole server, the dictation microphone
   included, so keep a multilingual model if people dictate in other languages.
5. **If you serve the app behind your own proxy,** it must send `.wasm` as
   `application/wasm` and `.mjs` as JavaScript. A Content-Security-Policy needs
   `'wasm-unsafe-eval'` in `script-src`.

No migrations. Node.js 22.13 or 24 is still the minimum, as in v2.5.1.

## Known issues

- **English only.** Sinhala, Tamil and other languages are next. Whisper's speech and
  noise scores haven't been measured for them yet.
- **One speech model and one voice list for everyone.** Admins can't yet offer several
  speech models or narrow the voices for people to choose among.
- **Speakers without headphones are untested.** The microphone may hear the reply. A
  clip that started during a reply is dropped when most of its words are the reply's
  own, but whether the browser's echo cancelling covers the app's audio is unknown.
  Headphones avoid the question.
- **Two people talking at once queue,** because Whisper and Kokoro each run one job at a
  time per worker, and read-aloud shares Kokoro with Talk.
- **Reload People before ticking someone who just logged in.** A tick sends the
  person's whole set of capabilities as the list shows them. If their roles changed
  since you opened the list, the tick puts back the old set. This was already true
  before this release, for every role.
- **The known issues in [v2.5.1](RELEASE_NOTES_v2.5.1.md#known-issues) still apply.**

Not tested: a real room, real speakers, a real phone's microphone, and browsers other
than Chromium. The tests used a simulated microphone playing clean recordings.

## Full changelog

[CHANGELOG.md](../../CHANGELOG.md#260---2026-11-03) · diff:
[v2.5.1…v2.6.0](https://github.com/udaravima/natural-reader/compare/v2.5.1...v2.6.0)
