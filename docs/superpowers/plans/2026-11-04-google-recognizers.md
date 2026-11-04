# Google recognizers chosen in the console — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (native, the owner's standing rule: no
> subagents unless asked). Steps are RED → GREEN; every pytest run sets TEST_DATABASE_URL / TEST_ADMIN_DATABASE_URL
> to the scratch :5434.

**Goal:** an admin ticks Google recognizers, in order, from a live listing; each clip goes to the first ticked one
that lists its language.

**Architecture:** `google_engine.list_recognizers` reads Google (every location, every page); the ordered names live
in the chat options row (`google_recognizers`); `service` keeps one `GoogleSpeech` per name and routes by language
the way `catalogue.for_language` does for Whisper; the speech router probes the ticked ones before routing and in
status; the admin picker gains a Google recognizers group.

**Spec:** `docs/superpowers/specs/2026-11-04-google-recognizers-design.md`

## Global Constraints

- A saved name matches `^projects/[a-z0-9][a-z0-9-]*/locations/[a-z0-9][a-z0-9-]*/recognizers/[a-z0-9][a-z0-9-]*$`
  AND is in a fresh listing from Google. At most 10.
- `GOOGLE_SPEECH_RECOGNIZER` is the fallback when nothing is ticked.
- No credential is ever logged, returned or put in an exception message.
- No migrations: one more key in the `chat_options` app_settings row.

## Review Focus

1. A name with `#`, `?`, `@` or `.` in its location reaching the endpoint builder → refused before any request.
2. A worker that never served `/status` routing a Sinhala clip → probes first, routes to the recognizer listing `si`.
3. A ticked recognizer deleted in Google → listed as missing in the console; people's clips go to the others.
4. Google refusing the listing (403) → the console shows why; saved ticks are kept.
5. An empty tick list → back to `GOOGLE_SPEECH_RECOGNIZER`, Google stays choosable only if that is set.

### Task 1: listing and strict names (`server/speech/google_engine.py`)

Tests in `server/tests/test_google_speech.py`: `test_a_name_whose_location_could_pick_another_host_is_refused`
(`…/locations/evil.com#/recognizers/x`, `…/locations/a.b/…` refused; real names accepted, project number too);
`test_listing_reads_every_location_and_every_page` (mock transport: locations in two pages, recognizers in two
pages in one location; only ACTIVE kept; each row {name, id, project, location, model, codes, languages});
`test_listing_reports_googles_refusal_in_its_words` (403 on one location → a problem line naming the location,
others still listed); `test_label_names_the_recognizer_and_its_model`.

Produces: `is_recognizer_name(str) -> bool` (strict), `split_name(str) -> (project, location, id) | None`,
`async list_recognizers(projects, *, transport=None, token=_default_token, timeout_s=15.0) ->
Listing(recognizers: list[dict], problems: list[str])`, `GoogleSpeech.id`, `GoogleSpeech.label`.

### Task 2: options and routing (`server/services/chat_options.py`, `server/speech/service.py`)

Tests: `test_chat_options.py::test_google_recognizers_are_saved_in_order_and_an_empty_list_clears_them`;
`test_speech_service.py`: `test_the_admins_recognizers_win_over_the_environments`,
`test_a_language_goes_to_the_first_recognizer_that_lists_it`, `test_auto_and_an_unlisted_language_go_to_the_first`,
`test_google_can_be_chosen_once_a_recognizer_is_ticked`, `test_switching_recognizers_replaces_the_current_engine`.

Produces: `ChatOptions.google_recognizers: tuple[str, ...]`, `save_google_recognizers(conn, names, *, updated_by,
env)`, `service.recognizer_names(options, env) -> tuple[str, ...]`, `service.recognizer_source(options, env)`,
`service.google_offered(options, env) -> bool`, `engine_needs(name, env, options=None)`, `engines(env, options=None)`,
`service.google_engines(names, env) -> list[GoogleSpeech]`, `service.google_for(engines, code) -> GoogleSpeech`,
`get_transcriber(..., recognizers=None)`.

### Task 3: the speech routes (`server/routers/speech.py`)

Tests in `test_speech_router.py`: `test_google_status_lists_each_language_with_the_recognizer_that_hears_it`,
`test_a_sinhala_clip_goes_to_the_sinhala_recognizer_on_a_worker_that_never_answered_status`,
`test_auto_goes_to_the_first_recognizer`, `test_a_clip_answers_the_recognizer_and_model_that_heard_it`.

### Task 4: admin routes (`server/routers/admin.py`)

Tests in `test_speech_models_admin.py`: `test_the_console_lists_googles_recognizers_with_which_are_ticked`,
`test_ticking_saves_only_recognizers_google_listed`, `test_a_name_google_didnt_list_is_refused`,
`test_a_refused_listing_keeps_the_saved_ticks`, `test_an_empty_list_goes_back_to_the_server_setting`,
`test_the_view_says_when_to_show_the_recognizers`.

Produces: `GET /v1/admin/google-recognizers` → `{recognizers: [...+chosen], chosen: [names], missing: [names],
problems: [str]}`; `PUT /v1/admin/google-recognizers` `{names: [...]}` → the speech models view; the view gains
`google: {offered, chosen, source}`.

### Task 5: the panel and the chip (`src/components/admin/GoogleRecognizers.jsx`, `SpeechModelPicker.jsx`,
`src/lib/speech/speaking.js`)

Tests: `GoogleRecognizers.test.jsx` (lists with names, model, languages, location; ticking sends the order;
Move up; shows Google's problem; a missing ticked one is shown), `speaking.test.js` (Google lists each language
with its recognizer).

### Task 6: docs and the live walk

DEPLOYMENT (Google section), USER_GUIDE (admin), ARCHITECTURE, CHANGELOG `[Unreleased]`, `.env.example`, TESTING
(map row, counts). Walk: scratch backend :8100 with the owner's key, scratch DB, vite preview :5174; the admin
ticks both recognizers; one real clip per language through the app's route; the chip and What it heard.
