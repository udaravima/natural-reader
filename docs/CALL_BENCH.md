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

## Compare hearing engines and voices: the matrix

`scripts/bench_matrix.py` answers "which hearing and which voice?" for one chat model. It measures each stage once
and composes every pair, because Talk runs the stages one after another:

1. every caller clip through every hearing engine (text, word errors, seconds, network);
2. each engine's transcripts through the model as whole calls (timings; which calls each engine let succeed);
3. the replies of the best-heard conversation through every voice (seconds to make each piece, its length,
   network, the recording);
4. each pair's wait: end-of-turn silence + hearing + the model's first piece + voicing it, rated against the
   reference bands below.

```bash
.venv/bin/python scripts/bench_matrix.py --model gemini:gemini-3.1-flash-lite --model-rpm 14 --voice-rpm 9 \
    --hear whisper:small,whisper:small.en,google:projects/<p>/locations/<l>/recognizers/<id>,openai:whisper-1,openai:gpt-4o-mini-transcribe \
    --voice kokoro:af_heart,google:gemini-2.5-flash-tts:Kore,google:gemini-2.5-flash-lite-preview-tts:Kore,chirp:en-US-Chirp3-HD-Kore,chirp:en-IN-Chirp3-HD-Kore
```

| Engine | Means |
|---|---|
| `whisper:<id>` | a local model from the speech catalogue (`small`, `small.en`, the Sinhala and Tamil fine-tunes) |
| `google:<recognizer>` | Google Speech-to-Text v2 through a recognizer, as the app uses it |
| `openai:<model>[@<url>]` | the app's OpenAI-compatible engine. OpenAI's own API has `whisper-1` (Whisper large-v2), `gpt-4o-mini-transcribe` and `gpt-4o-transcribe`, but no `whisper-small`; a `whisper-small` you host behind the same API goes after the `@`. Key: `SPEECH_API_KEY`, else `INFERENCE_OPENAI_API_KEY` for OpenAI's API |
| `kokoro[:<voice>]`, `google[:<model>[:<voice>]]` | the app's two voices (Gemini-TTS models: `gemini-2.5-flash-tts`, `gemini-2.5-flash-lite-preview-tts`) |
| `chirp:<voice name>` | Google's Chirp 3 HD voices, such as `en-US-Chirp3-HD-Kore` or `en-IN-Chirp3-HD-Kore`: **not in the app yet**, benchmarked to see whether they should be |
| `chirp-stream:<voice name>` | the same voices over Cloud Text-to-Speech's StreamingSynthesize: the audio comes in pieces, so a sentence can start playing at its first piece (about 0.3 s, against 0.75 s for `chirp:` to make the whole sentence, on 20 sentences on 2026-11-18). Its time is when the audio could start and play through without a gap. Needs `google-cloud-texttospeech` in the benchmark's environment, not the app's: `uv pip install --python .venv/bin/python google-cloud-texttospeech` |

Output, in `bench-out/matrix-<scenario>-<time>/`:

- `report.md`: the pairs, quickest first, with their band; each stage's own table; the calls; the references.
- `results.xlsx` (sheets Summary, Pairs by turn, Hearing, Model, Voice, Calls, References) and one CSV per sheet.
- `recordings/caller/`: the caller's clips. `recordings/<voice>/`: every piece each voice said, and its greeting.
  `recordings/calls/<voice>/<call>.wav`: **each call as it would sound**, with the measured waits as silence,
  so you hear the pauses as a caller would.

### Round 2: fillers, keypad digits, the end of a turn, streaming hearing and cost

Design: [the round 2 spec](superpowers/specs/2026-11-17-call-bench-round2-design.md). Every matrix run now also:

- plays a **recorded filler** the moment the model's step ends with a lookup and no words (one or more lines per
  tool in the scenario's `[agent.fillers]`, the agent's `filler` for any other tool), and a **"still checking"**
  line (`still_checking`) when nothing has sounded for `--still-after` seconds (1.5) after it. Lines are made once
  per voice before the calls, so they cost no time on the call, and never repeat back to back;
- runs **Smart Turn v3.2** (Pipecat, BSD-2-Clause, 8 MB, downloaded from Hugging Face on first use) on every
  caller clip: after 0.2 s of silence it says whether the caller has finished, else the turn ends at 3 s. The
  report gives its wait beside the fixed silence, and the **cut-offs**: pauses where it would have talked over the
  caller. `--no-smart-turn` skips it;
- costs each pair per call, per 1,000 calls and per 1,000 minutes (sheets Cost and Prices), from
  `server/calls/prices.toml`: every price names its source, the date it was checked and whether it is the
  provider's own. An engine with no price shows as unknown, not free. **A call is one scripted call, greeting to
  goodbye, not a minute**: the report's "What one call is" table gives its caller turns, seconds of caller audio,
  characters the voice speaks, the model's tokens, and how long a call lasts with each voice. A call's length is
  computed per pair as the whole-call recording is built, and counts every wait, so a slower model makes calls
  longer and the price per 1,000 minutes smaller while a call costs the same: compare pairs per call. When the
  model failed turns of a hearing's conversation (an error, or nothing heard), that pair's model cost counts only
  the answered turns: the report marks it **≥** (a lower bound), lists it last with the failures, and gives the
  model's cost where every turn was answered. For the streaming engines it also says what hearing would cost if a
  phone line streamed the whole call, both sides and every wait, rather than only the caller's turns.

