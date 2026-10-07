# Projects, roles & people directory (A0)

**Date:** drafted 2026-09-24/25, completed 2026-10-06
**Status:** Complete. Awaiting the user's review of this written spec, then an implementation plan.
**Branch:** `feat/c2-multi-document` (from `feat/v2.4-document-context` at 6edca75)
**Program:** the first of five, in this order: **A0** → V1 document versions → C2.1 project chat → V2 compare and review → C2.2 digests (agreed 2026-10-06).
**Depends on:** A1 (document content vs library entries), already built. A0's document rules (§5) are written against A1's model.
**Open-source rule:** this is a community project deployed by many organizations. Nothing deployment-specific is hardcoded. Every knob below is an env var documented in `.env.example`, with a secure default (§12).

## 1. Goal and user journeys

Project management works entirely from the UI, with no curl. Each journey maps to a screen control (§10), and the finished feature is checked in the running app (§11):

1. Create a project with a name and description.
2. Find a colleague and add them with a role.
3. Change a member's role, or remove them.
4. File one of my documents into a project.
5. As a Maintainer, remove a document from the project.
6. Share a single document with one person. (Today the backend supports this, but no screen has a Share control: it is API-only until A0.)
7. Leave a project.
8. Admin: see all projects (including ownerless ones), manage members, add themselves for recovery.
9. See what changed in a project, and who changed it (activity history).

Typical deployments are one organization, with teams sharing projects of 10–50 documents.

**Still not possible after A0, by design:** chatting over a whole project (C2.1), and uploading a revised file as a new version of a document (V1). Until then, a project organizes and shares documents, and chat works on one document at a time.

## 2. Membership model: GitLab-style roles

- Roles: `reader` < `contributor` < `maintainer` < `owner`.
- The owner is simply the highest role, and a project can have several.
- Membership (`project_members`) is the **only** source of truth for who can do what. This removes the two-sources bug class that C0 had to patch, where an owner had no member row.

## 3. Data model — migration `017` (keeps all existing data)

### 3.1 Users, members, projects

- `users`:
  - add `username`, `first_name` and `last_name` (all nullable TEXT). Every login refreshes them from the ID token's `preferred_username`, `given_name` and `family_name`; a claim missing from the token leaves the stored value unchanged.
  - `email` stays (unique, refreshed at login), and so does the existing `display_name`.
  - no search index: a few thousand rows scan in under a millisecond.
  - no uniqueness constraint on `username`: the identity provider owns it.
  - add `project_limit` (nullable INTEGER, `>= 0`): an admin's per-user override of `PROJECT_LIMIT_PER_USER`. NULL means "use the default".
- `project_members` gains:
  - `role` (TEXT, CHECK over the four roles);
  - `added_by` (→ users, `ON DELETE SET NULL`);
  - `added_at` (TIMESTAMPTZ, default `now()`);
  - `added_via` (`member` | `admin`, default `member`).

  The column default used for the backfill is dropped afterwards, so every new row must name its role.
- `projects.owner_user_id` is renamed `created_by`. It becomes nullable and `ON DELETE SET NULL`, and is **not used for access**. It only feeds "created by" on the project page and the project-limit count.
- Backfill:
  - existing members become `contributor`;
  - each project's former `owner_user_id` gets an `owner` row (an existing member row is raised to owner), with `added_by` = that user and `added_at` = the project's `created_at`;
  - each existing project gets one `project.created` event (§3.2) at its `created_at`, with `details.backfilled = true`, so its activity feed isn't empty.
- Behaviour change: deleting a user no longer deletes their projects; it only removes their memberships.
  - A project whose last owner is deleted becomes **ownerless** and shows up in the admin console for recovery.
  - Placements now survive user deletion, so `delete_user` drops A1's "enumerate owned-project placements and GC them" step: `doc_content.docs_referenced_by_user` loses its `project_documents` branch. It still GCs the user's own entry docs (A1 §3).
- Invariant: every project has ≥1 owner. The app enforces it: no demoting, removing or leaving as the last owner. User deletion is the one exception, and admin recovery covers it.
- Read predicate (after A1): the user has a library entry for the content, OR has **any membership** in a project where it is placed. C0's separate project-owner branch disappears, because owners are members. `readable_docs_where` and `visible_projects_where` in `server/auth/authz.py` lose their `owner_user_id` branches.

