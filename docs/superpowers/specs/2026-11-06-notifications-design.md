# Notifications for each person — design

**Status:** written 2026-11-06 from the owner's answers on the decision sheet ("Natural Reader — decisions and
next steps", the Notifications table) and the conversation that followed. **Approved** in conversation,
2026-11-06 ("you are correct go ahead"), with grouping added at the owner's ask ("make sure of notification
groupings"). Calls marked **(my call)** were left to me; everything else is the owner's.

## Why

Work in this app increasingly finishes when nobody is watching. A re-embed of the whole library takes minutes
(about 6 minutes per 1,000 passages for embeddinggemma-2 on this CPU); a Docling conversion of a long PDF takes
longer; the coming figures phase describes every picture with a vision model, which on a CPU takes minutes per
document. Today the only places these outcomes surface are the screen that started them (if it is still open),
the Overview (for admins who happen to look) and the server log. A passage a model refused and that was shortened
appears **only** in the server log. A re-embed that pauses because the new model stopped answering pauses
silently: search keeps working on the old model, so nothing looks broken, and the switch never happens.

Notifications give each person one place where the outcomes meant for them wait until they've seen them.

## The owner's fixed points

1. **An inbox for each person, not a popup.** Nothing appears over the page on its own.
2. **Mark all as read.**
3. **Old ones are cleaned up on a schedule.**

And from the sheet's answers (2026-11-06):

- **Senders at first:** a re-embed done or paused; passages a model refused and that were shortened; a
  conversion or indexing finished or failed; a rebuild that failed.
- **Admin notices go to every admin.**
- **A document notice goes to the person who uploaded or converted it.**
- **The inbox is a bell in the header with the unread count; the list opens under it, and it scrolls.**
- **A notice is read when it's clicked, or by Mark all as read.** Opening the list alone doesn't mark anything.
- **Read ones are kept 30 days, unread ones 90; the cleanup runs once a day.**
- **Read ones go below unread ones; important ones first.** "A notification that needs to act fast becomes
  more important."
- **In the app only, for now.** No email or other channel in this build.

## Goals

- Every outcome in the sender table below reaches its recipients exactly once per occurrence, even with several
  workers (`WORKERS > 1`) and across restarts.
- A person can tell at a glance, from across the room, whether anything needs them: the bell's count, and its
  colour when something unread needs action.
- Adding a sender later (figures' "pictures described", a share received) is one new kind and one call at the
  place the event happens.

## Non-goals (this build)

- Email, push, or any channel outside the app. The send function is the single place a channel would be added.
- Per-person choices of which kinds they receive.
- Notices for project activity (members, shares, versions): the project activity feed already covers those.
- Live delivery over a socket or SSE. Polling is enough at these volumes (see Delivery).

## Anti-goals (failure even if it "works")

- **A popup, toast or sound for a notice.** The owner said inbox, not popup.
- **A flood.** One notice per retry of a failing step, or per refused passage, would bury the useful ones. The
  re-embed loop logs "Re-embedding paused" on *every* failed attempt (`fill_step` in
  [embedding_migration.py](../../../server/services/embedding_migration.py)); a notice must follow the
  *transition* into paused, not the log line.
- **A notice that makes a job fail.** Writing a notice must never turn a finished index or a switched library
  into an error.
- **A notice for something that didn't happen.** A rolled-back change sends nothing.
- **Seeing someone else's notices**, or learning that they exist (another person's ids are ignored by the read
  route and never reported, as a missing document answers 404).
- **Names in the audit log.** Like project events, a notice may carry a document's name for its reader; the audit
  trail never does.
- **Background load from a hidden tab.** A tab nobody is looking at doesn't poll.

## Importance: two levels

The owner's rule: *a notification that needs someone to act soon is more important.* So every notice has a level:

- **Needs action**: something stays stuck until a person does something.
- **For your information**: something finished, or something worth knowing that needs nothing done.

| Kind | Level | Goes to | Sent when | Resolved when |
| --- | --- | --- | --- | --- |
| `reembed.paused` | Needs action | every admin | a re-embed *becomes* paused: its `error` goes from empty to set (not on each retry) | the re-embed fills again, switches, or is cancelled |
| `reembed.done` | Information | every admin | `switch()` makes the new model current | — |
| `rebuild.failed` | Needs action | every admin | a document's rebuild fails for the **third** time in a row (`attempts >= 3` in `doc_rebuilds`) **(my call)** | that document's rebuild finishes |
| `embed.shortened` | Information | every admin **(my call)** | a model refused passages and they were shortened: **one notice per job** (one indexing of one document, or one re-embed run), with the count | — |
| `doc.index_failed` | Needs action | the requester | indexing a document fails (`state = 'failed'`) | a later indexing of that document succeeds |
| `doc.indexed` | Information | the requester | indexing finishes (`state = 'indexed'`) | — |
| `doc.convert_failed` | Needs action | the requester | a Docling conversion fails (`conversion_state = 'conversion_failed'`) | a later conversion of that document succeeds |
| `doc.converted` | Information | the requester | a conversion **and the indexing it chains into** both finish: one notice, not two | — |