`--warm-model` sends one turn ("Hello.") with the scenario's instructions and tools just before the model stage,
for a local model: Ollama loads it and reads them once, as on a line already answering calls. Cold, the first turn
of a call on `ollama:gemma4:e4b` took 23.7 s to its first piece on a CPU (2026-11-18). The warm-up uses the calls'
own settings (no context size or keep-alive, as Talk sends by default): a different context size would make Ollama
load the model again. Keep-alive is Ollama's (5 minutes unless the server sets one).

`--facts-in-prompt` tells the agent the scenario's fixed facts in its instructions and leaves out the tool that
serves them (`[agent.facts]`: the tool, and the `data.json` table of topic = answer). A question about them then
takes one model request instead of two: on the hotel's questions call, the first piece came 1.6 s sooner on
`ollama:gemma4:e4b` (1.0 s against 2.65 s, median) and 2.9 s sooner on an overloaded Gemini (2026-11-18). The
facts' tool is then not expected to run, and their amounts count as told, not invented. Both `bench_call.py` and
`bench_matrix.py` take it.

`--keypad` plays the calls' `caller_keypad` lines: the caller keys the phone number or booking reference
(`[calls.keys]`) when the agent calls `collect_digits`, whose recorded instruction comes from `[agent.keypad]`.
Keying is taken as 1.0 s to start and 0.4 s a key, the hash included (`--keying 1.0,0.4`, an assumption); that
time is the caller's, not the agent's silence. The recordings play the keypad tones.

Two streaming hearing engines get the clip as it is spoken, then 0.2 s of silence; their time runs from the
end-of-turn decision to the final text:

| Engine | Means |
|---|---|
| `google-stream:<recognizer>[@<model>]` | Speech-to-Text v2 StreamingRecognize (gRPC only). Needs `google-cloud-speech` in the benchmark's environment, not the app's: `uv pip install --python .venv/bin/python google-cloud-speech`. Use the implicit recognizer and a model, such as `projects/<p>/locations/europe-west4/recognizers/_@chirp_2` or `.../locations/us/recognizers/_@chirp_3`; asia-southeast1 refused streaming in November 2026 |
| `openai-rt:<model>` | OpenAI's Realtime API in transcription mode, such as `openai-rt:gpt-live-transcribe`; the same key as `openai:` |

The report's pairs table then reads: first sound **without fillers** (round 1's way, the fixed silence), **with
fillers**, and **with fillers and the end-of-turn model**; **first answer** (when the reply itself starts) and
**dead air** (all the silence in a turn). The whole-call recordings use the last of these.

### Reference points for the wait

| Band | Up to | Basis |
|---|---|---|
| human-like | 0.5 s | People leave about 0.2 s between turns, most turn changes within -0.1 to 0.5 s ([Levinson & Torreira 2015](https://www.frontiersin.org/articles/10.3389/fpsyg.2015.00731/full); [Stivers et al. 2009](https://pubmed.ncbi.nlm.nih.gov/19553212/)) |
| natural | 0.8 s | The usual voice-agent target at the median: an industry rule of thumb ([example](https://www.twig.so/blog/voice-ai-agents-latency-budget-800ms)) |
| acceptable | 1.2 s | Rule of thumb; about 1 s is where a person's flow of thought breaks ([Nielsen](https://www.nngroup.com/articles/response-times-3-important-limits/)) |
| noticeable | 2 s | Rule of thumb: callers start talking over the agent |
| slow | above | Rule of thumb: callers wonder whether the line dropped |

[ITU-T G.114](https://www.itu.int/rec/T-REC-G.114) (0.15 s one-way, 0.4 s at most) is about the phone network's
own delay, not the agent's thinking, and applies only to a real phone line. Talk's end-of-turn silence (700 ms by
default) is part of the wait, so it uses most of the 0.8 s target before any engine starts.

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

- `scenario.toml`: `id`, `name`, a fixed `today`, the `[agent]` (name, greeting, filler, backstory, rules,
  `still_checking`, `[agent.fillers]` per tool, `[agent.keypad]` instructions, `[agent.facts]` for
  `--facts-in-prompt`) and `[[calls]]`, each with the
  caller's lines, `[calls.expect]`, and for `--keypad` the `caller_keypad` lines and `[calls.keys]`;
- `data.json`: the business's data, copied fresh into every call;
- `tools.py`: plain functions marked `@tool(description, json_schema)`, taking `(state, args)` and returning a
  dict; raise `ToolError` for a refusal the agent should pass on.

The hotel folder is the worked example. Keep real businesses' names and data out of the repo.