### 3.2 Activity history — `project_events`

One row per change, written in the **same database transaction** as the change, so a rolled-back change leaves no event.

| Column | Type | Meaning |
|---|---|---|
| `id` | BIGSERIAL PK | Order. Two events in one transaction share a timestamp, so the feed sorts by `id`, not `at` |
| `project_id` | UUID NOT NULL → projects, `ON DELETE CASCADE` | Deleted with its project (the `audit.log` line remains) |
| `at` | TIMESTAMPTZ NOT NULL DEFAULT `now()` | When |
| `actor_user_id` | UUID → users, `ON DELETE SET NULL` | Who did it. NULL shows as "a former member" |
| `kind` | TEXT NOT NULL | See below. Validated in the app against one constant set, not a DB CHECK, so V1/V2 can add kinds without a migration |
| `subject_user_id` | UUID → users, `ON DELETE SET NULL` | The person a member event is about |
| `doc_id` | TEXT, no foreign key | The document an event is about. No FK, so history survives the document |
| `details` | JSONB NOT NULL DEFAULT `'{}'` | Per-kind facts, below |

Index: `(project_id, id DESC)`.

| `kind` | `details` |
|---|---|
| `project.created` | `{name}` (+ `backfilled: true` from the migration) |
| `project.renamed` | `{from, to}` |
| `project.described` | `{}`: the description text itself isn't copied |
| `member.added` | `{role, via}` |
| `member.role_changed` | `{from, to}` |
| `member.removed` | `{role}` |
| `member.left` | `{role}` |
| `document.added` | `{name}`: the name the project shows for it at that moment |
| `document.removed` | `{name}` |

- People are stored by ID and shown by their **current** names, so a rename never leaves stale names in history. Document names are copied, because the document may be gone.
- Project deletion writes no event (it would cascade away). It is logged to `audit.log`.
- Retention: `PROJECT_EVENTS_RETENTION_DAYS` (§12), in **days**, default `0` = keep forever. When it is above 0, events older than that are deleted at startup, and then every 24 hours by a background task started in the existing startup hook and cancelled at shutdown. The DELETE is idempotent, so several server workers purging is harmless.

### 3.3 Concurrency: the last-owner race

Two Owners demoting each other at the same moment would each see "another Owner remains", both commit, and the project would have zero Owners. To prevent it:

- every membership write and every project delete first takes `SELECT … FROM projects WHERE id = %s FOR UPDATE`;
- the caller's own role is then read **after** that lock;
- the last-owner check runs inside the same transaction.

A second concurrent request waits, then sees the first one's result and is refused (409 `last_owner`).

## 4. People directory

| Env key | Values | Default |
|---|---|---|
| `USER_DIRECTORY_MODE` | `exact` \| `domain` \| `open` | `exact` |
| `USER_DIRECTORY_DOMAINS` | comma list of org email domains | empty |
| `USER_DIRECTORY_SHOW_EMAIL` | `true` \| `false` | `false` |

- `exact`: lookup only by a full email.
- `domain`: type-ahead over users sharing the caller's email domain, and only if that domain is listed in `USER_DIRECTORY_DOMAINS`.
  - Unlisted domains (e.g. gmail.com) fall back to `exact`, so a public webmail domain never becomes a shared directory.
  - Domain matching is exact and case-insensitive; subdomains are separate unless listed.
