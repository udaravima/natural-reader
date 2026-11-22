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
sends a frame every 20 ms for the whole call, silence included.

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
| `PHONE_MAX_UTTERANCE_S` | `30` | 5 to 120, **seconds** | The longest stretch of unbroken caller sound heard as one clip; longer sound is cut into clips this long. Constant background sound at talking level, such as a television next to the phone, still keeps a reply paused, exactly as in Talk: this cap only bounds how much audio is held. |

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

## Limits

| Limit | Where it is set | Default | What happens at the limit |
|---|---|---|---|
| Longest call | the line, **Longest call (minutes)**, 1 to 60 | 10 minutes | the call ends, recorded as "reached the time limit" |
| Calls at once on a line | the line, **Calls at once**, 1 to 20 | 2 | the next caller hears "busy" |
| Calls at once, all lines | `PHONE_MAX_CALLS` | 2 | the next caller hears "busy" |
| Tokens a day | the line, **Tokens a day** (empty = the deployment's default, `INFERENCE_DAILY_TOKEN_BUDGET`) | the deployment's | the line is closed until the next day (recorded "token budget") |

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
- **Spend is bounded** by the tokens a day, the two call caps and the longest call.
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
| It has **no project** (the project was deleted, or removed from the line) | **Closed** (amber) | "line was closed" |
| It **lost access** to its project: a maintainer removed the line's account from the project's members. **Give it access again** in the editor puts it back (only a maintainer or owner of that project can) | **Closed** (amber), and the editor says "No access to <project>" | "line was closed" |
| Its **token budget** for the day is spent (**Tokens a day**, or the deployment's budget; it resets with the day) | **On** | "line's token budget was used up" |
| Its **model was withdrawn**: the environment no longer allows it, the console's chat list no longer offers it, or the provider no longer has it | **On** | "line was closed" |

So the badge only knows the project causes. For a line that reads **On** and still answers
"closed", read the call list for the budget, and for a model the backend's log line
`phone line <id>: its model <model> may no longer answer; closed`. Pick another model, or
offer the old one again.

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
and `SPEECH_WINDOW_SILENCE_MS` are the levers (the last is shared with Talk).

**A TV or a busy room keeps the reply paused.** As in Talk, sound at talking level over
the line counts as someone talking. `PHONE_MAX_UTTERANCE_S` only caps how much of it is
held. See [DEPLOYMENT.md § Talk in a busy room](DEPLOYMENT.md#talk-in-a-busy-room).
