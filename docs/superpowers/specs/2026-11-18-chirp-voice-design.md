# Google Chirp 3 HD as a chat voice (short design)

**Status:** approved by the owner 2026-11-18 ("yeah that is better go ahead").

## Why

In the call benchmark, the voice was the slowest step of the all-Google chain. Gemini-TTS lite took 2.74 s to make
each piece of a reply (round 1 median); Google's Chirp 3 HD voices took 0.57 to 0.63 s. A live check on 2026-11-18
gave 0.64 to 0.80 s for a sentence lasting 2.8 s. The app could only use Gemini-TTS: `chat_voice.google_speak`
always sends a Gemini `modelName`, so no setting reached Chirp.

## What

A third engine for the chat's replies and Talk, `chirp` ("Google Chirp 3 HD"), beside `kokoro` and `google`
(Gemini-TTS). An admin picks it in the console's "Voice for the chat and Talk"; a deployer can set
`CHAT_VOICE=chirp`. Documents, audiobooks and the extension keep Kokoro, as with Gemini-TTS.

Facts checked live on 2026-11-18 (Cloud Text-to-Speech voices list and two requests):

- Chirp 3 HD has the same 30 voice names as Gemini-TTS (Kore, Puck…), written `<language>-Chirp3-HD-<name>`. The
  admin's voice and each person's own voice (Settings → Chat) carry over unchanged.
- Voices exist for en-US, en-IN, en-GB (and other English) and ta-IN. **None for si-LK**: Google lists no Sinhala
  voice of any family.
- `speakingRate` works (1.25 turned 2.76 s of audio into 2.16 s).
- No `modelName`: the voice name says it all.

## Decisions

| Decision | Who |
|---|---|
| A third engine, not a value of `GOOGLE_TTS_MODEL` | owner (approved) |
| A Sinhala sentence is read by Gemini-TTS when `GOOGLE_TTS_MODEL` is set; otherwise the person is told there is no Sinhala voice and to ask their admin, and the admin's detail names the setting | owner (approved) |
| Text that mixes languages (the Settings sample has English, Sinhala and Tamil sentences) is split into runs of sentences in one language, each run spoken in its language, and the audio joined. A single-language piece (nearly every reply piece) is one request, as now | my call: otherwise Chirp reads every sentence in the majority's voice |
| Chirp can be chosen when `GOOGLE_APPLICATION_CREDENTIALS` or `GOOGLE_TTS_MODEL` is set; otherwise it "needs GOOGLE_APPLICATION_CREDENTIALS" | my call: the same signal speech/service.py uses for Google hearing, plus the Gemini setting, which already says Google is set up |
| The console Overview says the Chirp voice in use, and warns when Sinhala has no voice | my call |

## Addendum: the English accent (owner, 2026-11-18: "include settings to change the accent")

The owner thought both voices defaulted to en-IN; the deployment sets no `GOOGLE_TTS_LANGUAGE`, so it was en-US.
Both voices took en-US, en-GB, en-IN and en-AU live (Chirp lists 30 voices in each). As with the voice (my call,
following the existing pattern): the admin picks the default accent in the console, beside Google's voice, stored
as `chat_voice.accent` and kept through voice and engine changes (null removes it); each person may pick their own
in Settings → Chat → Reply accent (kept in the browser, sent as `google_accent`). Order: the person's, the admin's,
`GOOGLE_TTS_LANGUAGE`, en-US. Only English sentences change.

## Approaches considered

1. **A third engine (picked).** The admin switches in the console without a redeploy, and Gemini-TTS stays
   configured for the Sinhala fallback.
2. **A value of `GOOGLE_TTS_MODEL`** (such as `chirp3-hd`). Smaller, but switching needs a redeploy and the Sinhala
   fallback needs both voices at once.

## Not in this change

- Chirp's streaming synthesis (gRPC): it could start audio before a sentence is fully made; untried.
- The older Chirp HD voices (three per language, letters for names).
- Any change to how documents are read.
