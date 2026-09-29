### Task 4: The reader's local library belongs to the signed-in user

**Why:** The welcome screen's "Your Library" lives in the IndexedDB store `neural-pdf-library/books` (`src/db.js:6`), shared by everyone who uses the browser. In the A1 walk, user B saw and could open user A's files.

**Change:**
- Add `ownerId` to every saved record.
- `getRecentBooks` and `getBook` return only records whose `ownerId` matches the signed-in user's id (from `/v1/auth/me`).
- On sign-in, records with no `ownerId` (saved before this release) are claimed by the first user who signs in on that browser. That keeps a single-user install's library.
- On logout, nothing is deleted; the records just stop showing.
- With the dev loopback bypass (no auth), use `"local"` as the owner id.

**Tests:**
- Two users on one browser each see only their own records.
- A legacy record is claimed once.
- Logout hides the records.

