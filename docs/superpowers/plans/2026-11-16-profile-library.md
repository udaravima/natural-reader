# Profile Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A deployer names the app; an admin keeps house rules and several named profiles with placeholders; a person picks a profile per chat.

**Architecture:** App identity is three environment values served by a public route. Profiles move from one `app_settings` row to a table; a chat row remembers its profile; the turn resolves house rules + profile, fills placeholders, and hands one text to `system_rules`.

**Tech Stack:** FastAPI, psycopg 3, PostgreSQL (plain SQL migrations in `server/sql`), React + Vite, vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-11-16-profile-library-design.md`

## Global Constraints

- Tests run on the scratch database only: `set -a; . <scratchpad>/testdb.env; set +a` before pytest. Never print its URL.
- Walks never use the compose Keycloak: `AUTH_ENABLED=false` or the throwaway one on :18081.
- No profile, house-rules or description text in any log line: who, id and length only.
- No deployment specifics in code or docs; docs use example.com.
- Limits, verbatim from the spec: name 1..60, description 0..200, profile text 1..8,000, house rules 0..2,000, `APP_NAME` 40, `APP_TAGLINE` 120, `APP_COPYRIGHT` 80.
- A person never receives a profile's text.
- Tests first: each test is watched failing before its code is written.
- No commit without the owner's word for that commit.

## Review Focus

1. A chat whose profile is deleted mid-conversation: the next turn uses the default and the chat row keeps `profile_name`.
2. Upgrade with a console profile, with only the file, and with neither: the first turn's system message is byte-identical to before.
3. A profile containing `{{date}}` or `{{app}}`: the app's own line doesn't say it twice; `{{dat}}` stays literal and warns.
4. Two admins: one deletes the default while another makes a different default. Exactly one default remains or the request is refused.
5. `APP_NAME` with a newline, control characters or 500 characters: one clean line of at most 40 in the prompt and on the page.

---

### Task 1: The app's name, tagline, copyright and version

**Files:** Create `server/services/app_identity.py`, `server/routers/app_info.py`, `server/tests/test_app_identity.py`, `src/lib/appIdentity.js`, `src/lib/appIdentity.test.js`, `src/components/AppFootLine.jsx` (+ test). Modify `server/app.py` (include router), `server/chat/prompt.py`, `src/components/auth/AuthGate.jsx`, `src/components/settings/SettingsDialog.jsx`, `vite.config.js` (`define: { __APP_VERSION__ }` from `package.json`), `.env.example`.

**Interfaces — Produces:**
- `app_identity.Identity(name: str, tagline: str, copyright: str)`; `app_identity.load(env=os.environ) -> Identity` (cleaned, one line, cut; defaults per spec); `app_identity.current() -> Identity` (read once, cached).
- `GET /v1/app` (no sign-in) → `{"name", "tagline", "copyright"}`.
- `prompt.first_line(has_profile: bool, identity: Identity) -> str` replacing `FIRST_LINE` / `FIRST_LINE_WITH_PROFILE`: "You are the assistant in {name}, {tagline}." / "You are working in {name}, {tagline}."
- JS: `loadAppIdentity(apiBase) -> Promise<{name, tagline, copyright}>` (falls back to the defaults on any failure, sets `document.title`); `<AppFootLine identity />` renders "{name} {version} · {copyright}".

- [ ] Tests: defaults when unset; cleaning (newline, control chars, 500 chars → ≤ limit, one line); blank after cleaning → default; route answers without a principal; `system_rules` with default identity is byte-identical to today's two lines; with `APP_NAME=Acme Docs` both lines carry it.
- [ ] Run, watch fail; implement; run green.
- [ ] JS tests: fallback on fetch failure; title set; foot line omits the copyright part when empty.

### Task 2: Storage and the upgrade

**Files:** Create `server/sql/030_assistant_profiles.sql`, `server/services/profile_library.py`, `server/tests/test_profile_library.py`. Modify `server/services/assistant_profile.py` (keep `clean_profile`, `profile_warnings`, `file_path`, `_from_file`; `load_profile`/`save_profile` go).

**Interfaces — Produces:**
- Table and columns exactly as the spec's "What is stored"; partial unique index on `is_default`; `chat_sessions.profile_id`, `profile_name`. The migration moves `app_settings.assistant_profile` into a row named "Assistant" (enabled, default) and deletes the old row.
- `ProfileRow(id, name, description, text, enabled, is_default, position)`.
- `async list_profiles(conn, *, enabled_only=False) -> list[ProfileRow]`
- `async create_profile(conn, *, name, description, text, updated_by) -> ProfileRow` (first one becomes the default)
- `async update_profile(conn, id, *, name=None, description=None, text=None, enabled=None, is_default=None, updated_by) -> ProfileRow`
- `async delete_profile(conn, id) -> int` (chats that used it)
- `async reorder(conn, ids: list[str]) -> None`
- `async chats_using(conn, id) -> int`
- `async house_rules(conn) -> str`; `async save_house_rules(conn, text, *, updated_by) -> None`
- `LibraryError(code)` with codes `not_found`, `name_taken`, `default_required` (turning off or deleting the default), `invalid`.

- [ ] Tests: each function; name uniqueness without regard to case; the default can't be disabled or deleted; making another the default clears the old one in one statement; delete sets chats' `profile_id` NULL and keeps `profile_name`; the migration SQL run on a database seeded with the old row yields one default "Assistant" with the same text.

### Task 3: Placeholders and what a turn sends

**Files:** Create `server/chat/profile_text.py`, `server/tests/test_profile_text.py`, `server/tests/test_turn_profile.py`. Modify `server/chat/prompt.py`, `server/chat/orchestrator.py`, `server/chat/store.py`, `server/routers/chat_turns.py`, `server/routers/chat_sessions.py`.

**Interfaces — Produces:**
- `profile_text.PLACEHOLDERS = ("date", "app", "profile")`
- `profile_text.fill(text, *, date: str, app: str, profile: str) -> Filled(text: str, used: frozenset[str])`; unknown `{{x}}` left as written.
- `profile_text.unknown(text) -> list[str]` (for the editor's warning).
- `async profile_text.resolve(conn, *, session_id, asked_id: str | None) -> Resolved(id: str | None, name: str, text: str, house_rules: str)` following the spec's four rules, the file fallback when the library is empty.
- `system_rules(..., profile=<house rules + profile, filled>, placed: frozenset[str])`: the app line omits the app sentence when `"app"` in placed and "Today is…" when `"date"` in placed.
- `TurnIn.profileId: str | None` (max 64); `TurnRequest.profile_id`; `store.set_session_profile(session_id, profile_id, profile_name)`; the `start` event carries `profileId`, `profileName`; a session's JSON carries `profileId`, `profileName`, `profileRemoved`.
- `{{date}}` fills with `today_line`'s date part without "Today is" and the full stop.

- [ ] Tests: fill and unknown; resolve's four rules plus disabled-asked and deleted-chat cases; the turn stamps the chat; a default moved later doesn't change a stamped chat; no profile + no file is byte-identical to today; Review Focus 1, 2, 3.

### Task 4: Routes

**Files:** Modify `server/routers/admin.py`, create `server/routers/chat_profiles.py`, `server/tests/test_profile_routes.py`.

- Admin (require_admin): `GET/POST /v1/admin/assistant/profiles`, `PUT/DELETE /v1/admin/assistant/profiles/{id}`, `PUT /v1/admin/assistant/profiles/order`, `GET/PUT /v1/admin/assistant/house-rules`. A profile's view carries `chats`, `warnings` (tool names + unknown placeholders), `preview` (filled system message), `combinedChars`. `GET/PUT /v1/admin/assistant` removed. Errors: 404 `not_found`, 409 `name_taken`, 409 `default_required`.
- People: `GET /v1/chat/profiles` → `[{id, name, description, isDefault}]`, enabled only, never `text`.

- [ ] Tests: each route and refusal; a member gets 403 on admin routes; the people route's JSON has no `text` key; Review Focus 4.

### Task 5: The admin screen

**Files:** Modify `src/components/admin/AssistantProfileSection.jsx` (+ test); create `src/components/admin/ProfileEditor.jsx`, `HouseRulesEditor.jsx` (+ tests).

- House rules editor (2,000) with "Applies to every chat, before the profile."
- Profiles list: name, description, Default mark, On/Off, Edit, Set as default, Delete (confirm names the chat count), Move up/down, Add profile.
- Editor: Name, Description, text with count and combined count, warnings, preview, Describe + Generate (existing behaviour).
- A notice while house rules are empty and more than one profile exists.
- Placeholder help under the text box: `{{date}}`, `{{app}}`, `{{profile}}`.

- [ ] Tests for each control, the refusals' messages, and 44 px targets on a phone (`phoneTargets.test.jsx` pattern).

### Task 6: The picker

**Files:** Modify `src/hooks/useChatEngine.js`, `src/components/ChatSidebar.jsx`, `src/App.jsx` (+ tests).

- Reads `/v1/chat/profiles` on mount and window focus. Hidden under two profiles.
- "Profile" select under Model, description as a hint line. The pick goes in the turn body as `profileId` and is remembered in the browser (`chatProfile`) for new chats; opening a chat shows that chat's profile; the `start` event corrects the picker.
- The removed-profile line: "The profile this chat used, {name}, was removed."

- [ ] Tests: hidden/shown; turn body carries the id; a chat's own profile wins over the remembered one; the correction; the removed line.

### Task 7: Docs and the walk

- USER_GUIDE, ARCHITECTURE, DEPLOYMENT (`APP_*`, the file's new meaning, what isn't renamed, the realm name), TESTING, CHANGELOG, `.env.example`.
- Walk in the running app with a logging proxy: set `APP_NAME`; sign-in screen and tab; add house rules and two profiles; generate one; pick per chat; read the outgoing system message for each; delete a profile in use; 360 px.
