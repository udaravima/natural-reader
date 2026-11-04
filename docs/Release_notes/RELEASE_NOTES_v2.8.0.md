# v2.8.0 — Talk settings, and Talk in a busy room

**Each person can now tune how Talk listens, on their own device.** **Settings → Talk**
(or the sliders button in the Talk panel) holds the microphone's own processing, the pause
that ends a turn, how much speech counts as a turn, how sure it must be that it's speech,
and a new **Ignore quieter voices** for a busy room. Every control shows this server's
tested value beside the person's, and **Reset to defaults** puts them all back.

Out of the box nothing changes: with no setting touched, Talk listens exactly as it did in
v2.7.0.

## Added

### 🎚️ Settings → Talk

- **Microphone**: automatic gain, noise suppression and echo cancellation, each on by
  default as before.
- **Ignore quieter voices**, with **How much quieter** (3 to 30 dB, default 9).
- **Ending your turn**: the pause that ends it (default 0.7 s), the speech that counts as a
  turn (0.25 s), and how sure it must be that it's speech (0.50).
- **Kept per device**, so a phone and a laptop each have their own. Only changed values
  are stored: a control moved back to its default follows the server again if an admin
  changes it.
- Shown only to someone with the `talk` role.

### 🏢 Ignore quieter voices

Silero, the voice detector Talk uses, tells speech from noise but not near from far: a
colleague two desks away held every turn open and paused replies. With this on, a voice
more than the margin below the person's counts as silence, so their turn ends when they
stop. A turn is judged against its own level, so someone who sits back from the
microphone isn't cut off; between turns, against the loudest of their last three turns.
The panel says *Ignoring voices 9 dB quieter than yours* while it's on.

**It's off by default, and it has a limit worth knowing.** It goes by loudness, so it
can't tell whose voice it is. Walked in Chromium with a colleague reading nonstop about
13 dB below the person, every version of it ended the person's turns, which nothing did
without it; but once one of the colleague's sentences opens a turn, Whisper accepts it,
because it is speech, and it is sent. A headset microphone is the sure fix. The numbers
are in [DEPLOYMENT.md, "Talk in a busy room"](https://github.com/udaravima/natural-reader/blob/master/docs/DEPLOYMENT.md#talk-in-a-busy-room).

### 👆 Tap the circle to send

While Talk is **Hearing you**, a tap on the circle sends what you said, for a room that
still holds a turn open. The microphone stays open.

### 🔎 What it heard

- **Copy numbers** includes the settings Talk listened with and what the browser applied.
- It says when the person turned automatic gain off and the browser kept it on, as some
  phones do.

## Fixed

- **What it heard** scrolls inside a fixed height instead of pushing the chat away.

## Upgrading

- **No migrations.** Rebuild both halves: the backend image and the frontend (`npm run
  build`).
- **New:** `SPEECH_WINDOW_GATE` (`off` by default) makes **Ignore quieter voices** the
  default for everyone; each person can still switch it.
- **Changed:** `SPEECH_WINDOW_GATE_DB` is now 3 to 30 and applies only with the gate on.
  `0` no longer means off: it is out of range, logs one warning, and the default (9)
  applies. If you set `SPEECH_WINDOW_GATE_DB=0` to turn off the gate a pre-release build
  had, remove it.

## Tests

2906 backend tests pass (16 skipped), 1751 frontend tests in 144 files, and lint has no
errors. The gate was simulated through Silero v5 on 20 recordings of real people, played
back 3, 6 and 9 dB quieter, and walked in Chromium with the real voice-detection library
and Whisper `small`. **A real office is untested** beyond one try of a pre-release
version.

## Full changelog

[CHANGELOG.md](../../CHANGELOG.md#280---2026-11-04) · diff:
[v2.7.0…v2.8.0](https://github.com/udaravima/natural-reader/compare/v2.7.0...v2.8.0)
