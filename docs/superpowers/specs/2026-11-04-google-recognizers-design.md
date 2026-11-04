# Google recognizers chosen in the console — design

**Status:** approved in conversation, 2026-11-04 20:42 ("sure go ahead"), after the owner made a recognizer per
language in Google Cloud.

## Why

Since v2.7.0, Google speech goes through exactly one recognizer, named in `GOOGLE_SPEECH_RECOGNIZER`. A recognizer
carries one model, and no one Google model is good for all three of the owner's languages: Google's own tables
(read 2026-11-04) list Sinhala (`si-LK`) for `chirp` and `chirp_2` only, never `chirp_3`, while Tamil and English
are on `chirp_3`. So the owner made a recognizer per language (`asr-si-en`, `asr-ta-en`) and wants the app to use
them, each for its language, chosen in the admin panel instead of `.env`.

Verified live with the owner's key, 2026-11-04 20:45: their service account may list recognizers; Speech v2 has
17 locations and lists them five per page; recognizers are listed per location on that location's own endpoint.

## What a person gets

- **An admin**, under **Admin → Chat options → Speech**, sees every recognizer in the Google project, read live:
  its id, model, languages (as names: a slip such as `sl-SI` reads "Slovenian") and location. They tick the ones
  to use, in order; the first ticked hears Auto and any language the others don't list. A problem Google reports
  (no permission to list, a location that refused) is shown in Google's words.
- **A person** sees, on the Speaking chip, each language the ticked recognizers list, with the recognizer that
  hears it, and **What it heard** names the recognizer and its model per clip.
- A clip in a language goes to **the first ticked recognizer that lists the language**, the same rule as
  Whisper's Offered to people. Auto goes to the first ticked one. The person's language is still sent with the
  clip, in the recognizer's own code (`en` → `en-GB` on a recognizer that lists `en-GB`).

## Rules

- **Only a recognizer this server found in Google can be saved.** The location inside a recognizer's name becomes
  the host the server sends its Google token to (`https://<location>-speech.googleapis.com`), so a name from the
  browser must never choose it. A saved name is checked twice: against a strict pattern (lowercase letters, digits
  and hyphens in each part), and against a fresh listing from Google.
- **`GOOGLE_SPEECH_RECOGNIZER` stays the fallback**: with nothing ticked, it is the one recognizer, as before. The
  strict pattern applies to it too; every real Google name fits it.
- **Google can be chosen** as the engine once a recognizer is ticked or named in the environment. The recognizer
  list is shown when Google credentials or a recognizer are configured (`GOOGLE_APPLICATION_CREDENTIALS` or
  `GOOGLE_SPEECH_RECOGNIZER`), so an admin can tick one before choosing Google.
- **Languages come from Google**, read when a recognizer is probed (kept 60 s, as today). Before a clip in a named
  language is routed, the ticked recognizers are probed (cached), so a worker that never answered a status request
  still routes correctly; a recognizer Google can't answer for lists no language, so only Auto would reach it if
  it is first.
- Which projects are listed: the key's own project, plus the project of `GOOGLE_SPEECH_RECOGNIZER` and of every
  ticked name. At most ten recognizers can be ticked.
- Only recognizers in state `ACTIVE` are listed.

## Not in this

- Creating or editing recognizers from the app: that stays in Google's console.
- A recognizer per person or per profile (the agent profiles the owner raised next are a separate design).
- Mixed-language speech within one clip: Google's docs don't say how Chirp handles it; untested.

## What's left to the builder

Where the listing code lives, its concurrency, the exact wording of messages, and the panel's layout, within the
existing admin picker's style.
