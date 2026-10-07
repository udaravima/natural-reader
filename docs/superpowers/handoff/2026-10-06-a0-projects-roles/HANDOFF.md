# A0 — projects, roles & people directory: handoff

**Branch:** `feat/c2-multi-document` (not pushed). **Spec:** `docs/superpowers/specs/2026-09-25-projects-roles-directory-design.md`. **Plan:** `docs/superpowers/plans/2026-10-06-a0-projects-roles-directory.md`.
**Commits:** `9ae24d5` (plan) … `a3ef173` (Task 16), plus this handoff.

## Gate (2026-10-07)

| Check | Result |
|---|---|
| Backend `pytest server/tests` | 1037 passed, 16 skipped, 0 failed |
| Frontend `vitest run` | 507 passed |
| `eslint src` | clean |

## Running-app walk (2026-10-07)

**Set-up.** Throwaway Keycloak 26.0 (separate container, port 18081, the checked-in dev realm with `localhost:5173` added to the client's redirect URIs in a scratch copy), backend on a scratch database (`natural_reader_walk`), Vite on 5173. The repo's docker-compose Keycloak was not used: with this machine's `.env` it is the deployer's real identity server (see "Found during the walk"). All throwaway resources were removed afterwards.

| # | Journey | Control used | Result |
|---|---|---|---|
| 0 | Enroll a second user | Admin → Users → enroll form (email, username, first, last, active, reader) | Pass — "Enrolled … temp password: …"; row stored username `second`, first/last name, linked in Keycloak; first sign-in forced a password change |
| 1 | Create a project | Library → New project | Pass — Projects tab card "Walk Test · Owner · 1 member · 0 documents" |
| 2 | Find a colleague, add with a role | Project → Members → + Add people → full email | Pass — found "Sam Second @second"; role defaulted to Contributor |
| 3 | Change a role | Role menu on the member row | Pass — Contributor → Maintainer → Contributor; Activity recorded both |
| 4 | File my document | Project → Documents → File a document | Pass — only my unfiled upload offered; it appeared with a remove control |
| 5 | Maintainer removes a document | (as Sam, Maintainer) × → Confirm remove | Pass — confirmation shown; project empty; Activity "Sam Second removed "walk-report.txt"" |
| 6 | Share one document with one person | Library row → Share → pick person | Pass — dialog lists Sam; Sam's Library shows "shared by Admin User" |
| 7 | Leave | (as Sam) header → Leave → Confirm leave | Pass — confirmation shown; Sam's Projects tab empty |
| 8 | Admin recovery | Admin → Users → delete Sam (sole Owner) → Projects → Ownerless only → Add me as Owner | Pass — project survived ownerless; recovered; member shows "added by admin"; server logged the self-add at WARNING with IDs only |
| 9 | See what changed | Project → Activity | Pass — every step, newest first; the deleted user reads "a former member"; the removed document keeps its name |

Also checked: a Maintainer sees Edit and Leave but no Delete; the reader's "Project for this document" picker listed only projects the user may file into; the walk's server log was checked for plain-text emails, names, project names, file names and lookup text, and access-log lines carried no query strings. The final review then found that httpx request lines can carry URL-encoded emails (e.g. `%40`) at INFO; fixed by raising `httpx`/`httpcore` to WARNING in `server/logging_config.py`.

**API-only or env-only:** none of journeys 1–9. The six settings (`USER_DIRECTORY_*`, `PROJECT_CREATION`, `PROJECT_LIMIT_PER_USER`, `PROJECT_EVENTS_RETENTION_DAYS`) are deployer env settings by design.

**Not checked:** phone-width layout of the new screens (the enroll row now has seven inputs).

## Found during the walk

1. **The compose Keycloak is the real identity server on this machine.** `docker-compose.yml` interpolates `KC_HOSTNAME` and `KC_PROXY_HEADERS` from `.env`, and Keycloak stores users in the shared Postgres (`KC_DB_SCHEMA: keycloak`). Running a "local" walk against it would create real accounts. A walk needs a separate throwaway Keycloak, as done here.
2. **Real domain in tracked files** (open-source rule): `docker-compose.yml` (a `KC_HOSTNAME` comment) and `deploy/keycloak/realm-export.json` (client redirect URIs and web origins), in addition to the `docs/DEPLOYMENT.md` issue already owed from v2.4.
3. **UI wording:** the admin delete-user confirmation now says what happens to libraries and projects (fixed in the final-review pass).
4. **UI staleness:** after deleting a user, the admin Projects section keeps showing them as an owner until it reloads (toggle the filter or revisit).
5. **Activity wording:** an admin adding themselves reads "Admin User added Admin User as Owner" (should read "added themselves").
6. **Not A0, unverified cause:** the app's "Log out" returned to the app without ending the Keycloak session, so the next "Sign in" skipped the password. Logging out at Keycloak's own logout page worked.
7. **Test automation note:** Playwright's role-name locators clicked the wrong element for header view buttons and the enroll form's checkboxes (DOM clicks worked). Worth knowing before scripting the app.

## Deferred minors (from the task and final reviews)

None blocks merge (final-review triage). Grouped as the ledger recorded them.

- **Task 1:** users.py:155 docstring line too long; stale "owner or member" wording in link_doc docstring and test_list_excludes_other_users_projects docstring; no test of add_member role/added_by insert (T6 replaces it); no API test of created_by=null in list (T5 covers project object).
- **Task 2:** no direct test that require_project_role(lock=True) holds the lock (T6 race test covers it end to end); untested admin-on-missing-project / admin+member admin_ok=False / non-maintainer 403 messages; lock SQL in two places; unknown role string → KeyError (callers validate via Literal); non-UUID project_id → psycopg error unless routes type it uuid.UUID (plan's routes do → 422).
- **Task 3:** empty-string token claims overwrite stored names ("" passes COALESCE); type-ahead can't match "First Last" as one string; display_name that is an email leaks while emails hidden; branch 2/3 name updates untested; caller email without "@" makes the whole string the "domain" (harmless).
- **Task 4:** no tests for start_retention/stop_retention/_purge_loop; RED not observed (import error would have failed all tests); "Ben" log assertion near-vacuous; stop_retention swallows CancelledError unconditionally; huge PROJECT_EVENTS_RETENTION_DAYS passes validation and fails make_interval daily (caught, events kept).
- ~~**Task 2:** test_project_roles.py module-level pytestmark on sync parametrized tests emits 15 PytestWarnings — mark only async tests.~~ — fixed in Task 8
- **Task 5:** load_project_policy untested (defaults/invalid); config WARNING repeats per POST; assert_addable non-UUID branch untested (T6 covers malformed); PATCH/DELETE non-member 404 and DELETE reader/contributor 403 untested (T8 matrix covers); audit line before commit (codebase-wide pattern); owner_user_id 403 precedes policy check.
- ~~**Task 6:** add a leave/leave race (two sole Owners DELETE themselves at once → one 409 last_owner, one Owner remains) — remove_member's lock is otherwise unguarded by tests; make the PUT race test's finally robust (cancel/await b_task; seed-admin check after try).~~ — fixed in Task 8
- ~~**Task 6:** disabled members can't be re-roled (assert_addable 404) — Members tab must not offer the role menu on disabled rows (removal still allowed).~~ — fixed in Task 13
- **Task 6:** make_project on autocommit conn can leak one project row if a member insert fails; no test of admin hitting the last-owner 409; admin already a member raising self to Owner logs no WARNING (only adds do); DELETE says "Member not found" vs PUT "User not found"; remove_member parses UUID twice.
- **Task 7:** admin non-member DELETE doc untested in T7 (T8 matrix "remove any doc"/admin=404 covers it); role-before-doc-id only implicitly tested; admin events test checks status only; name fallback + legacy-placement replacement path untested; _filed_name/_placement_name near-duplicate queries.
- **Task 8:** privacy test doesn't file/remove documents (docstring promises file names), nor hit admin self-add or project_limit WARNING paths; matrix lacks the admin "add themselves" row (covered in test_project_members); race tests leave `setup` unclosed if the body raises; admin non-member doc cells are 404 (spec gives no code; consistent with §6).
- **Task 9:** no show_email test for the shares list; upload-entry 404 test lacks an unverified-entry case; list keeps shares to now-disabled recipients (design question); double _ensure_ready.
- **Task 10:** project_limit 422 after status/caps applied (same as budget pattern); no upper bound → INTEGER overflow 500; Keycloak-path enroll names not asserted (stub discards kwargs); no PATCH project_limit=0 assertion; no >100-char name 422 test; users.username not unique (by design, IdP owns it).
- ~~**Task 11:** PeoplePicker says "Nobody found…" when every result is excluded (already a member/shared) — should say they're already added.~~ — fixed in the final-review pass
- **Task 11:** empty-state wording follows live q during debounce; no test for "too-short query response must not render"; no projectsApi URL tests; thrown errors carry message only (no status), network errors surface raw; unmount doesn't bump reqId.
- **Task 12:** ProjectDocsTab.load depends on showToast (stable today via useCallback; fragile); confirm banner/confirmId survive a refusal; removeMember with undefined currentUserId → /members/undefined; ProjectDocsTab has no request-ordering guard; no last-Owner-leave page test; LibraryPage test spacing and un-reindented moved block.
- **Task 12:** filter-reset and Members-tab onChanged paths untested; failed loadProjects leaves a dead filter.
- ~~**Task 13:** member-load failure shows an empty list (toasted) with no retry; refusal reload/onChanged after add and failed loads untested; busy cleared before reload completes (brief window for stale double-click); newRole may stay 'owner' after manage_owners lost (server refuses); add controls not disabled while another row is busy; docblock wrap.~~ — the empty-list part fixed in the final-review pass
- **Task 14:** "Shared X with Y" toast when the recipient already held the document (no-op share; not listed); a failing reload toast replaces the success toast; dialog has no focus move/Escape.
- **Task 15:** no tests for recovery failure, Open→ProjectPage→Back swap, invalid project-limit notice; no RED captured; enroll row now 7 inputs (phone-width layout unchecked until the app walk).
- **Task 16:** ARCHITECTURE invariant bullet lacks bold lead-in; CHANGELOG Changed items without bold leads; "Awaiting approval" capitalised in guide vs lowercase badge.
- **Final:** ActivityTab Load-more 404 doesn't reload/route back; ProjectPage goes back on any reload error (not just 404); "Admin User added Admin User" wording; can_manage_project_docs now dead; docs.py docstrings "own or belong to"; ARCHITECTURE says re-run "stops startup" (init_db retries then 503); project_limit no upper bound (500); per-user project_limit 0 = unlimited (design note).