- `open`: type-ahead over all users (GitLab's default).
- Type-ahead needs 2+ characters and returns up to 10 **active** users. It matches the start of the first name, last name, username or display name, and also the start of the email when `USER_DIRECTORY_SHOW_EMAIL=true`.
- Exact-email lookup works across domains in every mode, so an external collaborator can be added.
  - It finds **active and pending** users. `pending` means an account waiting for an admin's approval: a brand-new sign-in lands there (`server/auth/users.py`), and admin enroll defaults to it. A team can add such a person before the approval, and they get access once an admin activates them.
  - **Disabled** users never appear in any lookup, and can't be added or shared with (404).
- The caller is never in their own results.
- A person is labelled by one rule (one helper, used by lookup, members and activity):
  1. "First Last", if either name is set;
  2. otherwise `display_name`;
  3. otherwise `@username`, unless it contains `@` while emails are hidden;
  4. otherwise a masked email (`u•••@domain`).

  `@username` is shown beside the label when it's set and passes the same `@` rule. Emails appear only when `USER_DIRECTORY_SHOW_EMAIL=true`.
- Admin enroll: the form takes username, first name and last name.
  - The app stops setting `username = email`.
  - With a Keycloak admin client, `create_user` sends `username`, `firstName` and `lastName`. If no username is given, Keycloak still receives the email as its username (Keycloak requires one), but the app row keeps `username` NULL until the first login fills it from the token.
- Invalid config: log a WARNING and fall back to `exact`.
- Named gap (outside A0, not re-verified): federated login links accounts by email (`server/auth/users.py`); that is safe only when the identity provider has verified the email.
- Named risk, accepted 2026-10-06: exact-email lookup tells any signed-in user whether an email has an account here. Adding a colleague by email can't avoid that, and GitLab behaves the same way. A0 doesn't rate-limit lookups; a deployment that needs one sets it at its reverse proxy. The user confirmed deployments are mostly organization-wide, where this is expected.

## 5. Permission matrix

| Action | Reader | Contributor | Maintainer | Owner | Admin (any project) |
|---|---|---|---|---|---|
| See project, members, activity, its documents | ✅ | ✅ | ✅ | ✅ | project + members + activity + doc counts; documents only if a member |
| File **own** document into the project | — | ✅ | ✅ | ✅ | as a member only |
| Remove **any** document from the project | — | — | ✅ | ✅ | — |
| Edit name / description | — | — | ✅ | ✅ | ✅ |
| Add, remove or re-role members up to Maintainer | — | — | ✅ | ✅ | ✅ |
| Add, remove or re-role Owners | — | — | — | ✅ | ✅ |
| Delete project | — | — | — | ✅ | ✅ |
| Add **themselves** | — | — | — | — | ✅ (WARNING log + `added_via=admin` badge) |

- **Projects govern their documents** (decided 2026-09-25):
  - once a document is in a project, only Maintainer+ can remove it from that project;
  - the uploader loses C0's "doc owner can always unlink" power;
  - deleting your own copy never removes the project's copy (A1);
  - A0 swaps the body of A1's `can_manage_project_docs` seam to "role ≥ Maintainer".
- **Admins file documents only as members** (decided 2026-10-06): today `link_doc` lets an admin file into any project without being in it. That bypass is removed.
- A Maintainer may act on a member only when both the member's current role and the new role are ≤ Maintainer. Owner rows are Owner-only (and admin).
- `added_via = 'admin'` whenever the actor's authority came from the admin capability rather than from a project role.
- Creating projects:
  - `PROJECT_CREATION` = `readers` (anyone with the reader capability) \| `admins`; default `readers`;
  - admins can always create;
  - the creator becomes Owner;
  - an admin may create a project on someone else's behalf and name its first Owner, who must be an active or pending user; `created_by` is then the admin.
- Project limit:
  - `PROJECT_LIMIT_PER_USER` (default `20`, `0` = unlimited) counts projects the user created (`created_by`);
  - an admin can override it per user (`users.project_limit`, mirroring the token-budget pattern);
  - admins are not subject to the limit;
  - hitting the limit returns 429 `project_limit` and logs a WARNING.
- Per-document sharing: A1 replaces grants with shared library entries; only holders of a verified upload entry can share.
- Leaving: any member except the last Owner. An Owner can demote themselves only if another Owner remains. A Maintainer can never promote anyone to Owner.

The server computes these rules once, from the caller's role, and returns them as a `can` object (§9). The UI reads `can` and never re-derives the rules.

## 6. Refusals and user-facing notices

- Not a member → **404** (existence not revealed). A member whose role is too low → **403** `{"error": "insufficient_role", "required": "<role>"}`. Every refusal carries a `message`.
- The role check runs before any other validation, so a non-member gets 404 before a user ID or document ID can be used to probe the project.
- [src/lib/apiErrors.js](../../../src/lib/apiErrors.js) (`noticeFor`) gains these codes in place of raw `HTTP nnn` toasts:

| Case | Notice |
|---|---|
| 403 `insufficient_role` | "Only Maintainers or Owners can do that." (Owner-only actions: "Only Owners can do that.") |
| 403 `project_creation_restricted` | "Only admins can create projects here." |
| 404 in a project context | "This project doesn't exist or you don't have access." |
| 409 `last_owner` | "A project must keep at least one Owner." |
| 429 `project_limit` | "You've reached your project limit (N). Ask an admin to raise it." |
| 5xx | "Something went wrong on the server. Try again." (the only notice that blames the server) |

## 7. Logging

Builds on `server/logging_config.py` (`LOG_LEVEL`, `LOG_DIR`, and time-based rotation via `LOG_FILE_ROLL_OVER_TIME`).

| Level | Events |
|---|---|
| DEBUG | directory decisions (mode, result count) |
| INFO | project and membership changes (actor, target, project, role, via) |
| WARNING | admin self-add, last-owner guard trips, project limit hit, invalid directory or retention config |
| ERROR | unexpected failures in membership writes or Keycloak enroll calls |

- The `server.audit` logger and `LOG_AUDIT_FILE` already exist (A1). A0 adds every membership, role and project-lifecycle change to it (`project.created`, `project.updated`, `project.deleted`, `member.added`, `member.role_changed`, `member.removed`, `member.left`). The existing `placement.*` and `share.*` names stay as they are.
- `audit.log` is the operators' trail and `project_events` is the members' feed. Both are written for each change.
- **No personal data at INFO or above**: log user and project IDs, never emails, names or search text.
- Tests assert that the audit events are emitted.

## 8. Non-goals

- Visibility levels (private/internal/public) and invitations for users who don't have an account yet.
- A switch that isolates exact-email lookup to listed domains only. Add it if a deployment needs it.
- Read/write tiers on per-document shares (shares stay read-only).
- Notifications (email or in-app) about project events; editing or deleting individual events.
- Rate-limiting the people lookup (a reverse-proxy concern, §4).
- Project chat (C2.1) and document versions (V1, V2): their own specs.

## 9. API surface

All routes need a signed-in user with the `reader` capability. Admins pass every check in §5's admin column without being members.

### 9.1 Projects — `/v1/projects`

| Call | Who | Behaviour |
|---|---|---|
| `POST /v1/projects` `{name, description?, owner_user_id?}` | per `PROJECT_CREATION`; `owner_user_id` admin-only | 201 with the project. The creator (or the named user) becomes Owner. Writes `project.created` |
| `GET /v1/projects` | any reader | Projects the caller is a member of, newest first. Admins too: every project is listed under `/v1/admin/projects` |
| `GET /v1/projects/{id}` | member, admin | One project |
| `PATCH /v1/projects/{id}` `{name?, description?}` | Maintainer+, admin | Writes `project.renamed` and/or `project.described` only for fields that changed |
| `DELETE /v1/projects/{id}` | Owner, admin | 204. Placements go, and content nobody else holds is GC'd (as today) |

A project object (list, get, create and patch return the same shape):

```json
{"id": "…", "name": "…", "description": "…", "created_at": "…",
 "created_by": {"id": "…", "name": "…"} ,
 "my_role": "contributor",
 "member_count": 4, "doc_count": 12,
 "can": {"edit": false, "manage_members": false, "manage_owners": false,
         "file_docs": true, "remove_docs": false, "delete": false, "leave": true}}
```

- `created_by` is null once that user is deleted.
- `my_role` is null for an admin who isn't a member.
- `doc_count` counts verified placements.
- `is_owner` and `owner_user_id` are removed (breaking; CHANGELOG).

### 9.2 People and members

| Call | Who | Behaviour |
|---|---|---|
| `GET /v1/users/lookup?q=` | any reader | §4 rules. `[{id, name, username, email?, status}]`, at most 10. Under 2 characters, or no match: `[]` |
| `GET /v1/projects/{id}/members` | member, admin | `[{user_id, name, username, email?, status, role, added_at, added_by: {id, name} \| null, added_via}]`, Owners first, then by name |
| `PUT /v1/projects/{id}/members/{user_id}` `{role}` | Maintainer+ (Owner rows: Owner), admin | Adds the person or changes their role. 200 with the member row. Writes `member.added` or `member.role_changed`; the same role again is a no-op with no event |
| `DELETE /v1/projects/{id}/members/{user_id}` | Maintainer+ (Owner rows: Owner), admin; **or the member themselves** (= leave) | 204. Writes `member.removed`, or `member.left` when it's yourself |

- A missing or disabled target user → 404 "User not found", after the role check.
- An unknown or missing `role` → 422. The body-less `PUT` that works today now fails: this is a breaking change for scripts (CHANGELOG). It doesn't default to Reader, because that would quietly give scripted adds less access than they get today (they're effectively Contributors).
- Removing, demoting or leaving as the last Owner → 409 `last_owner`.
- An admin adding themselves → `added_via=admin` and a WARNING log.

