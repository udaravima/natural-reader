# Phone lines

A **phone line** lets someone ring a number and talk to an assistant that answers
from one project's documents. The caller needs nothing but a phone. It is the same
conversation as **Talk** in the chat (listen, think, speak, and a caller who talks
over the answer cuts it off), run on the server and fed by your phone system.

- [What a line is](#what-a-line-is)
- [What works today](#what-works-today)
- [Setting one up](#setting-one-up)
- [The Asterisk side](#the-asterisk-side)
- [Settings](#settings)
- [The line's model stays loaded](#the-lines-model-stays-loaded)
- [How a line finds its answers](#how-a-line-finds-its-answers)
- [A call's first answer](#a-calls-first-answer)
- [Limits](#limits)
- [Privacy and security](#privacy-and-security)
- [Trying it without a phone system](#trying-it-without-a-phone-system)
- [Troubleshooting](#troubleshooting)

People's guide to the console section:
[USER_GUIDE.md § Phone lines](USER_GUIDE.md#phone-lines-calls-answered-from-a-project).

## What a line is

A line is a record an admin makes in **Admin → Phone lines**. It names:

- **who answers**: an assistant profile (or the default one);
- **what it knows**: one project, whose documents the assistant reads and searches
  for each caller's question;
- **the model** that writes the answers, and **the voice** that speaks them;
- **a welcome message**, played first (the default says the caller is talking to an
  automated assistant; keep that in anything you replace it with);
- **limits**: the longest call, calls at once and tokens a day.

Behind the scenes each line has a **service account** of its own (it never signs in
and never appears in **People**). That account is a *reader* of the line's project,
which is how a line is allowed to see the documents, and how it is taken away: a
project maintainer or owner removes that account ("Phone line: <name>") from the project's member list, and the
line's next call hears that it is closed. A line has an **id** (a UUID). The phone
system sends it with every call, and that is how a call finds its line.

A call is a chat. Each call writes one chat, owned by the line's service account,
holding the welcome, what the caller said and what the assistant answered. The
console shows it as the call's transcript.

## What works today

- **English only.** Phase 1 hears and speaks English. Other languages, and keypad
  menus, are later phases.
- **One way in: Asterisk's AudioSocket.** The app listens on a TCP port and your phone
  system connects to it for each call. Nothing else (SIP accounts, a browser test
  call) is built yet.
- **The app sends nothing to Asterisk.** The console shows three dialplan lines for
  each line; an admin pastes them into the phone system's configuration. Nothing in
  the app changes the phone system.
- **Everything about a line is made in the console**: create, edit, switch on and off,
  hear the welcome, read and delete a call and its transcript, delete the line. No
  command line is needed once the listener is on.
- **Not yet walked on a real phone line.** The call loop, the AudioSocket adapter and
  the console are tested, and a recorded question has been put through the whole
  path with the [fake phone system](#trying-it-without-a-phone-system). A call from a
  real handset into a real PBX is the next step, so this page states no timings for
  real lines: measure the first sound on yours (the console lists the slowest one for
  each call).

## Setting one up

1. **Turn the listener on.** In the backend's environment (`.env`) set
   `PHONE_AUDIOSOCKET_LISTEN` and `PHONE_AUDIOSOCKET_ALLOW` (see [Settings](#settings)),
   for example `PHONE_AUDIOSOCKET_LISTEN=192.0.2.10:9092` (this machine's LAN address,
   which faces the phone system) and `PHONE_AUDIOSOCKET_ALLOW=192.0.2.20` (the phone
   system's), plus a firewall rule that allows only that address to the port. Then restart the backend (with the container: `./startup.sh rebuild`, because the
   container reads `.env` only when it is created). The console says
   "Listening for your phone system at …" when it worked.
2. **Make sure a project exists** that you are a maintainer or owner of, with the
   documents in it that callers should hear about. A line can only be pointed at a
   project you maintain or own, and every caller of the line hears answers drawn from
   it, so choose one that is meant for callers.
3. **Admin → Phone lines → New line.** Fill in the name, who answers, what it knows,
   the model and the voice; leave the other fields to their defaults. **Save line.**
   The first save renders the line's recorded phrases in its voice (the welcome, "busy",
   "closed", "goodbye" and a few more), so a caller never waits for a voice.
4. **Hear the welcome**, and change it if you like (save again to hear the new one).
5. **Copy the dialplan lines** the console shows under *Connect your phone system* and
   put them in your Asterisk configuration (below).
6. **Tick *Line is on*** and save. A new line starts off, and a line with no project
   can't be switched on.
7. **Ring it.** The call appears under **Calls** in the line's editor, with its
   transcript.

## The Asterisk side

The console builds these for the line (with the real id and your backend's address;
the extension 810 is an example, so pick any free one):

```
exten => 810,1,Answer()
 same => n,AudioSocket(6f1c2a4e-0b7d-4c1e-9a52-3d8e7b6f9a10,192.0.2.10:9092)
 same => n,Hangup()
```

Put them in the dialplan context your callers reach (for example in
`extensions.conf`), reload the dialplan (`dialplan reload` at the Asterisk console),
and send the number you want to that extension. Asterisk needs the `res_audiosocket`
and `app_audiosocket` modules (`module show like audiosocket`).

The address in the snippet is the one Asterisk *dials*, so it must be reachable from
the PBX. It is `PHONE_PBX_ADDRESS` when you set that (used exactly as written: the app doesn't check
that it is reachable or is this machine), and otherwise the listen address (a wildcard such as
`0.0.0.0` can't be dialled, so with one you must set `PHONE_PBX_ADDRESS`; the console says so
instead of showing a broken snippet).

**How the protocol works**, for anyone debugging with a packet capture: a frame is one
byte of kind, two bytes of length (big endian), then the payload. Asterisk's first
frame is kind `0x01` with the 16-byte line id. After that audio is kind `0x10`, signed
16-bit little-endian mono at 8 kHz, 320 bytes for every 20 ms, both ways. Kind `0x00`
means hang up and `0xFF` an error. `0x03` is a key press (Asterisk sends it; phase 1
ignores it, and the app never sends one).

**Asterisk drops a call after 2 seconds with no audio from the app.** The app therefore
sends a frame every 20 ms for the whole call, silence included, from the moment the call
connects: while it checks the line and opens the call, the caller hears silence, then the
welcome. Those checks are given at most 3 seconds; past that the caller hears "busy" (or,
if this server process hasn't loaded the line's phrases yet, just the hang-up).

## Settings

Set in the backend's environment. Every variable has a safe default. A number that can't be
read or is out of range, and an allow-list entry that isn't an IP address, is logged as a
warning and replaced by the default or skipped; a listen address that can't be used turns
the listener off (the log says why).

| Variable | Default | Range | Meaning |
|---|---|---|---|
| `PHONE_AUDIOSOCKET_LISTEN` | empty (**the listener is off**) | `host:port` | Where the app listens for the phone system. Port `0` picks a free one (tests). **Listen on the LAN address itself** (`192.0.2.10:9092`, a fixed address) on bare metal and with the project's compose file, whose backend uses the host's network: `0.0.0.0` there would listen on every interface, the internet-facing one included. `0.0.0.0:9092` is for a *bridged* container, with the published port bound to the LAN address (`-p 192.0.2.10:9092:9092`). Never `[::]:9092`: it is IPv6 only, and an IPv4 phone system can't connect to it. Add a host firewall rule that allows only the phone system to the port, as a second layer. Any listen address that is not loopback needs `PHONE_AUDIOSOCKET_ALLOW`, or the listener stays off and the log says why. |
| `PHONE_AUDIOSOCKET_ALLOW` | empty | comma-separated IP addresses | The only peers that may connect, e.g. `192.0.2.20`. A peer not listed is closed before a byte is read, a loopback peer included once the list is set. (To try the fake PBX on such a server you may list `127.0.0.1`, but only with host networking or bare metal: behind a container port forwarder every peer can look like loopback, and listing it admits anyone; see [Trying it without a phone system](#trying-it-without-a-phone-system).) With the list empty, only a loopback peer is let in. A word that isn't an IP address is skipped, with a warning. |
| `PHONE_PBX_ADDRESS` | the listen address (unset when that is a wildcard) | `host:port` | What the console writes into the dialplan lines: where the phone system dials. Used exactly as written. It may differ from the listen address, for example behind a published container port. |
| `PHONE_MAX_CALLS` | `2` | 1 to 20 | Calls at once across all lines. Each line has its own cap too; a call over either hears "busy". |
| `PHONE_IDLE_S` | `30` | 1 to 600, **seconds** | Seconds of a silent caller before the assistant asks "Are you still there?". After a second period of silence it says goodbye and hangs up. |
| `PHONE_CHECKING_S` | `4` | 0.5 to 60, **seconds** | How long a tool (a document search, say) may run before the caller hears one "Still checking" phrase. |
| `PHONE_MAX_UTTERANCE_S` | `30` | 5 to 120, **seconds** | The longest stretch of unbroken caller sound heard as one clip; longer sound is cut into clips this long. With constant background sound at talking level, such as a television next to the phone, each of the first two stretches pauses the reply for up to this long; once both are judged noise the call is in **babble mode** and the reply plays on through the sound ([how, and what it costs](#troubleshooting)). Lowering it to 10–15 shortens that silent start. |
| `PHONE_MODEL_KEEP_ALIVE` | `15m` | a duration (`15m`, `1h`, or **seconds**: `900`); `0` turns it off | How long Ollama keeps a line's model loaded after the phone last asked for it. While the listener is on the app asks again every 2 minutes, so the model of a line that is on stays loaded, and this is how long it lingers once the line is off or the backend stops. Capped by `INFERENCE_KEEP_ALIVE_MAX` (30 minutes by default). Under 4 minutes, after the cap, the refresher stays off ([The line's model stays loaded](#the-lines-model-stays-loaded)). `0` also turns off the read of a line's prompt as a call connects ([A call's first answer](#a-calls-first-answer)). A value that isn't a duration is logged and replaced by the default. |

How the caller is *heard* is not repeated here. The voice-activity thresholds, the
silence that ends a turn (700 ms), the pre-roll and the noise limits are the **speech
window's** settings (`SPEECH_WINDOW_*`,
[DEPLOYMENT.md § The speech window](DEPLOYMENT.md#the-speech-window)), shared with
Talk, so one change tunes both. The recognizer is the deployment's speech engine for
English (Whisper on the server unless you chose another).

**Choosing a voice.** A line may use any voice engine the deployment has set up:
Kokoro on the server (English voices), and, when Google is configured, Chirp 3 HD or
Gemini-TTS, which also take an English accent (American, British, Indian or
Australian). The console's **Voice** control lists what is available; an engine that
was set up and later removed stays on the saved line, marked "not offered now", until
you pick another. A phone call is narrowband (8 kHz), so voices that are good in the
chat sound thinner here; listen to the welcome before you ship a line.

## The line's model stays loaded

Ollama unloads a model that has had no request for 5 minutes, and loading it again is
slow: on a CPU-only machine gemma4:e4b took about 30 seconds from disk, and about 6 when
the file was still in the page cache. A caller would hear the welcome, ask, and then wait
that long for the first answer. So while the listener is on, the app keeps the models of
its lines loaded:

- **When the listener starts, and every 2 minutes after,** it asks Ollama to load the
  model of each line that would take a call: on, still a member of its project, with a
  model the deployment still offers and the server still lists. The request loads the
  model without generating anything: no tokens, nothing against a budget, no chat. Lines
  that share a model ask once.
- **When a call is let in,** it starts reading that line's prompt, which loads the model
  too, without waiting for the answer ([A call's first answer](#a-calls-first-answer)). If
  the model had been pushed out, loading it overlaps the welcome and the caller's first
  question instead of following them.
- **Every turn of a call** carries the same keep-alive, so a call keeps its model loaded.

A load asks for exactly what the call's turns will, the context size above all, because
Ollama reloads a model when a request changes its options. Only a model on an Ollama you
host gets a request. A hosted model gets none: Gemini, an OpenAI-compatible API, an Ollama
cloud model (a name ending in `-cloud` or `:cloud`, even on your own server) and
ollama.com. There is nothing to load there, and a request could cost money.

**What it costs: the model's memory, all the time a line is on.** For example Ollama
reports gemma4:e4b at about 2 GB (2,093,366,638 bytes, on a CPU-only machine) with the
16,384-token context a phone turn uses by default (Ollama 0.40.13). `curl
<OLLAMA_URL>/api/ps` shows what each loaded model holds on your machine. After the line is
switched off, the model is freed `PHONE_MODEL_KEEP_ALIVE` after its last load or turn.

**The keep-alive and its cap.** `PHONE_MODEL_KEEP_ALIVE` (15 minutes by default) is capped
by `INFERENCE_KEEP_ALIVE_MAX` (30 minutes by default), the same ceiling a chat's own
keep-alive has, so a deployment that frees models quickly still does. A model loaded at
one refresh must still be loaded at the next, with time to spare, so a keep-alive under 4
minutes after the cap leaves the refresher off, with a warning when the listener starts:
calls still load their model as they connect, so the wait overlaps the welcome rather than
going away. A cap of 0 (`INFERENCE_KEEP_ALIVE_MAX=0`, "unload after each use") means the
lines' models aren't kept at all: no refresher (with a warning naming the cap), no load
or read when a call connects, and no keep-alive on a call's turns, which would otherwise unload
the model after every answer.

**`PHONE_MODEL_KEEP_ALIVE=0` turns all of it off,** the read of the line's prompt at
connect included, and restores the behaviour from before this feature exactly: no loads
or reads, no keep-alive on a
call's turns, and Ollama keeps the model as it would for any chat. It is the switch to
reach for if the loading itself ever misbehaves.

**It doesn't fight anyone for memory.** At each refresh the app looks at what Ollama holds
(`/api/ps`) and, for each line's model:

- **not loaded** (Ollama restarted, someone ran `ollama stop`, it crashed, or its own
  keep-alive ran out): it loads it. Refreshing is for exactly that, so it never gives up;
- **loaded at a different context size** than a phone turn's: someone else is using it at
  their size (a chat whose Settings ask for another context size, say). The app leaves it
  alone, and says so once in the log (`… is loaded at another context size …: it is left
  alone`). The line's next call reloads it at its own size, which costs that call a
  reload, overlapped by the welcome. The phone's size is the one it asks for, or the one
  Ollama reported right after the phone's own load: an Ollama may report another number
  than it was asked for (with `OLLAMA_NUM_PARALLEL` above 1, say), and the app never
  mistakes its own copy for someone else's;
- **loaded at the phone's size:** it asks again, which restarts the keep-alive.

While **any call is live**, the refresher still refreshes every model already loaded at the
phone's size (that only restarts its timer and takes no memory), but it loads a model that
isn't in memory, or one whose server can't say, only if a live call uses it: loading
another line's model then could push the live call's model out of memory. Those wait for a
refresh with no call live.

**When the models don't fit together.** If two lines that are on use different models that
don't fit in memory at once, the refresh that loads both ends with one of them already
gone, pushed out by the other. The app then stops refreshing the pushed one until the
backend restarts, and logs one warning naming both (`Phone lines: model … was pushed out
of memory by …, loaded in the same refresh`). That is the only thing that stops a
refresh. What follows:

- one model stays loaded and refreshed;
- calls on the other line load their model as they connect (the welcome covers part of
  the wait), which pushes the first one out for the length of the call;
- the refresher waits for calls to end, then loads the first one again;
- a restart of the backend tries both again.

To keep both loaded, give Ollama the memory, or put the lines on one model.

**When Ollama or the database is down,** the app logs one warning for the outage and keeps
trying every 2 minutes; when it's back, it says nothing more. A model the server refuses (a
404, say, for a model deleted from it) gets one warning of its own. A load never holds up
or ends a call.

## How a line finds its answers

**Before the model answers a caller, the app searches the line's project with the
caller's words,** and hands what it finds to the model as the result of a document
search made on its behalf. The search tool stays offered, so the model can still
search again in its own words when that first search doesn't answer the question.

**A call is offered the search and the clock, nothing else.** The model on a call can
search the line's documents and ask the time (for "are you open right now?"). The
chat's other document tools, reading pages, extracting from many pages and a
document's full outline, are not offered on a call: a caller has no use for them, and
every question is searched first anyway. Their descriptions were a large part of what
a call's first answer reads (the four document and clock tools took 1,027 of a line's
2,145 prompt tokens: the search 343, page reading 201, extraction 385, the clock 98),
and leaving them out, with the rules' sentences about them, takes about 640 tokens off
every call's prompt. When the project's list of documents is too long to show every
outline, it says so on a call without offering the outline tool. The chat and Talk
still offer every tool.

**Why.** A call runs the model with thinking off, to keep the caller's wait short. A
small model such as gemma4:e4b then searches reliably only for the first thing a caller
asks. Once the call holds one plain exchange (a "Good morning" answered without looking
anything up, as a greeting should be), it follows that pattern. It offers to check
instead of checking ("Would you like me to look that up?"), or it answers from a heading
in the project's list of documents, which names a topic but doesn't say what it is. In
one test call the line gave no fact from its document in 10 turns. Handed the app's
search, the same model answered all 60 replayed questions right.

**When the search keeps nothing.** A follow-up such as "Yeah, sure." names no topic, so
nothing it matches clears the score floor. Then the model's copy of the caller's words
gets one sentence added: look the answer up with a tool in this turn, because what was
said earlier in the call is not a source. With it the model searched 5 times in 5, and
without it once. The sentence is added only when the model is offered the document
tools, and it is never stored: the transcript and the chat hold only what the caller
said.

**What it costs.** Whenever the search keeps passages, the model reads them before it
answers, so the first word comes about 3 seconds later than for a reply that looks
nothing up (gemma4:e4b on a CPU-only machine). On that machine a reply that looked
nothing up was first heard about 3 to 5 seconds after the caller stopped speaking, so a
found answer starts roughly 6 to 8 seconds after (an estimate from those two
measurements). The search itself takes about 20 ms. A greeting can clear the score floor
too ("Good morning" did, just, on a one-page document), so it may wait the same 3
seconds longer. When the caller's words are a question (the recognizer wrote a "?"), the
caller hears the filler phrase about 1.5 seconds after they stop, while the model reads,
and "Still checking" after `PHONE_CHECKING_S`, as for a tool. Other words get no filler,
so "Good morning" never hears "Let me check". The filler depends on that "?": a
recognizer that writes no punctuation, such as a Google recognizer whose punctuation is
turned off in Google's console, never plays it, and its callers wait through those
seconds in silence.

**The first answer of a call can be slower still,** when Ollama doesn't hold the line's
prompt yet: see [A call's first answer](#a-calls-first-answer).

**Turning it off.** `CHAT_PREFETCH=off` turns the search off for phone lines too; the
model is then left to search for itself, with the added sentence on every turn. The
default, `auto`, searches on every phone turn. In the chat, `auto` still leaves a model
that has the document tools to search for itself. `CHAT_PREFETCH_MIN_SCORE` (0.6) and
`CHAT_PREFETCH_K` (4) set what the search keeps, for phone lines as for the chat (see
`.env.example`).

## A call's first answer

**What the model reads first.** Before it writes a word, the model reads the line's whole
prompt: the profile and rules, the list of the project's documents, the tools'
descriptions, then the caller's words and the app's search. Ollama keeps what it has
read, so each later question of a call reads only what is new: about 240 tokens with the
app's search, about 3 seconds for gemma4:e4b on a CPU-only machine. The first question
of a call whose prompt Ollama doesn't hold yet reads all of it. For a line on a
one-document project that is about 1,500 tokens, about 18 seconds on the same machine
(about 2,150 tokens and 25 to 32 seconds while calls were also offered the page and
extract tools). It happens on the first call after the model loads, after the line's
project, profile or documents change, and on the first call of a day (the rules carry
the date).

**Reading the prompt while the welcome plays.** So when a call is let in, the app asks
Ollama to read the line's prompt at once: everything the first question will send before
the caller's words, built by the same code that builds the question's request. The
caller hears the welcome and asks while Ollama reads, and the first answer then reads
only the caller's words and the search. Measured with the question sent 9 seconds after
the call connected (a 4.3-second welcome, a 3-second question, the 0.7-second pause and
recognition), the model's first words came 9 to 11 seconds after the question was sent,
against 17 to 19 seconds without the read. The read itself took about 15 seconds, so it
wasn't finished when the question came, and the answer waited for the rest of it; a
longer welcome hides more. While the answer waits, a caller whose words are a question
hears "Let me check" and, after `PHONE_CHECKING_S`, "Still checking", then silence until
the answer. The read is not a turn: nothing is billed, stored or shown,
and the one word it generates is thrown away. If the call ends first, the read is given
up. It loads the model too, with the same settings as the call's turns.

**When it helps less.**

- The reuse was measured on gemma4:e4b, whose prompt puts the tools before the
  conversation. A model whose chat template puts the tool list inside the last user
  message (Llama 3.x, read from its template, not run) reuses only the system message:
  its tools are read again with the question.
- Ollama answers one request at a time per slot, and a slot holds the prompt it read
  last. A chat on the same model, or a call on another line, between the read and the
  question puts another prompt there. The Ollama measured here (0.40.13, gemma4:e4b)
  also keeps earlier prompts in memory: the same prompt, sent again after another had
  pushed it out, came back in a fifth of a second. A read pushed out and then followed
  by the question was not measured; an Ollama that keeps no earlier prompts reads the
  line's prompt again.
- With more than one parallel slot (`OLLAMA_NUM_PARALLEL` above 1), a question that comes
  while the read is still running may go to another slot and not reuse it, and the two
  then share the CPU, so the first answer may come later than with no read at all (not
  verified).
- The read uses the CPU while the welcome plays, beside the recognizer that hears the
  caller's question. On a busy CPU-only machine the question's transcript may come a
  little later (not measured).
- Only a model on an Ollama you host is read ahead, as for loading.
  `PHONE_MODEL_KEEP_ALIVE=0` turns the read off with the rest.

**In the log.** Each call's read is one line, `phone call <id>: its line's prompt was read
ahead in 14.8 s`. A read that fails is one warning per outage (`phone line <id>: model
<model> didn't read the line's prompt ahead of the first question (...)`), and the first
answer then reads the prompt itself.

## Limits

| Limit | Where it is set | Default | What happens at the limit |
|---|---|---|---|
| Longest call | the line, **Longest call (minutes)**, 1 to 60 | 10 minutes | the call ends, recorded as "reached the time limit" |
| Calls at once on a line | the line, **Calls at once**, 1 to 20 | 2 | the next caller hears "busy" |
| Calls at once, all lines | `PHONE_MAX_CALLS` | 2 | the next caller hears "busy" |
| Tokens a day | the line, **Tokens a day**. Empty: the server's default (`INFERENCE_DAILY_TOKEN_BUDGET`). 0: no limit | the server's (none when that is unset or 0) | the line is closed until the next day (recorded "line's token budget was used up") |

The counters live in the backend process. With `WORKERS` above 1, **each worker counts
its own calls**, so the real ceiling is the cap times the number of workers. The
listener binds with `reuse_port`, so the workers share one port; that also means a
*second* backend process started under the same user, on the same address, shares the
calls quietly instead of failing with "address in use". If a port seems to be
answering by two programs, look for a leftover process.

## Privacy and security

- **The socket has no password.** Anyone who can reach the port can start a call to a
  line whose id they know. The defences are: the listener is off unless configured;
  an allow list of peer addresses, checked before anything is read; and a refusal to
  listen on any address that is not loopback without one. The line's id is not a
  defence: it is a routing key, not a secret (anyone who can reach the port can already
  dial the number). Keep the port off the internet: listen on the LAN address that
  faces the phone system (or publish a bridged container's port on it), and add a host
  firewall rule that allows only the phone system.
- **Publishing a project is an explicit act.** Only a project's maintainer or owner can
  point a line at it; the console says in words that *anyone who calls this line hears
  answers drawn from this project*; the project's member list shows the line's account, named "Phone line: <name>"; and
  removing that member closes the line from its next call.
- **What is kept:** each call's transcript (the chat), for as long as the line exists,
  and deletable one call at a time (**Delete call**) or all at once (**Delete line**).
  No audio is stored, and no caller number: the phone system does not send it in phase 1.
- **The logs** hold ids, durations and end reasons, never what anyone said or any
  document text.
- **Spend is bounded** by the two call caps and the longest call, and by the tokens a day when a token budget
  applies. A line's **Tokens a day** of 0 means no limit, and so does an empty one on a server with no daily
  budget.
- **The line's account** can't sign in, is never listed under **People**, and can't be
  edited there: its limits are the line's.

## Trying it without a phone system

`scripts/phone_fake_pbx.py` connects to the listener the way Asterisk does: it sends the
line's id, streams WAV files as a caller, records everything the app says back, and
prints how long the first sound of each answer took after the question ended. It needs
no Asterisk and no phone, so it is the way to check a new setup, a model or a voice.

```bash
# in the backend's environment: PHONE_AUDIOSOCKET_LISTEN=127.0.0.1:9092 (loopback needs no allow list)
python scripts/phone_fake_pbx.py <line id> server/tests/fixtures/phone/question1.wav \
    server/tests/fixtures/phone/question2.wav --address 127.0.0.1:9092 --out ~/phone-calls
```

Once `PHONE_AUDIOSOCKET_ALLOW` is set, a loopback peer is dropped unless it is listed, so
to try the fake PBX on such a server add `127.0.0.1` to the list. That is safe only with
host networking or bare metal: behind a port forwarder, "loopback" is the forwarder, and
listing it admits everyone who reaches the published port.

- `--address host:port`: the listener (default `127.0.0.1:9092`; a real PBX's address is always
  given with this flag).
- `--out DIR`: where `phone-call-<time>.wav` (everything the app sent, 8 kHz mono, so you
  can listen to the greeting and the answers) is written. Default: the current folder,
  so give it a folder outside the repository (`.gitignore` also ignores `phone-call-*.wav`).
  Per question it prints the first-sound time and the seconds of sound the answer held.
- The WAVs are played in order, each after the previous answer has ended; any sample rate
  is converted to 8 kHz. Between them (and before the first, until the greeting has ended)
  it sends silence, as Asterisk does, because the app needs that silence to know a
  question is over.
- "The greeting ended" means what comes back went quiet after sound:
  `--quiet-s` (default 1.5 s of quiet) and `--sound-rms` (default 100 of 32768). On a slow
  machine a voice pauses between sentences, and a short `--quiet-s` mistakes a pause for
  the end; raise it. `--greeting-timeout` (30 s) and `--answer-timeout` (120 s) are the waits.
- It hangs up (`0x00`) after the last answer, and handles the app hanging up or sending
  an error first. Exit code 0: every question was answered; 1: one wasn't; 2: no call
  could be made.

The automated version is `server/tests/test_phone_e2e.py`: it starts the real service
on a scratch database, runs this script, and checks the greeting, the answer audio and
the call's record. It is skipped unless `PHONE_E2E_MODEL` names a model (for example
`PHONE_E2E_MODEL=ollama:gemma4:e4b`); see [TESTING.md](TESTING.md).

## Troubleshooting

**The call connects and drops about 2 seconds later, with nothing heard.** Asterisk hangs
up a call that gets no audio from the app for 2 seconds. Either the app never answered
(the line id is unknown, so the app hung up at once and logged "no such line"; the
listener refused the peer; or the backend is stopped), or its audio stopped
(an overloaded server: Kokoro, Whisper and the model share the CPU). Read the backend's
log first. A line that is switched off or has lost its project still plays its "closed"
phrase, so silence is a different problem.

**The allow list, and the address to put in it.** The backend logs
`audiosocket: dropped a peer <address> that is not allowed` for a refusal, and
`audiosocket <address>: call for line <id>` for a call that got in. Ring once and read
the address the backend actually saw. Then check that it is your phone system's.
Behind a container's port forwarder (a bridged container, or a proxy in front), the
backend may see every peer as the gateway or loopback address. Listing *that* address
admits anyone who can reach the published port. In that setup make the forwarder pass
the real address, or put the filter in front of the port (a firewall rule on the host
that allows only the phone system), and publish the port on the LAN interface only
(`-p 192.0.2.10:9092:9092`, not `-p 9092:9092`), with the app listening on `0.0.0.0:9092`
inside the container. With the project's own compose file the backend uses the host's
network, so it sees the real address, and should listen on the LAN address itself.

**The PBX can't connect at all.** The listen address must be this machine's own, fixed (a
static address or a DHCP reservation) and already up when the backend starts: if it isn't
(yet), the backend logs `The phone listener is off: it can't listen on <address> (...)`
and the listener stays off until the next restart. Check the listener: the console says
"The phone listener is off" when `PHONE_AUDIOSOCKET_LISTEN` is empty, malformed, or a
non-loopback address with no allow list (the log has the reason, at start). If it is on,
look at the address: an IPv4 one such as `192.0.2.10:9092` works for an IPv4 phone system;
`[::]:9092` does not (IPv6 only). Then check the firewall and, from the PBX, `nc -vz 192.0.2.10 9092`.

**Callers hear "closed".** A switched-on line plays its "closed" phrase and hangs up. The
causes, in the order the app checks them, with what the console shows for each:

| Cause | The list shows | The call list says |
|---|---|---|
| The line is **off** (never switched on, or switched off) | **Off** | "line was closed" |
| It has **no project** (the project was deleted, or removed from the line) | **Closed: no project** (amber) | "line was closed" |
| It **lost access** to its project: a maintainer removed the line's account from the project's members. **Give it access again** in the editor puts it back (only a maintainer or owner of that project can) | **Closed: no access** (amber), and the editor says "No access to <project>" | "line was closed" |
| Its **token budget** for the day is spent (**Tokens a day**, or the deployment's budget; it resets with the day) | **Closed: token budget used up** (amber) | "line's token budget was used up" |
| Its **model was withdrawn**: the environment no longer allows it, or the console's chat list no longer offers it | **Closed: model withdrawn** (amber) | "line was closed" |
| Its **model's provider no longer has it** | **On** (the list doesn't ask the provider) | "line was closed" |

The list reads these with the call's own checks, except the provider's model list, which
needs the network: a line that reads **On** and still answers "closed" has lost its model
at the provider, and the backend logs `phone line <id>: its model <model> may no longer
answer; closed`. Pick another model, or offer the old one again. While the listener is off
(no `PHONE_AUDIOSOCKET_LISTEN`, or it couldn't start), a line that would answer reads
**On · the listener is off**: no call can reach it until the listener is set and the
backend restarted.

**Callers hear "busy".** A line over its **Calls at once**, or the service over
`PHONE_MAX_CALLS`, answers "busy" and hangs up; the refusal is recorded as "line was
busy". Raise the cap if the machine can take more simultaneous calls (each call keeps
the recognizer, the model and the voice busy). With several workers, each counts for
itself.

**The assistant says "Sorry, I didn't get that".** The recognizer failed on a clip or the
model's answer failed or was empty. The call goes on. If it is every time, check the
speech engine (**Admin → Overview**) and the model.

**It answers late.** First sound is the sum of the end-of-turn silence (700 ms), the
recognizer, the model's first sentence and the voice's first clause. A model that is too
slow for a phone is the usual cause; a smaller or hosted model, a faster voice (Chirp),
and `SPEECH_WINDOW_SILENCE_MS` are the levers (the last is shared with Talk). If only the
first answer of a call is late, the model was reading the line's prompt for the first
time, or loading: see [A call's first answer](#a-calls-first-answer) and [The line's model
stays loaded](#the-lines-model-stays-loaded). The log says how long each call's read took
(`its line's prompt was read ahead in …`) and why a model wasn't kept loaded (`pushed out
of memory`, `couldn't be loaded`, or `aren't kept loaded`, which names the setting that
turned it off).

**A TV or a busy room beside the caller: babble mode.** Sound at talking level over the
line counts as someone talking. So at first the app does what Talk does: when the sound
starts it pauses the reply, and it waits for that stretch of sound to end and be judged
before it answers anything. If the recognizer judges the stretch to be noise, the reply
carries on. After **two stretches in a row judged noise**, the call is in **babble mode**:
the sound no longer pauses the reply, which plays on through it, and nothing waits for a
stretch to end. An echo, a clip the recognizer couldn't make out, or a sound too short to
count as speech between the two doesn't break the row; quiet does (below). Every stretch
is still heard and judged, so the caller still gets through. When one is judged to be
speech (the caller, or a TV whose words the recognizer makes out, which are taken as the
caller's), the reply is cut there and the question is answered at once, while the room
carries on.

- **The silent start.** When the sound never pauses, a stretch ends only at
  `PHONE_MAX_UTTERANCE_S` (30 seconds by default), and what the line is saying, the
  welcome included, is paused through each of the first two, so the first minute of such
  a call can be mostly silence. Lowering `PHONE_MAX_UTTERANCE_S` to 10–15 seconds shortens
  it, at the cost of cutting a long-winded caller's question into more clips.
- **Only quiet ends it.** A question answered doesn't end babble mode: the room is still
  loud. It ends when the line has been quiet for about **two and a half seconds** with the
  default speech window: 2 seconds with no stretch open, plus the quiet that ends a
  stretch (`SPEECH_WINDOW_SILENCE_MS`, 0.7 s by default), less the quarter of a second of
  new sound it takes to notice the next one (`SPEECH_WINDOW_MIN_SPEECH_MS`, 250 ms). The
  next stretch then pauses the reply again, as at the start. The same quiet also starts
  the count again before babble mode is reached: two stretches judged noise make babble
  mode only with less than that quiet between them. So in a quiet room a cough now and
  another a minute later never add up to it, and a caller listening leaves that much quiet
  all the time; a TV talking rarely does.
- **What babble mode costs.** A caller who really talks over the answer cuts it off only
  once their words have been judged, so the reply plays over them until then. Over
  unbroken sound their words end only when their stretch does, at the cap. And a caller
  who keeps talking past the end of one stretch into the next reaches the assistant as two
  questions, the second cutting into the answer to the first, not as one.
- **On a line that echoes, watch for missed interruptions.** In babble mode the reply keeps
  playing while the caller talks, so their stretch carries the line's own voice too. One
  that is mostly the line's own words is judged an echo and ignored.

Babble mode is the phone's own: Talk in the browser still pauses on every stretch of sound
(for Talk, see [DEPLOYMENT.md § Talk in a busy room](DEPLOYMENT.md#talk-in-a-busy-room)).
A turn, or a clip the recognizer couldn't make out, judged in babble mode is marked
`babble` in the call's stored stats; the console doesn't show that yet.
