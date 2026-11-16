# Profile library: several assistant profiles, chosen per chat

Status: approved by the owner 2026-11-16. Plan: docs/superpowers/plans/2026-11-16-profile-library.md.

## Why

Today a deployment has exactly one assistant profile. An admin writes it, it leads every chat's system
message, and nobody can choose. A tutor, a careful contract reviewer and a plain helper need different
voices, and the people chatting know which one they need. The owner also plans to give profiles a
backstory and to build agents on them later, possibly reachable over the A2A protocol, so a profile
has to become a named thing with an identity, not one blob of text.

## Goals

- An admin keeps several named profiles and one set of house rules that always applies.
- A person picks a profile for a chat from the admin's list, in one tap, where the chat is.
- A chat keeps its profile. Another person's pick, or a change of the default, never changes it.
- Upgrading changes nothing a person can see until an admin adds a second profile.
- A profile's record is shaped so that a backstory, and later an agent built on it, are added columns
  and not a redesign.

## Not in this work

- People writing their own profiles. The profile is trusted, unfenced text ahead of the app's rules;
  admin-written keeps that trust simple.
- A profile per project (planned next: a project owner picks one of the admin's profiles).
- Backstory, per-profile tools or model, agents, A2A. Section "What comes later" says how this design
  leaves room for them.
- Limiting a profile to some roles.

## What must never happen

- A person's choice removes or weakens the house rules.
- A person reads a profile's text. They see its name and description only: the text is the
  deployer's instruction and may hold things not meant for readers.
- A chat silently changes voice because an admin deleted its profile.

## Approaches considered

1. **A library the admin writes, a pick per chat (chosen by the owner, 2026-11-16).** Described below.
2. **A profile per project only.** Fits shared work, but a personal chat has no project, so most chats
   would still have one voice.
3. **People write their own profiles.** The most freedom, but it puts a person's text in the trusted
   position, and house rules would then need enforcing against it. Left out for now.

## How it works

### What is stored

A new table `assistant_profiles` (migration 030):

| column | meaning |
|---|---|
| `id` | UUID. The stable identity; later an agent's identity. Never reused. |
| `name` | 1 to 60 characters, unique without regard to case. Shown in the picker. |
| `description` | 0 to 200 characters. One line under the name in the picker. |
| `text` | 1 to 8,000 characters. The profile itself, cleaned as today (`clean_profile`). |
| `enabled` | Off: hidden from the picker; chats already using it keep it. |
| `is_default` | Exactly one row when any row exists (a partial unique index). Must be enabled. |
| `position` | Order in the picker. |
| `created_at`, `updated_at`, `updated_by` | As other admin records. |

House rules are one `app_settings` row, `assistant_house_rules`, `{"text": ...}`, 0 to 2,000 characters.

`chat_sessions` gains `profile_id` (nullable, `REFERENCES assistant_profiles ON DELETE SET NULL`) and
`profile_name` (the name when it was last used). A chat with a `profile_name` and no `profile_id` used
a profile that was since deleted.

### The upgrade

If the admin console holds a profile today (`app_settings` row `assistant_profile`), the migration
turns it into one profile named "Assistant", enabled and default, with the same text, and removes the
old row. House rules start empty. Every chat then reads exactly what it read before.

A deployment that uses the file (`CHAT_ASSISTANT_PROFILE_FILE`) keeps it: while the library is empty,
the file's text is the profile, as today. Once an admin saves a first profile, the library wins, as
the console wins over the file today.

### What a turn sends

The system message begins with the house rules, then the profile, then the app's own rules, which
still come last and so still win on documents, tools and data. Both texts are read from the database
on every turn, so an admin's edit applies from the next message.

Which profile, first match wins:

1. the profile the turn asks for (`profileId` in the turn body), if it exists and is enabled;
2. the chat's own `profile_id`, if that profile still exists (enabled or not);
3. the default profile;
4. none (an empty library and no file).

The turn writes the profile it used to the chat (`profile_id`, `profile_name`). So a chat started on
the default keeps that profile when the default later moves to another one. An unknown or disabled
`profileId` is not an error: the turn falls through to rule 2, and the reply's `start` event names
the profile used, so the picker corrects itself.

Editing a profile's text does change chats that use it, from the next message. That is intended: an
admin fixing a rule must reach every chat. Only the choice of profile is pinned, not its wording.

### Placeholders (the owner's choice, 2026-11-16)

Today the app adds one fixed line after the profile: "You are working in Natural Reader, an app for
reading and listening to documents. Today is Sunday, 15 November 2026 (UTC)." An admin can't move or
reword it. House rules and profile text may instead carry placeholders, filled in on every turn:

| placeholder | becomes | changes |
|---|---|---|
| `{{date}}` | "Sunday, 15 November 2026 (UTC)", in the person's timezone as today | once a day |
| `{{app}}` | the app's name (see "The app's name") | never |
| `{{profile}}` | this profile's name | never |

Rules:

- The app's own line still follows the profile, but leaves out what the text already placed: no
  "Today is ..." when `{{date}}` was used, no app sentence when `{{app}}` was used. A profile that uses
  neither reads exactly as today, so no profile can lose the date by accident.
- An unknown placeholder is left as written and the editor warns about it, like the tool-name warning.
  The preview shows the text filled in.
- Only these three. The person's name was considered and left out by the owner. A document's name, a
  project's name or anything else a person can type is never a placeholder: the profile is trusted
  text, and a value a person wrote would be an instruction in it.

### The app's name, tagline and copyright (asked for by the owner, 2026-11-16)

"Natural Reader" is written into the code. A deployer sets these in the server's environment:

| setting | unset means | limit |
|---|---|---|
| `APP_NAME` | "Natural Reader" | 40 characters |
| `APP_TAGLINE` | "an app for reading and listening to documents" | 120 characters |
| `APP_COPYRIGHT` | no copyright line | 80 characters, shown as written (e.g. "© 2026 Example Org") |

Each is cleaned like a profile (no control characters, one line). They are read once at start.
`APP_NAME` is a first-deployment choice: the Keycloak realm carries its own display name, set in
Keycloak, and the two should match from the start. Changing it later works after a restart, but the
realm's name has to be changed by hand too. DEPLOYMENT says so.

Where a person or the model meets them:

| place | today | after |
|---|---|---|
| the chat's system message (`server/chat/prompt.py`, both first lines) | "Natural Reader, an app for reading and listening to documents" | `APP_NAME`, `APP_TAGLINE` |
| the sign-in screen's title (`AuthGate.jsx`) | "Natural Reader" | `APP_NAME`, the tagline under it |
| the browser tab's title (`index.html`) | "natural-reader" | `APP_NAME`, set when the page has read it |
| the foot of the sign-in screen and of Settings | nothing | one line: name, version, copyright ("Natural Reader 2.12.0 · © 2026 Example Org") |

The version in that line is the app's own release number (`package.json`), built into the page, so a
person and a deployer can see which release is running. It is not a setting. This is the builder's
reading of the owner's "so we can keep track of the version"; the owner should correct it if wrong.

The browser reads name, tagline and copyright from `GET /v1/app`, which answers before sign-in,
because the sign-in screen shows them. There is one place to set each: no second, build-time setting
that could disagree.

Not renamed, and DEPLOYMENT says so:

- The read-aloud browser extension keeps its name: it is packaged on its own and can't read a
  server's settings when it is installed.
- The Keycloak sign-in page shows the realm's own display name.
- The favicon and icons.

This part doesn't depend on the library and is built first, as its own commit.

### For a person

`GET /v1/chat/profiles` returns the enabled profiles: `id`, `name`, `description`, `isDefault`. Never
the text.

In the chat sidebar, under the Model picker, a **Profile** picker lists them with the description as
a second line. It is hidden when fewer than two profiles are enabled. Picking one applies from the
next message of the open chat, and the browser remembers the pick for new chats, the same way it
remembers the model. There is no entry in Settings.

A chat whose profile was deleted shows the default in the picker, and one line under it: "The profile
this chat used, Tutor, was removed." The line goes when the person sends the next message.

Talk uses the chat's profile like any other turn.

### For an admin

Admin → Chat → Assistant becomes two parts:

- **House rules**: one editor, 2,000 characters, "Applies to every chat, before the profile."
- **Profiles**: a list (name, description, Default mark, On/Off). Add, edit, set as default, turn
  off, delete, move up and down. The editor for one profile is today's editor with Name and
  Description above it, the same character count, tool-name warning and preview (the preview shows
  house rules, this profile, then the app's rules), and the same Describe and Generate, which drafts
  into the profile being edited.

The default profile can't be turned off or deleted until another is made the default. Deleting asks
first and says how many chats use the profile; those chats move to the default.

Routes, admins only: `GET/POST /v1/admin/assistant/profiles`, `PUT/DELETE
/v1/admin/assistant/profiles/{id}`, `PUT /v1/admin/assistant/profiles/order`, `GET/PUT
/v1/admin/assistant/house-rules`. `POST /v1/admin/assistant/draft` stays. `GET/PUT
/v1/admin/assistant` is removed with the single profile; the console is its only caller.

As today, no profile or house-rules text is ever logged: a log line carries who, which id and a length.

## Costs and traps

- **Every character is read before every reply.** House rules and a profile together can reach
  10,000 characters. The screen shows the combined count for each profile.
- **Two places for a rule.** After the upgrade the one profile holds persona and rules together. An
  admin who adds a second profile must move the shared rules up to House rules, or the second profile
  lacks them. The Profiles screen says so while House rules is empty and more than one profile exists.
- **The picker's memory is per browser.** A person on a second device starts on the admin's default
  there. That matches how the model and other preferences behave today.
- **A turn from an old browser tab** sends no `profileId`; rules 2 and 3 cover it.

## What comes later, and how this leaves room

- **A profile per project.** `projects.profile_id`; a project chat's rule 1 becomes the project's
  profile, and the picker is fixed in that chat.
- **Backstory.** A column on `assistant_profiles`. The open question for that design is cost: a long
  backstory read before every reply is expensive, so it may need to be searched, not always sent.
- **Agents.** An agent is a profile plus what it may do: a model, a set of tools, perhaps documents.
  Those become columns or a table keyed on the profile's `id`, which is why `id` is stable and the
  picker never depends on the name.
- **A2A.** In the A2A protocol an agent publishes a card with a name, a description and a list of
  skills, and other agents send it tasks over HTTP. `name` and `description` here are the first two
  of those. Accepting tasks from other agents is a new way into the deployment and needs its own
  design, above all for who may call and what a caller's text is trusted to do. The A2A details in
  this paragraph are from memory and were not checked against the current version of the protocol.

## Left to the builder

- Tests first, on the scratch database. The walk uses a throwaway Keycloak or `AUTH_ENABLED=false`,
  never the compose one.
- Whether the picker reuses the Model picker's component.
- Exact wording of toasts and empty states, in the app's existing voice.
- Docs: USER_GUIDE, ARCHITECTURE, DEPLOYMENT (the file's new meaning), TESTING, CHANGELOG.