### 9.3 Documents and shares

- Filing and removing stay `PUT`/`DELETE /v1/projects/{id}/docs/{doc_id}`. Filing needs Contributor+ plus a verified upload entry (as today); removing needs Maintainer+. Each writes `document.added` / `document.removed`. Re-filing a document that is already there writes nothing.
- Listing stays `GET /v1/docs?project_id=`, members only.
- **New:** `GET /v1/docs/{doc_id}/shares` → `[{user_id, name, username, shared_at}]`: the people the caller shared this document with. It needs a verified upload entry, otherwise 404. The Share dialog needs it to show and revoke existing shares; no call returns this today.
- Sharing itself stays `PUT`/`DELETE /v1/docs/{doc_id}/shares/{user_id}`. Sharing with a disabled user becomes a 404.

### 9.4 Activity

`GET /v1/projects/{id}/events?before=<event id>&limit=50` (member, admin):

- `limit` is between 1 and 100, default 50;
- results are newest first;
- the response is `{events: [{id, at, kind, actor, subject, doc, details}], next_before}`:
  - `actor` and `subject` are `{id, name}` or null;
  - `doc` is `{id, name}` or null;
  - `next_before` is null on the last page.

### 9.5 Admin

- `GET /v1/admin/projects?ownerless=true|false` → `[{id, name, created_at, member_count, doc_count, owners: [{id, name}], ownerless}]`, newest first.
- Recovery reuses `PUT /v1/projects/{id}/members/{admin's own id}` `{role: "owner"}`.
- `PATCH /v1/admin/users/{id}` gains `project_limit` (integer ≥ 0, or null to restore the default).
- `POST /v1/admin/users` (enroll) gains optional `username`, `first_name` and `last_name` (§4).
- `GET /v1/admin/users` returns those fields plus `project_limit`.

