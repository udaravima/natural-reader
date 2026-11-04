# Speech each person chooses — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A person switches the language they speak mid-conversation from a control beside the microphone and in the Talk panel, each language is heard by the model the admin offered for it, and with Google reading replies each person picks and previews their own Google voice.

**Architecture:** The server picks the model from the language (no model id from the browser): `catalogue.for_language` over the admin's offered list, then a per-worker pool of loaded engines (`service.py`) that holds an engine per request, waits for loads, and trims memory when it drops one. The browser's switch is the existing `speechLanguage` setting, placed in context. The reply voice is one more field on `POST /v1/chat/voice`.

**Tech Stack:** FastAPI, faster-whisper 1.2.1, Postgres `app_settings` (no migration: `chat_options` is a JSON row), React 19 + vitest + Testing Library, Playwright for walks.

**Spec:** `docs/superpowers/specs/2026-11-04-speech-choices-per-person-design.md`

**Execution:** native (the owner's standing rule: no subagents unless asked); commit after each task on `feat/speech-choices` (commits delegated to me 2026-11-04 15:11).

## Global Constraints

- pytest always with `TEST_DATABASE_URL=postgresql://natural_reader:natural_reader@127.0.0.1:5434/natural_reader_test TEST_ADMIN_DATABASE_URL=postgresql://natural_reader:natural_reader@127.0.0.1:5434/postgres`.
- Walks: scratch backend on 8100 with `AUTH_ENABLED=false` (or a throwaway Keycloak on :18081 for the non-admin walk), scratch DB on 5434, vite preview on 5174. Never 8000, 5433 or 18080.
- Routes and the service must still import without faster-whisper, PyAV or numpy (`test_the_routes_and_the_service_import_without_the_audio_packages`): import them inside functions.
- **No model id travels from the browser**; only an id on the admin's offered list is ever loaded.
- `SPEECH_MODELS_LOADED`: default **3**, range 1 to 4, a bad value warns once and uses 3.
- `offered_speech_models` empty means nothing extra (today's behaviour).
- On Auto, Talk keeps sending `SPEECH_WINDOW_LANGUAGE`, and the switch says so: "Auto (English)".
- Google reply voices: all 30 of `chat_voice.GOOGLE_VOICES`; the sample is cached in the browser for the session.
- Sliders, if any, are `SnapSlider`; a control someone changes while doing the task sits where they do it ([[feedback-controls-in-context]]).
- OSS: no deployment specifics in the repo; docs use example.com.

## Review Focus

1. **A stored Speech language the switch doesn't list** (picked from Settings' full list, say German): the switch shows it as the current choice, and it is still sent. Test in Task 5.
2. **The admin withdraws a model while someone talks**: the next clip is heard by the default in the language asked for, nothing is lost. Test in Task 3.
3. **A Google recognizer with no language list, or status not loaded yet**: the switch shows Auto only and doesn't crash. Test in Task 5.
4. **Hear it tapped twice quickly, or for two voices in turn**: one request per voice, and the second voice's sample replaces the first's playback. Test in Task 6.
5. **A download that fails** (no network): the console keeps saying it isn't here yet, people never see the model, and ticking again retries. Test in Task 3.

---

### Task 1: The offered list, the disk, and the model for a language

**Files:**
- Modify: `server/services/chat_options.py` (field, loader, a save function, `remove_speech_model`)
- Create: `server/speech/disk.py`
- Modify: `server/speech/catalogue.py`
- Test: `server/tests/test_chat_options.py`, `server/tests/test_speech_catalogue.py`, `server/tests/test_speech_disk.py` (new)

**Interfaces:**
- Produces: `ChatOptions.offered_speech_models: tuple[str, ...] = ()`; `async save_offered_speech_models(conn, ids: list[str], *, updated_by: str, env=os.environ) -> ChatOptions` (audit `chat_options.offered_speech_models`); `disk.on_disk(model: SpeechModel, env=os.environ) -> bool`; `disk.start_download(model: SpeechModel, env=os.environ) -> None`; `catalogue.offered(options, env=os.environ) -> list[SpeechModel]`; `catalogue.for_language(options, code: str | None, env=os.environ, on_disk=disk.on_disk) -> SpeechModel`; `catalogue.hears(options, env=os.environ, on_disk=disk.on_disk) -> list[dict]` with `{code, name, model, label}`.

- [ ] **Step 1: Write the failing tests**

```python
# test_chat_options.py
async def test_offered_speech_models_are_saved_in_order_and_read_back(db_conn): ...
    # save ["whisper-small-si", "whisper-small-ta"] -> load -> == ("whisper-small-si", "whisper-small-ta")
async def test_a_row_without_offered_models_offers_nothing(db_conn): ...   # () 
async def test_removing_an_added_model_takes_it_off_the_offered_list(db_conn): ...

# test_speech_catalogue.py
def test_offered_keeps_the_admins_order_and_leaves_out_unknown_and_all_language_models(): ...
    # offered ids ["whisper-small-ta", "nope", "small", "whisper-small-si"] -> ids ["whisper-small-ta", "whisper-small-si"]
    # ("small" hears every language: offering it changes nothing, the default already does)
def test_a_language_is_heard_by_the_first_offered_model_on_disk_made_for_it(): ...
    # for_language(opts, "si", on_disk=lambda m: True).id == "whisper-small-si"
def test_a_model_not_on_disk_yet_leaves_its_language_to_the_default(): ...
    # on_disk=lambda m: False -> for_language(opts, "si") == wanted(opts)
def test_auto_and_an_unoffered_language_are_heard_by_the_default(): ...   # None, "de" -> wanted(opts)
def test_hears_lists_each_offered_language_once_with_its_model(): ...
    # [{"code": "si", "name": "Sinhala", "model": "whisper-small-si", "label": "Whisper small, Sinhala (run12)"}, {"code": "ta", ...}]

# test_speech_disk.py
def test_a_converted_model_is_on_disk_once_its_model_bin_is_there(tmp_path, monkeypatch): ...
def test_a_faster_whisper_repo_is_checked_without_downloading(monkeypatch): ...
    # monkeypatch faster_whisper.utils.download_model: asserts local_files_only=True; raising -> False
def test_a_model_found_on_disk_is_not_checked_again(monkeypatch): ...   # second call doesn't call the checker
def test_a_download_starts_once_per_model_while_it_runs(monkeypatch): ...
def test_a_failed_download_is_logged_and_can_start_again(monkeypatch, caplog): ...
```

- [ ] **Step 2: Run them and see them fail** — `pytest server/tests/test_chat_options.py server/tests/test_speech_catalogue.py server/tests/test_speech_disk.py -q` (with the TEST_* variables): FAIL, missing names.

- [ ] **Step 3: Implement.** `offered` filters `all_models(env, options.added_speech_models)` to the offered ids, in order, keeping only models with a non-empty `languages` and an empty `why_not`. `on_disk`: faster-whisper → `faster_whisper.utils.download_model(repo, local_files_only=True, revision=…)` succeeds (a local directory: `Path(repo).is_dir()`); converted → `converted_dir(model, env) / "model.bin"` exists; transformers → `huggingface_hub.snapshot_download(repo, revision=…, local_files_only=True)` succeeds; an ImportError is False; a True is remembered per process in a set of `(id, revision)`. `start_download` runs one daemon thread per model id (a set of running ids under a lock): faster-whisper `download_model(repo, revision=…)`, converted `convert.ensure_converted(model, cpu_threads=1)`, transformers `snapshot_download`; a failure logs a warning with the exception's type name and frees the id.

- [ ] **Step 4: Run them and see them pass.**

- [ ] **Step 5: Commit** — `feat(speech): the models an admin offers, and which one hears a language`.

### Task 2: A pool of engines per worker

**Files:**
- Modify: `server/speech/service.py`
- Test: `server/tests/test_speech_service.py`

**Interfaces:**
- Consumes: `SpeechModel` (catalogue).
- Produces: `get_transcriber(env=os.environ, wanted=None, engine=None, default=None) -> Transcriber` (local engines from the pool; `default` is the deployment's model, taken to be `wanted` when None, and is kept loaded when the limit is 2 or more); `async transcribe_for(user_id, audio, mime, language, engine=None) -> Transcript` (engine None: `get_transcriber()`); `models_loaded(env=os.environ) -> int`; `_release_memory()` (gc + `malloc_trim(0)`, a no-op where libc has none).

- [ ] **Step 1: Write the failing tests**

```python
def test_up_to_the_limit_of_models_stay_loaded_and_the_least_recently_used_goes(monkeypatch): ...
    # SPEECH_MODELS_LOADED=2, default TINY: A then B -> pool holds TINY, B; A dropped; _release_memory called once
def test_the_default_keeps_its_place(): ...     # limit 2, three non-default models in turn: TINY always in the pool
def test_with_one_place_models_take_turns(): ...
def test_a_model_in_use_is_never_dropped(): ...  # a transcription running on A, B and C asked for: A still pooled until it ends, then dropped
def test_the_limit_defaults_to_3_and_a_bad_value_warns(monkeypatch, caplog): ...   # "0", "5", "x" -> 3
async def test_each_clip_is_heard_by_the_engine_its_request_resolved(): ...
    # A = Slow(); start transcribe_for("u1", ..., engine=A); while it runs get_transcriber(wanted=B) for u2;
    # transcribe_for("u2", ..., engine=B_engine); release both: A.finished == 1 and B.finished == 1
async def test_a_clip_for_a_model_still_loading_waits_for_it(): ...
    # a fake engine whose status is loading for 0.3 s then ready: transcribe_for returns the text, no EngineNotReady
async def test_a_model_that_fails_to_load_is_unavailable_not_busy(): ...   # status error -> EngineUnavailable
async def test_the_log_line_names_the_model(caplog): ...   # "model=tiny" in the line
```

Change `test_a_swap_restarts_the_retry_clock` to the per-model clock: a newly built engine has no retry clock of its own.

- [ ] **Step 2: Run and see them fail.**

- [ ] **Step 3: Implement.** `_pool: OrderedDict[str, Transcriber]`, `_in_use: Counter[str]`, `_attempt_at: dict[str, float]`; `get_transcriber` for the local engine returns the pooled engine for `wanted.id` when its `spec == wanted`, else builds one, moves it to the end, sets `_transcriber` to it (so a caller with no opinion keeps the last one, as `test_a_chosen_model_replaces_this_workers_engine_once` expects), then shrinks: drop least recently used entries that are neither in use nor (limit ≥ 2) the default, until the pool fits; call `_release_memory()` once if anything went. Remote engines stay the single `_transcriber`. `transcribe_for`: count the engine in use from its first line (before any await), a deadline of `LOCAL_WAIT_S` from the start shared by the load wait (warm, then `status()` every 0.1 s: ready → go on, error → `EngineUnavailable(detail)`) and the slot; in `finally` release the count and shrink.

- [ ] **Step 4: Run the whole of `test_speech_service.py` and `test_speech_router.py`; all pass.**

- [ ] **Step 5: Commit** — `feat(speech): a pool of speech models per worker, held per request, trimmed when dropped`.

### Task 3: The routes: status, transcription, the admin's offer

**Files:**
- Modify: `server/routers/speech.py`, `server/routers/admin.py`
- Test: `server/tests/test_speech_router.py`, `server/tests/test_speech_models_admin.py`

**Interfaces:**
- Consumes: Task 1 (`offered`, `for_language`, `hears`, `disk.*`, `save_offered_speech_models`), Task 2 (`get_transcriber(..., default=)`, `transcribe_for(..., engine=)`).
- Produces: `GET /v1/speech/status` gains `hears: [{code, name, model, label}]` and `default_model: {id, label}` (local engine only; `[]`/`null` otherwise); `POST /v1/speech/transcriptions` response gains `model`; `PUT /v1/admin/speech-models/offered` `{ids: list[str]}` → the speech-models view, which gains `offered: bool`, `offerable: bool` and `on_disk: bool` per model.

- [ ] **Step 1: Write the failing tests**

```python
# test_speech_router.py
async def test_status_lists_the_languages_the_offered_models_hear(...): ...
async def test_status_loads_nothing_but_the_default(...): ...   # get_transcriber called only with the default
async def test_a_sinhala_clip_is_heard_by_the_sinhala_model_and_says_so(...): ...   # language=si -> response model == "whisper-small-si"
async def test_a_clip_in_a_language_nobody_offers_is_heard_by_the_default(...): ...
async def test_a_model_withdrawn_mid_conversation_leaves_the_next_clip_to_the_default(...): ...   # Review Focus 2
async def test_a_model_that_fails_to_load_hands_the_clip_to_the_default(...): ...
async def test_a_remote_engine_hears_every_clip_and_ignores_the_offer(...): ...

# test_speech_models_admin.py
async def test_the_admin_offers_models_in_order_and_downloads_the_missing_ones(...): ...   # start_download called for the one not on disk
async def test_offering_an_unknown_or_unrunnable_model_is_refused(...): ...   # 422 each
async def test_the_list_says_which_models_are_offered_offerable_and_on_disk(...): ...
async def test_offering_again_retries_a_failed_download(...): ...   # Review Focus 5
async def test_only_an_admin_may_offer(...): ...   # 403 for a member
```

- [ ] **Step 2: Run and see them fail.**

- [ ] **Step 3: Implement.** `SpeechAccess.engine(language=None)` resolves `catalogue.for_language(options, language)` with `default=catalogue.wanted(options)`; the transcription route calls it **after** the upload is parsed (there is no global swap any more, so stale options can't swap a newer model back; update the comment) and passes the engine to `transcribe_for`; on `EngineUnavailable` from an engine that isn't the default, it transcribes once more with the default. `model` in the response: the engine's `spec.id` for a local engine, else its `model`. The admin route validates every id with `offered()`'s rules, saves, and calls `disk.start_download` for each offered model `on_disk` says is missing.

- [ ] **Step 4: Run all speech and admin tests; they pass.**

- [ ] **Step 5: Commit** — `feat(speech): status says who hears each language; a clip goes to its language's model`.

### Task 4: The admin's ticks

**Files:**
- Modify: `src/components/admin/SpeechModelPicker.jsx`
- Test: `src/components/admin/SpeechModelPicker.test.jsx`

**Interfaces:**
- Consumes: Task 3's view (`offered`, `offerable`, `on_disk`) and `PUT /v1/admin/speech-models/offered`.

- [ ] **Step 1: Write the failing tests**: `'offers a model to people with one tick, keeping the list order'` (PUT body `{ids: [...]}`), `'says a ticked model is downloading until it is on this server'` (text "Downloading: people see it once it's here" / "On this server"), `'has no tick for a model that hears every language or can't run here'`, `'hides the ticks with a remote engine'`.
- [ ] **Step 2: See them fail.**
- [ ] **Step 3: Implement** a `role="group" aria-label="Offered to people"` under the model select: one checkbox per `offerable` model (label · languages), its state line, and one line of help: "People pick the language they speak beside the microphone and in Talk; a ticked model hears its language. The model above hears everything else." Refresh the list every 5 s while a ticked model isn't on disk.
- [ ] **Step 4: See them pass.**
- [ ] **Step 5: Commit** — `feat(admin): offer speech models to people`.

### Task 5: The Speaking switch, where people talk

**Files:**
- Create: `src/lib/speech/speaking.js`, `src/components/speech/SpeakingSwitch.jsx`
- Modify: `src/components/ChatView.jsx` (beside `MicButton`, and into `TalkPanel`), `src/components/speech/TalkPanel.jsx`, `src/App.jsx` (`setSpeechLanguage` and `onOpenSpeechSettings` into ChatView and `talk`)
- Test: `src/lib/speech/speaking.test.js`, `src/components/speech/SpeakingSwitch.test.jsx`, `src/components/speech/TalkPanel.test.jsx`, `src/components/ChatView.talk.test.jsx`

**Interfaces:**
- Consumes: status `hears`, `languages`, `window.language`, engine (`heard_by`).
- Produces: `speakingOptions(status, current: string, talkLanguage: string | null) -> [{code: 'auto' | string, label: string, detail: string}]`; `<SpeakingSwitch theme value options onChange onMore autoMeans />`.

- [ ] **Step 1: Write the failing tests**

```js
// speaking.test.js
it('lists Auto, the Talk language, and each offered language with its model', ...)
  // [{code:'auto'}, {code:'en', label:'English', detail:'Whisper small'}, {code:'si', label:'Sinhala', detail:'Whisper small, Sinhala (run12)'}, ...]
it('keeps a stored language the list doesn't have, so it still shows and is still sent', ...)   // Review Focus 1: 'de'
it('with Google, the recognizer's languages', ...)
it('with no status yet, or no languages, Auto alone', ...)   // Review Focus 3
// SpeakingSwitch.test.jsx
it('is one button that says what you are speaking, "Auto (English)" in Talk', ...)
it('a tap on a language switches to it and closes', ...)
it('All languages opens Settings', ...)
// TalkPanel / ChatView.talk
it('switching mid-conversation sends the next clip in the new language, without stopping Talk', ...)
it('the composer has the switch beside the microphone, and it sets the same Speech language', ...)
```

- [ ] **Step 2: See them fail.**
- [ ] **Step 3: Implement.** The switch is the `ViewMenu` pattern (one button, `menuitemradio` items, `useMenuDismiss`), a compact chip: the language's name, or "Auto" / "Auto (English)". Its value is `speechLanguage`; `onChange` is `setSpeechLanguage`. Talk reads the language per clip from `latest.current`, so a switch takes effect on the next clip. Shown only when voice input is on.
- [ ] **Step 4: See them pass; run the whole frontend suite.**
- [ ] **Step 5: Commit** — `feat(talk): switch the language you speak beside the microphone and in Talk`.

### Task 6: The reply voice, chosen and heard first

**Files:**
- Modify: `server/routers/chat_voice.py`, `server/schemas.py` (a `ChatVoiceRequest(TTSRequest)` with `google_voice: str | None = Field(default=None, max_length=40)`)
- Modify: `src/hooks/useTtsEngine.js` (`google_voice` in the body for `/v1/chat/voice`), `src/App.jsx` (`usePersistedState('googleVoice', '')`), `src/components/settings/SettingsDialog.jsx` (ChatPanel: **Reply voice**)
- Create: `src/lib/speech/voiceSample.js`
- Test: `server/tests/test_chat_voice.py`, `src/components/settings/SettingsDialog.test.jsx`, `src/lib/speech/voiceSample.test.js`

**Interfaces:**
- Produces: `GET /v1/chat/voice` gains `voices: list[str]` (Google only); `POST /v1/chat/voice` reads with `google_voice` when it is in `GOOGLE_VOICES`; `voiceSample(voice, fetchSample) -> Promise<string>` (an object URL, one request per voice per session, in flight shared).

- [ ] **Step 1: Write the failing tests**: `test_a_persons_google_voice_reads_their_reply`, `test_a_voice_that_isnt_googles_reads_with_the_admins`, `test_kokoro_ignores_the_google_voice`; `'shows Reply voice only when Google reads the replies, the admin's as Default'`, `'Hear it plays a sample, and a second tap doesn't ask Google again'`, `'a chosen voice reaches the reply speech'`; `voiceSample`: `'two quick taps make one request'`, `'another voice, another request'` (Review Focus 4).
- [ ] **Step 2: See them fail.**
- [ ] **Step 3: Implement.** The sample text, one sentence each: "Hello, this is how I'll read your replies." / "ආයුබෝවන්, මම ඔබේ පිළිතුරු කියවන්නේ මෙහෙමයි." / "வணக்கம், உங்கள் பதில்களை நான் இப்படித்தான் படிப்பேன்." Played through one `Audio` the panel owns, so a new sample stops the last.
- [ ] **Step 4: See them pass.**
- [ ] **Step 5: Commit** — `feat(chat): each person picks Google's reply voice, after hearing it`.

### Task 7: Docs, the Sinhala and Tamil check, and the walk

**Files:** `docs/DEPLOYMENT.md`, `.env.example`, `docs/USER_GUIDE.md`, `docs/ARCHITECTURE.md`, `docs/TESTING.md`, `CHANGELOG.md`, `HANDOVER.md`

- [ ] **Step 1:** Run the verdict check (scratch `pool/verdicts.py`, with `language=si`/`ta`) on the Sinhala and Tamil recordings the v2.5.0 measurements used, on their fine-tunes; record the table in DEPLOYMENT.md. If a real question scores as noise, say so in the docs and the summary; don't change the limits without the numbers.
- [ ] **Step 2:** Docs: the memory table, `WORKERS` × models, `SPEECH_MODELS_LOADED`, the Talk-sends-English trap and the switch that ends it, Offer to people, the Speaking switch, Reply voice.
- [ ] **Step 3:** Walk in the app (Global Constraints): the admin ticks the Sinhala and Tamil models and sees them download; a non-admin with the `talk` role switches Sinhala → English → Tamil in one Talk conversation through the simulated microphone; the log shows each clip's model and language; the worker's memory before and after; with Google's voice on (only if the owner's key is available without asking anything new), two samples, then a reply in the chosen voice. Screenshots at 1366 and 360 px.
- [ ] **Step 4:** Full gates: pytest, vitest, eslint, build. Commit — `docs(speech): choosing the language spoken and the reply voice`.
