# Characters: The Librarian, a profile chosen in the chat, and backstories

**Status:** proposed 2026-11-17, awaiting the owner's approval. Builds on
[the profile library](2026-11-16-profile-library-design.md) (v2.13.0).

## Why

Three things the profile library left wrong, found on the owner's server the
day it was deployed:

1. **One profile answers everything.** The only profile there is a storyteller,
   so every document question is answered in a storyteller's voice. The app's
   own careful assistant can't be picked: once any profile exists, it is gone.
2. **People can't see or choose who they talk to.** The picker sits in the chat
   sidebar, appears only with two profiles, is missing from the chat beside the
   page, and nothing names the profile in use.
3. **Generate writes a thin profile.** Its brief asks for under 1,500 characters
   and forbids adding anything, so a described character comes back as two lines.

## Goals

- A plain, careful document assistant, **The Librarian**, that always exists.
- A person sees who is answering and picks in the chat, wherever they chat.
- An admin can make a detailed, story-backed character from their notes, hear
  it answer before saving, and have its one-line description written for them.
- A character never changes what a document answer says, only how it sounds.

**Not in this design:** a profile per project, agents, tools per profile, A2A,
profile pictures. The backstory field is shaped so agents can reuse it.

**Would make it a failure:** a character's invented past quoted as a fact from
a document; an admin's rule reworded by the drafter; an upgrade that changes
who answers on a server without the admin choosing it.

## Approaches

**The plain assistant.**

| | | |
| --- | --- | --- |
| **A. A built-in profile, always in the list** (chosen by the owner) | The app's own assistant is a permanent row beside the admin's profiles. | One special row. |
| B. Documents always answered by it | A profile is set aside when a document is open. | Rules out a Tutor over course notes, and a profile per project. |
| C. Only tell the model the steps win | One sentence after any profile. | Makes it likelier, guarantees nothing. |

A, plus C's sentence, since it costs one line.

**The backstory.** A separate field (chosen) or one longer text. Separate,
because the drafter may then invent in one field and must copy the other; the
app can label the backstory as the character's own life, never a source of
facts; and agents will need the same split.

## The Librarian

- A row in `assistant_profiles` with `builtin = true`, added by migration 031.
  Name "The Librarian", description "Careful answers from your documents, with
  page numbers."
- An admin may **rename** it, edit its **description**, reorder it, and make it
  or another profile the default. It **can't be deleted or turned off**, and it
  has no backstory or text to edit: it is the app's own rules and nothing else.
  The server refuses those changes (409), the screen doesn't offer them.
- Its instructions: `You are {name}, the assistant in {app}, {tagline}.` and
  then exactly what a chat with no profile gets today. House rules still lead.
- **Upgrade:** the row is added and nothing else changes. Whichever profile was
  the default stays the default; a database with no profiles gets The Librarian
  as its default. The admin screen says, once, that The Librarian exists and can
  be made the default.
- **`CHAT_ASSISTANT_PROFILE_FILE`** keeps its meaning: while The Librarian is
  the only profile, the file's text is used as the profile, as it was while the
  library was empty.

## Choosing in the chat

- **An empty chat shows the profiles as cards** (name, description, the default
  marked) under "Who would you like to talk to?", when more than one is on. One
  tap picks; typing without picking uses the default, as now.
- **The profile's name sits at the top of the chat** (in `ChatView`, so Chat
  view, the chat beside the page and Talk all have it). Tapping it opens the
  same list; a pick applies from the next message, as now. With one profile the
  name is shown and isn't a button.
- The sidebar's Profile dropdown goes. The "was removed" line moves under the name.
- The list is read again when a chat is opened or started, so a profile an
  admin just added appears without a reload.
- At 360 px the cards stack; the name truncates.

## Backstory and rules

- `assistant_profiles.backstory` (text, up to 6,000 characters, default empty).
  The existing `text` stays, up to 8,000, and the editor calls it **Rules**.
- What the model receives, in order: house rules; the backstory under
  `Who you are (your own life and voice, never a source of facts about
  documents or the world):`; the rules; the app's line; then one new sentence
  whenever a profile other than The Librarian is used: `Whatever your role, an
  answer from the documents follows the steps below and cites its pages.`
- `{{date}}`, `{{app}}` and `{{profile}}` work in both fields.
- The editor shows what both fields add to every message, as "about N tokens"
  (characters ÷ 4), and **What the model receives** includes the backstory.
- People still see a name and description only.

## Generate, for a whole character

- **Describe the character** grows to 6,000 characters.
- **Written by:** a model picker beside Generate, the helper by default, any
  chat model the admin may use. A cloud model means the notes leave the
  machine; the picker says so. Charged to the admin's daily budget, as now.
- The reply fills **name, description, backstory and rules** (name and
  description only where those fields are empty). The brief has two halves:
  the backstory may invent a past, a voice and habits that fit the notes; the
  rules copy each rule the admin gave, meaning unchanged, and add none.
- Nothing is saved until **Save profile**; unsaved text is replaced only after
  a confirm, as now.

## The description, written from the profile

**✦ Generate** beside the description field asks the helper for one line (up
to 200 characters) from the profile's name, backstory and rules: who a person
would be talking to, in words for that person, quoting no rule. It fills the
field; the admin can edit it. Hidden when no helper model is set.

## Hear a sample reply

In the editor, a question box (default "Introduce yourself.") and **Try it**:
the unsaved profile answers once, with a chat model the admin picks, with no
tools and no documents. The reply is shown under the box and stored nowhere.
Charged to the admin's budget.

## Routes

- `POST /v1/admin/assistant/draft` gains `model`; returns `name`,
  `description`, `backstory`, `text`.
- `POST /v1/admin/assistant/describe` → `{description}`.
- `POST /v1/admin/assistant/sample` → `{reply}`.
- Profile create and update take `backstory`; the list and
  `GET /v1/chat/profiles` say `builtin`.
- Profile text, backstories and notes are never logged.

## Proof before it's called done

- Walked in the UI: upgrade with one profile; cards; the name in all three
  chats; a pick remembered; The Librarian can't be deleted or turned off;
  Generate with the helper and with a larger model; the description button;
  Try it; 360 px.
- On the wire: the backstory label and the "steps win" sentence in the real request.
- **The document evaluation twice,** as The Librarian and as a character with a
  long backstory: right-page citations and invented facts, side by side.
- **Measured on the CPU:** the delay before the first word with a 6,000-character
  backstory, first message and later ones. If it's bad, the cap comes down.

## Order

1. The Librarian and choosing in the chat (migration 031, the cards, the name).
2. Backstory, Generate for a whole character, the description button, Try it.
3. The evaluation and the timing.

## Left to the builder

The card and name-menu styling; how the draft's four parts are marked in the
model's reply; the exact wording of the two new briefs (pinned by tests once
written).