Why `rebuild.failed` waits for the third failure: the rebuild queue already retries a failed row every 15 minutes
by itself (`RETRY_AFTER` in [rebuild_queue.py](../../../server/services/rebuild_queue.py)), and the old index keeps
answering meanwhile, so a single failure usually needs nobody. Three in a row (about 30 minutes) means it won't fix
itself.

Why `embed.shortened` goes to admins and not to the document's uploader: whether a model that refuses long
passages is the right model is an admin's decision (Admin → Models); the uploader can't act on it.

**A resolved notice drops to normal (my call).** When the problem behind a needs-action notice goes away by
itself, the notice is marked resolved: it keeps its text, shows a "Resolved" tag, and sorts as information from
then on. It is not deleted and not marked read (the person may still want to know it happened). The owner was
asked this and said "go ahead", leaving it to me.

## Who "the requester" is

Background jobs today don't know who asked: `run_pipeline(doc_id)`, `run_embed(doc_id)` and `_run_convert_job(doc_id,
options)` in [docs.py](../../../server/routers/docs.py) and
[doc_pipeline.py](../../../server/services/doc_pipeline.py) take a document only. So:

- The routes that start indexing or a conversion (upload, Index, Retry index, Convert, reconvert) pass
  `requested_by = principal.user_id` into the job.
- A job started with no person (the rebuild queue, a startup legacy swap) sends no document notice to anyone. Its
  failures reach admins through `rebuild.failed` instead.
- Documents are shared content (`library_entries`): two people can hold the same bytes. The notice goes only to
  the person whose request started this run, never to every holder.
- The document's name in the notice is **the requester's own name for it** (`library_entries.file_name`, else the
  content's canonical name), copied into the notice when it's sent, because the document may be renamed or gone
  by the time it's read (the project-events rule).

## Data

Migration **026**, `server/sql/026_notifications.sql`, with a manual Down block like 025's:

```sql
CREATE TABLE IF NOT EXISTS notifications (
    id           BIGSERIAL PRIMARY KEY,
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,
    level        TEXT NOT NULL CHECK (level IN ('action', 'info')),
    title        TEXT NOT NULL,          -- one line, names copied in
    body         TEXT,                   -- optional second line (an error in plain words, a count)
    doc_id       TEXT REFERENCES documents(doc_id) ON DELETE SET NULL,
    target       JSONB,                  -- where a click goes: {"doc": id} or {"admin": "models"}
    key          TEXT,                   -- what makes two notices "the same occurrence" (below)
    repeats      INT NOT NULL DEFAULT 1,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    read_at      TIMESTAMPTZ,
    resolved_at  TIMESTAMPTZ
);
CREATE INDEX ... ON notifications (user_id, read_at, created_at DESC);
CREATE UNIQUE INDEX ... ON notifications (user_id, key) WHERE key IS NOT NULL AND read_at IS NULL;
```

- **`key` stops floods.** A needs-action kind carries a key naming its occurrence (`reembed`: one re-embed runs at a time,
  `rebuild:<doc_id>`, `index:<doc_id>`, `convert:<doc_id>`). Sending a notice whose key matches an **unread**
  one of that person's updates that row instead (`repeats + 1`, newer text, `created_at = now()`, `resolved_at =
  NULL`) rather than adding a row. Once the person has read it, the next occurrence is a new notice.
- **Resolving** is `resolve(conn, key)`: sets `resolved_at` on every unresolved notice with that key, for every
  recipient.
- **The module** `server/services/notifications.py` (my call: modelled on
  [project_events.py](../../../server/services/project_events.py)) holds the closed `KINDS` set with each kind's
  level, `send(conn, user_ids, kind, ...)`, `send_admins(conn, kind, ...)` (every user with `admin` in
  `capabilities` and an active account at the moment of sending; an admin added later doesn't get older notices),
  `resolve`, the page query, and the retention loop.
- **Same transaction as the change.** Where the outcome is a database write (`switch()`, `rebuild_queue.fail`,
  the `UPDATE documents SET state = ...`), the notice is written in that transaction, so a rolled-back change sends
  nothing. Where a notice write could fail on its own (it shouldn't: same database), the job must still finish;
  the builder may wrap the send in a savepoint and log a failure.
- **Multi-worker safety** comes from those transactions: `fill_step`'s error write already runs under
  `read_state(lock="update")`, so only one worker sees the transition from no error to an error.

## Grouping

Approved by the owner as proposed: notices of the same kind that arrive close together show as **one row**. A bulk
upload of 20 documents gives "20 documents finished indexing", not 20 rows; a re-embed run that breaks several
rebuilds gives "3 documents keep failing to rebuild". This matters most for bulk project upload (on the wish-list),
which would otherwise bury everything else.

Two mechanisms, at two levels, and they don't overlap:

- **`key` (above) merges repeats of the same occurrence** (the same problem with the same document): one notice,
  `repeats` counted. That is storage.
- **Grouping merges different occurrences of the same kind** (different documents): several notices, shown as one
  row. That is presentation: each notice stays its own row in the table, so one can still be read, resolved or
  cleaned up on its own.

The rules:

- **A group is notices of one person, of one `kind`, all unread or all read**, where each one arrived within
  **30 minutes** of the one before it **(my call: the gap between neighbours, not a fixed window, so a slow bulk
  upload that indexes one document every few minutes over an hour stays one group)**.
- **Groups never mix levels.** `kind` fixes the level, so failures and finishes are always separate groups: a
  failure can never hide inside "20 documents finished indexing".
- **A group of one is shown as a plain row**, exactly as before.
- **A needs-action group counts as needs action while any member is unresolved**; its row says how many are
  resolved ("3 documents couldn't be indexed, 1 resolved").
- **The server groups**, so paging and the bell's count agree with what the list shows. A group carries its title
  (a plural per kind), its newest time, its unread and unresolved counts, the ids of **all** its members (so
  marking it read needs no second fetch), and the details of its newest 50 members **(my call)**, with "and N
  more" beyond that.
- **Group titles** (the builder may polish): "20 documents finished indexing", "3 documents couldn't be indexed",
  "4 documents are converted and ready", "2 conversions failed", "3 documents keep failing to rebuild",
  "Passages were shortened in 4 documents". Re-embed kinds rarely group (one re-embed at a time), but follow the
  same rule if they do.

## Delivery

Routes under `/v1/notifications`, for the signed-in person only (no capability needed beyond an active account):

| Route | Does |
| --- | --- |
| `GET /v1/notifications/unread` | `{"count": n, "action": m}`: unread **rows** as the list shows them (a group counts once), and how many of those need action |
| `GET /v1/notifications?offset=&limit=` | a page of the person's rows (groups and single notices) in inbox order (below); `limit` default 30, max 100 |
| `POST /v1/notifications/read` | body `{"ids": [...]}`: marks those notices read (one notice, or a whole group); ids that aren't the person's are ignored, never reported; returns how many changed |
| `POST /v1/notifications/read-all` | marks every unread one read; returns how many |

**Inbox order:** unread rows before read ones. Among unread, rows needing action (unresolved) first. Within each
part, newest first (a group by its newest member). A read needs-action notice does **not** stay above unread
routine ones **(my call)**: the top of the list should always mean "new to you". Offset paging over rows is enough
at a 90-day retention **(my call)**.

**The badge counts rows (my call)**, so "3" on the bell means three rows to look at, even if one of them is a
group of 20.

**Polling (my call):** the browser asks `/unread` every 60 s while the page is visible
(`document.visibilityState`), at once when the tab becomes visible again, and after its own Index, Convert or
Retry finishes. No polling while hidden or signed out. The list itself is fetched only when the bell is opened.

## What a person sees

- **The bell** sits in the header just before the Settings gear, at every width and in every view, for every
  signed-in active person. With unread notices it shows the count (`99+` above 99). When any unread one needs
  action and is unresolved, the badge is the warning colour; otherwise it is the quiet accent colour **(my call:
  colours from the existing theme tokens)**. Its label for screen readers says both: "Notifications, 3 unread,
  1 needs action".
- **The list** opens under the bell as a panel, and scrolls (owner: "scrollable"). On a phone it is a full-width
  sheet under the header. At the top: **Mark all as read** (disabled with nothing unread). Each row: an icon for
  its level, the title, the optional second line, how long ago, a **Resolved** tag where it applies, and an
  unread dot. Unread rows are visibly heavier than read ones.
- **Clicking a single row** marks it read and goes where it points: a document notice opens that document (a 404
  because the person no longer holds it shows the usual "not found" toast and the notice stays read); an admin
  notice opens **Admin → Models** (re-embed, shortened) or **Admin → Overview** (rebuild). The panel closes.
- **Clicking a group** marks **all** its members read (owner's rule) and expands it in place, showing each
  member as a sub-row (its document's name, its own second line, how long ago, Resolved where it applies). The
  panel stays open. Clicking a member goes where that member points, as a single row does. Clicking the group
  again collapses it. A group is a disclosure button (`aria-expanded`) for screen readers.
- **Escape** or a click outside closes the panel and returns focus to the bell (reuse `useMenuDismiss`).
- **Empty:** "Nothing new." with no illustration.
- **No** toast, popup, sound, or browser notification ever appears for a notice.

Sample wording (the builder may polish; plain words, the house style):

- "Re-embedding paused: embeddinggemma-2 stopped answering." / second line: Google's or Ollama's own words,
  shortened.
- "The library now uses embeddinggemma-2."
- "Couldn't index *Annual report.pdf*." / second line: the error in plain words.
- "*Annual report.pdf* is converted and ready to chat with."
- "nomic-embed-text refused 12 passages of *Annual report.pdf*; they were shortened to fit."
- "*Annual report.pdf* has failed to rebuild 3 times; its old index is still answering."

## Cleanup

A retention loop like `project_events.start_retention()`: at startup and then every 24 hours, on each worker
(the `DELETE` is idempotent, so several workers purging is harmless):

- read notices whose `read_at` is older than **30 days**: deleted;
- unread notices whose `created_at` is older than **90 days**: deleted.

`NOTIFICATIONS_READ_DAYS` (default 30) and `NOTIFICATIONS_UNREAD_DAYS` (default 90) override them, in days; `0`
means keep forever, as `PROJECT_EVENTS_RETENTION_DAYS` does **(my call)**. A person's notices also go when their
account is deleted (`ON DELETE CASCADE`).

## Hooks, one per sender

The builder places each `send` / `resolve` at the line where the state changes. Today's places:

- `reembed.paused`: `fill_step`'s error branch in
  [embedding_migration.py](../../../server/services/embedding_migration.py), only when the state's `error` was
  empty before this write. Resolved by the next successful fill (the branch that clears `error`), by `switch()`,
  and by `cancel()`.
- `reembed.done`: `switch()`, same transaction.
- `rebuild.failed` / resolve: `rebuild_queue.fail` (when the row's `attempts >= 3`) and `rebuild_queue.finish`.
- `embed.shortened`: `embed_texts` in [embeddings.py](../../../server/services/embeddings.py) is shared by every
  caller and knows no document or person, so it should **report** how many texts it shortened (a return detail or
  a counter the caller passes in), and the **caller** (an indexing job, a re-embed run) sends one notice when its
  job ends with a non-zero count. For a re-embed, one notice for the whole run, sent with `reembed.done`.
- `doc.indexed` / `doc.index_failed`: the success and failure writes in `run_embed` and `run_pipeline`.
- `doc.converted` / `doc.convert_failed`: `_run_convert_job`'s failure write, and, for success, the end of the
  indexing it chains into (the conversion's own success write sets `state = 'indexing'`, so "converted and ready"
  is only true once indexing ends). `_run_convert_job` ends by calling `_run_index_job(doc_id)`
  (`doc_pipeline.run_embed`, [docs.py](../../../server/routers/docs.py) after the conversion lock), so `run_embed`
  takes `requested_by` and a `via` (`"index"` or `"convert"`) and sends `doc.converted` instead of `doc.indexed`
  when it finishes a conversion. A conversion whose indexing then fails sends `doc.index_failed`, not both. A
  successful conversion also resolves `convert:<doc_id>`, a successful indexing `index:<doc_id>`.

## Tests and walking it

- **Backend (pytest):** each sender sends once and only once (the re-embed loop failing five times in a row gives
  one notice with `repeats = 5`, not five); a rolled-back change sends nothing; resolution drops a notice's
  order; inbox order; grouping (neighbours 29 and 31 minutes apart; a group never mixing kinds, levels, or read
and unread; a group of one; a group of 60 returning 50 details and 60 ids; the badge counting a group once);
another person's ids ignored by read; read-all; the cleanup's two ages and `0`; two workers on the
  pause transition (the existing two-connection pattern from the rebuild queue's tests).
- **Frontend (vitest):** the badge count and colour; `99+`; the panel's order, Mark all as read, a click marking
  read and navigating; a group click marking all read and expanding, a member click navigating; Escape and focus; no polling while hidden.
- **Walk (Chromium, 1366 and 360 px, scratch DB, as the house does):** upload and index a document and wait in
  another view for the bell; upload several at once and see one group; force an index failure; a re-embed to a model that's then stopped (pause), restarted
  (resolve), finished (done). Measure the header at **320 and 360 px with the bell added**: the phone header has
  overflowed before (HANDOVER 2026-11-04), so the bell must not bring it back.
- **Docs:** USER_GUIDE (the bell), ARCHITECTURE (the module, the table, the routes), DEPLOYMENT (the two env vars),
  TESTING (the new rows), CHANGELOG.

## Left to the builder

The exact colours and icons (from the existing theme and Lucide set); the panel's width; the final wording; whether
`target` is JSONB or two columns; how `embed_texts` reports its shortened count; the savepoint around a send.
