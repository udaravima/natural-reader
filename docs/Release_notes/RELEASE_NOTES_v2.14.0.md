# v2.14.0 — Characters: The Librarian, a backstory, and choosing who answers

**Every chat now says who is answering, and you choose right where you type.**
A chip beside the message box, **Talking to: …**, names the profile in the chat,
in the chat beside the page and in Talk, and opens the list. A new chat offers
the profiles as cards. The sidebar's **Profile** picker is gone.

**The Librarian is always there.** The app's own careful document assistant is
now a built-in profile beside the admin's. It can be renamed, described, moved
and made the default, but never deleted or turned off. On a fresh install it is
the only profile and the default. An upgrade adds it last and leaves the default
where it was.

**A profile can be a character.** Beside its **Rules**, a profile now has a
**Backstory** of up to 6,000 characters. **Generate** writes a whole character,
with name, description, backstory and rules, from a few lines of notes. **Try it**
asks the unsaved profile one question. Whatever the character, an answer from the
documents still follows the document steps and cites its pages.

## Added

### 📚 The Librarian, and choosing who answers

- **The Librarian** is listed with **Built in** in **Admin → Chat → Assistant**.
  Its row has **Edit**, **Make default**, **Up** and **Down**, but no **Turn off**
  or **Delete**. It has no text of its own: a chat that uses it gets the app's
  rules, with its name in the first line.
- **Talking to: …** sits beside the message box wherever you chat, and opens the
  list of profiles an admin left on. A chat keeps the profile it was given.
- **A new chat shows the profiles as cards** when there is more than one. Pick a
  card, or just type to use the default. The cards stack on a phone.
- **The list is read again when a chat opens,** so a profile an admin just added
  or turned off shows up without a reload.

### 🎭 Characters with a backstory

- **Backstory** (up to 6,000 characters) is who the character is: their past,
  their voice, what they care about. The model gets it after the house rules,
  labelled as the character's own life and never as a source of facts. **Rules**
  are what it must and must not do. The Librarian has no backstory.
- **Generate** writes all four parts from up to 6,000 characters of notes. The
  helper writes them, or any chat model you pick under **Written by**. A cloud
  model is marked, because your notes leave the machine. Rules given in the
  notes are copied, not reworded. Nothing is saved until **Save profile**.
- **✦ Generate** beside the description writes the line people see from the
  profile. **Try it** asks the unsaved profile one question with a model you
  pick, before anyone else meets it.
- **A character can't override the document steps.** A profile with text of its
  own is followed by "Whatever your role, an answer from the documents follows
  the steps below and cites its pages."

### Also new

- **Talk shows where each round's wait went.** Under **What it heard**, each
  round has a line per stage: **Heard** (the speech engine's own seconds, where
  it ran, and the upload and wait around it), **Model** (first word, whole
  reply, words a second) and **Voice** (who spoke, the engine's seconds, the
  network, how many sentences and the slowest). The slowest stage is marked, and
  **Copy numbers** carries it all.
- **The document evaluation can answer as a character.**
  `scripts/eval_doc_qa.py --character FILE` takes a file in Generate's four-part
  format and asks every question as that profile, so a character can be checked
  against the documents before it goes live. Each answer now shows the delay
  before its first word. `server/evals/fixtures/characters/seeya.md` is a
  storyteller with a full-length backstory.
- **`docs/MAINTENANCE.md`** gathers the jobs on a running server: is it
  healthy, surviving reboots, applying a `.env` change, backup and restore with
  a nightly job, upgrading, rolling back, rotating secrets and freeing disk
  space. Backup and restore and rotating the Keycloak secrets moved there from
  DEPLOYMENT.md, whose old headings point to them.

## Fixed

- **A newer Google key (`AQ.…`) is now hidden in provider errors,** like the
  older `AIza…` keys. A Gemini error that echoed one back showed it whole.
- **A project's page on a phone.** Below about 420 px its buttons squeezed the
  project's name and the **Projects** link to nothing, and **Delete project** ran
  off the screen. The buttons now go below the name and wrap.