### 9.6 Server structure

- `server/auth/authz.py` gains the project-role seam:
  - `project_role(conn, user_id, project_id, *, lock=False) -> str | None`;
  - `require_project_role(conn, principal, project_id, minimum, *, lock=False) -> str | None`, which returns 404 for non-members, 403 `insufficient_role` below `minimum`, and passes admins (returning their member role or None);
  - `can_for(role, is_admin) -> dict`, which builds the `can` object.

  `can_manage_project_docs` becomes `role ≥ maintainer`.
- `server/services/project_events.py`:
  - `record(conn, project_id, actor_user_id, kind, *, subject_user_id=None, doc_id=None, details=None)` validates `kind` and writes the row and the audit line;
  - `page(conn, project_id, before, limit)` returns one page of the feed;
  - `purge(conn, retention_days)` deletes expired events.
- `server/services/people.py`: the directory config and lookup (§4), plus `person_label`.
- `server/routers/people.py`: `GET /v1/users/lookup`.
- `server/routers/projects.py`: members, the project GET and events.
- `server/routers/admin.py`: projects list, `project_limit`, enroll names.

## 10. UI screens

**Where projects live:** the Library gets two tabs, **Documents | Projects**. There's no fifth top-level view: the view switcher already holds four (reader, chat, library, admin), and projects are a way of organizing documents.

