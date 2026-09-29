# Task 4 report — the reader's local library belongs to the signed-in user

## Summary

Every record in the `books` IndexedDB store now carries an `ownerId`, and
`getRecentBooks` / `getBook` / `deleteBook` / `updateBookMeta` are all scoped
to whichever user `setLibraryOwner(id)` (new export, `src/db.js`) currently
names. `useAuth.js`'s `check()` calls `setLibraryOwner` with `/v1/auth/me`'s
`id` on success, `'local'` when the auth check itself fails (network/offline),
and clears it (`null`) on 401/403/mid-session-401 — matching R2 exactly.
Pre-release records with no `ownerId` are claimed once, by the first real
owner set, inside one read-write IndexedDB transaction.

## Key design choice: the store's key, not just a filter (report per R1)

The brief flagged the risk directly: the store was keyed in-line by
`fileName` (`keyPath: 'fileName'`), so if I'd only added an `ownerId` field
and filtered on read, two users saving a file with the *same name* would
still silently overwrite each other's bytes on write (a `put` with the same
key replaces, regardless of who owns it) — filtering the read path alone
doesn't stop that.

**Choice made:** the store now uses out-of-line (explicit) keys built as
`` `${ownerId}\u0000${fileName}` `` (`bookKey()` in `db.js`). Two users can
each save `report.pdf` and get two independent records. `\u0000` is the
separator since it can't appear in a browser-supplied `File.name`.

This required a `DB_VERSION` bump, 4 → 5, with a migration in `openDB()`'s
`onupgradeneeded`:
- Fresh installs (`oldVersion === 0`) get the v5 shape directly (no store
  ever existed to migrate).
- Existing installs (`oldVersion` 1–4, in-line `fileName`-keyed store):
  `getAll()` every record out, `deleteObjectStore` + recreate out-of-line
  with `lastOpened` and a new `ownerId` index, then `put` each record back
  under its composite key, tagged with a sentinel `UNCLAIMED_OWNER
  ('__unclaimed__')` if it had no `ownerId` yet. **No record is dropped.**
  (One documented, effectively-dead edge case: a hypothetical install still
  on `oldVersion 1` would have its pre-existing `oldVersion < 2` fileType
  cursor-backfill run concurrently with my `getAll()` in the same upgrade
  transaction — cursor step-interleaving means my snapshot could grab a
  record before its `fileType` backfill lands. Every downstream read site
  already defaults a missing `fileType` to `'pdf'`, so this is harmless, and
  moot in practice since `DB_VERSION` was already 4 in production — no real
  user is on v1 today.)

`UNCLAIMED_OWNER` is a real string, not `null` — IndexedDB indexes silently
skip records whose indexed property is `null`/`undefined`, so a `null`
`ownerId` would be invisible to `index.getAll(IDBKeyRange.only(...))`
entirely. Every record's `ownerId` is always one of: a real user id, `'local'`,
or `UNCLAIMED_OWNER`.

