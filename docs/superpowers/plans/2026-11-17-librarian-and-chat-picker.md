# The Librarian and choosing in the chat — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A built-in profile, The Librarian, that always exists, and a profile a person sees and picks where they chat.

**Architecture:** Migration 031 adds `assistant_profiles.builtin`; a start-up step (`profile_library.ensure_librarian`) adds the row when it is missing, so it is there on a fresh database, after the upgrade, and after a hand edit. The Librarian has no text: a turn that uses it gets the app's own rules with its name in the first line. People pick in a chip beside "What this chat is about" and in cards on an empty chat; the sidebar dropdown goes.

**Tech Stack:** FastAPI + psycopg 3 (Postgres 16), React 19 + Vite + Tailwind 4, pytest (scratch DB on :5434), vitest.

**Spec:** [docs/superpowers/specs/2026-11-17-character-profiles-design.md](../specs/2026-11-17-character-profiles-design.md), sections "The Librarian" and "Choosing in the chat", plus the "steps win" sentence from "Backstory and rules" (it is what fixes a storyteller answering document questions, so it ships now).

## Departures from the spec (rulings)

1. **The name sits in the chip row above the message box, not at the top of the chat.** That row ("What this chat is about") is already shown in Chat view, in the chat beside the page and during Talk; a new bar at the top would cost a phone a line of every screen. Who answers sits beside what it is about.
2. **The Librarian is added at start-up, not by the migration.** Migration 031 changes the schema only. `ensure_librarian` runs in `db._after_migrations` (every worker, under the table lock), so it also comes back if someone deletes the row by hand. Tests' databases never run it, so existing tests keep an empty library.
3. **The admin note about The Librarian is always shown on its row,** not once: no dismissed-state to store.
4. **The Librarian goes last in the list** on the upgrade, so an admin's order is untouched; they can move it.

## Global Constraints

- The Librarian: name "The Librarian", description "Careful answers from your documents, with page numbers.", `builtin = true`, text empty, always enabled.
- An admin may rename it, edit its description, reorder it, make it the default; it can't be deleted, turned off or given text. The server refuses with 409, code `builtin`; the screen doesn't offer those controls.
- Its first line: `You are {name}, the assistant in {app}, {tagline}.` Then exactly what a chat with no profile gets today. House rules still lead.
- Upgrade: the default stays the default. A library with no default (empty) gets The Librarian as its default.
- `CHAT_ASSISTANT_PROFILE_FILE`: used as the profile's text while The Librarian is the only profile (as it was while the library was empty).
- The "steps win" sentence, exactly: `Whatever your role, an answer from the documents follows the steps below and cites its pages.` Only for a profile with text (not The Librarian, not house rules alone), only on a turn that gets the document data rule.
- People see names and descriptions, never text; profile text is never logged.
- Tests run against the scratch Postgres on :5434 (`set -a; . <scratchpad>/testdb.env; set +a`), never 5433.

## Review Focus

