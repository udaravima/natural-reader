# Session handover

> Working notes (now tracked in git). Latest session on top.

---

## 2026-11-04 late night — Google recognizers chosen in the console: built and walked live on `feat/google-recognizers`, UNCOMMITTED (START HERE)

**Update 21:50.** The recognizer work is committed (`cd1c3bd`, owner's "Yes commit"). The owner now has three Chirp 2 recognizers in asia-southeast1, one language each: `asr-si-en` (si-LK), `asr-ta-en` (ta-IN), `asr-en` (en-GB); Sinhala and Tamil walked through the app, Chirp 2 en-GB through Google's default recognizer. **Also built, UNCOMMITTED:** the `current_time` chat tool (owner: "for tool yes"), walked with `gemma4:31b-cloud`, plus DEPLOYMENT rows for the Chirp 2 results. Gates: pytest 2997 / 16 skipped, vitest 1817, eslint 0 errors, build OK. Profiles are parked by the owner ("for later days").

**State.** Branch `feat/google-recognizers` from `development` (bf2c012, v2.9.0), on the owner's "sure go ahead" to a recognizer list in the panel. Spec `docs/superpowers/specs/2026-11-04-google-recognizers-design.md`, plan `docs/superpowers/plans/2026-11-04-google-recognizers.md`. **Nothing committed**: commits for this work weren't delegated, so they wait for the owner's word. Gates: pytest 2988 passed / 16 skipped, vitest 1816 in 151 files, eslint 0 errors (24 old warnings), build OK.

**What an admin can do.** **Admin → Chat options → Google recognizers** (once `GOOGLE_APPLICATION_CREDENTIALS` or `GOOGLE_SPEECH_RECOGNIZER` is set): every ACTIVE recognizer in the key's project, read live from all 17 regions in about 3 s, with model, languages by name and location; tick in order, **Move up**; a clip goes to the first ticked recognizer that lists its language, Auto to the first. Only a name Google listed is saved (its location is the host the token goes to; names also pass a strict pattern, `server/speech/google_names.py`). The Speaking chip and What it heard name the recognizer.

**Live, with the owner's key (scratch backend and DB, Chromium 1366/360 px).** The console listed their two recognizers, reordered and saved through the UI. Three real Sinhala recordings → `asr-si-en` (`chirp_2`, `si-LK`, asia-southeast1) → Sinhala text in 1.0–1.5 s. Tamil and English reach `asr-ta-en` with the right codes, but **Google refuses `chirp_3` in asia-southeast1 on every clip** ("no longer generally available in region asia-southeast1") while reading the recognizer works, so status says ready. Through Google's default recognizer: `chirp_3` in `us` heard Tamil and English right and picks between them on Auto; `chirp_2` heard Sinhala in asia-southeast1 and us-central1. **The owner's fix:** a `chirp_3` recognizer in `us` (or `eu`) with `ta-IN` and `en-GB`, ticked in place of `asr-ta-en`.

**Found and fixed while walking.** Google names projects by number in recognizer names (a typed id is now saved as Google's name, and one project is listed once); six regions at once took 5.5 s, all at once 3 s; `chirp_3` with several languages no longer shows "Auto hears only …"; a refused clip now logs Google's words. Trap for walks: this shell is zsh, so `${2:+-F language=$2}` doesn't split and curl sent no language.

**Owner asks still open (not designed).** Agent profiles: several named assistant profiles (librarian-default, call-agent, librarian-telephony), add/edit/delete, one active, ready for a future PBX channel; today there is one `assistant_profile` (`server/services/assistant_profile.py`). The bucket list below is still pending. And the cache volume's 2.8 GB of original checkpoints (offered fix, no answer yet).

## 2026-11-04 night — v2.9.0 released: speech each person chooses

**State.** On the owner's "merge this to main and we can release this": `feat/speech-choices` (8c3b70a) fast-forwarded into `development`, the release commit on top (version 2.9.0, changelog dated, `docs/Release_notes/RELEASE_NOTES_v2.9.0.md`, TESTING counts), `development` merged into `master`, annotated tag `v2.9.0`. Gates on the merged tree: pytest 2946 passed / 16 skipped, vitest 1806 in 150 files, eslint 0 errors, build OK.

**Found since, not built.**
- **The owner's two new Google recognizers** (`asr-si-en`, `asr-ta-en`, `asia-southeast1`, `chirp_3`): the Sinhala one lists `sl-SI`, which is Slovenian. Google's Chirp 3 language table (read 2026-11-04) has no Sinhala at all; its supported-languages table lists `si-LK` only for `chirp` and `chirp_2`, in `asia-southeast1` among others. Today's engine takes one `GOOGLE_SPEECH_RECOGNIZER`; a recognizer per language is the owner's next ask (design not yet approved).
- **Disk:** the backend's `cache` volume holds 4.4 GB, of which about 2.8 GB are the original checkpoints of the three converted fine-tunes (925 MB each), never read once converted (`server/speech/convert.py` downloads into HF_HOME and keeps them). The owner was offered a fix: convert from a temporary download, and clear the three originals from the live volume (needs their yes).

## 2026-11-04 evening — speech each person chooses: built, walked, on `feat/speech-choices`, then released as v2.9.0

**State.** `feat/speech-choices` from `development` (cb70b8f), 12 commits, every one on the owner's delegated "you have your decision to commit": magnetic sliders (096c350), the phone header (f3a09f5), the plan (afd1216), the seven plan tasks (3e7821a..89c175f) and a Home/End fix (e790752). Not merged into `development`: that still needs the owner's word. The auto-pusher pushes the branch. Gates on e790752: pytest 2946 passed / 16 skipped, vitest 1806 in 150 files, eslint 0 errors (24 old warnings), build OK.

**What a person can do now** (spec `docs/superpowers/specs/2026-11-04-speech-choices-per-person-design.md`, plan `docs/superpowers/plans/2026-11-04-speech-choices.md`):
- **Admin → Chat options → Speech model → Offered to people**: tick the Sinhala and Tamil fine-tunes; ticking downloads them (the console says Downloading until they're here).
- **The Speaking chip** beside the microphone (above the message box on a phone) and in the Talk panel: Auto, English, Sinhala, Tamil, each with its model; one tap, the next clip goes in that language, Talk keeps going. On Auto Talk says "Auto (English)" (Talk sends SPEECH_WINDOW_LANGUAGE on Auto: the likely reason Google "only heard English"). What it heard names each clip's language and model.
- **Settings → Chat → Reply voice** when Google reads the replies: Google's voices, the admin's as default, a sample in three languages fetched once per voice per session.
- The server keeps up to `SPEECH_MODELS_LOADED` (3) models per worker, holds each request's engine, waits for a load, falls back to the default, trims memory on drop.

**Measured.** One Talk conversation Sinhala → English → Tamil (scratch, Chromium): heard by whisper-small-si / small / whisper-small-ta with correct text; first clips 1.8 / 0.8 / 1.6 s; the backend 1,238 → 2,854 MB. Talk's limits hold on the fine-tunes (3/3 synthetic clips each), but **they say no_speech 0.00 for any voice**, an English question and a colleague's English included, so on them only the VAD, empty text and echo checks filter (DEPLOYMENT.md, Offering models to people). Stock `small` dropped short Sinhala and Tamil greetings as noise. The real Sinhala/Tamil recordings of v2.5.0 aren't on disk any more: the check used Gemini-TTS voices.

**Not done.** The non-admin walk through a throwaway Keycloak (a scratch admin walked every screen). A real Google reply voice (the walk stubbed /v1/chat/voice: the owner's key wasn't used). Real Sinhala/Tamil speakers in Talk. Deferred minors in the ledger summary: a stale default id in the pool after an admin switch, conversion on offer with 1 thread, language names in English.

**Next.** The owner tries it; merge into development and release when they say. Still open: speaker verification (the busy-room colleague), the current_time tool, inline tool calls, the showHeaderControlsOnMobile phone overflow.

## 2026-11-04 afternoon — v2.8.0 merged and tagged; next, speech models and voices each person chooses

**State.** On the owner's "merge and tag currently where we are": `fix/talk-in-a-room` (3d43a4b, bf9f211, the release commit) fast-forwarded into `development`, `development` merged into `master`, annotated tag `v2.8.0` on that merge. Not pushed by this session and no GitHub release (the owner said merge and tag only); the outside auto-pusher had already pushed the branch up to bf9f211. The model-choice design (`docs/superpowers/specs/2026-11-04-speech-choices-per-person-design.md`) is NOT in the tag: it is being revised with the owner's 14:22 input.

**The owner's input, 14:22.** Today speaking Sinhala, English or Tamil means an admin switching the deployment's one model. They want to switch between (about) three models during a conversation. Google speech-to-text worked only for English for them (cause not checked; v2.7.0's live test heard all three, so likely their recognizer's language list), while Google's Sinhala voice is good: Google stays an admin choice because it costs money, but when the admin turns Google's voice on, a person should pick their own voice and hear a short sample first. Kokoro needs a finer speed control than the presets: a slider that moves linearly but hops between round values, 0.05 apart.

**Since, on `feat/speech-choices` (from `development`), uncommitted:**
- **Every slider is magnetic** (`src/components/SnapSlider.jsx`, logic `src/lib/snap.js`): a real range input with `step="any"`, pulled onto round values within 6 px (9 px for a home value like 1× speed), capped at 40% of the gap (50% for home) so values in between stay reachable; arrows jump to the next round value, Shift+arrow by the fine step, and a change with no pointer down (a screen reader) steps to the next round value. Used for Speed (0.5–2×, 0.05, home 1×; Kokoro refuses outside 0.5–2.0), Volume (5%) and the Talk settings sliders. The owner asked for no marks under it. Measured in Chromium sweeping the track 1 px at a time: 79% of positions land on a round value on a 470 px track, 84% on a 282 px one, and 58 / 46 values in between are reachable. Trap found: with `step={fine}` the browser rounds the pointer first, leaving only 60% or 100% possible. Admin → Defaults → Reading speed takes 0.05 steps.
- **Phone header fixed** (owner: "your recommendations are correct"): `ViewMenu` in `src/components/ViewSwitcher.jsx`, one button with the open view's icon and a menu (menuitemradio, `useMenuDismiss`), shown below `sm`, and in the reader below `xl` where the 271 px playback controls need the room. Measured in Chromium with phone emulation for an admin in all four views at 320–1440 px: nothing wider than the screen (before: 360 px +62, 768 px reader +164, 1024 px reader +15); the menu stays on screen at every width; Settings fits a 360 px phone. Left alone: `showHeaderControlsOnMobile` (the person's opt-in to playback controls on a phone) still overflows a phone.
- **The design is rewritten** as `docs/superpowers/specs/2026-11-04-speech-choices-per-person-design.md`: a "Speaking" language switch in Talk and beside the microphone (the server picks the model from the language, first offered model made for it, else the default), the per-worker pool (engine held per request, wait for a load, `malloc_trim`, default 3 places), and a per-person Google reply voice with a cached three-language sample. **Read from the code: Talk sends `SPEECH_WINDOW_LANGUAGE` (`en`) with every clip when Speech language is Auto**, which very likely is why Google "only heard English" for the owner (their recognizer lists si-LK, ta-IN, en-GB); setting Speech language to Sinhala is today's workaround.

## 2026-11-04 midday — Talk settings, and the busy-room gate made a setting: committed on `fix/talk-in-a-room`

**State.** Branch `fix/talk-in-a-room`: `3d43a4b` (the first gate) and the commit after it (this work), both committed on the owner's word. Nothing pushed; `development`'s `d48209f` isn't pushed either, and the branch isn't merged into `development`. At 13:30 the owner was deploying this commit to check it live; until then their stack ran the first gate (rebuilt 10:13–10:16), where `SPEECH_WINDOW_GATE_DB=0` turns it off. With this commit that value is out of range (3 to 30): one warning, and the gate is off by default anyway.

**The owner's report.** With the first gate their turns were "gone before I even finish": the live log showed sentences arriving as pairs of clips (4.7 s then 3.1 s, 8.4 s then 2.1 s). They had sat back from the microphone. They asked for runtime settings, with the known-good values referenced, and for people to choose a Whisper model from an admin's list.

**What changed.**
- **Settings → Talk** (`src/components/settings/TalkSettingsPanel.jsx`, logic in `src/lib/speech/talkSettings.js`), for someone who may talk: the three microphone switches, Ignore quieter voices and How much quieter, the pause that ends a turn, the speech that counts as a turn, how sure the VAD must be. Defaults are the server's `/v1/speech/status` window (the microphone's: on), shown beside each control; per device (`usePersistedState('talkSettings')`), only changed values stored, a value moved back to the default forgotten; Reset to defaults. The Talk panel has a sliders button to it, says "Ignoring voices N dB quieter than yours", and Copy numbers includes the settings.
- **The gate is off by default** (`SPEECH_WINDOW_GATE`, new; `SPEECH_WINDOW_GATE_DB` now 3 to 30). Automatic gain is back on by default.
- **The gate's rule** (`gate.js`): in a turn, that turn's own level; between turns, the loudest of the last three turns Whisper accepted; one margin for starting and staying. `endTurn()` returns `learn`, which the conversation calls on a speech verdict.

**What the tests found, in order** (scratch: `vadlevel/real*.py`, `drop*.py`, `office*.py`, `talk2/sitback.mjs`, results in `walk3/`):
1. On 20 real recordings, sitting back 6 dB: first gate 0–75% of speech kept, no gate 69–100%, the new rule 65–100%.
2. Walked in Chromium (Whisper `small`, a colleague reading nonstop ~13 dB below, three of four questions 6 dB further back): **every rule let her sentences through as turns once one opened**: first gate 19 of 22 clips hers, new rule at 9 dB 41 of 43, at 6 dB 52 of 56. Loudness can't say whose voice it is, and Whisper accepts her words because they are speech. The earlier clean walk of the first gate used `base.en` with every question at the usual level; which of the two kept her out isn't known. Not the model rejecting her: measured at 13:55 (scratch `pool/verdicts.py`), `base.en` calls 5 of 6 four-second pieces of her recording speech, `small` 4, `base` 3, and all 7 questions are speech on all three.
3. Tried and dropped: the last accepted turn alone (she took over at once); a strict 4 dB start with the in-turn rule (sitting back lost whole utterances again); a start needing 6 of 8 loud frames (lost real speech even at the usual level).
Every rule ends turns in that room, which gate-off never does. docs/DEPLOYMENT.md "Talk in a busy room" has the tables and says plainly what it can't do.

**Verified.** vitest 1751 in 144 files, pytest 2906 passed / 16 skipped, eslint 0 errors (24 old warnings, none in changed files). Every gate rule is checked by a wrong gate it catches. Settings → Talk and the panel walked at 1366 and 360 px (a phone's 44 px button rule had made the switch a circle; fixed). The 360 px page scrolls sideways because of the header, on every Settings tab: older than this work, not chased.

**Next, proposed and not built.**
- **Speaker verification**: recognise the person's voice, so a colleague's turn is dropped whatever its loudness. The real fix for the office.
- **Each person chooses their speech model**: design `docs/superpowers/specs/2026-11-04-speech-choices-per-person-design.md`, revised 14:00 after reading the code and measuring, not approved, uncommitted. The read found two things the first draft missed: the transcription route swaps the worker's one engine and reads it back, so two people on two models could be heard by each other's model; and a model that isn't loaded refuses the recording with 503. Measured (scratch `pool/`): a dropped model keeps ~250 MB in the process unless `malloc_trim(0)` runs; `small` + `base` held 886 MB; Talk's speech-or-noise limits hold on `small`, `base` and `base.en`.
- Still open from the night entry: the `current_time` tool and inline tool calls.

## 2026-11-04 night — Talk in a busy room: built and walked on `fix/talk-in-a-room`

**Superseded the next morning.** The owner deployed this and the gate cut their turns in half when they sat back from the microphone: its learned level only ratchets up, and a turn needs a voice within 4 dB of it to start. The entry above makes the gate a setting, off by default, and changes how it learns.

**State.** Branch `fix/talk-in-a-room` from `development` (`d48209f`, the v2.7.0 docs commit, which is committed and NOT pushed). All of the work below is uncommitted, awaiting the owner's word. The backend container on the owner's machine was rebuilt 2026-11-03 21:59 with v2.7.0 code, so the deploy has probably happened (frontend not checked).

**The owner's report.** In an office, on a phone, Talk heard a colleague two desks away and never sent the turn. On a laptop they work around it by lowering the microphone volume; a phone has no such control.

**Why.** Silero tells speech from noise, not near from far: a voice 35 dB quieter still scores 0.97. So her voice held every turn open and opened turns of its own. Threshold settings can't fix that. Walked in Chromium with her voice 14 dB quieter throughout: no turn ended without Send, no reply ever played, and the clips were 14–72 s of mostly her words.

**What changed.** `src/lib/speech/gate.js`: a frame more than `SPEECH_WINDOW_GATE_DB` (default 9) below the person's learned level is silence; a start needs 4 dB. The level is learned only during a turn under way (the walk caught her syllables between turns pulling it down 5 dB in 6 s), and a clip Whisper calls noise gives back what its turn taught (otherwise a cough could make Talk deaf for good). It hooks vad-web 0.0.31's private `frameProcessor.modelProcessFunc`; `listen.contract.test.js` fails if an upgrade moves it. The microphone opens without automatic gain. Tapping the circle while **Hearing you** sends at once (`setOptions({ redemptionMs: 32 })`, no microphone restart). **What it heard** scrolls inside 192 px, says when a phone kept its own gain, and **Copy numbers** includes what the browser applied. The walk with the gate on: turns ended 0.6–1.0 s after the last word, transcripts clean, no reply paused.

**Known limit.** If someone else is talking when Talk starts, the first turn begins with their words and ends about 3.7 s late. Real office, real voices and phones are untested: the owner's phone session is the check. The scratch DB `nr_websearch_walk` on :5434 and the walk scripts (`scratchpad/talk2/office*.mjs`, `scratchpad/vadlevel/office*.py`) are scratch.

**Found, not built: the owner's other three points of 2026-11-04.**

- **Web search** is fine underneath: an approved search answered the weather with sources in 6.2 s. In "Ask first" mode, though, a typed reply says "I don't have access…" above the card (`OFFER_NOTE_NO_DOCUMENT`), and Talk never searches (`orchestrator.py:301`: ask becomes off for spoken turns). With **Search without asking**, Talk answered weather and news in 3–4 s. The owner said to skip this part for now.
- **No clock.** Since v2.4 the rules carry only the date (`context.today_line`), to keep the prompt cache and because a minute clock was misread. Proposal: a `current_time` tool.
- **Tool calls out of order.** A reply's step texts are one string, and its tool calls are a separate list drawn under the whole reply (`ChatView.jsx` `ToolCallsDisclosure`). Nothing records where each call happened. Proposal: record the text offset on each call (live and saved) and draw a one-line chip there.

---

## 2026-11-03 evening — Google speech tested live against Google, released as v2.7.0

**State.** `feat/google-speech` merged to `development` and `master` and **released as v2.7.0** on the owner's earlier "go ahead" (which covered the live test, merge and release). **Not deployed.**

**What a person can do.** Admin → Chat options → **Speech recognition: Google Cloud** and **Voice for the chat and Talk: Google Gemini-TTS** (30 voices). People then dictate and Talk in English, Sinhala and Tamil, picking their language in **Settings → Chat → Speech language**, which lists only the recognizer's languages.

**Live findings** (owner's key, loopback, real Chromium with the simulated microphone; numbers in DEPLOYMENT "Google Cloud speech" and the spec's last section):
- The first 403 was the Speech API off in the **OAuth file's** project, which I had wrongly taken for the recognizer's. The recognizer lives in the service account's own project. A 403 with `SERVICE_DISABLED` now names the API and project instead of telling the admin to add a role.
- `long`/`global` handles Sinhala (the docs' table said it wouldn't), but with no language named it hears only its first (`si-LK`), so English and Tamil came back as Sinhala. Settings and the Overview now say "Auto hears only Sinhala"; `chirp_3` with `auto` tells all three apart (tested in `us` through the implicit recognizer, nothing created in the owner's project). Talk was never affected: it sends `SPEECH_WINDOW_LANGUAGE` (`en`) for Auto. The message-box microphone was.
- First sound in Talk, medians of 5 English questions: Whisper+Kokoro 2.07 s, Google+Kokoro 3.01 s, Google+Gemini-TTS 7.08 s (Sinhala 6.30, Tamil 5.43, one clip each). Gemini-TTS has a floor of about 2.3 s per sentence; `flash-lite-preview` saves 0.3–0.5 s. `speakingRate` works.

**Owed by the owner.** Listen to Gemini-TTS in Sinhala and Tamil before turning it on for people. Decide whether to make a `chirp_3` recognizer with `auto`, or set **Admin → Defaults → Speech language** for the `long` one. Real voices (the Sinhala and Tamil clips were Gemini's own). The older items below still stand: Talk on real hardware, the CPU ruling, the `BOOTSTRAP_ADMIN_EMAIL` finding, the stale People tick.

**Open levers, not built.** Streaming synthesis (gRPC) would cut Gemini-TTS's first sentence; a shared HTTP client would save a TLS handshake per sentence; Talk doesn't check a stored language against the recognizer's list.

### Bucket list (current, replaces the v2.5.0 one below)

Checked 2026-11-03 after the v2.7.0 docs pass. Done since the v2.5.0 list: the 375 px header, `AdminPanel.jsx`, `git config user.name`, Sinhala and Tamil (through Google).

**Deploy (owner).** v2.5.1's last fix, v2.6.0 and v2.7.0 are not live (live is `f8af89e`). Back up the database; `npm install` and `npm run build` (v2.6.0 added npm packages); rebuild the backend (`google-auth`); Node 22.13 or 24; the `talk` realm role (the owner made it); `GOOGLE_*` only if Google is wanted.

**Owner decisions.** `BOOTSTRAP_ADMIN_EMAIL` doesn't reserve the seed admin (security-relevant; fix in branch 3 of `resolve_or_provision_user`). A People tick from a stale list overwrites roles (fix: add/remove semantics on `PATCH /v1/admin/users/{id}`). The CPU anti-goal (recommended: accept and reword). Which recognizer for Google, and whether its voice is good enough. The auto-pusher. SDD workspaces. A file too large for the model: refuse or note. Warn before a model switch hides replies.

**Owed on real hardware (owner).** Talk with speakers and no headphones (echo), a real room, a phone, Safari; Kokoro `fp16` by ear; real Sinhala and Tamil voices through Google; Chinese and Japanese voices; a phone keyboard over the popup.

**Owed checks (never run).** A person without chat access; a blocked microphone prompt; the 2-minute stop; a real OpenAI-compatible speech server; several workers swapping speech models; `compose.speech.yml` on a running stack; Docling Describe and Accurate; Auto turned Ask, the yes path; V1 reader error, Retry index, the freeze fix; two users on one browser; a share recipient; `startup.cmd` on Windows; indexed documents with a NULL `embedding_model`.

**Next builds.** Admin-curated speech models and voices people choose among (spec: the speech window's "Admin lists"). Talk smoothness: a shorter silence (700 ms now), a turn detector (Smart Turn v3), streaming voice. Phase 2b embeddings (embeddinggemma-2, nomic, Qwen). Figures with a vision model and the DeepSeek-OCR preset. Audio straight to a model; a model per language or person; Sri Lankan Tamil.

**Bugs and limits.** Two people talking at once queue. The speech picker switches on every arrow key; conversions take no lock between workers. The `touch.stamp` second connection. A model can pass real values to `READER_MODEL`. Vitest flakes under load. Project eval `features` fails every run; citations 87.9% against 90%; the eval scorer's `_REFUSAL` lacks "list".

**Robustness.** Resumable streams, a tested backup restore, a lock around migrations, a pool-slot limit, backup guidance.

**Providers.** A `/models` fallback, per-provider params, a native Anthropic adapter; vLLM, LiteLLM and Unsloth unrun; a GPU or NPU image.

**Features (the owner's wish-list).** Bulk project upload; auto tags and descriptions; Markdown and PDF side by side; MCP; agents; knowledge-base refresh; chat search, export, edit and regenerate; saved roles; prices per model; Auto model routing; V2 compare and review-before-current; C2.2 digests.

**Housekeeping.** The AppArmor pasta fix (sudo); disk 92% full (40 GB free); 23 merged branches; stale memories.

---

## 2026-11-03 afternoon — Google Cloud speech for the chat and Talk: built, waiting for a service-account key

**State.** On `feat/google-speech`, cut from `development` at `9e05158` (v2.6.0), committed as it went on the owner's "go ahead" to the design (`docs/superpowers/specs/2026-11-03-google-speech-design.md`), which also covered testing live, merging and releasing as v2.7.0. **Not merged, not released, not deployed.** Release waits for one live test against Google with a real service-account key.

**What it does.** Off by default. A deployer sets `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_SPEECH_RECOGNIZER` and/or `GOOGLE_TTS_MODEL`; an admin then picks, under **Admin → Chat options**, **Speech recognition** (Whisper / OpenAI-compatible / Google Cloud) and **Voice for the chat and Talk** (Kokoro / Google Gemini-TTS, one of 30 voices). Documents, audiobooks, a message's Audio download and the extension keep Kokoro. Talk listens in the person's own Speech language when set. `startup.sh` mounts the key read-only (`compose.google.yml`).

**Walked in Chromium with Google configured and a key Google-auth refuses** (the owner's first file was an OAuth web client): the admin switches, the engine line, the 30-voice popup, both Overview lines naming the wrong file, Talk disabled with "Ask your admin", Read aloud's toast "Google's voice isn't working right now. Ask your admin.", and the Settings → Chat note. **Not yet against Google itself:** every request and response shape is from Google's docs and unit-tested with mocks only.

**The owner's Google side (from the conversation, not in the repo):** recognizer `asr-si-ta`, `global`, model `long`, en-GB/si-LK/ta-IN, in the project the OAuth file names; service account `natural-reader-speech@` in a different project. Google's tables list **no Sinhala for `long`** (only `chirp`/`chirp_2` in asia-southeast1, europe-west4, us-central1), and the service account needs Cloud Speech Client on the recognizer's project. Expect the live test to show both.

**Next.** The owner saves the service-account JSON key (`"type": "service_account"`) at the path they gave. Then: live test (English, Tamil, Sinhala clips through the recognizer; Gemini-TTS in each; first sound against 2.09 s; noise against the empty-result rule), fix what it finds, update TESTING counts, release v2.7.0 as v2.6.0 was.

---

## 2026-11-03 late morning — Talk moved into the chat, behind a `talk` role: released as v2.6.0

**State.** On `feat/speech-window`, merged to `development` and `master` and **released as v2.6.0** on the owner's word ("merge and drop a release"). **Not deployed.** The owner tried the Talk view and said "not smooth as other agents, but good start", and asked for it in the chat, as ChatGPT's and Gemini's talk modes are, with a role so others can use it.

**What a person can do.** In the chat (or the chat beside a page), with a model chosen: **Talk** beside **Send** → it listens at once, the typing box becomes the panel (circle, **Mute**, **End**, **What it heard**), the words fill the chat. Cut in by speaking or by tapping the circle. **End** or **Esc** gives the typing box back. An admin gives `talk` under **Admin → People** (or the Enroll form), or as the Keycloak realm role.

**What a person can't do yet.** Admin-curated speech models and voices for people to choose among: still the second half of the 2026-11-02 request, not built. Any language but English.

**Traps found by walking it** (throwaway Keycloak on :18081, scratch DB, fake microphone; numbers in `docs/TESTING.md`):
- **A brand-new account starts with no capabilities**; Keycloak roles are copied in at each later login, not on a reload. The owner had already added `talk` in their Keycloak: those people get it at their next login once v2.6.0 runs, or at once with a tick.
- **A People tick sends the whole set as the list shows it.** A list loaded before the person's second login sent `['talk']` and removed their `reader` and `chat`, in Keycloak too. Pre-existing for every role; documented in USER_GUIDE, not fixed. The fix would be add/remove semantics on `PATCH /v1/admin/users/{id}`.
- **`talk` alone gave an endless Loading…** (so did `docling` alone): fixed, the gate now needs a capability that opens a view (`src/lib/access.js`).
- **The admin's phone header was already too wide** without the Talk view (15 px at 375, 30 at 360); the narrow padding and hidden logo stay.
- **Pre-existing, security-relevant, not fixed: `BOOTSTRAP_ADMIN_EMAIL` doesn't reserve the seed admin.** On a fresh database with it set to `admin@example.com`, `tess@example.com` signed in first and became admin: branch 3 of `resolve_or_provision_user` claims the seed row `WHERE oidc_sub IS NULL` for any brand-new identity, without comparing the pinned email, and the callback gave her the `admin` realm role too. A deployment is exposed only between its first start and the founder's first login. The fix is for branch 3 to skip the seed row while its email is pinned. Ask the owner before changing it.

**Owed.** The owner, on real hardware: speakers without headphones, a real room, a phone. Whether the smoothness is better, and what still feels rough; the open levers are a shorter silence (700 ms now), a turn detector (Smart Turn v3), and streaming the voice. Then admin-curated lists, then Sinhala and Tamil. One vitest flake seen once under load: "Escape cancels while recording" (voice input), passed alone and in two more full runs.

---

## 2026-11-03 — The speech window works in English, with a baseline

**State.** On `feat/speech-window`, cut from `development` at `86161f2`, committed as it went on the owner's "commit as you go". **Not merged, and not deployed.** Spec: `docs/superpowers/specs/2026-11-02-speech-window-design.md`. Numbers and settings: `docs/DEPLOYMENT.md`, "The speech window". The owner chose English first, on `gemma4:31b-cloud`, and put local-model speed aside.

**What a person can do.** **Talk** in the view switcher, when voice input is on → choose a model → **Start** → speak. To cut in, speak: the reply pauses, real speech stops it, a cough resumes it at the same word. **Mute**, **End**. **What it heard → Copy numbers** gives every clip's scores and timings. **Admin → Chat options → Speech model** now offers **Whisper base, English only** and **Whisper small, English only**.

**What a person can't do yet.** Everything in the second half of the owner's request: an admin can't offer several speech models or narrow the voices, and a user can't choose among them. One speech model still serves everyone, and the 50 voices are open to all. Design and spec are ready; it was held back so the baseline came first.

**Baseline.** First sound after the person stops, median of 12 turns, cloud model, CPU only: 3.91 s as first built, **2.11 s** tuned with `base.en` (worst 3.12 s). Real browser path, `base.en`: 93 of 100 spoken words accepted, 2 of 28 hard noises. Idle listening takes 2.4% of one core. A real cut-in kept "It started small." of a 188-character reply, and the next request carried that.

**Traps found by running it for real, none visible to unit tests.**
- Silero's memory dulls it to short words: a live "stop" fired 6 in 25 against 92% offline, where each clip got a fresh model. `listen.js` clears it after the silence that ends a turn.
- 0.4 for `no_speech` came from `small` and let 9 of 16 noises through on `base.en`. 0.25 suits all three models.
- The first echo check threw away a repeated question. Only a clip that began during a reply can be an echo.
- Most interruptions hit a reply saved `complete`, not `aborted`, so `heard` cuts either.
- `int8` Kokoro is 4 times slower on a CPU. `fp16` is faster but not the same audio, so it's off until the owner has listened.

**Owed.** The owner, on real hardware: speakers without headphones (whether Chrome's echo cancelling covers the app's own audio is unknown), a real room, and comparing Kokoro's `fp16` by ear. Then Sinhala and Tamil, whose `no_speech` scores are untested. Admin lists next. The CPU ruling below matters more now: Whisper, Kokoro and a local model share the machine on every turn. The header still overflows by 14 px below 375 px for an admin.

---

## 2026-11-02 evening — All 50 voices, popup dropdowns, Library on Projects, favicon: released as v2.5.1

**State.** Built and walked on `feat/voices-and-dropdowns`, cut from `development` at `06b4569`, then merged and **released as v2.5.1** on the owner's word. **v2.5.1 is not deployed:** live is the branch at `f8af89e`. Nothing is migrated, but it needs Node 22.13 or 24 and both halves rebuilt. The owner deployed v2.5.0 on the evening of 2026-11-02, so the v2.5.0 entry's "Nothing is deployed" is out of date, and then deployed this branch at `f8af89e` at 22:38. The `en-us` bug and the `af_river` label are fixed here.

**Trap:** live serves its own copy of the build, under `/var/www`, not this repo's `dist/`, so `vite build` here doesn't touch it.

**How Sinhala reads.** Kokoro takes phonemes, never text. espeak switches language by itself on a script it wasn't asked for, so any voice can read Sinhala or Tamil. Each switch left `(si)` or `(enus)` in the phonemes, in characters Kokoro accepts, so it spoke them. `server/tts_language.py` removes them and picks espeak's language from the voice's first letter. English audio is identical, sample for sample, on three test texts.

**npm update.** Everything is on latest. Three traps. `npm update` alone moves `pdfjs-dist` from 5.4.530 to 5.7.284, into CVE-2026-16633 (`>=5.6.83 <6.2.108`, a malicious PDF runs script), so PDF.js 6 is a fix, not a luxury; live was built on 5.4.530 and was never in that range. Tailwind 4.3 changed its default font stack to lead with Roboto and Noto, which reflowed the reader's toolbar on Linux, and no test sees that: `src/index.css` pins the old stack. react-hooks 7.1 flags 24 working spots (load on mount, a ref holding the latest value), so those three rules are warnings in `eslint.config.js`. `npm audit` still shows 6 moderate, all `minimatch` inside ESLint, a dev tool reading our own globs; the fix needs minimatch 11 and ESLint 10 is on 10.2. Checked by comparing the live bundle with the new one in a real browser: a real PDF painted the same 26,017 dark pixels, and 18 screens differ only in Lucide's redrawn `BookOpen`.

**The list the owner meant by "squeezed"** was **Admin → Models → Chat models offered**, found only after the popup shipped: all 424 rows measured 0 px tall on desktop. `SearchableList`'s scroll box was a height-limited flex column, and those rows carry `md:min-h-0`, so they shrank instead of scrolling; 44 px at phone width hid it from the phone walk, and the desktop walk measured Save, not rows. The box is a block now: 16 px rows, scrolling. That fix came after the 22:38 deploy, so live has the popup and the favicon but not this.

**Long dropdowns** are now `PopupSelect` in `src/components/SearchableList.jsx`, replacing the search box above a `<select size>`: that design was the "squeezed" bug, with rows about 15 px tall. The popup is a portal to the nearest dialog, else `document.body`. Lists of 12 or fewer stay native selects, and lists of controls keep their search box.

**Not verified.** How the Chinese and Japanese voices sound: Kokoro's own pipeline uses a different front end (misaki) for those two, and espeak reads kanji as the English words "Chinese letter". Whether Hindi voices are the right default for Sinhala is the owner's ear. A real phone keyboard over the popup.

---

## 2026-11-02 — v2.5.0: speech models, long lists and the message box inputs

**State.** `development`, `master` and the tag `v2.5.0` are pushed, and the GitHub release is out. **Nothing is deployed.** The live backend is still the image rebuilt at `445d2ae` on 2026-11-01 (see the entry below), with no speech models and no long-list work. Deploying needs a database backup and the `docling` role in the live Keycloak. Speech-model work is on `feat/speech-models`, merged by fast-forward.

The owner's request was: make three extra models available, from the admin panel as well as config, as a stopgap until audio can go straight to a model; add a VAD; make long lists scroll and search so Save is reachable; English stays primary. The spec is `docs/superpowers/specs/2026-11-02-speech-model-choice-design.md`. It was built inline with one final reviewer rather than a plan and task reviews, because the owner wanted it in this release and each part was walked as it was built. The reviewer found six Important issues the tests hadn't.

### What a person can do now

- **Choose a model (admin):** **Admin → Chat options → Voice input → Speech model**. It saves on selection, and the next recording uses it on every worker. **Use the server setting** hands back. Choose before turning voice on, or `small` downloads first.
- **Add one (admin):** **Add a model from Hugging Face** → `owner/name` → **Add model**, then choose it. It's checked and pinned at once.
- **Remove one (admin):** **Added here** → **Remove**, in use or not.
- **Find things in a long list:** past 12 entries a list gets a search box. Admin → Models rows scroll in their own box, and Save stays on screen.
- **Deployer:** `SPEECH_MODELS_FILE` (a host path, mounted by `startup.sh` through `compose.speech.yml`), or `SPEECH_MODEL`. Converting elsewhere is in DEPLOYMENT, "Converting somewhere else".

### Facts worth keeping

- **The pasted list had a wrong commit.** The `whisper-small-si-run15` revision pasted by the owner was run12's. The real one is `ab6154e9…`. run15 has no benchmark behind it, and the `voice-agent-79` session that did the research doesn't know where that entry came from.
- **Two pieces of research advice didn't survive measuring.** `beam_size=1` changed real Sinhala words and `condition_on_previous_text=False` dropped the end of a 269-word English recording, so both stay at Whisper's defaults. VAD threshold 0.5 doubled Tamil error; 0.2 invented text on noise; 0.4 is what's set.
- **Trap:** Auto needs the one-language rule. run12 called Sinhala Kannada at 0.92, and the Tamil model's config lists no languages, so faster-whisper crashes on detection.
- **Trap:** faster-whisper's own `decode_audio` fails on PyAV 19 (`metadata_errors`). The app never calls it (`server/speech/audio.py` decodes), but a one-off script will hit it.
- **embeddinggemma-2** is on the local Ollama as `embeddinggemma-2:740m`: 768 dimensions, the same as `nomic-embed-text`, so no schema change but a full re-embed. Its capabilities list vision and audio. Not tested.

### What the final review caught

A card language outside Whisper's list made a model "Added" and then missing, with no way to remove it. Removing needed the model chosen first, which starts its download, and a model that can't run couldn't be removed at all. One malformed YAML `languages` entry took voice down. A failed options read swapped the running model. `SPEECH_MODELS_FILE` couldn't be reached in a container. The documented hand conversion failed on every built-in fine-tune. Eleven smaller ones were fixed too. Left as they are: the picker saves on selection, so each arrow key in the closed select is a live switch; conversions take no lock between workers, and a dead worker leaves a `.converting-*` folder.

### Gates at the release

Backend `2756 passed, 16 skipped, 2 deselected`, eslint clean, frontend `1523 passed` in 128 files. Cloud evals are in `docs/TESTING.md`: document QA 9/9, citations 4/4, project QA 12, 10 and 10 of 13 against 10, 9 and 9 on v2.4.0, so no regression.

**Not walked:** the real plain image; `compose.speech.yml` on a running stack; more than one worker swapping; Sri Lankan Tamil; a fine-tune for a language Whisper doesn't list, past adding it.

### Bucket list

**Owner decisions.** Spec §2 CPU anti-goal (a local reply 1.5-2× slower during a transcription; recommended: accept and reword). Confirm the live rebuild at `445d2ae` with 024 applied was intended. Find the auto-pusher (not git hooks, cron, user timers or VS Code's post-commit; next look at Claude Code hooks and system timers). Deploy. Keep or delete the SDD workspaces. Whether a file too large for the model is refused or noted. Whether to warn before a model switch hides replies.

**Next build.** Phase 2b embedding provider and trials (embeddinggemma-2, nomic, Qwen). Figures with a chosen vision model, plus the DeepSeek-OCR preset. Audio straight to a model. A model per language or person. Browser auto-stop on silence. Sri Lankan Tamil. Multi-language handling for a server with English and Sinhala speakers.

**Bugs.** The header scrolls sideways at 375 px. The `touch.stamp` second connection. A model can pass real values to `READER_MODEL`. Dead `AdminPanel.jsx`. The unidentified vitest flake. Project eval `features` fails every run. Citation quality 87.9% against the 90% target. The speech picker's keyboard switching and conversion locking, above.

**Robustness.** Resumable streams. A tested backup restore. A lock around migrations. A pool-slot limit. Backup guidance.

**Providers.** `/models` fallback, per-provider params, a native Anthropic adapter; vLLM, LiteLLM and Unsloth still unrun; a GPU or NPU image.

**Features.** Chat search, export, edit and regenerate, saved roles, prices per model, Auto model routing, bulk upload, auto tags, MD and PDF side by side, MCP, agents, cloud stats, knowledge-base refresh, V2 compare and review-before-current, C2.2 digests.

**Owed checks.** Safari and a real phone. A person without chat access. A blocked microphone prompt. The 2-minute auto-stop. A real remote speech engine. Docling Describe and Accurate. Auto turned Ask, the yes path. V1 reader error, Retry index and the freeze fix. Two users on one browser. A share recipient. Real Kokoro read-aloud. `startup.cmd` on Windows. Indexed documents with a NULL `embedding_model`.

**Housekeeping.** The AppArmor pasta fix. `git config user.name` is the email. Disk space (97% full). Old merged branches. Stale memories.

---

## 2026-11-02 — Message box inputs built, walked and reviewed; merged in v2.5.0 (see above)

**State.** The plan `docs/superpowers/plans/2026-10-27-chat-composer-inputs.md` is finished on `feat/chat-composer-inputs`. That branch is stacked on `fix/docling-keep-pictures` (`bd2eaaa`), which is stacked on `docs/accuracy-audit` (`59c2c86`), which is on `development` (`0d9305b`), so merging the top branch carries all three. **Nothing is merged into `development` by us and no version is bumped.** The auto-pusher has published these branches anyway. The ledger with every ruling is `.superpowers/sdd/2026-10-27-chat-composer-inputs/progress.md` (git-ignored, kept for the owner to decide on).

**Live is not where the docs assume.** On 2026-11-01 at 22:43 +0530, while this run's agents were stopped by a usage limit, the live backend image was rebuilt from this working tree at `445d2ae` and restarted. **Migration 024 is applied on the live database** (the container's log says so). That image has no speech code and no `faster-whisper`, and `dist/` is still from 2026-10-26, so no screen reaches text attachments there. Nobody here ran it; the owner should confirm it was intended.

### What a person can do now

- **Attach a text file:** the paperclip (**Attach an image or a text file**), drag and drop, or paste. The chip shows the name, size and "N values masked". Clicking it shows exactly what will be sent.
- **Send real values:** in that view, **Send real values** → **Yes, send real values**. The confirmation names the model and says when it's a cloud one. Changing the model turns masking back on.
- **After that:** the file and the replies that read it go only to that model. On another model they're held back, and a note saved with the reply says so. To use the file elsewhere, attach it again.
- **Turn voice on (admin):** **Admin → Chat options → Voice input → On**; **Admin → Overview → Voice input** shows it getting ready, then OK. It's off by default, and nothing downloads until then.
- **Speak a message:** the mic beside the paperclip → **Stop** (or **Cancel**, Escape). The text lands in the box at the caret, unsent. **Settings → Chat → Speech language** (Auto first); **Admin → Defaults for users → Speech language**.
- **Keep a PDF's pictures:** Convert → Images → **Keep** → the **MD** view shows them.

### Gates at `6a5ed35`

Backend `2646 passed, 16 skipped, 2 deselected`, eslint clean, frontend `1480 passed` in 126 files on 28 of 29 full runs.

**One unexplained failure.** The first frontend run on this tree read `1 failed | 1479 passed` (started 08:53:34). Only the tail of its output was kept, so the test's name is unknown. It never came back in 28 more runs: one straight after, 6 idle, 3 with all 32 cores busy, 3 straight after a full pytest, and 15 in a row. No test reads `docs/`, and nothing else on the tree changed between them. Treat it as an unidentified timing flake, not a cleared one: if vitest fails again, save the whole output.

### What was walked

- **`task-8-walk-report.md`:** 33 of 35 steps passed on the bare-metal scratch stack. One FAIL was fixed (a dropped connection logged a traceback). The other is W3 below.
- **Logging proxy in front of Ollama, in a real browser:** real values on `gemma4:e4b`, then `gemma4:31b-cloud`, then back. At `6a5ed35` the cloud request carries no real value (`real: false`), and the note shows in the chat.
- **Container image built from this branch, under its own tag:** voice on from the environment reached ready in 33 s, including the download into `/app/.cache`. 5.7 s of speech in 1.7 s; 100.8 s in 21.5 s with narration alongside. Memory with Kokoro: 1.26 GB after load, 1.95 GB after use.
- **A real voice refusal:** the toast reads `Voice input isn't working right now. Ask your admin.`

**Not walked:** real Safari or a real phone; a person without chat access (the walk has one admin); a real blocked-microphone prompt; the 2-minute auto-stop; a real remote speech engine (fake transport only); Docling Describe and Accurate; Auto-turned-Ask's yes path in a browser; Sinhala and Tamil quality.

### What the reviews caught that tests didn't (worth keeping in mind)

- **Masking was rebuilt.** Three rounds of patching a multi-pass rewrite each broke something new, because passes read each other's output. Detectors now report ranges over the original text, and one render writes the placeholders. Two tested invariants replaced case-hunting: masking a masked file changes nothing, and the count is the truth.
- **The privacy promise crossed task boundaries.** R4 guarded the staged file; history re-sent it to any model (final review). Then the logging proxy showed the local model's *reply* carrying the value to the cloud, past 2,639 green tests. Only a real run with the real prompt in view caught it.
- **FastAPI wrote recordings to disk.** Multipart parsing spools over 1 MB before the route runs. The route parses in memory now; a spy on rollover pins it.
- **A mock hid a bug for days of work.** The voice hook read `detail` as a string and the server sends an object, so all seven refusals showed the fallback. The mock used a shape the server never sends.
- **`warm()` reaches one worker.** With `WORKERS>1`, each worker warms itself, and retries from `error` every 5 minutes.

### Rulings the owner may want to reverse

Every ruling with its cost is in the ledger. The ones that shape behaviour:

- "Go ahead" (HANDOVER step 4) read as permission for per-task commits. From Task 4 on, implementers staged and the controller committed.
- A short all-digit value stays unmasked in an env file (spec §3.2's integer rule): `PASSWORD=123456` is sent.
- A blocked microphone stays pressable, with its reason, where the spec says disabled.
- Switching models after Send real values hides that model's replies from the new model.
- A file too big for the model's window is sent, with a note, not refused.
- Lower bounds `av>=19.0` and `numpy>=2.5` are what the code was tested on.

### Waiting on the owner now

- **A ruling on spec §2's CPU anti-goal.** On this CPU-only machine a local model's reply took about 1.5-2× as long during a transcription, and narration about 1.6-2.4×. A cloud model wasn't slowed. The spec says it fails if chat is "noticeably slower for everyone else on a CPU-only box". `SPEECH_CPU_THREADS` now defaults to half the cores across all workers. The owner decides whether that's acceptable, or `cores // 4` is worth trying.
- Merge the stack; deploy with a database backup; the `docling` realm role in the live Keycloak. Migration 024 is already on live, and the 22:43 log applied only 024, so 023 was there before it.
- Find what auto-pushes. W3: the header scrolls sideways at 375 px (the composer itself fits).
- `/v1/synthesize` hardcodes `lang="en-us"` (`server/endpoints.py:79`, `:139`), so non-English Kokoro voices come out wrong through the app. Outside this plan.
- `server/auth/touch.py:22` takes a second pooled connection while auth holds the first: existing, outside this branch. A reviewer reproduced a `PoolTimeout` at pool size 2; not tried at the real 10.
- A model can pass text to `READER_MODEL` in a written request, so a model holding real values could forward them. Read from code, not run.
- `src/components/admin/AdminPanel.jsx` is dead code; `af_river` is labelled Male though Kokoro's `af_` means American female (unverified).
- The cached Sinhala and Tamil Whisper models are in Transformers format and need `ct2-transformers-converter` before faster-whisper can load them (DEPLOYMENT, "Choosing a model"). `large-v3-turbo` loads as it is. Their quality here is unmeasured, and it's the natural next test.

### Next, unchanged from the order below

Phase 2b (the embedding provider), then figures with a vision model and the DeepSeek-OCR preset.

---

## 2026-10-27 — Phase 3 merged; how we work, and the recommended order of work

**State.** `feat/admin-console-v2-phase3` was fast-forwarded into `development` (`85b85d6`) on the owner's "merge"; the gates on the merged tree were backend `2447 passed, 16 skipped, 2 deselected` (scratch 5434), frontend `1190 passed`, eslint clean. The local branch was deleted (its origin copy is identical). `d7f106d` adds the design and plan for the message box inputs. **`origin/development` is at `85b85d6`: the unidentified auto-pusher pushed `development` itself**, so phases 1, 2a and 3 are public; nobody here ran `git push`. **Not deployed:** live still runs an older build; deploying phase 3 needs a database backup first (migration 023; its Down SQL is in the file's header).

### How a piece of work goes (the procedure we follow)

1. **Brainstorm** what the owner wants, or skip the questions when the owner says "go with your recommendations"; the designer's calls are then listed as decisions in the spec.
2. **Spec** in `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`: why, goals, non-goals, anti-goals, constraints, decisions with their cost if wrong, what's not verified.
3. **Plan** in `docs/superpowers/plans/YYYY-MM-DD-<topic>.md`: tasks with exact names, signatures and tests; a Review Focus list; the walk before the docs.
4. **The owner reviews and says "Go ahead".** That is the permission for per-task commits on a feature branch cut from `development`, and nothing more.
5. **Execution, subagent-driven by default:** a fresh implementer per task, a task review, a fix loop; a ledger with every ruling in `.superpowers/sdd/<plan>/progress.md` (git-ignored).
6. **Walk the user's journey in the running app** (scratch DB, port 8100, no compose Keycloak), naming the control for each step. A step with no control is a gap, said plainly.
7. **Docs** from the walk's results, then a whole-branch final review on the most capable model, one fix wave, a re-review.
8. **A HANDOVER entry**, then the finishing menu (merge locally / push and PR / keep). **Merge, push, deploy and every commit outside step 4 need the owner's word, each time.**

The environment and safety rules (test DB, Keycloak, secrets, PIDs) are in the 2026-10-25 entry's "Environment and safety rules"; they still hold.

### Recommended order of work

1. **Fix: Docling's "Images: embed" keeps no pictures** (done on `fix/docling-keep-pictures`; read from the code and the installed Docling 2.142.1, not run). We set `generate_picture_images`, but `export_to_markdown`'s default `image_mode` is `PLACEHOLDER`, so every picture becomes `<!-- image -->` and the crops are dropped; the README promises embedded images. Check first: a document converted with embed shows no pictures in **MD** view. Small, user-visible, and the owner uses this option, so it goes first. ("Describe" works, but with Docling's built-in SmolVLM-256M, not a model we choose.)
2. **Message box inputs** (`docs/superpowers/plans/2026-10-27-chat-composer-inputs.md`, 9 tasks; built, see the 2026-11-02 entry): text files with secrets masked in the browser; voice input that transcribes into the box (faster-whisper on the server, or any OpenAI-compatible transcription server), off until an admin turns it on. Native audio models (gemma4 and others, many languages, any provider) plug in later through the same `Transcriber` interface (spec §6).
3. **Admin console v2 phase 2b: the embedding provider.** Indexing still calls Ollama at `OLLAMA_URL` whatever `INFERENCE_PROVIDERS` says, so a vLLM-only or Claude-only deployment can't index documents. It also unblocks the embedding-model trials (backlog item 4).
4. **Figures, designed after 2b:** keep each picture beside the converted document (shown in MD view, same access rules); describe it with a vision model the admin picks (gemma4 or a cloud one; Docling 2.142.1's `PictureDescriptionApiOptions` takes any OpenAI-compatible URL, with `enable_remote_services=True`); let a chat model that can see images look at the figure itself when a search lands on it. **DeepSeek-OCR** as a conversion preset for scans, tables and formulas: Docling ships it (`VLM_CONVERT_DEEPSEEK_OCR`, `deepseek-ocr:3b` through Ollama's `/v1`), and wiring it needs the four changes in the 2026-10-26 research note. No setting turns it on today. Its CPU cost is unmeasured.
5. **Later, by value:**
   - Robustness: replies that survive closing the tab (LibreChat's resumable streams); a backup **restore** actually tested; a lock around migrations (`WORKERS>1`); citation quality (87.9% against a 90% target).
   - Providers: when a provider's `/models` fails, fall back to its `INFERENCE_<NAME>_MODELS` list (otherwise the picker silently drops it; Anthropic's OpenAI-compatible layer may do this, unverified); per-provider parameters to drop or add; a native Anthropic adapter if Claude becomes a real option (prompt caching suits our stable prompt prefix; the compatibility layer has none, ignores `reasoning_effort` and strips audio).
   - From LibreChat and LLMRouter: chat search and export, edit and regenerate, saved assistant roles, prices per model (turns phase 3's token counts into money), an Auto model choosing by simple rules.
   - The owner's backlog: bulk upload into a project, auto tags, MD and PDF side by side, MCP, agents.

### Provider notes (for the DEPLOYMENT.md pass)

- **vLLM:** `KIND=openai`, URL ending `/v1`. It needs `--enable-auto-tool-choice --tool-call-parser <family>` (e.g. `hermes` for Qwen), or the app turns the model's tools off and it never searches the document. It needs a GPU, which this machine doesn't have. vLLM and LiteLLM are still unrun against this app.
- **Unsloth:** its GGUF quants run through the existing Ollama (`ollama pull hf.co/unsloth/<repo>:Q4_K_M`, then listed under `local:`); Unsloth Studio is an OpenAI-compatible server with `sk-unsloth-…` keys. Neither has been run here.
- **OpenAI:** set `INFERENCE_OPENAI_MODELS`, or the picker lists its embedding, TTS and image models too.
- **Claude:** `https://api.anthropic.com/v1` with `KIND=openai` works for chat, tools, images and token counts per Anthropic's docs; it is a testing layer by Anthropic's own description. The app sends no `temperature` (Claude 4.7+ rejects values below 1).
- **Audio in Ollama:** 0.40.8 lists `audio` for gemma4:e4b, but its API has no audio field (ollama/ollama#11798) and gemma4 audio regressed into made-up transcripts (#16584); hence transcription first.

### Still waiting on the owner

- Deploy (database backup first); the `docling` realm role in the live Keycloak.
- Find what auto-pushes (VS Code's sync is the suspect).
- W3: the header scrolls sideways at 375 px.
- A cloud eval before the next release.
- Keep or delete the SDD workspaces under `.superpowers/sdd/`.

---

## 2026-10-26 — Admin console v2, phase 3 (usage by model) built, walked and final-reviewed (merged 2026-10-27, see above)

**Branch:** `feat/admin-console-v2-phase3`, cut from `development` at `8e13803` (phase 2a merged). **Not merged into `development`, not deployed, no version bump; nobody here ran `git push`** (CHANGELOG entries sit under `[Unreleased]`). The owner's "Go ahead" on the subagent-driven plan was read as approval of its per-task commits on this feature branch (ledger opening ruling). **The branch is on origin up to `99a0bc0` anyway** (`git reflog show origin/feat/admin-console-v2-phase3`: one "update by push"), the same unidentified pusher as for phases 1 and 2a (see the entry below; VS Code's sync is the suspect). Everything after `99a0bc0`, including this docs commit, was local when this was written. Plan: `docs/superpowers/plans/2026-10-26-admin-console-v2-phase3.md`. Workspace ledger (rulings, every review finding and which were deferred): `.superpowers/sdd/2026-10-26-admin-console-v2-phase3/progress.md`, with each task's brief, report and review diff beside it, and the walk in `task-6-walk-report.md`. Spec: `docs/superpowers/specs/2026-10-25-admin-console-v2-design.md`.

**Commits** (`git log --oneline 8e13803..HEAD`, newest first; the docs commit that carries this entry follows them):

- `9e7fd7f` fix(admin): a person's models follow the Model filter (R5)
- `94a08f3` test(admin): Usage race tests flush and can fail
- `d561de3` feat(admin): Usage by model, person and day
- `a1aea8a` refactor(usage): admin_usage is the user_day grouping
- `f68fc68` feat(admin): usage grouped by model, person and day, tagged local or cloud
- `99a0bc0` feat(usage): chat and reader writes name their model and kind
- `78ba314` feat(usage): record usage per model and kind (migration 023)

**Gates on the final code tree (`9e7fd7f`, 2026-10-26):** backend `2447 passed, 16 skipped, 2 deselected` (was 2418 at the end of phase 2a), run with `TEST_DATABASE_URL` and `TEST_ADMIN_DATABASE_URL` pointing at the scratch Postgres on 5434; frontend `1190 passed` in 119 files (was 1170 in 118); `npx eslint .` clean. Every task was reviewed on its own diff and ended review-clean. **Whole-branch final review (`8e13803..89ed8a5`): done, verdict "with fixes".** Its findings were docs and a rollback script, no behaviour change; they were applied in one docs-only fix wave (below). The gates above are unchanged by it (the wave touches docs and a comment header in the migration; backend re-run after it, same counts).

**Final fix wave (commit `docs: final review fixes — usage docs, rollback Down for 023`, on top of `89ed8a5`):**
- `docs/DEPLOYMENT.md`: the reader-usage paragraph no longer says usage can't name the model; batches are booked to the reader model's own row (kind `reader`), and the `reader usage` log line still names it.
- `docs/ARCHITECTURE.md`: `is_local` feeds the Usage **Where** column (computed at read time by the admin usage route), not "usage later".
- `CHANGELOG.md` and `docs/USER_GUIDE.md`: an unplaceable reader model is a **Where** = Unknown case (the Model cell shows the bare name); By person shows the same token figures without Model and Where; **Requests** = model calls (each chat step, each reader batch; a tool-using reply makes several); the old Prompt/Eval columns and person-by-day rows are gone from the UI but `GET /v1/admin/inference/usage` with no `group` still returns them (`group=day&user_id=<id>` gives one person's days).
- **Rollback:** `server/sql/023_usage_by_model.sql` now carries runnable Down SQL in its header (one transaction: aggregate per `(user_id, day)`, restore the old key, drop the columns, `DELETE FROM schema_migrations WHERE version = 23`). Proved on a throwaway database on 5434 (merge sums right, old PK back, no model/kind/estimated_tokens, up re-applies); script and output in `.superpowers/sdd/2026-10-26-admin-console-v2-phase3/final-fix-report.md`. CHANGELOG Upgrade notes say plainly that the previous image against a migrated database without the Down makes every usage write fail (logged, fail-open), so budgets stop counting new usage, and the only sign is a log warning. Editing the header is safe: `server/db.py` tracks versions in `schema_migrations`, not checksums.

### What a person can do now (and where)

- **See usage by model** (Admin -> **Usage**, which now opens on **By model**): one row per model with **Where** (Local / Cloud / Unknown), **Chat** and **Reader** tokens, **Requests**, **Total** and **Estimated**.
- **See usage by person** (**By person**) and open one person's own per-model rows (the arrow on their row, label **Models for** their address).
- **See usage by day** (**By day**), days counted in the deployment's timezone.
- **Narrow By person and By day to one model** (the **Model** select: **All models**, a model, or **Unknown**). An opened person's rows follow it.
- **Change the range** (the **Usage days** select: last 1, 7, 14, 30 or 90 days). It applies to all three views and resets the **Model** select to **All models**.
- **Read the same figures by script:** `GET /v1/admin/inference/usage?days=&group=user_day|model|user|day&user_id=&model=`. No `group` returns the old per-person-per-day list (U5). `model=` empty means the Unknown rows. A bad `group` or `user_id` is a 422. `tag` appears on `group=model` rows only.
- Reading it, an admin should know: **reader tokens are booked to the reader model's own row** (the chat model's row can show **Reader** 0); **usage from before the upgrade shows as model Unknown and can't be split** (**Chat** and **Reader** read 0 while **Total** doesn't, so Chat + Reader can be less than Total); **Where follows the provider's current Local/Cloud mark**, so changing the mark re-tags history; **Estimated** is the share of tokens counted from text length because the provider sent no counts (Ollama always sends counts, so with Ollama it is normally a dash); a reader model the router can't place is recorded under its bare name (the **Model** cell) and its **Where** cell shows Unknown.

**Not possible yet:** per-person *budgets* per model, usage charts, export, or an alert when a model's spend jumps. The budget is still one number per person per day, summed over every model and kind.

**Upgrade note:** migration `023_usage_by_model.sql` runs on start and rewrites `inference_usage`'s primary key to `(user_id, day, model, kind)`. Old rows keep their tokens as model `''`, kind `''` and still count toward each person's budget that day (tested: migrating mid-day changes nobody's remaining budget). **There is no automatic Down**: the file header holds a manual, tested Down SQL (run it before starting the previous image on a migrated database, or its usage writes fail and budgets stop counting). Back up the database before deploying, as for 2a. The live site does not run this branch.

### Rulings (full text in the ledger and the plan; "cost" = what changing it later touches)

Made while building (R, ledger):
- **R1.** Task 1 left the orchestrator's writer call alone, so 16 orchestrator tests were red between Task 1 and Task 2. Cost: bisect noise on one commit (`78ba314`).
- **R2.** The "Usage recorded before this version has no model and shows as Unknown." note shows whenever the range's By model data contains a model-less row, in any view. Cost: harmless copy on a view whose rows show no Unknown.
- **R3.** The walk seeds usage with real turns on cloud or already-pulled small models, never local `llama3.2:3b`. Cost: the walk proved the UI on mostly writer-produced rows plus one seeded pre-upgrade row.
- **R4.** The walk ran before the docs, so the docs describe the journey as walked. Cost: none beyond order.
- **R5.** A person's expanded rows filter the cached per-person rows client-side to the **Model** select (still one fetch per person per range). Replaced an earlier "expansion ignores the filter". Cost: an admin who wants all of a person's models clears the select (one click).

Decisions in the plan (U):
- **U1.** Old rows get model `''` and kind `''`, both shown as Unknown (they mix chat and reader, so calling them chat would misreport).
- **U2.** The model is stored canonical (`provider:name`), so `gemma4:e4b` and `ollama:gemma4:e4b` are one row; an unplaceable reader keeps its name.
- **U3.** The tag is computed at read time from the provider's current mark, not stored. Cost: history re-tags; storing it would freeze the old mark.
- **U4.** Estimated = estimated tokens / tokens, shown as a whole percent, a dash for none, `<1%` for a small share.
- **U5.** No `group` means the old shape.

### What was walked, and what was NOT verified

- **Walked in the running app (plan Task 6, at `94a08f3`, `task-6-walk-report.md`):** backend on 8100 against a scratch database (migrated 001-023 by the app), Vite, auth off, Playwright at 1280 and 375 px. **Admin** button -> **Usage** tab -> **By model** (both models with right tags, the cloud one carrying the Reader figure), **By person** with the **Models for admin@localhost** disclosure (matched By model), the **Model** select on By person and By day, the **Usage days** select, and 375 px (no Usage table scrolls sideways; every control at least 44 px). Seeded: one pre-upgrade row; everything else was written by real turns.
- **Walk defect W1** (a person's expansion ignored the **Model** select) was fixed afterwards in `9e7fd7f` (R5).
- **Re-walked after the final fix wave (at `ec06db0`, `task-6-rewalk-report.md`), all six steps passed:** two people (a second user and 9 usage rows seeded by SQL, labelled seeded). **By person** sorted by Total, each person equal to their seeded sums; both **Models for <email>** disclosures open under **All models**, each adding up to its person row (one `user_id=` request per person); picking a model in the **Model** select left only that model's row in each open expansion, equal to the row above, with no new `user_id=` request; **Unknown** left only the person with a pre-upgrade row; **Usage days** closed the expansions, reset the filter, and a reopen fetched once per person. A production build (`vite build` + `vite preview`) sent `group=model` **once** on first open, so the doubled request seen in dev is React StrictMode only.
- **Not verified:**
  - **The Estimated percentage.** Seen only in unit tests: Ollama always sends counts, so both walks saw only dashes.
  - An expanded person who drops out under a filter and comes back; a removed provider and a changed Local/Cloud mark in the browser (backend-tested); a non-admin refused; dark mode.
  - No cloud eval was run for this phase. It touches the reader's model string (now the canonical `provider:name` key the router resolves) but no prompt; the spec's pre-release eval is still owed for the release.
- **Walk observations, not fixed:** the page is 390 px wide at 375 px on every admin view, from the app header's button group (pre-existing, phase 2a W3, not Usage); model names wrap mid-word at 375 px (`break-all`); table text is 10 px; the Estimated note shows whenever rows exist, even when every cell is a dash.
- **Deferred minor review findings** (none blocks; ledger has the rest): `KINDS` is defined mid-module in `inference_budget.py`; no router test pins the day window to `today(conn)` (it would pass with `CURRENT_DATE`); equal-token tie-breaks, `user_day` with `user_id`/`model`, and the days clamp with `group` are untested; the `group` vocabulary lives in three places; in By person and By day a failed load shows two toasts and "No usage recorded yet."; the disclosure has no `aria-controls` and the nested table no label; if the By model fetch fails the **Model** select collapses to **All models** unexplained; the **Model** select persists across the view buttons; an expansion filtered to nothing says "No usage recorded yet."; one orchestrator test doesn't assert `estimated_tokens` on the `_finalize` path.

### Open items

1. **Merge** (the final review, its fix wave, the scoped re-review and the W1 re-walk are all done): `feat/admin-console-v2-phase3` into `development`: merge and push both need the owner's go-ahead. `development` itself is still unpushed with phases 1 and 2a on it.
2. **Docs that keep the old wording on purpose:** the older `docs/superpowers/handoff/2026-10-18-project-chat/HANDOFF.md` and the design spec's problem statement keep "no model column" as history, on purpose.
3. **Deploy:** needs a database backup first (migration 023; the Down SQL in its header is the rollback). As of the phase 2a entry, live ran a build from before that phase's final fixes; check what it runs now.
4. **Phase 2b** (embedding engine and model) is next in the design; the header scroll at 375 px (W3) is still open.

---

## 2026-10-26 — Admin console v2, phase 2a built, walked and final-reviewed; merged into development

**Branch:** `feat/admin-console-v2-phase2a`, cut from `development` at `2fadfa2`. **Fast-forward merged into `development` on 2026-10-26; `development` is not pushed; no version bump** (CHANGELOG entries sit under `[Unreleased]`). **The feature branch reached origin without anyone running `git push` here** (`git reflog show origin/feat/admin-console-v2-phase2a`: seven "update by push" entries from 10:13 to 16:22, each a few minutes after a commit), as happened to phase 1's branch; the branch config carries `vscode-merge-base`, so VS Code's sync is the suspect. **Deployed anyway:** the live site has run this branch since 2026-10-26 11:39, from an image built at `0f9aec7` (before the final review); the final-review fixes are not deployed yet. Plan: `docs/superpowers/plans/2026-10-26-admin-console-v2-phase2a.md`. Workspace ledger (rulings, every review finding and which ones were deferred): `.superpowers/sdd/2026-10-26-admin-console-v2-phase2a/progress.md`, with each task's brief, report and review diff beside it. Spec: `docs/superpowers/specs/2026-10-25-admin-console-v2-design.md`.

**Commits** (`git log --oneline 2fadfa2..HEAD`, newest first; the docs commit that carries this entry follows them):

- `87a74bb` docs: the handover says live runs this branch, and records P11-P13 (renumbered P15-P17 below)
- `aec2add` fix(settings): show the whole Auto label, and the admin's size on a locked row
- `3f3ded4` fix(admin): the console's Reader must be a model the deployment allows
- `772d0b5` fix(models): the Cloud mark is cost-only; a self-hosted Ollama is always sized
- `fd7a659`, `cc325e1`, `571dc5b`, `fbc23f0`, `0f9aec7` docs: phase 2a docs and their review rounds
- `88484f4` feat(admin): search and filter People on the server
- `658423e` feat(settings): show the context size the server will use for a local model
- `0c0bdfc` fix(admin): a hidden invalid size no longer blocks Save in Models
- `bbe601a` feat(admin): the Models section
- `5d6dcfc` fix(admin): the reader line never fails because the model list does
- `fcfc134` feat(admin): Overview's reader and context lines follow the Models settings
- `dbde911` feat(admin): GET /v1/admin/models, the catalog the Models section chooses from
- `f87b38a` feat(models): the console narrows the chat models; the model list reports each local model's context size
- `7d667c8` fix(reader): a reader on a non-sized chat model sends no context size
- `facbe87` fix(reader): fall back to the chat model, choose it in the console, size it to its model
- `cc8a906` fix(chat): one context size per local Ollama model, and trim history to it
- `6aa3c9e` test(models): an empty host after the trailing-dot strip is cloud
- `c964d80` fix(models): size every self-hosted Ollama model, not only local-looking hosts
- `b1f74af` fix(models): count .local/.lan/.internal/.home.arpa hosts and 100.64/10 as local
- `80a86cb` feat(admin): the models settings section and its resolution rules
- `03acb1d` docs: admin console v2 phase 2a plan (Models, People search)
- `42bdcd6` fix(admin): say which Keycloak role to create when a capability's realm role is missing

**Gates on the final tree (2026-10-26, after the final-review fixes and the `summarize_model` removal):** backend `2418 passed, 16 skipped, 2 deselected` (was 2281 at the end of phase 1), run with `TEST_DATABASE_URL` and `TEST_ADMIN_DATABASE_URL` pointing at the scratch Postgres on 5434; frontend `1170 passed` in 118 files (was 1121 in 117); `npx eslint .` clean. Every task was reviewed on its own diff; the whole-branch final review (opus, `final-review.md` in the workspace) found no Critical and two Important issues, both fixed and re-reviewed (`final-fix-rereview.md`).

### What a person can do now (and where)

- **Choose the reader** (Admin → Models → **Reader**): the chat model of each conversation (the default) or any listed model. With none of `READER_MODEL`, `SUMMARIZE_MODEL`, `WEB_SEARCH_SUMMARY_MODEL` set, the reader is the chat model, no longer `llama3.2:3b`.
- **Set the context size of self-hosted Ollama models** (Models → **Context size for local Ollama models**, and **Per-model sizes**). Every request to such a model names a size (the per-model size, else the deployment's, else the person's own value, else 16,384; at most `INFERENCE_NUM_CTX_MAX`), history is trimmed to it, and a reader that is the chat model uses it too, so Ollama stops reloading the model between chat and reader calls. Env fallback: `INFERENCE_LOCAL_NUM_CTX`.
- **Declare a context window** for a model whose provider reports none (Models → **Declared context windows**, or `INFERENCE_<NAME>_CONTEXT_WINDOW` for a provider). Without one, history for that model is not trimmed and the Overview says "Unknown".
- **Mark a provider local or cloud** (Models → **Providers**: Auto / Local / Cloud). Cost flag only (the Overview's quota hint, later usage); it never changes sizing, so a self-hosted Ollama marked Cloud is still sized (final-fix ruling P15).
- **Narrow the chat models offered** (Models → **Chat models offered**). Intersected with the environment's allow-lists, never unioned. The turn route refuses a model outside it (422 `model_not_allowed`), and the picker lists only the ticked models.
- **Search People** (Admin → People: **Search people**, **Status**, **Capability**, 50 at a time with **Show more**, "Showing N of M").
- **Settings → Chat → Context window** shows "Set by your admin: N tokens." (locked) for a self-hosted Ollama model whose size the admin fixed, otherwise **Auto** names the size the server will use.
- Overview's **Reader** line is **OK** by default (it was a warning for the fallback); its **Context windows** line reports the size a request sends. Deployment's read-only list no longer has "Reading model".

**`SUMMARIZE_MODEL` and `WEB_SEARCH_SUMMARY_MODEL` are only older names for the Reader** (the reader's env chain is `READER_MODEL`, then those two). There is no separate summary model: the unused `InferenceConfig.summarize_model` field was removed; `GET /v1/admin/inference/config` still reports the reader under the old `summarize_model` key.

**Still not possible without `.env` and a restart:** adding or changing a provider; changing the embedding model (phase 2b). **Not possible at all yet:** usage by model or by person and model (phase 3, written afterwards, adds it: the usage table had no model column, so usage from before that migration can't be split). **No migration** (`models` is one more `app_settings` row).

**Upgrade notes** (also in CHANGELOG and DEPLOYMENT): the reader moves to the chat model (cloud chat model: reader calls count against its quota; pick a local reader in Models); local Ollama models get 16,384 tokens instead of Ollama's 4,096, which reserves more memory when the model loads (size it down in Models or with `INFERENCE_LOCAL_NUM_CTX`), and a model already loaded at another size reloads once; a person's own context size stops applying to those models once the admin sets one.

### Rulings (full text in the ledger and the plan; "cost" = what changing it later touches)

Made while writing the plan (R, `docs/superpowers/plans/2026-10-26-admin-console-v2-phase2a.md`):
- **R1.** The deployment's size is `INFERENCE_LOCAL_NUM_CTX`. Cost if wrong: renaming one env var.
- **R2.** Size order: per-model, deployment (console then env), the person's value, 16,384; clamped to `INFERENCE_NUM_CTX_MAX`. Cost: the owner wanting 16,384 to beat personal values moves step 3 below step 4.
- **R3.** A non-sized model (Ollama cloud, `openai`-kind) keeps the person's value and the reader sends none. Cost: none known.
- **R4.** Locality guess, first match wins: `-cloud`/`:cloud` name, the admin's override, the host, else cloud. Cost: a dotted LAN name reads cloud until overridden (P3 widened the host rule).
- **R5.** The default reader (the chat model) is OK, not a warning. Cost: one state word.
- **R6.** A chat-model selection whose models all vanished offers nothing and the Overview warns. Cost: none known.

Made during the build (P, ledger):
- **P1.** A provider's `detected` flag applies the host rule alone (no model name, no override). Cost: one label.
- **P2.** The turn route reads the `models` row on its own short pool connection, fail-open, not a new `Depends(get_conn)` on the streaming route. Cost: a second read pattern.
- **P3.** `.local`, `.lan`, `.internal`, `.home.arpa` hosts and 100.64.0.0/10 count as local; one trailing dot is ignored. Cost: a wrong Auto label on an exotic public host with such a suffix (the admin overrides).
- **P4.** Sizing applies to every self-hosted Ollama model whatever its host; locality is the cost flag only. `sizes_context` is the predicate. Cost: one predicate swap.
- **P5.** A reader batch never grows past `READER_INPUT_CHARS`; it shrinks to fit the window. Cost: one word if the owner wants window-filling batches.
- **P6.** A batch holds at most 16 passages (`MAX_BATCH_PASSAGES`). Cost: a few extra reader calls on many tiny passages.
- **P7.** A reader on a non-sized chat model sends no size, even if the person set one. Cost: one line.
- **P8.** The model list's `context_size` key stays snake_case beside camelCase `limits`. Cost: one inconsistent payload, one route and one parser to rename.
- **P9.** Deployment's read-only list drops "Reading model" (it reads only the environment and would contradict a console choice). Cost: one row.
- **P10.** The Overview's unset-size copy prints the window actually used ("8,192 tokens unless …" when the ceiling is 8,192). Cost: one string.
- **P11 (Task 7).** A valid per-model size whose provider is flipped to Cloud stays in the map. Moot since P15: flipping to Cloud no longer hides the box.
- **P12 (Task 10).** Docs review minors fixed in the docs round.

Made in the final fix:
- **P15.** `sizes_context` ignores the admin's Local/Cloud mark; Cloud is cost-only. Sized = `ollama`-kind, model not `-cloud`/`:cloud`, host not ollama.com. Cost if wrong: a hosted, non-ollama.com Ollama endpoint marked Cloud is sent a `num_ctx` (it loads at our size instead of its own default).
- **P16.** Saving a Reader that is not "chat" and not allowed by the deployer's allow-lists (`is_allowed`, as the chat path) is refused with 422 ("That model isn't in this deployment's model list."); the Overview's Reader line is an error when the reader, from any source, names such a model. Chat turns are unchanged. Cost: one check.
- **P17.** A Context window row locked by an admin size shows that size; the person's stored value is kept and applies again when the admin clears it. Cost: one display line.
- **P18.** The app header's sideways scroll at 375 px (walk bug W3) is parked: the header is unchanged on this branch, so the bug predates it. Cost: a later small CSS fix.

### What was walked, and what was NOT verified

- **Walked in the running app (plan Task 11, before the final fix; `task-11-walk-report.md`):** backend on 8100, scratch DB, Vite, auth off, the owner's Ollama. A document chat on `gemma4:e4b` loaded it once at 16,384 tokens (`/api/ps` and Ollama's journal: one runner, three `/api/chat` calls at `n_ctx_slot=16384`) and the reader call ran in the same runner, so **no reload between chat and reader**; an overflowing chat logged "history trimmed … (window 16384)". Models: set, save, reload, **Use the server setting** for Reader, Context size (validation too), Per-model sizes, Providers, Chat models offered; narrowing to one model left one in the picker and a turn on another got 422 `model_not_allowed`. Settings lock strings. People search by first and last name and Pending, **Show more** to 61 of 61. 0 of 542 Models/People controls under 44 px at 375 px.
- **Walk bugs:** W1 (clipped Auto label) and W2 (locked row showed the stored personal value) fixed in `aec2add`; W3 (header sideways scroll at 375 px) parked, P18.
- **Not walked:** the final-review fixes themselves (Cloud-marked sizing, Reader refusal, the two Settings fixes are unit-tested only); dark mode; a non-admin refused the admin routes in the browser; the Overview's env-sourced strings; Declared context windows (both walk models report their own window, so the row had no box).
- **No cloud eval was run.** Spec §5 requires one before a release, on `ollama:gemma4:31b-cloud` or OpenRouter (never local `llama3.2:3b`); it needs the owner's go-ahead. The one final local `gemma4:e4b` run is still owed too.
- **The 16,384 default's memory cost on the owner's machine** was not measured.
- **Deferred minor review findings** (none blocks; ledger has the rest): `test_inference_router.py` defines the `client` fixture twice and the later one wins, so every test in it runs as the seed admin; a SQL error reading the `models` row on `GET /v1/inference/models` aborts that request's transaction, so the budget is omitted too, and `GET /v1/admin/inference/config` returns 500 if the row is unreadable; the turn route holds one extra pool connection per turn; the client ignores `num_ctx_max` when validating a size (the server refuses; a toast); People: a reload racing **Show more** can drop the appended page, a failed load reads "Showing 0 of 0", a search containing a NUL character is likely a 500 not a 422, past 200 loaded rows a reload after an edit collapses to the first 200; the Overview logs a warning with a traceback per load while a provider hangs.

### Open items

1. **Push `development`**: needs the owner's go-ahead. It carries phase 1 and phase 2a; v2.4.0 is not deployed. Find what auto-pushes feature branches before the next one.
2. **Deploy the final-review fixes.** Live runs `0f9aec7` (since 11:39), which has the I1 bug (a self-hosted Ollama marked Cloud gets no size) and accepts any Reader through the API. Rebuilding from this branch's head fixes both.
3. **Live config worth setting now:** one deployment context size under Models (two people with different personal sizes otherwise reload the same model between their turns); a small local Reader if people chat on a cloud model (reader calls count against its quota) or on a large local one (every batch runs through it, slow on CPU), with `OLLAMA_MAX_LOADED_MODELS` ≥ 3 so the chat, reader and embedding models don't evict each other.
4. **Phase 3 (usage by model) is planned:** `docs/superpowers/plans/2026-10-26-admin-console-v2-phase3.md`, branch `feat/admin-console-v2-phase3` from `development`; awaiting the owner's review of the plan. **Phase 2b** (embedding engine and model) follows.
5. **The live Keycloak still lacks the `docling` realm role** (needed only for `DOCLING_CONVERT_BY=role`); People now says how to create it. W3, the header scroll at 375 px, is open.

---

## 2026-10-26 — Admin console v2, phase 1 built and walked

**Branch:** `feat/admin-console-v2`, cut from `development` at `9459201`. **Fast-forward merged into `development` on 2026-10-26** (`git log --oneline 9459201..development` lists the plan's commit first, then the tasks, the final-review fixes and this entry). **`development` is not pushed** (origin/development is still `955231d`). **Nothing deployed.** **The feature branch was pushed:** not by us. Something pushed this branch to origin four times with nobody running `git push` here: `328f68f` at 23:34:59 on 2026-10-25, then `7637c9a` (00:02:31), `469d2d8` (00:07:14) and `292a0ce` (00:07:47) on 2026-10-26 (from `git reflog show origin/feat/admin-console-v2`, all "update by push"). The cause is unknown (no git hook or Claude hook pushes this repo; VS Code's publish/sync is the suspect). Everything after `292a0ce`, including this docs commit, is local only. The owner never granted a push.

**Gates at `9a3f569` (the last code commit, 2026-10-26):** backend `2281 passed, 16 skipped, 2 deselected` (was 2139 passed at `9127981`), frontend `1121 passed` in 117 files (was 1022), `npx eslint src` clean.

### What a person can do now (and where)

Admins open the console with the **shield button in the header** (the profile menu has no Admin entry; the plan text said it had). It opens on **Overview**, with sections Overview, People, Projects, Documents, Chat, Defaults for users, Usage, Deployment (a picker on a phone).

- **See whether the deployment works.** Overview runs six checks (providers, reader, context windows, embeddings, Docling, web search), each OK / Warning / Problem / Off, in at most 5 seconds each, concurrently. **Check again** reruns them.
- **Set the deployment's timezone** (Deployment section). It decides the daily token budget's day (reset at the next local midnight, also on a daylight-saving night), the usage days, the "Today is" line without a browser zone, and People's dates. Console value, then `TZ`, then UTC. `tzdata` is now in `requirements.txt` (Windows).
- **Limit Docling conversion** (Documents section): who may convert, presets, OCR, picture descriptions, a page limit. The convert dialog greys out what is off ("Turned off by your admin"), and the server refuses a request that breaks a rule (403 `convert_not_allowed`).
- **Every setting in those sections has one look**: a source line (Set here. / From the server setting `VAR`. / The default.), **Use the server setting**, one Save, an unsaved-changes prompt. Each saved change writes an audit line.
- **No migration.** Settings are `app_settings` rows `deployment` and `documents`.

**What a user still can't do until a later phase:** choose the reader model or context sizes in the console (phase 2a), change the embedding model in the console (2b), see usage by model (3). The reader/context problems found on 2026-10-25 (a local model silently cut to 4k; the reader reloading the model) are still unfixed: they are 2a.

### Rulings that changed the plan (full text in `.superpowers/sdd/2026-10-25-admin-console-v2-phase1/progress.md`)

- **"Who may convert" is `readers` or `role`**, not "admins". `role` means people with the new **`docling` capability, a Keycloak realm role** (admins can always convert); the default `readers` keeps today's behaviour. `DOCLING_CONVERT_BY=readers|role`. The owner hadn't answered the question when Task 4 came up, so this was built on the recommendation: if wrong, revert the vocabulary entry, the realm-export role and the People checkbox and swap "role" back to "admins" (one task's worth).
- **The `documents` token scope carries `docling`** (conversion is document work), so a role holder's documents-scoped token converts. Other scopes don't.
- **OCR off refuses Accurate too**, because `docling_convert` runs OCR for Accurate whatever the flag.
- **An existing Keycloak realm must have the `docling` realm role created by hand.** The realm export only seeds a fresh database. Until then ticking docling under People fails at the Keycloak write (a 502, capability unchanged) while the Keycloak admin client is configured. A role granted in Keycloak applies at the person's next login; one ticked in People applies at once. Only needed for `role` mode.
- **The Overview reads the database first, then runs the network checks** (a check cancelled mid-query would break the connection).
- **Env fallbacks:** `DOCLING_CONVERT_BY=readers`, `DOCLING_PRESETS=fast,standard,accurate`, `DOCLING_ALLOW_OCR=true`, `DOCLING_ALLOW_PICTURE_DESCRIPTION=true`, `DOCLING_MAX_PAGES=0` (0 = no limit). The env value is the outer bound; the console can only narrow it.
- **The walk backend ran on port 8100**, never 8000: nginx on this host proxies the live site's `/v1` to 8000, and an `AUTH_ENABLED=false` backend there would serve the seed admin to the public.
- **44 px targets** were brought to the moved sections (People, Projects, Chat, Defaults, Usage) with sizing classes only, after the walk measured 13-34 px there (`1100e71`).
- Task 6's profile test asserts `>= n + 900`, not the plan's `n + 1000` (the plan's arithmetic was wrong; the delta is 999 tokens).

### What was walked, and what wasn't

Walked in the running app (headless Chromium, scratch database on 5434, `AUTH_ENABLED=false`, report at `.superpowers/sdd/2026-10-25-admin-console-v2-phase1/task-13a-walk-report.md`): Overview and each state; `/api/ps` really carries an integer `context_length` and a `name` (nomic-embed-text: 2048); Check again; the timezone, the unsaved prompt (Keep editing, Escape, phone variant), Save, reload, People's dates in Colombo, Use the server setting; Documents with OCR off and a limit of 2; the Convert dialog's greyed options and page gate, then a real 2-page conversion; the server's 403s by `curl` (6 pages, Accurate with OCR off, forced OCR); phone picker; keyboard (Arrow, Home, End, wrap); a canary `SESSION_SECRET` found nowhere in the page or the API payload.

**Not walked:**
- **A non-admin refused by "Only people with the Docling role."** It needs a second signed-in user, which `AUTH_ENABLED=false` can't give. Covered by `test_convert_limits.py` only. Walk it with the throwaway Keycloak on `:18081`, never the compose one.
- **The Context windows row of a *loaded* local chat model.** The `/api/ps` shape is confirmed live; the row itself is tested with fakes. Load a local model with one message, then Check again: it should read "Loaded now (Ollama)."
- **The 44 px fix** (`1100e71`) is class-tested (`phoneTargets.test.jsx`) but not re-measured in a browser; the walk predates it.
- Dark mode; the Keycloak write when a `docling` role doesn't exist yet.

### Open items

1. ~~Merge into `development`~~ (done 2026-10-26, fast-forward). Still open: push `development`, and find out what pushed the feature branch.
2. **Deploy** is a separate decision (v2.4.0 itself is also not deployed; see the 2026-10-25 entry). This work needs no migration. For `role` mode, create the `docling` realm role first. **Upgrade note: if the backend's environment sets `TZ` (this host's `.env` does), the daily token budget and the usage days follow it from the deploy, with no console action: budgets reset at local midnight instead of UTC midnight, the switch day gives an early or late reset, and usage days shift. To keep UTC days, choose UTC under Deployment.**
3. **Deferred review findings** (minor, none blocks; the ledger has the rest):
   - `GET /v1/admin/inference/config` (`admin.py`, from before this work) calls `urlsplit(...).hostname` unguarded: a malformed provider URL is a 500.
   - (Fixed after the final review: the embeddings check now makes its own request, so it no longer waits on the indexing semaphore, and a 404 reads "answered HTTP 404".)
   - A scheme-less URL (`OLLAMA_URL=localhost:11434`) shows an empty host in the Overview and Deployment lists.
   - A page limit with a PDF pdfium can't open skips the limit; a range starting past the end counts negative pages.
   - A day boundary in a zone whose local midnight doesn't exist (a DST gap at 00:00, e.g. America/Havana) is about an hour off.
   - Console a11y: `Check again` loses focus while it runs and there is no live region; small amber/green text is about 3:1 contrast; "Use the server setting" unmounts and drops focus; two `<h1>`s while the console is open (pre-existing).
   - An invalid page-limit text alone doesn't count as unsaved; edits made during a save need a second Save with no prompt.
   - Some scoped tokens (`read_aloud`, `chat`) drop `docling` by design; only `documents` carries it.
4. **Phase 2a** (reader falls back to the chat model, one context size per local model) is next in the spec; the §7 decisions are approved.
5. The old plan workspace `.superpowers/sdd/2026-10-25-admin-console-v2-phase1/` can be kept or deleted; the `nr-c21-testpg` scratch container is still running.

### Research note: Docling can already drive DeepSeek-OCR (read from the installed code, not run)

The installed Docling 2.142.1 ships a VLM-convert preset `deepseek_ocr` (`docling/datamodel/stage_model_specs.py` `VLM_CONVERT_DEEPSEEK_OCR`): the VLM pipeline renders each page at scale 2.0 and sends it to Ollama's OpenAI-compatible `/v1/chat/completions` with model `deepseek-ocr:3b` (pulled locally, 6.7 GB), prompt `<|grounding|>Convert the document to markdown.`, `max_tokens` 4096, and parses the reply with `ResponseFormat.DEEPSEEKOCR_MARKDOWN`. Docling 2.142 also has a legacy `ApiVlmOptions` form, `vlm_model_specs.DEEPSEEKOCR_OLLAMA` (timeout 90 s, concurrency 4).

Our code does not use it. `docling_convert._build_converter` gives **Accurate** a bare `VlmPipelineOptions()`, whose default is the `granite_docling` preset run in-process (transformers, on this CPU-only machine). Wiring DeepSeek in as a preset needs four things:

- **`enable_remote_services=True`**: Docling refuses any API VLM without it, even on localhost.
- **The Ollama URL from our own config.** The preset defaults to `localhost:11434`, which inside the backend container is the container itself.
- **A per-page timeout sized for CPU.** No timing has been measured.
- **A place in the console's allow-list.** It reads page images, so the "OCR off" rule should refuse it too.

It also loads a 6.7 GB model into the same Ollama as chat and embeddings, which is the reload problem phase 2a deals with. Backlog item 3 (DeepSeek-OCR vs Docling) starts here.

### Where to look

- `CHANGELOG.md` `## [Unreleased]` (written for users), `docs/USER_GUIDE.md` § Admin console, `docs/ARCHITECTURE.md` (the five new services `console_settings`, `deployment_settings`, `deployment_info`, `docling_limits`, `console_overview`; routes `GET`/`PUT /v1/admin/settings/{section}`, `GET /v1/admin/overview`, `GET /v1/settings/docling`), `docs/IDENTITY_AND_ROLES.md` § The `docling` capability, `docs/TESTING.md` § Admin console v2, phase 1.
- Spec: `docs/superpowers/specs/2026-10-25-admin-console-v2-design.md` (phase 1 is §6 item 1). Plan: `docs/superpowers/plans/2026-10-25-admin-console-v2-phase1.md`.
- The environment and safety rules in the 2026-10-25 entry below still apply unchanged (scratch DB on 5434, never 5433, never the compose Keycloak, never `pkill -f run.py`).

---

## 2026-10-25 — v2.4.0 released; six post-release fixes; eval "drift" diagnosed; admin console v2 designed

**Branch:** `fix/folder-index-docling-progress-doc-refs`, cut from `development` at `955231d`. **Commits on it:** `6ef6ef5`, `9127981`, and the commit that added this entry and the spec. **Not merged into `development`, not pushed.** **Tests at `9127981`:** backend 2139 passed and 16 skipped, frontend 1022 passed, eslint clean. **Releases:** v2.4.0 is on `master` (`ff71f75`), tagged, pushed, and is the GitHub release marked Latest; v2.3.0 also has a GitHub release now. **The live site is NOT redeployed** (see "Deploying 2.4.0" below).

### Starting a fresh session

Paste this as the first message:

> Read `HANDOVER.md` (the 2026-10-25 entry) and `docs/superpowers/specs/2026-10-25-admin-console-v2-design.md`. I approve the spec's §7 decisions [or: change X]. Write the implementation plan for **phase 1** of that spec with superpowers:writing-plans, on a new branch from `development` after merging the fix branch.

Before that session builds anything, the owner decides the first two items under "Waiting on the owner".

### What this session changed (all on the fix branch)

**Commit `6ef6ef5`:**

1. **Index works for a file opened with Open folder.** The reader keeps a workspace file's bytes (`docBytes` in `src/hooks/usePdfEngine.js`). `src/utils/docHash.js` `readOpenDocBytes` / `openDocHash` hash and upload those bytes, never a same-named library book's. Walked in the running app: folder → README.md → **Index** → "Indexed 3 chunks", stored under the file's own hash.
2. **Docling shows progress.** The convert job passes Docling 2.142's `progress_callback` (`services/docling_convert.py` `progress_from_event`). `_ConversionProgress` (`routers/docs.py`) stores the latest report in `documents.conversion_progress` (**migration 022**). It writes only the latest value and has at most one write in flight, posted from Docling's thread back to the event loop. The progress lives in the database, not memory, because of `WORKERS>1`. The button reads Queued → Preparing → Converting n/N → Assembling → Enriching n/N. A conversion Docling fails on offers Retry convert. Walked with a 6-page PDF.
3. **A project chat finds "17-README.md".** `server/chat/tools/scope.py` `resolve_document` takes a document's number and name together (`17-README.md`, `17. "README.md"`) or its quoted name, but only when both name the same document. Error lists now use the map's form: `1. "Alpha.md"`.

**Commit `9127981`:**

4. **`search_documents` names a document that isn't searchable yet** the way the other tools do: `Document 16 ("…") isn't searchable yet.` (`scope.documents_named_in_project`).
5. **Convert shows Queued until Docling starts.** Uploading a new PDF runs its own indexing first, which holds the document's lock. That indexing is redundant once the PDF is converted, but it is cheap and makes the document searchable sooner, so it stays.
6. **Tool specs fit the chat they're in** (`tools.tool_spec`). A project chat gets project wording for `search_documents` and `read_document_pages`; a one-document chat no longer gets the `documents`/`document` fields its code ignored. This is the "I2" change that was reverted before release on noise. It was re-measured side by side with a control, 3 repeats on `gemma4:31b-cloud`: project eval 33/42 against 32/42, document eval 27/27 both. Kept.

### The eval "drift", diagnosed

- **Not our code.** The shipped code `26ace78` scored 38/42 on 2026-10-20 and 34/42 today, the same as HEAD.
- **Not context length for the cloud model.** A 19k-token prompt to `gemma4:31b-cloud` with no `num_ctx` kept a word from its first line; Ollama's docs say cloud models get their maximum context.
- **About 2 passes per run are the eval's own scorer.** The model now writes "The documents **do not list** …", and `_REFUSAL` (`server/evals/doc_qa.py:103`) has no "list". Rescored, today's runs come to 35–36/42.
- **The rest:** sampling noise (the eval sets no temperature; four runs on unchanged code spanned 32–34). Ollama was also upgraded locally on 2026-10-22 (0.40.8); its effect wasn't separated.
- **A real bug found on the way, for local models:**
  1. Ollama gives a local model 4k tokens of context below 24 GiB of VRAM, so always 4k on this machine.
  2. With no `num_ctx` set, the app trims history against the model's *maximum* context (`chat/orchestrator.py:684-689`), so it never trims, and Ollama silently cuts the prompt.
  3. Also, Ollama reloads a model whenever a request's `num_ctx` differs from the one it was loaded with. The reader sends a per-batch size (`chat/reader.py:320-322`), so `READER_MODEL=chat` on a local model reloads the model when chat and reader calls alternate.

  Both are in the spec's phase 2a. Neither is fixed yet.

### Admin console v2: the design (`docs/superpowers/specs/2026-10-25-admin-console-v2-design.md`, revision 2)

The console gets the Settings window's layout: nine sections, one row pattern, and each setting's source shown with a reset. Console row, then env var, then default; allow-lists can only narrow. Revision 2 folds in a review whose four must-fix findings were verified against this repository and Ollama's source:

- the 768-dimension embedding probe must never trust a returned length, because Ollama cuts any vector to `dimensions`;
- changing the embedding model builds first, then switches, with a second `vector(768)` column;
- the rebuild queue moves into the database (`FOR UPDATE SKIP LOCKED`);
- one context size per local Ollama model, with the reader sized to it.

The spec also adds an embedding provider for both provider kinds, which is what removes the dependency on Ollama.

**Phases:**

1. The shell, timezone, and Docling limits.
2. Models, in two parts: **2a** the reader falling back to the chat model, plus the context-size fixes; **2b** the embedding provider and build-then-switch.
3. Usage by model.

The §8 measurements (gemma4:e4b as the reader, reload time) are deferred by the owner. Don't run them unasked.

### Waiting on the owner

1. **Merge** the fix branch into `development` (then push?).
2. **Approve the spec's §7 decisions:**
   - the reader falls back to the chat model;
   - local Ollama models get 16,384 tokens of context, one size per model;
   - Docling stays open to everyone with the reader capability.
3. **The eval scorer:** add "list" to `_REFUSAL`. Recommended.
4. **Deploying 2.4.0 plus these fixes.** Order matters:
   - back up the Postgres volume first: the compose Keycloak goes 26.0 → 26.8.0 and migrates its database irreversibly on first start;
   - run the `embedding_model` query from the release notes;
   - migrations 020–022 apply at backend start.
5. **Still open from earlier:**
   - the one local `gemma4:e4b` eval run (only with the owner's go-ahead);
   - replace the old access token (which one is in Claude's memory, deliberately not in git);
   - remove the `nr-c21-testpg` container when done;
   - delete or keep the old plan workspace under `.superpowers/sdd/`.

### Environment and safety rules (each one was learned the hard way)

- **The real database:** `127.0.0.1:5433`, database `natural_reader`. **Never write to it, and never run tests against it.** Without the variables below, pytest defaults to 5433.
- **The scratch database** is the container `nr-c21-testpg` (`pgvector:pg16`) on `127.0.0.1:5434`. Its data is on **tmpfs**, so stopping the container wipes every database in it. Start it with `podman start nr-c21-testpg`, then run tests with:
  `TEST_ADMIN_DATABASE_URL=postgresql://natural_reader:natural_reader@127.0.0.1:5434/postgres TEST_DATABASE_URL=postgresql://natural_reader:natural_reader@127.0.0.1:5434/natural_reader_test .venv/bin/python -m pytest -q`
  Shell variables don't persist between agent tool calls: put them on every command, or in a wrapper script.
- **Frontend:** `npx vitest run`, then `npx eslint src`.
- **The compose Keycloak is the owner's real identity server** (its `.env` points at the public auth host). Never enroll or test against it.
- **Walking the app** doesn't need Keycloak: `AUTH_ENABLED=false` is accepted on a loopback bind only. Run `run.py` under `env -i` with explicit values: `HOST=127.0.0.1`, a scratch `DATABASE_URL` on 5434, scratch `DOC_STORAGE_DIR` and `LOG_DIR`, `DOCLING_ENABLED=true` when needed, `OLLAMA_URL`, `INFERENCE_MODELS=gemma4:31b-cloud`, `EMBEDDING_MODEL=nomic-embed-text`. Then `npx vite --port 5173`.
  - Headless Chromium: use the Playwright in `~/.npm/_npx/*/node_modules/playwright` with `executablePath` set to `~/.cache/ms-playwright/chromium_headless_shell-1217/...`, because its own browser build isn't downloaded.
  - To open a folder, delete `window.showDirectoryPicker` in an init script, so the app falls back to its `webkitdirectory` input, which `setFiles(dir)` can fill.
- **Evals** (`docs/superpowers/handoff/2026-10-18-project-chat/EVAL.md`):
  - always a scratch database (the script refuses `natural_reader`);
  - always `SUMMARIZE_MODEL=gemma4:31b-cloud`, with `--model ollama:gemma4:31b-cloud --repeat 3`;
  - **never the local `llama3.2:3b`**;
  - comparing two versions needs a same-day control: a `git worktree` at the old commit, run in parallel against a second scratch database;
  - SearXNG isn't running, so the document eval's `live` case logs `ConnectError`, the same in both arms.
- **Processes:** never `pkill -f run.py`; stop a process by its PID, or by the agent's task ID.
- **Secrets:** never print `.env` values; check a key's presence with `grep -c '^KEY=' .env`.
- **Git:** commit or push only with per-action permission from the owner. Stage explicit paths.
- **Open source:** no deployment-specific value in code or docs; docs use example.com.
- **zsh traps:** quote globs (`--include='*.py'`), and don't use `#` as the `sed` delimiter on Markdown.

### Pointers

- `CHANGELOG.md` `## [Unreleased]`: the six fixes, written for users.
- The project chat's full record: `docs/superpowers/handoff/2026-10-18-project-chat/` (`HANDOFF.md`, `EVAL.md`, `WALK.md`).
- The owner's feature wish-list (bulk project upload, auto tags, DeepSeek-OCR vs Docling, embedding trials, MCP, agents, Markdown and PDF side by side, and more) is in Claude's memory as `project_feature_backlog.md`. The admin console v2 covers its timezone, Docling-limits, cloud-stats and embedding items.

---

## 2026-09-22 — Fresh-deploy debugging: founder lockout, login-flow bugs, library bounce, and the docs-404 (ownership guard) + access-model design discussion

**Branch:** `development` · **Commits:** `175ef05`, `df82ef3`, `b20a0cc` (all today) · **Tests:** backend 245, frontend 229 (guard 10/10) · Nothing merged to `master`. Two threads got fixed-and-committed; the last (docs-404) was **diagnosis only — no code**, and opened a design discussion the user is still deciding.

### Deployment topology (verified live this session — keep this, it's not written elsewhere)
- **This box IS the deployment host.** nginx `sites-enabled/`: `chat.example.com` (443, root `/var/www/chat.example.com/dist`), **`chat-ip-fallback`** (`server_name 192.168.11.245`, port 80 — Android/no-DNS access), `auth.example.com`, `default`. Both chat vhosts proxy `/v1/` → `http://127.0.0.1:8000/v1/` and `/api/` → `127.0.0.1:11434` (Ollama).
- **Backend is a bare uvicorn on `:8000` — NOT containerized** (sub-project D still pending). Compose only runs `postgres` (`127.0.0.1:5433`), `searxng`, `keycloak` (`18080`).
- **Postgres:** `127.0.0.1:5433`, db/user `natural_reader`, pw default `natural_reader`. Query: `PGPASSWORD=natural_reader psql -h 127.0.0.1 -p 5433 -U natural_reader -d natural_reader`.
- **`podman ps` on this box needs `env -u XDG_DATA_HOME`** (snap-storage mismatch) — see memory `startup-podman-env-gotchas`.

### Fixed & committed
- **Founder lockout — "account is active but has no access" for the pruned admin** (`175ef05`). Three disarmed safety nets aligned: (1) `admin-user` had **no realm roles** in `realm-export.json`; (2) `BOOTSTRAP_ADMIN_EMAIL` set routes the founder through JIT **branch 2** (email-match, **no capability floor**) instead of **branch 3** (first-user, force-grants `{admin,reader,chat}`); (3) `KC_ADMIN_*` unset ⇒ the callback self-heal ([auth.py](server/routers/auth.py) `if kc is not None`) is dead. Fix: added `"realmRoles": ["admin","reader","chat"]` to admin-user; wired `KC_ADMIN_CLIENT_ID/SECRET` in `.env` (gitignored); documented across DEPLOYMENT/IDENTITY_AND_ROLES/.env.example/deploy README/README/ARCHITECTURE. **`--import-realm` drift trap:** editing the export only seeds a **fresh** DB — the live realm (Postgres `keycloak` schema) is never re-seeded; must `down -v` + re-import or do live-realm surgery.
- **OIDC callback 500** (`df82ef3`). Keycloak returned `error=temporarily_unavailable` (no code) → `authorize_access_token` raised an unhandled `OAuthError` → 500. Fix: error-param short-circuit + `try/except OAuthError` → 303 redirect to login. Test `test_callback_idp_error_redirects_not_500`.
- **Logout "Invalid redirect uri"** (`df82ef3`). Two causes: live-realm drift (post-logout URI not registered) **and** an export bug — `post.logout.redirect.uris` was space-delimited but Keycloak splits on **`##`** (`Constants.CFG_DELIMITER`). Fixed the delimiter in the export; the **live realm still needs the URI registered**.
- **Library view bounce** (`b20a0cc`). `useViewModeGuard` coerced `library` (not a capability) back to reader; Library is intentionally **ungated**. Fix: `CAP_FREE_VIEWS = ['library']`. Answer to the user's question: **no, Library does not need its own capability** — the guard was the bug. Frontend-only ⇒ needs `npm run build` + redeploy to `/var/www/chat.example.com/dist` to take effect live.

### `POST /v1/docs 404` while indexing — diagnosed, NOT a bug, no code change
- **Not a routing 404.** Route is live (unauth `POST` → 401). It's the **anti-hijack ownership guard** in `register_document` ([docs.py:271](server/routers/docs.py#L271)): a `doc_id` that already exists owned by a **different** user → `404 "Document not found"` (deliberately vague to hide existence). `doc_id = sha256(file bytes)` ([docHash.js](src/utils/docHash.js)) ⇒ same file = same id across accounts.
- **DB state:** users = `udaravima@gmail.com` (`f7432eb4`, caps `{reader,projects}`) + `admin@example.com` (`00000000-…0001`, caps `{admin,chat,reader}`). **Both docs (README.md, OpenCode…pdf) owned by udaravima.** So indexing from `admin@example.com` a file udaravima owns → collision → 404.
- **The real knot:** identities are split two ways — the account that **owns** docs (udaravima) lacks `chat`; the account with `chat` (admin) owns nothing and can't re-register. Neither can own-and-chat the same file. Same identity-churn class as the founder lockout.

### Access model reference (verified this session — for whoever builds the design changes)
- **Ownership-gated** (`assert_owns_doc`, non-owner→404): register/index/chunks/convert/**delete**, and grant add/remove (`_require_doc_owner`, **no admin escape hatch** — admin can't self-grant via API; use SQL or act as owner).
- **Read-gated** (`assert_can_read_doc` = owner OR project-member OR grantee, [authz.py:26](server/auth/authz.py#L26)): `GET /{id}`, `/search`, `/markdown`, list — **no admin bypass**.
- **Delete cascade:** `doc_chunks`/`doc_pages`/`doc_grants` are all `ON DELETE CASCADE` on `documents(doc_id)` → no storage orphans. But other users' grants vanish **silently**, chat/pins citing the doc dangle, and per-browser IndexedDB copies are untouched.
- **Projects:** `POST /v1/projects`; members `PUT /v1/projects/{id}/members/{uid}`; file a doc `PATCH /v1/docs/{id} {"project_id":…}` (owner-only; must own/belong to target project). **No create-project UI in the frontend yet** — it only *lists* projects. A doc lives in **≤1 project**.
- **Admin asymmetry:** admin **can reassign** any doc's ownership (admin-only PATCH path, [docs.py:336](server/routers/docs.py#L336)) but **cannot read** it. Give-away without open.

### Design decisions raised — user is deciding, NOTHING built
1. **Same-file collision → auto-grant read instead of 404** (possession of bytes ⇒ read right; tradeoff: loses the existence-hiding the 404 provides).
2. **Delete → pre-delete dependency preflight** (surface other users' grants / chat refs before destroying; storage cascade already safe).
3. **Admin-read-all** — currently no; if added, keep opt-in/audited and **split from delete** (admin read ≠ admin delete).
4. **Build a real "New Project" + doc-assignment UI.**
- **Open question that shapes all four:** single-tenant (few trusted identities → collapse to one shared library) vs. genuinely multi-user (isolation is a feature)?
- **Given to the user as the immediate no-code fix:** create a shared *project* (owner udaravima, member admin, file both docs in) → both identities read+chat. Alternatives: grant udaravima `chat` in Keycloak, or grant admin read on the two docs (SQL, since grant API is owner-only).

### Next
- User decides tenancy model + which of the 4 changes to build → then brainstorm → spec → plan.
- **Deploy still pending:** `npm run build` + redeploy `dist` for the Library fix; **re-import realm (or live surgery)** for founder roles + the logout post-logout URI + granting udaravima/admin the roles they're missing.
- Nothing to `master` (standing rule).

---

## 2026-09-21 — Document library (RAG Phase 0) built end-to-end

**Branch:** `feat/document-library-rag` (off `feat/admin-console`) · **Tests:** backend **195**, frontend **208**, lint clean · Migration **009**. NOT merged, NOT on `master`.

### What shipped
The 10-task document-library plan (`docs/superpowers/plans/2026-09-20-document-library-rag-phase0.md`), executed subagent-driven with a per-task review gate. New owner doc: **`docs/LIBRARY.md`**.

- **Access model** — `can_read` = owner OR project-member OR grantee, resolved entirely in SQL (`server/auth/authz.py`: `assert_can_read_doc`, `readable_docs_where`). **404-indistinguishable**: a doc you can't read is indistinguishable from one that doesn't exist, and a stranger's doc can never surface in a list even on a matching filter (no Python post-filter).
- **Schema (009)** — `projects`, `project_members` (read membership), `doc_grants` (per-doc read), `documents.project_id` (FK, `ON DELETE SET NULL`) + `documents.tags` (`TEXT[]`). Self-registers v9. Independent of auth's 008.
- **API** — read routes (`GET /v1/docs/{id}`, `/search`, `/markdown`) widened owner→`can_read`; `GET /v1/docs?q=&project_id=&tag=` list; `PATCH /v1/docs/{id}` (owner: tags/project with cross-tenant IDOR guard; admin: ownership reassignment); doc grants + a `projects` router (CRUD + members), all owner-only writes, idempotent.
- **Frontend** — a **Library** view (`viewMode:'library'`, `LibraryPage.jsx`): list/search/project-filter, owner-only tag-edit/reassign/delete, read-only "shared" rows. Upload/register gained an optional project+tags picker (`src/lib/docMeta.js` `registerDocument`, per-document `useDocMetaPicker`).

### Review-caught fixes (unit tests passed; review found these)
- **IDOR / cross-tenant assignment** (2 security reviews): a doc owner could file their doc into a stranger's project → it surfaced in that project's members' lists. Fixed: non-admin must own/be-member of the target project (`b2655b7`).
- **Search race** (Task 8): stale in-flight `GET /v1/docs` could overwrite a newer query → request-id guard (`98eecb0`).
- **Keyboard leak** (Task 8): playback/Page-nav keys were live in library/admin, mutating the hidden reader → `inReader` excludes them (`98eecb0`; closed a pre-existing admin leak too).
- **Cross-document metadata leak** (Task 9, Important): the upload picker was session-level, so a project/tag chosen for doc A silently carried onto doc B → per-document `useDocMetaPicker` reset (`6d575d9`).

### Deferred / known
- **Owner-row delete** in the Library was added beyond the literal spec (safe: server owner-gated + confirm) — flagged to the user for a keep/remove call.
- A failed metadata `PATCH` after register is logged, not surfaced (doc still registers; the Library gives owners a recovery path). Could add a toast.
- Read routes gain `require_capability("reader")` only at **integration with the auth branch** (`feat/keycloak-identity-permissions`, migration 008) — deliberately not in this branch.

### Next
- **Final whole-branch review (opus)** of `feat/document-library-rag`, then report.
- Manual browser E2E (library list/search/share/upload-picker) — not yet walked.
- **Merge decisions** for both this branch and the auth branch remain the user's — nothing goes to `master` until the UI and all are complete.

---

## 2026-09-17 (final) — post-1.9.0 documentation completeness pass + admin-console spec

**Branch:** `feat/model-router-gateway` · **Tests:** backend 143, frontend 163, lint clean (re-verified after docs-only changes) · docs only, no code touched.

### What happened
User asked: comprehensive, ordered, complete docs + test cases for **everything after tag v1.9.0** (auth + model routing), plus a proper **separate admin page** design (implementation NOT on this branch). Audit → three real gaps found and fixed:
- **CHANGELOG `[Unreleased]` was missing the entire auth feature set** (only inference items were listed). Added: multi-user OIDC auth (JIT provisioning, sessions, PATs, ownership, admin mgmt, migrations 005/006), the local Keycloak rig (with the persistence semantics), security hardening — plus a **Breaking: every route now requires auth** entry under Changed.
- **ARCHITECTURE.md had zero inference-gateway coverage.** Added: `inference.py` in § Routers (validated envelope, byte-faithful streaming, 429 pre-check + `_UsageTap`, fail-open), `model_router.py` + `inference_budget.py` in § Services, migration 007, the `callChat()`/source-switch rewrite of the `useChatEngine` row, `inferenceSource` persisted setting, ChatSidebar/InferenceSourceSelect/budget-meter components, updated "one chat turn" flow.
- **deploy/README.md was stale**: said Keycloak uses "embedded store" (now the persistent `keycloak` schema in Postgres — with the fresh-volume-only init-script semantics + hand-create-schema command), said admin console password is `admin/admin` (bootstrap creds only apply on first volume init; user has since changed it), had no inference-gateway env vars, no offboarding warning. All fixed; added the live-verified Keycloak-deletion-doesn't-propagate note to the two-user walkthrough.

### New docs
- **`docs/superpowers/specs/2026-09-17-admin-console-design.md`** — the approved-in-principle design for the separate admin page, to be built on a future `feat/admin-console` branch. Covers: `viewMode: 'admin'` + shield entry with boot-time coercion for demoted admins, Users section (incl. **user deletion** with rails: no self/last-admin/seed-row deletes; delete = full wipe via existing cascades + PDF sweep), Inference usage dashboard, read-only deployment config endpoint, 17-item TDD test plan, rollout, open questions (soft-delete, CSV export, session visibility).
- **`docs/TESTING.md`** — the post-1.9.0 test map: every test case in every file and what it asserts (verified against source, not from memory), test conventions (ASGITransport/MockTransport/`db_conn`/`stream_response`), integration seams, the manual/live-verification ledger (gateway scripted pass + Keycloak propagation matrix), and known gaps (no E2E, OIDC flow mocked at claims level, usage/budget UI + user deletion pending admin console).
- **`docs/README.md`** — docs index: all ten docs in reading order with quick-routing pointers ("why can't this user log in?", "what env vars?", "which tests guard this?").

### Next
- **`feat/admin-console`** branch (spec above) after the gateway branch merges — the usage/budget endpoints it consumes are this branch's work.
- The remaining browser click-through (dropdown/meter/toast/local-mode parity) — still open, unit-covered.
- `startup.sh` has an uncommitted user edit (Keycloak wait 30→60 retries) — left alone on purpose.

---

## 2026-09-17 (later still) — Keycloak↔app propagation live-verified; user guide written

**Branch:** `feat/model-router-gateway` · docs + live verification only · **Tests:** unchanged (143/163, lint clean).
### The question
Does creating/deleting a user in Keycloak propagate to the app? **Live-verified end-to-end** against the running rig (Postgres + Keycloak + backend auth-on + Vite), using the Keycloak Admin API + a full scripted OIDC login dance (curl through `/v1/auth/login` → KC form → `/v1/auth/callback` → `nr_session`). Results:
- **Create in KC → nothing happens app-side until first login.** Verified: created `prop-test`, users table unchanged; after the login dance, the row appeared as `pending`/`member` with `oidc_sub` = the KC UUID (JIT provisioning).
- **Delete in KC → NOTHING propagates.** Verified: row stayed `active`, and the user's **existing session still worked** (`/v1/auth/me` 200 after KC deletion — the app never re-checks the IdP).
- **The email trap is real:** a second KC user with the same email got **409 "email already linked to another identity"** at login. And the dev DB already contained a **real orphan of this class**: `aakash.n@…` is `active` in the app but has no Keycloak identity at all (its sub has no `user_entity` row) — this is what happens when KC deletion happens without app-side disable, or a leftover from the H2-era store.
- **Correct offboarding order: disable in the app first (hard-revokes sessions), then delete in Keycloak.** Documented in IDENTITY_AND_ROLES.md + USER_GUIDE.md.

### Also this session
- `docs/USER_GUIDE.md` — end-user guide (sign-in states, PATs & why they exist — "tokens are on the Account panel, not Admin", admin buttons, Server/Local source, budget meter/429/UTC-midnight, FAQ). Linked from README.
- Test artifacts cleaned up: prop-test + prop-test2 removed from both stores (KC natural-reader realm back to just `udara.v`; app back to 2 rows).
- Notes for replaying the dance: Keycloak's login form `action` URL needs `&amp;` → `&` unescaping; header matching must be case-insensitive (`Location:` from KC vs `location:` from uvicorn); form session codes are one-time — the whole login dance must run in one shot; KC Admin API access tokens expire in ~60s; Admin-API-created users hit `VERIFY_PROFILE` unless `firstName`/`lastName` are set. Master-realm admin password is not the bootstrap `admin/admin` (user-changed).

### Next (unchanged from below, plus)
- Admin-side **user deletion** (to free blocked emails / clean orphans) is now a motivated follow-up alongside the `viewMode: 'admin'` panel.

---

## 2026-09-17 (later) — identity deep-dive doc + gateway live verification on `feat/model-router-gateway`

**Branch:** `feat/model-router-gateway` · **Tests:** backend 143, frontend 163, lint clean · **Working tree:** docs only.

### What happened
- User commit `286fa41` ("Keycloak Presistent settings") moved the realm out of Keycloak's embedded H2 into the shared Postgres (schema `keycloak`, seeded by `deploy/postgres/init/` on first volume init). This fixed the recurring 409 "email already linked to another identity" desync: with H2, every container recreation minted new `sub` UUIDs, so returning users stopped matching `users.oidc_sub`.
- Wrote **`docs/IDENTITY_AND_ROLES.md`** — the deep-dive answering "how do Keycloak users map to natural_reader users": two stores (Keycloak schema = credentials, `public.users` = accounts) joined only by the mirrored `(oidc_iss, oidc_sub)` pair; the three `resolve_or_provision_user` branches and their guards; roles live in the app (NOT Keycloak claims) and take effect immediately (Principal rebuilt from the users row per request); credentials (sessions/PATs, sha256-only); special identities (seed admin, dev bypass); and a **proposal** for a third `viewMode: 'admin'` (gated on `role === 'admin'`) to house users + the still-UI-less inference usage/budget endpoints. Cross-linked from ARCHITECTURE.md § auth.
- **Task 13's manual pass (scripted subset) — done and green**, against a live backend (dev bypass) + real Ollama: models endpoint (allowlist filters to `gemma4:latest`; budget key with UTC-midnight `reset_at`), allowlist 422, real NDJSON streaming (thinking chunks byte-faithful), `INFERENCE_DAILY_TOKEN_BUDGET=1` → models shows `remaining_tokens: 0` + chat **429 with structured detail** (accounting had already recorded the earlier stream — recording is unconditional, budget only gates), tool_calls passthrough (gemma4 emitted a proper `current_time_date` call), `GET /v1/admin/inference/usage` (today's 27 tokens for the seed admin — the row's email is the user's real one, i.e. already linked via `BOOTSTRAP_ADMIN_EMAIL`/first-login).
- Not yet human-clicked: the browser-side bits (Server/Local dropdown, budget meter, 429 toast, disabled send in local-mode parity). Unit-tested; final confidence click-through still open.

### Next
- **AdminPanel follow-up** (now designed in IDENTITY_AND_ROLES.md § last): `viewMode: 'admin'` surface with the usage view + per-user budget knob (`PATCH /v1/admin/users/{id}`; `day` from the usage endpoint is an ISO date, not a timestamp; absent field ≠ null in the PATCH semantics).
- Document-library RAG (unblocked by the gateway), then sub-project D.
- `origin/feat/model-router-gateway` is behind local — push only when asked.

---

## 2026-09-17 — Model-router gateway (sub-project E) built on `feat/model-router-gateway`

**Branch:** `feat/model-router-gateway` (branched off `feat/security-hardening` line) · **Tests:** backend 143, frontend 163, lint clean · **Working tree:** clean at last commit.

### What happened
- Implemented the full [gateway spec](docs/superpowers/specs/2026-09-17-model-router-gateway-design.md) (all phases E1–E3) via the 13-task TDD plan ([docs/superpowers/plans/2026-09-17-model-router-gateway.md](docs/superpowers/plans/2026-09-17-model-router-gateway.md)).
- **The pipe (E1):** new `server/routers/inference.py` — `GET /v1/inference/models` + `POST /v1/inference/chat` behind `get_current_user`; strictly validated envelope (`extra="forbid"` down to per-message fields, incl. `images` + `tool_calls`); byte-faithful NDJSON passthrough (uses `client.send(stream=True)` + closes in the generator's `finally` — the StreamingResponse trap); upstream errors forwarded status+body so the SPA's think/tools fallback chain still branches correctly. Frontend: `src/lib/chatTransport.js` seam, `useChatEngine`'s four `/api/chat` sites collapsed into `callChat()`, model list via the gateway, persisted **Inference source: Server | Local Ollama** setting in ChatSidebar.
- **The router (E2):** `server/services/model_router.py` owns everything — `INFERENCE_MODELS` allowlist (422 otherwise), task models (`SUMMARIZE_MODEL` w/ `WEB_SEARCH_SUMMARY_MODEL` fallback, `EMBEDDING_MODEL`), `INFERENCE_TIMEOUT_S`, `INFERENCE_DAILY_TOKEN_BUDGET`. `embeddings.py` / `web_search.py` / `docs.py` (embedding metadata) all read from it now — no service reads Ollama env directly anymore.
- **The budgets (E3):** migration `007_inference_budgets.sql` (`inference_usage` PK (user_id, day) — **day computed in Python, UTC**, no SQL default; `users.inference_daily_token_budget` NULL=default/0=unlimited), `inference_budget.py` service, 429 pre-check + `_UsageTap` stream accounting (only counts completed generations; fresh pooled conn in the generator `finally`; fail-open on DB errors). Admin: `GET /v1/admin/inference/usage` + budget field on the user PATCH (`exclude_unset` semantics: absent=untouched, null=clear). Frontend: budget rides `/v1/inference/models`, meter in ChatSidebar, 429 intercepted in `callChat` BEFORE the retry chains, send disabled at zero.
- **Docs/config:** `.env.example` gateway section; README (new Inference Gateway API table, threat-model `/api/*` rows removed, nginx `/api/` block deleted everywhere + `proxy_buffering off` moved to `/v1/`); CHANGELOG `[Unreleased]`.

### Mid-session incident (resolved)
- The branch was accidentally switched to `master` mid-implementation. **No commits were lost** — all were on `feat/model-router-gateway`; only uncommitted Task-6 scratch files were wiped by the force-checkout and were recreated. Note: `searxng/` is owned by a container uid; checkout errors there are fixed with `git checkout -f` (the files are identical).

### Not verified here / next
- **The live browser walk-through (plan Task 13 step 5) was NOT done** — needs Ollama + a real model: server-mode dropdown/streaming, tool loop through the gateway, image attach, local-mode parity, allowlist, and a budget-exhaustion 429. All of these are covered by unit tests with a mocked upstream; the manual pass is a final confidence check.
- **AdminPanel UI** doesn't surface the usage view / budget knob yet (API only). Small follow-up.
- **Document-library RAG is now unblocked** — its Phase 2 description generation routes through `model_router`'s `summarize` task, and Phase 3's multi-round loop checks budget via the 429 detail.
- Then sub-project **D** (containerization/prod Keycloak/TLS).
- The **admin usage endpoint returns `day` as a date object** — FastAPI serializes it; if the SPA later renders it, remember it's ISO date, not timestamp.

### How to run / test
- Postgres must be up: `env -u XDG_DATA_HOME podman-compose up -d postgres` (container stops between sessions).
- Backend: `.venv/bin/pytest server/tests/` (143). Frontend: `npm run test:run` (163), `npm run lint`.
- Manual: `./startup.sh up` (loopback dev bypass = seed admin) + `npm run dev`; chat defaults to Server mode → `/v1/inference/*`.

---

## 2026-09-17 — SPA auth UI + local OIDC rig built; merged onto `feat/security-hardening`

**Branch:** `feat/spa-auth-ui` (off, and merged back into, `feat/security-hardening`) · **Tests:** backend 92, frontend 150, lint clean · **Working tree:** clean.

### What happened
- Built the **SPA auth UI** — the frontend half of multi-user, so the app is usable through the browser (before this it only ran with `AUTH_ENABLED=false`). Followed the 9-task TDD plan ([docs/superpowers/plans/2026-09-17-spa-auth-ui.md](docs/superpowers/plans/2026-09-17-spa-auth-ui.md)) implementing the [spec](docs/superpowers/specs/2026-09-16-spa-auth-ui-design.md).
- **Pieces:** a `src/utils/apiFetch.js` credential seam (every `/v1` call sends the cookie; a 401 anywhere fires a global handler → back to the login gate); `src/hooks/useAuth.js` state machine (`loading/anonymous/pending/disabled/active/error` from the `/v1/auth/me` probe); `src/components/auth/AuthGate.jsx` + screens; App gated behind it; migrated **every** `/v1` fetch across App/useTtsEngine/lib/MarkdownReader to `apiFetch` (grep-audited); `AccountPanel` (PAT create/list/revoke) and admin-only `AdminPanel` (activate/disable/role) as new collapsible **Sidebar** sections.
- **Backend:** one change — the not-active `403` now carries structured `detail.status` (`pending`/`disabled`) so the SPA branches the gate screens.
- **Local OIDC rig:** `docker-compose.yml` gains a **Keycloak** service (host port **18080**, `start-dev`, imports `deploy/keycloak/realm-export.json`); a **Vite dev proxy** makes `:5173` same-origin with the backend; `deploy/nginx/natural-reader.conf` is a reference site for the prod-like path; `deploy/README.md` has the two-user walkthrough. Verified Keycloak imports the realm and serves correct discovery (`issuer: http://localhost:18080/realms/natural-reader`).

### Project state
- **A + B+C + SPA UI now all on `feat/security-hardening`.** Still **not pushed and not merged to master** (per the standing "keep everything here until the UI and all is done" instruction).
- **The live browser login flow was NOT walked here** — infra (realm import, discovery) and all unit tests are verified, but the human two-user click-through in `deploy/README.md` still wants doing before this goes near master.

### Not done / next
- Wire the **browser extension** to send a PAT (separate repo — the Account panel now produces the token). Until then the shipped extension breaks under `AUTH_ENABLED=true`.
- Sub-project **E** (server-side model-router gateway) and **D** (full containerization / prod Keycloak / TLS / secrets) — still pending. See [memory/project_multiuser_hardening.md].

### How to run
- `deploy/README.md` is the source of truth. Short version: `env -u XDG_DATA_HOME .venv/bin/podman-compose up -d postgres keycloak`; backend with the OIDC env from `.env.example`; `npm run dev`. The pending screen needs a **second** Keycloak user (first login always becomes admin).

---

## 2026-09-16 — Multi-user auth+authz backend done; merged A+B+C onto `feat/security-hardening`

**Branch:** `feat/security-hardening` (integration branch — both sub-projects live here now) · **Tip:** `c9b4949` · **Working tree:** clean · **Tests:** 91 backend passing (needs Postgres up).

### What happened
- **Sub-project B+C (multi-user auth + authz) — backend complete.** 16-task TDD plan ([docs/superpowers/plans/2026-09-14-multiuser-auth-authz.md](docs/superpowers/plans/2026-09-14-multiuser-auth-authz.md)) all green. New `server/auth/` package (config, users+JIT provisioning, DB-backed sessions, personal access tokens, principal-resolution deps, row-ownership `authz`, OIDC RP client), routers `auth`+`admin`, migrations `005` (users + per-user ownership) / `006` (sessions + PATs), ownership guards wired into docs/chat-sessions/tools/TTS, app wiring + startup guard + `bootstrap_admin`.
- **Two rounds of background security-review findings fixed.** Round 1 (4 findings) in `9cacf8f`: TOCTOU seed-claim → atomic UPDATE, `email_verified` gate on email-match provisioning, dev-bypass keyed to the real bind host via `ipaddress`, session hard-revoke on user disable. Round 2 (1 HIGH) in `c9b4949`: the `SessionMiddleware` secret no longer falls back to a hardcoded `"dev-insecure-change-me"` — `startup_guard` refuses to boot a non-loopback bind without a strong (>=32 char) `SESSION_SECRET`; loopback dev degrades to an ephemeral per-process secret.
- **Integrated:** fast-forward-merged `feat/multiuser-auth` (22 commits, `454f2f4..c9b4949`) into `feat/security-hardening`. Sub-project A (vuln hardening, commit `21e6e39`) was already the base of that branch. Both branches now point at `c9b4949`.
- **Cleaned up a stray git state:** an in-progress cherry-pick of `defcf03` ("upgrade vulnerabilities on npm packages: humanfs", from `origin/development`) was stuck on a `package-lock.json` conflict and was aborted. **Loose end:** that npm-audit fix still needs to land on this line — bring it in properly via a `development` → `master` merge, not a cherry-pick onto a backend branch.

### Project state
- **A + B+C are done on `feat/security-hardening`, not pushed and not merged to `master`.** Awaiting the push/PR decision.
- **SPA is NOT auth-wired yet.** The frontend has no login screen / token panel and doesn't send credentials, so it only works with `AUTH_ENABLED=false` (which `startup_guard` permits *only* on a loopback bind). A follow-up plan is needed before the SPA is usable multi-user.
- Sub-projects **E (server-side model-router gateway)** and **D (containerize + proxy + secrets)** are still pending — see [memory/project_multiuser_hardening.md] and the locked design decisions there.

### How to run / test
- Start Postgres (snap-podman env gotcha — the `env -u` is required):
  `env -u XDG_DATA_HOME .venv/bin/podman-compose up -d postgres` (container **stops between sessions** — restart it; `env -u XDG_DATA_HOME podman ps` to check).
- Backend suite: `.venv/bin/python -m pytest server/tests/` (test DB `natural_reader_test`, per-test rollback; router tests use `httpx.AsyncClient`+`ASGITransport`, never `TestClient`).
- New env vars in [.env.example](.env.example): `AUTH_ENABLED`, `OIDC_*`, `SESSION_SECRET` (mandatory for exposed binds), `COOKIE_*`, `BOOTSTRAP_ADMIN_EMAIL`.

### Next step / where to resume
- **Decide integration for `feat/security-hardening`:** push + open a PR to `master`, or keep local. (Push/merge to `master` still needs explicit per-action approval.)
- Then: brainstorm → spec → plan the **SPA login-UI** follow-up (login screen, `credentials:'include'`, PAT management panel) so the frontend actually uses the new auth.
- Then sub-project **E** (model router), then **D** (containerize).

---

## 2026-09-14 — Understood project; captured document-library RAG suggestion

**Branch:** `development` · **Version:** v1.9.0 (`ed83a3f`) · **Working tree:** was clean at start; this session adds two untracked/ignored docs (see below).

### What happened
- Read the project end-to-end to understand the RAG/chat architecture (README, [CHAT_WITH_PDF.md](docs/CHAT_WITH_PDF.md), the `superpowers/specs` + `plans`, and the actual code paths).
- Captured a new feature idea — **ask chat across a library of many indexed docs (multi-project, with a document-routing step and multi-round retrieval)** — as a rich, analyzed suggestion:
  **[docs/superpowers/specs/2026-09-14-document-library-rag-suggestion.md](docs/superpowers/specs/2026-09-14-document-library-rag-suggestion.md)**
- No code changed. No spec written, no plan, nothing approved to build.

### Project state (as understood this session)
- **What it is:** local-first reader — Kokoro ONNX TTS + Ollama chat side panel, fused into "Chat with PDF" (v1.6.0+). FastAPI + Postgres/pgvector backend, React/Vite frontend. Fully local (Ollama does chat *and* embeddings).
- **Retrieval today is single-doc:** index the open doc → `nomic-embed-text` 768-dim chunks in `doc_chunks` → model gets a `search_document` tool → **one** autonomous round-trip.
- **Tool loop is capped at 1 round** on purpose ([useChatEngine.js:694-763](src/hooks/useChatEngine.js#L694-L763)); follow-up call drops `tools`.
- Recent shipped work (git log): file logging + license/contributing + v1.9.0 release; SearXNG-backed `web_search` tool (first backend `/api/generate` path, in `services/web_search.py`); hardcoded doclin support.

### Key file map (for the suggested feature)
| Concern | File |
|---|---|
| Tool registry (add tools here) | [src/lib/chatTools/index.js](src/lib/chatTools/index.js) |
| Existing single-doc search tool + `when` gate | [src/lib/chatTools/searchDocument.js](src/lib/chatTools/searchDocument.js) |
| Chat engine + tool loop + round cap | [src/hooks/useChatEngine.js](src/hooks/useChatEngine.js) |
| Docs + search endpoints (`WHERE doc_id`) | [server/routers/docs.py](server/routers/docs.py) |
| Schema (documents, doc_chunks, HNSW) | [server/sql/001_init.sql](server/sql/001_init.sql) |
| Backend generation pattern to reuse | [server/services/web_search.py](server/services/web_search.py) |

### Next step / where to resume
- Decision made this session: capture as suggestion (done), backlog lives under `docs/superpowers/specs/`.
- When building: start **Phase 1 (corpus search, no routing)** from the suggestion doc, via brainstorming → spec → plan. Phases 2 (descriptions + routing tool) and 3 (budgeted multi-round loop) follow.
- Open decisions still to make (all listed in the suggestion): project = column vs. table; routing = semantic vs. list-and-pick; round budget; descriptions LLM-generated vs. editable.
