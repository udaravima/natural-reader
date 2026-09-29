### Task A: Browser-only chats, the workspace and reading positions belong to the signed-in user

**Why:** The final review of v2.1 found three stores that are still shared on one browser:
- **Browser-only chats:** `sessionStore.getRecentSessions` merges `idb.getRecentSessions()` for whoever is signed in.
- **The last workspace folder:** `App.jsx`'s restore effect re-opens it on mount, before the app even knows who is signed in.
- **Reading positions:** `localStorage` keys `neural-pdf-progress-<fileName>`.

**Change:**
- **`chat_sessions` (IndexedDB):**
  - Records get an `ownerId`, stamped by `saveSession`.
  - `getRecentSessions`, `getSession`, `saveSession` and `deleteSession` see only the current owner's records, and nothing when no owner is known.
  - Records with no `ownerId` are claimed by the claim in `setLibraryOwner(id, { claimLegacy: true })`.
  - Keys are unchanged (session ids are unique), so there is no version bump.
- **`workspaces` (IndexedDB):**
  - The single record `'last'` becomes one per owner, `last:<ownerId>`.
  - The legacy `'last'` record is claimed.
  - With no owner, reads return null and writes are refused.
  - App's restore effect waits until the user is known and re-runs when the user changes.
- **Reading positions:**
  - Keys become `neural-pdf-progress@<ownerId>/<fileName>`.
  - Legacy `neural-pdf-progress-<fileName>` keys are moved to the claiming user.
  - With no owner, nothing is saved or restored.

**Tests:**
- Two users on one browser: each sees only their own chats, workspace and positions.
- Legacy data of each kind is claimed once.
- With no owner, all three are empty and writes are refused.
- The workspace restore does not run before the user is known.