**Projects tab:** one card per project, showing its name, your role badge and member and document counts, plus a **New project** button (today's `NewProjectForm`). A card opens the project page:

```text
← Projects   Q3 Audit                         [Contributor]  [Leave]
             Evidence for the Q3 audit  ✎ (Maintainer+)
  ┌───────────┬─────────┬──────────┐
  │ Documents │ Members │ Activity │
  └───────────┴─────────┴──────────┘
  Members (4)                                     [+ Add people]
  Asha Perera  @asha     Owner        ▾  ×
  Ben Silva    @ben      Maintainer   ▾  ×
  You          @you      Contributor
  Admin        @root     Owner  (added by admin)
```

- **Header:**
  - **Leave** for everyone; the last Owner gets the `last_owner` notice.
  - Edit name and description when `can.edit`.
  - **Delete project** when `can.delete`, behind a confirmation that says documents only this project held are removed with it.
- **Documents tab:**
  - the project's documents, using the Library's row component;
  - **File a document** (when `can.file_docs`) lists the caller's own uploads that aren't already filed;
  - × on a row when `can.remove_docs`.
- **Members tab:**
  - the role menu and × appear only where `can` allows; otherwise the role is plain text;
  - "Owner" appears in the menu only with `can.manage_owners`;
  - a pending member shows "awaiting approval", and a disabled one shows "disabled";
  - an admin-added member shows "(added by admin)".
- **Add people:** opens the **people picker**.
  - Type a name or an exact email (debounced, 2+ characters), then pick from the results.
  - Choose a role (default Contributor), then **Add**.
  - "Nobody found" names the directory rule in force, e.g. "Type their full email address" in `exact` mode.
- **Activity tab:**
  - lines such as "Asha added Ben as Maintainer · 2 h ago", newest first, with **Load more** (`next_before`);
  - a deleted actor reads "a former member";
  - a removed document keeps its name.

**Library rows:**

- The "+ project" menu lists only projects with `can.file_docs`, and × appears only where `can.remove_docs`. Today this is an `is_owner` check.
- A new **Share** button on documents the caller uploaded opens the same people picker. It also lists existing shares (`GET /v1/docs/{id}/shares`), each with ×.

**Reader upload:** the "add to project" picker in `App.jsx` lists only projects with `can.file_docs`, so a Reader is never offered a project that would refuse them.

**Admin console:**

- A new **Projects** section lists every project with its owners and counts, and has an **Ownerless only** filter.
  - **Open** shows the same project page in admin mode: members, activity and counts, and documents only if the admin is a member.
  - Ownerless rows also get **Add me as Owner**.
- The Users table gains a **Project limit** field (blank = the default), next to the token budget.
- The enroll form gains username, first name and last name.

**New components:**

- `src/components/library/ProjectsTab.jsx`
- `src/components/projects/ProjectPage.jsx`
- `src/components/projects/ProjectDocsTab.jsx`
- `src/components/projects/MembersTab.jsx`
- `src/components/projects/ActivityTab.jsx`
- `src/components/people/PeoplePicker.jsx` (shared by Members and Share)
- `src/components/library/ShareDialog.jsx`
- `src/components/admin/AdminProjectsSection.jsx`

`LibraryPage.jsx` gains the tab switch and loses its `is_owner` logic.

**Every journey has a control:**

| # | Journey | Control |
|---|---|---|
| 1 | Create a project | Library → Projects → New project |
| 2 | Find a colleague, add with a role | Project → Members → Add people |
| 3 | Change a role / remove | Role menu / × on the member row |
| 4 | File my document | Project → Documents → File a document (or the Library row's "+ project") |
| 5 | Maintainer removes a document | × on the project's document row |
| 6 | Share one document with one person | Library row → Share |
| 7 | Leave | Project header → Leave |
| 8 | Admin recovery | Admin → Projects → Ownerless only → Add me as Owner |
| 9 | See what changed | Project → Activity |

## 11. Testing and journey verification

**Backend (pytest, real Postgres):**

| Area | What the tests prove |
|---|---|
| Migration 017 | Backfill on a pre-017 database, following the existing `test_migration_0NN.py` pattern. Covers: an owner with no member row (gets an Owner row); an owner who was also a member (raised to Owner); plain members (made Contributors); one backfilled `project.created` per project. Also that deleting a user no longer deletes their projects, and that losing the last Owner makes a project ownerless |
| Permission matrix | **One parametrized table**: role (none, reader, contributor, maintainer, owner, admin non-member, admin member) × action → expected status. It mirrors §5 row for row |
| Last-owner rule | Demoting, removing or leaving as the last Owner → 409; with two Owners it succeeds. **Race:** two Owners demote each other on two connections at once; exactly one succeeds and one Owner remains |
| Maintainer limits | A Maintainer can't add, promote to, demote or remove an Owner, but can manage Readers, Contributors and Maintainers |
| Activity | Each write records its kind and details; a rolled-back change leaves no event; a re-file or same-role PUT writes none. A deleted actor → null; a removed document keeps its name. Paging with `before=`; non-member 404; admin non-member allowed. Retention: `N` days purges older rows, `0` keeps all, invalid → WARNING + 0 |
| Directory | Each of the three modes. An unlisted domain falls back to exact. Disabled users never appear; pending users appear for exact email only. The caller is excluded. Emails hidden by default; `person_label` fallbacks; invalid config → WARNING + exact |
| Limits | Per-user default, admin override, `0` = unlimited, admins exempt, 429. `PROJECT_CREATION=admins` → 403 `project_creation_restricted` |
| Read access | A Reader member can read the project's documents; a removed member immediately can't. The read predicate no longer reads `created_by` |
| Shares | `GET …/shares` lists only the caller's shares; non-holders get 404; sharing with a disabled user gets 404 |
| Privacy | Log capture asserts that no email, name or lookup text appears at INFO or above, and that the audit events are emitted |

**Frontend (vitest):**

- the people picker: debounce, exact-email results, the "nobody found" wording per mode;
- the Members tab driven by `can`: an Owner sees "Owner" in the menu, a Maintainer doesn't, and a Reader sees plain text;
- the new notices in `apiErrors.js`;
- Library chip filters by `can`;
- the Share dialog: list, add, revoke;
- the admin Projects section: the ownerless filter and "Add me as Owner";
- the Activity tab: lines, "a former member", Load more.

**The "done" check: a walk through the running app.**

- Set-up: the dev Keycloak (`localhost:18080`) with two browser sessions. The admin enrolls a second user from the console (this exercises the new name fields), and that user signs in with the temporary password.
- Every journey in the §10 table is done through its control, and the result is recorded in the handoff.
- Last: the admin deletes the second user while they're a project's only Owner, then recovers the project from the admin console.

If the browser tools are unavailable, the walk is reported as still owed, and A0 isn't called done.

**Gate:** the full backend and frontend suites plus eslint, compared against the recorded baseline failures.

## 12. Configuration summary

Every key is read at request time unless noted, has a safe default, and gets a commented line in `.env.example`.

| Key | Unit / values | Default | What `0` or empty means | Invalid value |
|---|---|---|---|---|
| `USER_DIRECTORY_MODE` | `exact` \| `domain` \| `open` | `exact` | — | WARNING, `exact` |
| `USER_DIRECTORY_DOMAINS` | comma list of domains | empty | `domain` mode behaves as `exact` | — |
| `USER_DIRECTORY_SHOW_EMAIL` | `true` \| `false` | `false` | — | WARNING, `false` |
| `PROJECT_CREATION` | `readers` \| `admins` | `readers` | — | WARNING, `readers` |
| `PROJECT_LIMIT_PER_USER` | projects (count) | `20` | `0` = unlimited | WARNING, `20` |
| `PROJECT_EVENTS_RETENTION_DAYS` | days (integer) | `0` | `0` = keep forever | WARNING, `0`. Read at startup |

## 13. Docs and changelog

- `docs/USER_GUIDE.md`: a "Projects and people" section (roles, adding people, sharing, activity, leaving).
- `docs/ARCHITECTURE.md`: membership as the access source, `project_events`, the lock rule (§3.3).
- `.env.example`: the six keys in §12.
- `CHANGELOG.md`:
  - **breaking:** the members `PUT` needs `{role}`;
  - **breaking:** project objects drop `is_owner` and `owner_user_id` for `my_role` and `can`;
  - user deletion no longer deletes their projects;
  - admins file documents only as members.
- A0 does not touch `docs/DEPLOYMENT.md`'s real-domain issue (owed from v2.4, tracked separately).
