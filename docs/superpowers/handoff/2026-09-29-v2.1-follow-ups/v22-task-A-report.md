# v2.2 Task A report — browser-only chats, workspace, reading positions per user

BASE f55206f. The plan is `docs/superpowers/plans/2026-09-29-v2.2-per-user-browser-state.md` (committed with this task).

## Change (`src/db.js`, `src/hooks/usePersistedState.js`, `src/App.jsx`)
- **Browser-only chats (`chat_sessions`):**
  - `saveSession` stamps `ownerId` and refuses when no owner is known, or when the id belongs to someone else.
  - `getSession`, `getRecentSessions` and `deleteSession` see only the current owner's records, and nothing when no owner is known.
  - No key change and no DB version bump (session ids are unique).
- **Workspace:** one record per owner, `last:<ownerId>`. `get`/`save`/`clear` return null/false when no owner is known.
- **Reading positions:** stored at `neural-pdf-progress@<ownerId>/<fileName>`. `db.js` exports `readingProgressKey` and `getLibraryOwner`, and `usePersistedState` imports them (one-way dependency). With no owner, nothing is saved or loaded.
- **Claim:** `setLibraryOwner(id, { claimLegacy: true })`, after the books claim, runs `claimLegacyBrowserState`:
  - one read-write transaction over `chat_sessions` and `workspaces` gives the owner every session with no `ownerId`, and moves `'last'` to `last:<owner>` (never overwriting an existing one);
  - then legacy `neural-pdf-progress-<file>` keys move to the owner's keys (never overwriting).
  - It skips stores that don't exist and always closes its connection. An open connection blocks the next version change; the Task 4 tests' fake v4 database found this.
- **`App.jsx` workspace restore:** it waits for `auth.state === 'active'` and a user id, and re-runs when the user changes. Before, it ran once on mount, before the owner was known. That would now find nothing, so it would never restore for anyone.
- **Docs:** ARCHITECTURE (`usePdfEngine` and `usePersistedState` rows) and one CHANGELOG Fixed line.

## Tests
- **New `src/db.browser-state-owner.test.js` (9).** For each store: per user, nothing with no owner, and pre-release data claimed once, seeded in a real v5-shaped database and a legacy localStorage key. All 9 were red before the change.
- **Existing tests updated to sign a user in:**
  - `src/lib/db.workspace.test.js`;
  - `usePdfEngine.openServerDoc.test.js` (its reading-position tests).
- **Not covered by a test:** the App restore effect isn't mounted (R3). Its gate is one line: `signedInUserId`.

## Suites
- backend: 610 passed (Postgres had stopped in the sandbox; restarted);
- frontend: 400 passed, **exit 0**;
- eslint: clean.

---

## Fix round 1 — FIX_BASE 69cde96 (Task A head was 7a70e4e; Tasks B and C sit between, untouched here)

- **Important: the brief's "restore waits for the user" test was missing.** The gating now lives in a new hook, `src/hooks/useWorkspaceRestore.js`:
  - no lookup until the reader is ready and a user id is known;
  - a new lookup when the user changes;
  - a superseded lookup's result is dropped;
  - a failed lookup is logged.

  `src/hooks/useWorkspaceRestore.test.js` has 3 tests.
- **Minor: nothing cleared in-memory state on a user change.** App's `restoreWorkspace(saved, userId)` runs in the lookup's promise callback, so it doesn't set state inside the effect. It first drops a workspace opened for a different user (`workspaceOwnerRef`, which adopt, restore and reconnect set, and close clears).
- **Minor: a missing store skipped the reading-position migration.** Only the IndexedDB part is skipped now, via an `if`; no error is logged, so test output stays clean.
- **Minor: session cleanup ignored the owner.** `cleanupOldSessions` now caps per owner. Test: one user's 51 chats never evict another's.
- **Minor: stale comments.** Both are updated (the session record shape, the workspace store).
- **Minor: the ownership check and the save weren't atomic.** `saveSession` now checks the id's owner and writes in one read-write transaction.

Suites:
- frontend: 407 passed, exit 0, no "Failed to claim" noise in the log;
- eslint: clean.

## Fix round 2 — FIX_BASE 6298c48 (re-review of round 1: findings 3 and 5 open, 2 new Minor)

- **Findings 3 and 5 plus the two new Minors: the previous user's workspace, reconnect banner or late restore could reach the next user.** The design changed from clearing state to owning it.
  - `workspace` and `reconnect` are now stored as `ownedBy(userId, value)` and read through `visibleTo(owned, signedInUserId)` (`src/lib/visibleTo.js`).
  - A different user sees neither, from the render where they sign in: no timing window, nothing to clear, and no setState in an effect.
  - A restore for user A that finishes after user B signed in is tagged A, so B never sees it.
  - `workspaceOwnerRef` and the clearing block are gone.
- **Hook comment reworded:** a superseded lookup isn't passed on, and `onSaved` must tag what it opens.
- **Finding 5 (the last stale comment):** `db.js`'s workspace record shape now reads `id:'last:<ownerId>'`.

Tests and checks:
- `src/lib/visibleTo.test.js` (2).
- Frontend: 409 passed, exit 0. eslint clean. `vite build` exit 0.
- Running app (headless Chromium, dev bypass): opening a folder shows its note; after a reload the reconnect banner appears for the same user.
