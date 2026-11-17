# Call benchmark

Plays scripted phone calls to a voice agent through the app's own engines and tells you, per stage, where the
caller's wait goes and what the agent got wrong. Use it to decide **what to fine-tune** (the hearing model on names
and numbers, the chat model on looking things up, the voice) and **what to fix** in the pipeline.

The first scenario is a reservations line for an invented hotel, Lotus Bay Hotel in Colombo: six calls covering a
booking, a sold-out request, a cancellation, hotel questions, a spelled name with a phone number, and a complaint
that must go to a person. Design: [the spec](superpowers/specs/2026-11-17-call-simulation-design.md).

**What it is not (yet):** you can't call the hotel from the browser. Talk still talks to the document assistant;
tying a profile to a scenario is phase 2 of the spec.

## Run it

From the repo root, with the backend's `.env` (provider keys, `GOOGLE_APPLICATION_CREDENTIALS`, speech models).
No database and no running backend are needed.

```bash
# The model alone: the script's words go straight in, nothing is spoken (fast, cheap)
.venv/bin/python scripts/bench_call.py --model gemini:gemini-3.1-flash-lite --model-rpm 14

# The whole local chain: Whisper hears a synthesized caller, Kokoro speaks
.venv/bin/python scripts/bench_call.py --model gemini:gemini-3.1-flash-lite --model-rpm 14 \
    --hear whisper:small.en --voice kokoro:af_heart

# Google's hearing and voice
.venv/bin/python scripts/bench_call.py --model gemini:gemini-3.1-flash-lite --model-rpm 14 \
    --hear google:projects/<p>/locations/asia-southeast1/recognizers/<id> \
    --voice google:gemini-2.5-flash-tts:Kore --voice-rpm 9

# Two runs side by side
.venv/bin/python scripts/bench_call.py compare bench-out/hotel-20261117-1500 bench-out/hotel-20261117-1530
```

| Option | What it does |
|---|---|
| `--hear` | `text` (no hearing), `whisper:<model id>` (any model in the speech catalogue), `google:<recognizer>` |
| `--voice` | `none` (a sentence lasts 0.4 s a word), `kokoro[:<voice>]`, `google[:<model>[:<voice>]]` |
| `--caller-voice` | who reads the caller's lines when there's no recording (default `kokoro:bm_george`) |
| `--caller-audio DIR` | real recordings, `<call id>-<turn>.wav` (such as `book-deluxe-3.wav`); better than a synthetic caller for accents and names |
| `--calls`, `--repeat` | which calls, and how many times each (models vary; three repeats is a fair minimum) |
| `--model-rpm`, `--voice-rpm` | stay under a per-minute quota; the wait is left out of every timing |
| `--speak first` | voice only each reply's first piece: fewer cloud voice requests, no measure of gaps between sentences |
| `--silence-ms`, `--first-clause` | Talk's end-of-turn silence (700 ms) and opening cut (30 characters); default from the same env settings |

Output goes to `bench-out/<scenario>-<time>/`: `report.md`, `results.json`, and every clip, so you can listen to
what the caller said and how the agent sounded. Exit status 0 means every call passed.

## Read the report

**First sound after the caller stopped** is the number a caller feels. It is the chain Talk runs:

```
end-of-turn silence → hearing → model's first piece of text → voicing that piece
      0.70 s            (clip)      (sentence end, or a comma      (one request per
   (Talk waits for it)              past 30 characters)            piece)
```

Each cloud stage is split into **connect** (TCP and TLS on a new connection), **upload**, **wait** (the service's own
time plus one round trip) and **download**. The model's row also shows how long opening connections took.

The calls are scored; each failed check points at something:

| Check | Fails when | Points at |
|---|---|---|
| `tools` | a lookup the call needs never ran, or ran only with errors | the model's tool discipline |
| `money` | the agent said an amount no tool returned | invented prices: the worst failure on a sales line |
| `says` | the caller never heard the price or the reference | the answer, or hearing that lost a number |
| `booking` | the booking is missing or has the wrong name, phone, room or dates | hearing names and digits, or the model |
| `never_tools`, `transferred`, `cancelled` | it booked on a complaint, didn't hand over, didn't cancel | the model following the rules |
| `answered` | a turn ended in silence after a tool | an app fix: ask the model again for the answer |

The **Hearing** section lists word error rate per turn and the script's words the engine missed most: those are
what phrase hints or a fine-tune would target.

## Network

`scripts/voice_network.py` measures, from the machine it runs on, how far each configured cloud service is, and
with `--live` what a kept-open connection would save on real requests. Run it on the server and on a laptop: the
network's share depends on where the code runs.

```bash
.venv/bin/python scripts/voice_network.py
.venv/bin/python scripts/voice_network.py --live --model gemini:gemini-3.1-flash-lite --clip question.wav
```

## Write another scenario

A scenario is a folder (in `server/calls/scenarios/`, or anywhere, passed as `--scenario PATH`):

- `scenario.toml`: `id`, `name`, a fixed `today`, the `[agent]` (name, greeting, filler, backstory, rules) and
  `[[calls]]`, each with the caller's lines and `[calls.expect]`;
- `data.json`: the business's data, copied fresh into every call;
- `tools.py`: plain functions marked `@tool(description, json_schema)`, taking `(state, args)` and returning a
  dict; raise `ToolError` for a refusal the agent should pass on.

The hotel folder is the worked example. Keep real businesses' names and data out of the repo.