- **After changing `.env`, run `./startup.sh rebuild`.** DEPLOYMENT.md said a
  restart would do after changing the Keycloak secrets. It doesn't: the backend
  container reads `.env` only when it is created. The backend's own warning
  about a published secret now says `./startup.sh rebuild` too.

## Upgrading

- **Migrations 031 and 032 run at start:** a built-in flag on profiles, and the
  backstory. Back up first (MAINTENANCE.md, "Backup and restore"), then rebuild
  both halves: the backend image (`./startup.sh rebuild`) and the frontend
  (`npm run build`).
- **The Librarian is added at start, last in the list.** Your default stays the
  default, and every chat keeps its profile. Make The Librarian the default if
  you want new chats to start with the app's own document assistant.
- **No new settings.** `.env.example` changed only in its comments.
- **The admin API grew:** `POST /v1/admin/assistant/describe` and `/sample` are
  new. `/draft` takes an optional `model` and now returns `name`, `description`,
  `backstory` and `text`. Profiles carry `backstory` and `builtin`. The
  transcription and chat-voice replies gain `engine_seconds` and `where`, and
  the voice reply says `engine`. Nothing was removed.
- **Downgrading:** run the Down blocks in the headers of 032, then 031. Every
  backstory is lost, and The Librarian's row goes. If The Librarian is the
  default, make another profile the default first.
- **Coming from v2.12.0 or earlier?** Read v2.13.0's notes too, and v2.12.0's
  from v2.11.0 or earlier: rotate the Keycloak secrets.

## Known limits

- **A long backstory costs time on a CPU, on a chat's first message.** With
  `gemma4:e4b` on the test machine's CPU, a 6,000-character backstory made the
  first word of a new chat take 54 s, against 26 s for The Librarian. Later
  messages in the same chat answered in about a second, because Ollama keeps the
  start of the prompt. When two chats take turns, each message reads its prompt
  again: 14 to 33 s.
- **A character on a small local model holds the documents less well over a
  chat.** In four-message chats on `gemma4:e4b`, Seeya (a 6,000-character
  backstory) passed 3 and 4 of 8 in two runs, against 6 and 8 of 8 for The
  Librarian. On `gemma-4-26b` through OpenRouter, Seeya passed 12 of 12 and 26
  of 27 single questions.
- **A small helper can write the description in the character's own voice**
  ("…I offer patient wisdom…, child"). Read it before saving, as you would a
  generated profile.
- **Walked on a local model only.** Ollama's cloud refused for want of credits
  (HTTP 402), and the app said so in the chat.
- **Talk appears after a reload** when an admin turns **Voice input** on, not in
  a tab that is already open. It has worked this way since Talk arrived.
- **The final reviews of these changes were self-reviews,** not a second
  reviewer's.

## Tests

3,615 backend tests pass (16 skipped), and 2,113 frontend tests in 177 files.
Lint has no errors, the build works, and CI is green on `development`.

Walked in headless Chromium on the merged tree, against a scratch backend and
database, with `gemma4:e4b` and a logging proxy recording what the model
received:

- **A fresh install:** The Librarian is the only profile and the default, with
  no **Turn off** or **Delete**. With one profile there are no cards, and it
  answered as itself.
- **Generate:** a character from four lines of notes in 26 s with the helper,
  both rules copied word for word; **✦ Generate** for the description;
  **Try it**; **Save profile**.
- **Choosing:** the cards in a new chat, a card picked, the chip switched in the
  middle of a chat, and each answer from the profile picked. At 360 px the cards
  stack and nothing scrolls sideways.
- **The chat beside the page as a character:** in character, the right facts and
  the page cited. On the wire: the backstory under its label, then the rules,
  then the document steps, with the passage sent as a tool result.
- **Talk:** a spoken question from a Kokoro recording, with each round's
  **Heard**, **Model** and **Voice** lines and **Copy numbers**.

## Full changelog

[CHANGELOG.md](../../CHANGELOG.md#2140---2026-11-17) · diff:
[v2.13.0…v2.14.0](https://github.com/udaravima/natural-reader/compare/v2.13.0...v2.14.0)