`currentOwnerId` (module state) starts `null` ("owner not known yet"), which
`effectiveOwnerId()` resolves to `UNCLAIMED_OWNER` too — so before any
`setLibraryOwner` call (e.g. `usePdfEngine`'s mount effect, which fires
before `useAuth`'s async `/v1/auth/me` resolves), reads/writes land in the
same bucket as pre-release legacy data. This is intentional and safe: once a
real owner claims that bucket, it's no longer visible under
`UNCLAIMED_OWNER`, so a second signed-in user's transient pre-auth-resolution
read never sees the first user's now-claimed data.

## Claim: idempotent + race-safe (R1)

`setLibraryOwner(id)`:
1. Sets `currentOwnerId` synchronously (so the owner switch itself is
   instant — no await needed for the *filter* to apply).
2. If `id` is truthy, opens **one** `readwrite` transaction, `getAll()`s the
   `UNCLAIMED_OWNER` index, and for each record: skips it if the new owner
   already has a record under that filename (never clobbers), else
   `delete`s the old key and `put`s it back under the new composite key.

**Idempotent:** once nothing remains under `UNCLAIMED_OWNER`, a repeat call
(same or different id) does a no-op `getAll` and returns.

**Race-safe:** IndexedDB serializes read-write transactions against the same
object store — two overlapping `setLibraryOwner` calls for two different
owners each open their own transaction, but the second transaction's
`getAll(UNCLAIMED_OWNER)` only executes after the first has fully committed,
so it sees nothing left to claim. Verified with a test that fires two
`setLibraryOwner` calls without awaiting the first — exactly one owner ends
up with the record (never both, never neither).

## Beyond the letter of the brief (flagging, not silently expanding scope)

- **`deleteBook`/`updateBookMeta` are also owner-scoped.** The brief named
  only `getRecentBooks`/`getBook` explicitly, but leaving delete/update
  unscoped would mean user B could delete or corrupt user A's book by
  filename alone — that's the same class of bug the task exists to close, so
  I scoped all five exported book functions consistently.
- **The 5-book LRU cap (`MAX_BOOKS`) is now per owner.** Previously
  `cleanupOldBooks` capped the whole store globally — under the old
  single-owner-implicit world that was fine, but with real per-user
  ownership, user B uploading files would have evicted user A's oldest
  saved book once the *combined* total crossed 5. `cleanupOldBooks` now
  queries the `ownerId` index and only trims that owner's own set. Tested
  explicitly (userA saves 6 → capped to 5; userB's own single save is
  untouched).

## Auth-layer wiring (`useAuth.js`, kept minimal per R1's note about line ~51)

Added one import and four call sites inside `check()` / the unauthorized
handler — no changes to the effect at (now) line 63 (`useEffect(() => {
check(); }, [check]);`), which still carries the pre-existing
`react-hooks/set-state-in-effect` lint error the brief said a later task
(Task 8) removes. Its line number shifted from 51 to 63 purely because of
lines added above it in the same function — content and error are unchanged.

- 200 `/v1/auth/me` → `await setLibraryOwner(body.id)` **before** `setUser`/
  `setState('active')`, so any effect reacting to `state === 'active'` (the
  App-side library refresh, below) only fires after the claim transaction
  has fully committed — no need for a second refresh round to catch
  newly-claimed legacy records.
- 401 → `setLibraryOwner(null)`.
- 403 (pending/disabled) → `setLibraryOwner(null)` (no id is available in
  either body, and the app blocks access anyway via `AuthGate`).
- Any other status, or the `catch` (network/offline) → `setLibraryOwner('local')`,
  matching R2 ("use `'local'` only when `/auth/me` is unavailable").
- The existing `setUnauthorizedHandler` callback (fires on a 401 from *any*
  apiFetch call site, not just `/me` — the actual mid-session "logout"
  path) also now clears the owner.

## App/usePdfEngine wiring (R1's carved-out exception)

`usePdfEngine` gained one new returned function, `refreshLibrary` (re-runs
`getRecentBooks().then(setRecentBooks)`) — `saveBook`/`getBook` call sites
inside it are untouched, as required. `App.jsx` gained one new effect keyed
on `[auth.user?.id, auth.state, refreshLibrary]` that calls it — this is what
makes the welcome screen's list actually flip when the signed-in identity
changes (sign-in, sign-out, session-loss, user switch), since
`usePdfEngine`'s own `recentBooks` state doesn't know the owner changed until
told to re-read.

## TDD evidence

New file `src/db.library-owner.test.js` (fake-indexeddb, same pattern as the
existing `src/lib/db.workspace.test.js`), 10 cases:

**RED** (before any implementation): all 10 failed with
`TypeError: setLibraryOwner is not a function` — confirmed failing for the
right reason before writing any db.js changes.

**GREEN** (after implementation):
```
✓ two users on one browser — each user sees only their own saved books
✓ getBook returns nothing for a record owned by someone else (as if absent)
✓ deleteBook and updateBookMeta only ever touch the caller's own record
✓ the 5-book cap applies per owner, not across all owners combined
✓ a legacy (no-owner) record is claimed by the first signed-in user, and a
  second user never sees it
✓ claim never overwrites an existing record the claiming user already owns
  under that name
✓ claim is race-safe: two overlapping setLibraryOwner calls for different
  owners never double-claim
✓ logout (setLibraryOwner(null)) hides records without deleting them;
  signing back in shows them again
✓ "local" (dev loopback bypass) behaves like any other owner id
✓ migrates a real pre-release fileName-keyed (v4) database without losing
  the record, and it's claimable
```

## Test results (full suites, run after all wiring was in place)

- `npx vitest run` — **48 files / 321 tests, all passed** (was 47/311 before
  this task's new test file; no existing test needed changes).
- `.venv/bin/python -m pytest server/tests -q` — **579 passed** (this task
  touched no backend code; ran as the required gate check).
- `npx eslint src` — **exactly 1 error**, unchanged from baseline
  (`useAuth.js`, now line 63 instead of 51 — same pre-existing
  `react-hooks/set-state-in-effect` finding on `useEffect(() => { check();
  }, [check]);`, left for Task 8 as instructed).

## Files changed (staged, not committed)

- `src/db.js` — `setLibraryOwner`, `UNCLAIMED_OWNER`, `bookKey`,
  `effectiveOwnerId`, DB_VERSION 4→5 + migration, all five book functions +
  `cleanupOldBooks` scoped to owner.
- `src/hooks/useAuth.js` — imports and calls `setLibraryOwner` at each auth
  outcome; no change to the pre-existing lint-flagged line's logic.
- `src/App.jsx` — new effect refreshing the library list when
  `auth.user?.id`/`auth.state` changes.
- `src/hooks/usePdfEngine.js` — new `refreshLibrary` export.
- `src/db.library-owner.test.js` (new) — the 10 TDD cases above.
- `CHANGELOG.md` — one `[Unreleased] / Fixed` entry.
- `docs/ARCHITECTURE.md` — corrected the `usePdfEngine` row's "5-book LRU
  library" claim to "5-book-per-user LRU library ... scoped to whoever
  `setLibraryOwner` names".

## Self-review / concerns

1. **The pre-v1-fileType-backfill interleaving edge case** (documented above
   and in a `db.js` comment) is real but, as far as I can determine, dead in
   this deployment (production `DB_VERSION` was already 4). I chose not to
   over-engineer a fix for it given downstream code already tolerates a
   missing `fileType`; flagging it explicitly rather than silently ignoring it.
2. **`setLibraryOwner` failures are swallowed** (try/catch + `console.error`),
   matching every other function in this file — consistent with existing
   style, but it does mean a claim that throws partway (e.g. quota exceeded)
   fails silently from the caller's perspective. This matches the existing
   `saveBook`-returns-`false`-on-failure pattern elsewhere in the file, so I
   kept it consistent rather than introducing a different error-handling
   convention for just this one function.
3. **I did not add a UI-visible indicator** that a login is "claiming" the
   library — the claim is fast (one transaction, typically near-empty after
   the first real user ever signs in) and R1 didn't ask for one; flagging
   this as an intentional non-change, not an oversight.
4. Per the "judge work by the user's journey" standard: the actual
   user-visible surface here is the welcome screen's "Your Library" list
   (`src/components/WelcomeScreen.jsx`, fed by `usePdfEngine.recentBooks`).
   I did not add a new browser-driven end-to-end check in this pass (task
   brief scoped this as a TDD/db-layer task with unit tests named
   explicitly); the `App.jsx` refresh-effect wiring is the piece that makes
   the fix visible in the running app, and it's covered by the full
   `vitest run` (321 passing, including existing `usePdfEngine`/App-adjacent
   tests) but not by a fresh browser walkthrough. Worth a manual/E2E check
   before this ships if one wasn't already planned elsewhere in the v2.1
   follow-ups.

No blockers. Nothing committed — staged only, per the global constraint.
