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
