# Call simulation: a hotel reservations line, benchmarked stage by stage

**Status:** phase 1 built on `feat/call-simulation`. Phase 2 (calling the agent from Talk) is designed here and not built.

## Why

The owner wants to benchmark voice agents on a realistic call, to find out **what to fine-tune** (the hearing
model on names and numbers, the chat model on tool discipline, the voice) and **what to fix** in the pipeline (slow
stages, waits, network). Talk's per-round timings (v2.14.0) show one person's rounds. A benchmark needs the same
call, played the same way, against different engines, with a score. The owner's work project runs a call agent
(Google ADK); this is the same kind of line, built fresh for an open-source repo. No code or text comes from that
project.

## Goals

- A scenario pack: a reservations agent for an invented hotel ("Lotus Bay Hotel, Colombo"), its tools, the hotel's
  data, and scripted calls with what each must achieve.
- Pretend tools that keep state for one call: a booking made in turn 2 can be found and cancelled in turn 4.
- A call agent that answers only from those tools, with rules written for the ear.
- `scripts/bench_call.py`: plays every scripted call through the app's own engines (hearing, chat model, voice),
  times each stage the way Talk would, splits cloud calls into connection set-up, upload, Google's own time and
  download, and scores each call.
- `scripts/voice_network.py`: how far each cloud service is from the machine it runs on, and what a kept-open
  connection saves.

## Non-goals (phase 1)

- Calling the hotel from the browser. Phase 2.
- Real phone lines (8 kHz μ-law, server-side turn detection). The work project covers that.
- Fixing what the benchmark finds. Each fix is its own change, measured with this tool before and after.

## Anti-goals

- A benchmark that measures something other than the app. Every stage calls the app's own engine code
  (`server/speech/*`, `server/chat_voice.py`, `server/llm/router.py`), so a fix in the app shows up in the numbers.
- Any real hotel's name or data in the repo (open source; deployers can write their own pack).

## Decisions

| Question | Decision | Why |
|---|---|---|
| Where the agent runs | `server/calls/`, its own small turn loop over the app's model router; not the document chat | The chat's prompt carries the document steps and its tools are document and web tools. A call agent needs a short prompt, its own tools and none of the document rules |
| Scenario format | A folder per scenario: `scenario.toml` (agent, calls), `data.json` (the hotel), `tools.py` (what each tool does) | TOML is in the standard library and holds long text well; tool logic is a few lines of Python, so a new scenario is three files and no change elsewhere |
| The greeting | Fixed text in the scenario, told to the model as already said | It would be a recording in a real line; the model must know it was said so it doesn't greet twice. Starting the history with an assistant message breaks strict chat templates |
| "Today" | Fixed per scenario (`today = "2026-12-01"`) | "From the twelfth" must resolve the same way on every run |
| Hearing input | A synthesized caller (Kokoro voice unlike the agent's), or real recordings from `--caller-audio` | Recordings are more realistic for accents and names; synthesis makes the benchmark run anywhere |
| First sound | end-of-turn silence (Talk's `silence_ms`, 700 ms) + hearing + time to the first speakable piece + voicing that piece | The same chain Talk runs; the first piece is cut with Talk's splitter rules (sentence end, or a comma past 30 characters) |
| Waits inside a reply | Simulated playback: each sentence's audio starts when its text is ready, the voice has made it (two at a time, like Talk) and the previous one has finished | Shows whether a slow voice leaves silence between sentences |
| Scoring | Per call: tools that must run, tools that must not, words that must be said (any of several spellings), money amounts no tool returned (invented), the booking or cancellation in the final state, a transfer; per turn: word error rate of the hearing | Each check maps to something to fine-tune or fix |

## Phase 2 (designed, not built): call the agent from Talk

- A profile can be tied to a scenario. Its chats use the call agent and the scenario's tools instead of the
  document chat, with fresh hotel data per chat.
- Talk plays the greeting from a recording made when the profile is saved, and a filler line ("One moment while I
  check that for you") the moment a tool starts.
- Needs: a column on profiles, a picker in Admin → Chat → Assistant, a call-agent endpoint that streams like the
  chat, and the per-chat state. Until it ships, the hotel can be benchmarked only with the script.

## Verification

- Unit tests: scenario loading and its errors, every hotel tool (including state across calls and bad input), the
  sentence splitter against Talk's rules, scoring, the playback model, the turn loop with a scripted fake model.
- The benchmark run end to end on this machine with a cloud chat model, with text input, with local hearing and
  voice, and with Google hearing and voice.
