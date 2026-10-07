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

Also checked: a Maintainer sees Edit and Leave but no Delete; the reader's "Project for this document" picker listed only projects the user may file into; the walk's server log contained no emails, names, project names, file names or lookup text, and access-log lines carried no query strings.

**API-only or env-only:** none of journeys 1–9. The six settings (`USER_DIRECTORY_*`, `PROJECT_CREATION`, `PROJECT_LIMIT_PER_USER`, `PROJECT_EVENTS_RETENTION_DAYS`) are deployer env settings by design.

**Not checked:** phone-width layout of the new screens (the enroll row now has seven inputs).

## Found during the walk

1. **The compose Keycloak is the real identity server on this machine.** `docker-compose.yml` interpolates `KC_HOSTNAME` and `KC_PROXY_HEADERS` from `.env`, and Keycloak stores users in the shared Postgres (`KC_DB_SCHEMA: keycloak`). Running a "local" walk against it would create real accounts. A walk needs a separate throwaway Keycloak, as done here.
2. **Real domain in tracked files** (open-source rule): `docker-compose.yml` (a `KC_HOSTNAME` comment) and `deploy/keycloak/realm-export.json` (client redirect URIs and web origins), in addition to the `docs/DEPLOYMENT.md` issue already owed from v2.4.
3. **UI wording:** the admin delete-user confirmation still says it "permanently deletes this user's documents" — since A0, documents filed in projects survive (the toast after deletion was updated; the confirmation wasn't).
4. **UI staleness:** after deleting a user, the admin Projects section keeps showing them as an owner until it reloads (toggle the filter or revisit).
5. **Activity wording:** an admin adding themselves reads "Admin User added Admin User as Owner" (should read "added themselves").
6. **Not A0, unverified cause:** the app's "Log out" returned to the app without ending the Keycloak session, so the next "Sign in" skipped the password. Logging out at Keycloak's own logout page worked.
7. **Test automation note:** Playwright's role-name locators clicked the wrong element for header view buttons and the enroll form's checkboxes (DOM clicks worked). Worth knowing before scripting the app.