1. **A profile already named "The Librarian"** (an admin's own, made as a stopgap): start-up must not fail on the unique name; the built-in one is "The Librarian (built in)". Pinned in Task 1.
2. **The deployment file with only The Librarian:** the file's text is the profile and the first line says "You are working in…", not "You are The Librarian". Pinned in Task 2.
3. **A remembered pick that was turned off since:** the chip shows the profile the turn will really use (the chat's own or the default), never an empty label. Pinned in Task 5.
4. **A renamed Librarian:** the next turn's first line and the `start` event carry the new name. Pinned in Task 2.
5. **360 px:** cards stack, a 60-character name truncates in the chip, no sideways scroll. Checked in the Task 6 walk.

---

### Task 1: The built-in row

**Files:**
- Create: `server/sql/031_builtin_profile.sql`
- Modify: `server/services/profile_library.py` (ProfileRow, `_COLUMNS`, `update_profile`, `delete_profile`, new `ensure_librarian`), `server/db.py` (`_after_migrations`)
- Test: `server/tests/test_librarian.py` (new)

**Interfaces:**
- Produces: `ProfileRow.builtin: bool` (last field); `LIBRARIAN_NAME = "The Librarian"`, `LIBRARIAN_DESCRIPTION`; `async def ensure_librarian(conn) -> ProfileRow`; `LibraryError` code `"builtin"`.

- [ ] **Step 1: Write the failing tests** (the test database is migrated from `server/sql`, so it has the column once the migration file exists; `_new` is the helper in `test_profile_library.py`)

```python
async def test_the_librarian_is_added_once_and_is_the_default_of_an_empty_library(db_conn):
    a = await lib.ensure_librarian(db_conn)
    b = await lib.ensure_librarian(db_conn)
    assert a.id == b.id and (a.name, a.builtin, a.enabled, a.is_default, a.text) == ("The Librarian", True, True, True, "")
    assert a.description == "Careful answers from your documents, with page numbers."
    assert len(await lib.list_profiles(db_conn)) == 1

async def test_on_the_upgrade_the_default_stays_and_the_librarian_goes_last(db_conn):
    story = await _new(db_conn, "The Story Teller")          # the first profile: the default
    librarian = await lib.ensure_librarian(db_conn)
    assert (librarian.is_default, (await lib.default_profile(db_conn)).id) == (False, story.id)
    assert [p.name for p in await lib.list_profiles(db_conn)] == ["The Story Teller", "The Librarian"]

async def test_a_profile_already_called_the_librarian_keeps_its_name(db_conn):
    await _new(db_conn, "the librarian")
    assert (await lib.ensure_librarian(db_conn)).name == "The Librarian (built in)"

async def test_the_librarian_can_be_renamed_and_described_but_not_deleted_off_or_given_text(db_conn):
    lb = await lib.ensure_librarian(db_conn)
    renamed = await lib.update_profile(db_conn, lb.id, name="Ada", description="Reads with you", updated_by=None)
    assert (renamed.name, renamed.description, renamed.builtin) == ("Ada", "Reads with you", True)
    for kw in ({"enabled": False}, {"text": "You are a pirate."}):
        with pytest.raises(lib.LibraryError) as e:
            await lib.update_profile(db_conn, lb.id, updated_by=None, **kw)
        assert e.value.code == "builtin"
    with pytest.raises(lib.LibraryError) as e:
        await lib.delete_profile(db_conn, lb.id)
    assert e.value.code == "builtin"

async def test_a_new_profile_is_not_the_default_while_the_librarian_is(db_conn):
    await lib.ensure_librarian(db_conn)
    assert (await _new(db_conn, "Tutor")).is_default is False
```

- [ ] **Step 2: Run, expect failure** — `.venv/bin/python -m pytest server/tests/test_librarian.py -q` → `AttributeError: ... ensure_librarian`.
- [ ] **Step 3: Migration `031_builtin_profile.sql`** — header with the Down block (drop the index and column, restore the two checks, `DELETE FROM schema_migrations WHERE version = 31`). Body: `ADD COLUMN IF NOT EXISTS builtin BOOLEAN NOT NULL DEFAULT FALSE`; replace the text check (`DROP CONSTRAINT IF EXISTS assistant_profiles_text_check`, add `CHECK (char_length(text) <= 8000 AND (builtin OR char_length(text) >= 1))`; confirm the real constraint name with `\d assistant_profiles` on :5434 first); add `CHECK (enabled OR NOT builtin)`; `CREATE UNIQUE INDEX IF NOT EXISTS assistant_profiles_one_builtin ON assistant_profiles ((TRUE)) WHERE builtin`; record version 31.
- [ ] **Step 4: Service.** `builtin` joins `ProfileRow` and `_COLUMNS`. `ensure_librarian`: in a transaction under `_LOCK`, return the builtin row if one exists; else insert with the first free name of `LIBRARIAN_NAME`, `f"{LIBRARIAN_NAME} (built in)"`, then `f"{LIBRARIAN_NAME} (built in) {n}"` (n = 2, 3…; compare `lower(name)`), text `''`, `is_default = NOT EXISTS (SELECT 1 FROM assistant_profiles WHERE is_default)`, position `max + 1`. `update_profile` refuses `enabled is False` or `text is not None` on a builtin row, and `delete_profile` refuses a builtin row: `LibraryError("builtin", "The Librarian is always here: it can be renamed, not deleted, turned off or given text.")`. Log line: id only.
- [ ] **Step 5:** `db._after_migrations` calls `await profile_library.ensure_librarian(conn)` (after the existing sweeps; its own failure is logged and doesn't stop the start).
- [ ] **Step 6: Run** the new file and `server/tests/test_profile_library.py test_db_boot.py` → all pass.
- [ ] **Step 7: Commit** `feat(assistant): The Librarian, a built-in profile that is always there (migration 031)`.

### Task 2: A turn that uses The Librarian, and "steps win"

**Files:**
- Modify: `server/chat/profile_text.py` (`Resolved`, `resolve`, `largest`), `server/chat/prompt.py` (`first_line`, `system_rules`, new `STEPS_WIN`), `server/chat/context.py` (`TurnInput.assistant_name`, pass-through), `server/chat/orchestrator.py` (pass `assistant_name`), `server/routers/admin.py` (preview passes nothing new)
- Test: `server/tests/test_turn_profile.py`, `server/tests/test_profile_text.py`, `server/tests/test_chat_prompt.py`

**Interfaces:**
- Consumes: `ProfileRow.builtin`, `ensure_librarian` (Task 1).
- Produces: `Resolved.builtin: bool` (True only when the row is builtin and no file text is used); `first_line(has_profile, identity, name: str = "") -> str`; `system_rules(..., assistant_name: str = "")`; `STEPS_WIN` constant.

- [ ] **Step 1: Failing tests**

```python
# test_chat_prompt.py
def test_the_librarian_is_named_in_the_first_line():
    text = system_rules(None, [], has_pins=False, assistant_name="The Librarian")
    assert text.startswith(f"You are The Librarian, the assistant in {app_identity.current().name}, ")

def test_a_profile_with_text_is_told_the_document_steps_win():
    text = system_rules(DOC, TOOLS, has_pins=False, profile="You are an old storyteller.")
    assert STEPS_WIN in text and text.index(STEPS_WIN) < text.index("How to handle each message:")

def test_no_steps_win_line_for_the_librarian_house_rules_or_a_chat_without_documents():
    assert STEPS_WIN not in system_rules(DOC, TOOLS, has_pins=False, assistant_name="The Librarian")
    assert STEPS_WIN not in system_rules(DOC, TOOLS, has_pins=False, profile="Be kind.", persona=False)
    assert STEPS_WIN not in system_rules(None, [], has_pins=False, profile="You are an old storyteller.")

# test_turn_profile.py
async def test_a_chat_with_the_librarian_resolves_to_it_by_its_current_name(db_conn): ...
    # ensure_librarian, rename it "Ada": resolve(...) -> (id, name, text, builtin) == (lb.id, "Ada", "", True)
async def test_the_file_is_the_profile_while_the_librarian_is_alone(db_conn, tmp_path, monkeypatch): ...
    # CHAT_ASSISTANT_PROFILE_FILE -> "You are Ada." ; resolve -> text "You are Ada.", builtin False, id == librarian id
    # add a profile with text: the file is no longer read
```

`DOC` and `TOOLS` are the indexed sample document and its tools the file already uses (reuse its fixtures). `STEPS_WIN` is imported from `server.chat.prompt`.

- [ ] **Step 2: Run, expect failures** (`TypeError: unexpected keyword 'assistant_name'`, `ImportError: STEPS_WIN`).
- [ ] **Step 3: Implement.**
  - `first_line`: a non-empty `name` gives `f"You are {name}, the assistant in {identity.name}, {identity.tagline}."`; otherwise as now.
  - `system_rules`: pass `assistant_name` to `first_line` (still left out when `"app" in placed`). Compute the data-rule condition once; when it holds and `profile and persona`, append `STEPS_WIN` right after the document/project line.
  - `resolve`: when the row found is builtin, the text is the file's if no non-builtin profile exists, else `""`; `builtin = row.builtin and not text`. When no row at all (a hand-emptied table), behave as today.
  - `largest`: the longest text among non-builtin rows, else the file.
  - `TurnInput.assistant_name: str = ""`, passed by `context.py` to `system_rules`; the orchestrator sets it to `profile.name if profile.builtin else ""`.
- [ ] **Step 4: Update pinned prompt expectations** that now carry `STEPS_WIN` (a profile with text and a document): search `test_chat_prompt.py`, `test_chat_orchestrator.py`, `test_assistant_profile.py` for the profile cases and add the line where the new rule puts it; no other wording changes.
- [ ] **Step 5: Run** `server/tests/test_chat_prompt.py test_turn_profile.py test_profile_text.py test_chat_orchestrator.py test_assistant_profile.py -q` → pass.
- [ ] **Step 6: Commit** `feat(chat): a turn with The Librarian names it; a character is told the document steps win`.

### Task 3: Routes

**Files:**
- Modify: `server/routers/admin.py` (`_LIBRARY_STATUS`, `_library_view`), `server/routers/chat_profiles.py`
- Test: `server/tests/test_assistant_profile.py`

**Interfaces:**
- Produces: admin view profile objects gain `builtin: bool`; `file.inUse` is true while no non-builtin profile exists and the file has text; `GET /v1/chat/profiles` items gain `builtin`.

- [ ] **Step 1: Failing tests:** with `ensure_librarian` run first — `DELETE /v1/admin/assistant/profiles/{librarian}` → 409 `{"code": "builtin"}`; `PUT` with `{"enabled": false}` → 409 builtin; `PUT {"name": "Ada"}` → 200 and the view shows `"builtin": true, "name": "Ada"`; `GET /v1/chat/profiles` lists it with `builtin: true` and no `text` key.
- [ ] **Step 2: Run, expect failures.**
- [ ] **Step 3:** `"builtin": 409` in `_LIBRARY_STATUS`; `builtin` in both outputs; the view's `file_text` uses "no non-builtin row". `combinedChars` for The Librarian is the house rules' length.
- [ ] **Step 4: Run** → pass. **Step 5: Commit** `feat(api): say which profile is built in; refuse deleting or turning it off`.

### Task 4: The admin screen

**Files:**
- Modify: `src/components/admin/AssistantProfileSection.jsx`, `src/components/admin/ProfileEditor.jsx`
- Test: `src/components/admin/AssistantProfileSection.test.jsx`

**Interfaces:**
- Consumes: `builtin` on each profile (Task 3).
- Produces: `ProfileEditor` prop `builtin: boolean` (default false).

- [ ] **Step 1: Failing tests:** a library with The Librarian (builtin) and a default Story Teller renders a `Built in` badge and the line `The app's own document assistant, always here: it can be renamed, not deleted or turned off.` on The Librarian's row; that row has no `Delete The Librarian` and no `Turn off The Librarian` button but has `Make The Librarian the default`, `Edit The Librarian`, Up and Down. Its editor has the name and description fields and no `Profile text` box, no Generate, no "What the model receives"; Save sends `{name, description}` only.
- [ ] **Step 2: Run, expect failures** (`npx vitest run src/components/admin/AssistantProfileSection.test.jsx`).
- [ ] **Step 3: Implement** with `p.builtin` guards; `saveProfile` omits `text` for builtin. The "No profiles yet" note and "Add profile starts from the file" now test for non-builtin profiles.
- [ ] **Step 4: Run** → pass. **Step 5: Commit** `feat(admin): The Librarian's row and editor`.

### Task 5: Choosing in the chat

**Files:**
- Create: `src/components/chat/ProfileChip.jsx`, `src/components/chat/ProfileChip.test.jsx`, `src/components/chat/ProfileCards.jsx` (with its test)
- Modify: `src/components/ChatView.jsx` (chips row, `EmptyState`, mount refresh), `src/components/ChatSidebar.jsx` (remove the dropdown and the removed-line), `src/hooks/useChatEngine.js` (refresh on `newSession` and `switchToSession`), `src/App.jsx` (`chatViewProps` gets the profile fields; `ChatSidebar` loses them)
- Test: `src/components/ChatSidebar.profiles.test.jsx` (now: the sidebar has no Profile control), `src/hooks/useChatEngine.profiles.test.js`, a new `src/components/ChatView.profiles.test.jsx`

**Interfaces:**
- Consumes from `useChatEngine`: `profiles` ([{id, name, description, isDefault, builtin}]), `profileId`, `setProfile(id)`, `refreshProfiles()`, `currentProfile` ({id, name, removed} | null), `removedProfile` (name | null).
- Produces: `ProfileChip({theme, profiles, profileId, currentProfile, removedProfile, onChange})`; `ProfileCards({theme, profiles, profileId, onPick})`; new ChatView props `profiles`, `profileId`, `setProfile`, `refreshProfiles`, `currentProfile`, `removedProfile`.

- [ ] **Step 1: Failing tests**
  - ProfileChip: label `Talking to: The Story Teller`; with two or more choices it is a menu button (`aria-haspopup="menu"`, ScopeChip's keyboard pattern) whose `menuitemradio` items are the profiles ("(default)" after the default, "(turned off)" for the chat's own that is off), the current one `aria-checked`; choosing calls `onChange(id)`. With one choice it renders plain text, no button. A `profileId` not in the list falls back to `currentProfile.name`, then the default's name, never an empty label. `removedProfile` shows `The profile this chat used, X, was removed.` under the chip.
  - ProfileCards: heading `Who would you like to talk to?`; one `button` per profile with name and description, `aria-pressed` on the current; a tap calls `onPick(id)`; renders nothing with fewer than two profiles.
  - ChatView: the chip is in `composer-chips` whenever profiles exist; an empty chat with two profiles shows the cards; mounting calls `refreshProfiles` once.
  - ChatSidebar: no element labelled `Profile`.
  - useChatEngine: `newSession()` and `switchToSession(id)` each read `/v1/chat/profiles` again.
- [ ] **Step 2: Run, expect failures.**
- [ ] **Step 3: Implement.** The chips row shows when `scope && setScope`, the speaking switch, or `profiles.length > 0`. Cards go in `EmptyState` above the model line, wrapping (`flex flex-wrap`, each card `basis-56 grow`), so they stack at 360 px; the chip's label truncates (`max-w-full truncate`).
- [ ] **Step 4: Run** the files above, then `npx vitest run` → all pass; `npx eslint .` → 0 errors.
- [ ] **Step 5: Commit** `feat(chat): see and choose who answers, where you chat`.

### Task 6: Docs, the walk, and proof on the wire

**Files:**
- Modify: `docs/USER_GUIDE.md` ("Who the assistant is", the admin's Assistant section), `docs/ARCHITECTURE.md` (migration list: 031), `CHANGELOG.md` (Unreleased), `docs/TESTING.md` (row)

- [ ] **Step 1: Docs** in the house style, saying what a person can do: the chip and cards, The Librarian and its limits, the "steps win" line in "What the model receives".
- [ ] **Step 2: Walk** in headless Chromium against a scratch backend (`AUTH_ENABLED=false`, its own database on :5434, the logging proxy in front of Ollama): start with a Story Teller as the only profile → start-up adds The Librarian last, Story Teller stays default; admin row and editor; make The Librarian default; chat: cards on an empty chat, pick, chip, a document question; side panel chip; Talk shows the chip; 360 px. On the wire: The Librarian's first line, and `STEPS_WIN` present for the Story Teller with a document open and absent for The Librarian.
- [ ] **Step 3: Full gates:** pytest (scratch DB), vitest, eslint, `npm run build`.
- [ ] **Step 4: Commit** `docs: The Librarian and choosing who answers`.
