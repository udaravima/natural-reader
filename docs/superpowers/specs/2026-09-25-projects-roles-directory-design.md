# Projects, roles & people directory (A0) — DRAFT

**Date:** 2026-09-24/25
**Status:** DRAFT. Every decision below is agreed with the user. Sections still pending: §9 API surface, §10 UI screens + testing. Do not plan or implement until the spec is complete and approved.
**Branch:** `development`
**Depends on:** A1 (document content vs library entries). A1 is built first; A0's document rules (§5) are written against A1's model.
**Open-source rule:** this is a community project deployed by many organizations. Nothing deployment-specific is hardcoded. Every knob below is an env var documented in `.env.example`, with a secure default.

## 1. Goal and user journeys

Project management works entirely from the UI, with no curl. Each journey must map to a screen control, and the finished feature is checked in the running app:

1. Create a project with a name and description.
2. Find a colleague and add them with a role.
3. Change a member's role, or remove them.
4. File one of my documents into a project.
5. As a Maintainer, remove a document from the project.
6. Share a single document with one person.
7. Leave a project.
8. Admin: see all projects (including ownerless ones), manage members, add themselves for recovery.

## 2. Membership model: GitLab-style roles

- Roles: `reader` < `contributor` < `maintainer` < `owner`.
- The owner is simply the highest role, and a project can have several.
- Membership (`project_members`) is the **only** source of truth for who can do what. This removes the two-sources bug class that C0 had to patch, where an owner had no member row.

## 3. Data model — migration `013` (keeps all existing data; A1 is `011`–`012`)

- `users`: add `username`, `first_name`, `last_name`, filled from the ID token (`preferred_username`, `given_name`, `family_name`) at every login. `email` stays (unique, refreshed at login). No search index; a few thousand rows scan in under a millisecond.
- `project_members`: add `role` (CHECK over the four roles), `added_by` (→ users, SET NULL), `added_at`, `added_via` (`member` | `admin`).
- `projects.owner_user_id` → renamed `created_by`, nullable, `ON DELETE SET NULL`, **not used for access**.
- Backfill:
  - existing members become `contributor`;
  - each project's `owner_user_id` gets an `owner` row (an existing member row is raised to owner).
- Behaviour change: deleting a user no longer cascades to their projects. It only removes their memberships. A project whose last owner is deleted becomes **ownerless** and shows up in the admin console for recovery. Because placements now survive user deletion, `delete_user` drops A1's "enumerate owned-project placements and GC them" step. It still GCs the user's own entry docs (A1 §3).
- Invariant: every project has ≥1 owner. The app enforces it: no demoting, removing or leaving as the last owner. User deletion is the one exception, and admin recovery covers it.
- Read predicate (after A1): the user has a library entry for the content, OR has **any membership** in a project where it is placed. C0's separate project-owner branch disappears, because owners are members.

## 4. People directory

| Env key | Values | Default |
|---|---|---|
| `USER_DIRECTORY_MODE` | `exact` \| `domain` \| `open` | `exact` |
| `USER_DIRECTORY_DOMAINS` | comma list of org email domains | empty |
| `USER_DIRECTORY_SHOW_EMAIL` | `true` \| `false` | `false` |

- `exact`: lookup only by a full email.
- `domain`: type-ahead (2+ characters, up to 10 **active** users) over users sharing the caller's email domain, and only if that domain is listed in `USER_DIRECTORY_DOMAINS`. Unlisted domains (e.g. gmail.com) fall back to `exact`, so a public webmail domain never becomes a shared directory. Matching is exact and case-insensitive; subdomains are separate unless listed.
- `open`: type-ahead over all active users (GitLab's default).
- Exact-email lookup works across domains in every mode, so an external collaborator can be added.
- Results show "First Last · @username". Emails appear only when `USER_DIRECTORY_SHOW_EMAIL=true`, and type-ahead then also matches the start of an email. Usernames that contain `@` are hidden when emails are hidden. With no name and a hidden email-username, the result falls back to a masked email (`u•••@domain`).
- Admin enroll: the form takes username, first name and last name, and stops setting `username = email`.
- Invalid config: log a WARNING and fall back to `exact`.
- Named gap (outside A0, not re-verified): federated login links accounts by email (`server/auth/users.py`); that is safe only when the identity provider has verified the email.

## 5. Permission matrix

| Action | Reader | Contributor | Maintainer | Owner | Admin (any project) |
|---|---|---|---|---|---|
| See project, members, its documents | ✅ | ✅ | ✅ | ✅ | project + members + doc counts; documents only if a member |
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
- Creating projects:
  - `PROJECT_CREATION` = `readers` (anyone with the reader capability) \| `admins`; default `readers`;
  - the creator becomes Owner;
  - an admin may create a project on someone else's behalf and name its first Owner.
- Project limit:
  - `PROJECT_LIMIT_PER_USER` (default `20`, `0` = unlimited) counts projects the user created;
  - an admin can override it per user (nullable `users.project_limit`, mirroring the token-budget pattern);
  - hitting the limit returns 429 and logs a WARNING.
- Per-document sharing: A1 replaces grants with shared library entries; only holders of an upload entry can share.
- Leaving: any member except the last Owner. An Owner can demote themselves only if another Owner remains. A Maintainer can never promote anyone to Owner.

## 6. Refusals and user-facing notices

- Not a member → **404** (existence not revealed). A member whose role is too low → **403** `{"error": "insufficient_role", "required": "<role>"}`. Every refusal carries a `message`.
- The SPA gets one central code-to-notice mapper in place of raw `HTTP nnn` toasts:

| Case | Notice |
|---|---|
| 403 `insufficient_role` | "Only Maintainers or Owners can do that." |
| 404 in a project context | "This project doesn't exist or you don't have access." |
| 409 last owner | "A project must keep at least one Owner." |
| 429 project limit | "You've reached your project limit (N). Ask an admin to raise it." |
| 5xx | "Something went wrong on the server. Try again." (the only notice that blames the server) |

## 7. Logging

Builds on `server/logging_config.py` (the `LOG_LEVEL`, `LOG_DIR` and rotation settings).

| Level | Events |
|---|---|
| DEBUG | directory decisions (mode, result count) |
| INFO | project and membership changes (actor, target, project, role, via) |
| WARNING | admin self-add, last-owner guard trips, project limit hit, invalid directory config |
| ERROR | unexpected failures in membership writes or Keycloak enroll calls |

- The `server.audit` logger and `LOG_AUDIT_FILE` are introduced by A1. A0 adds every membership, role and project-lifecycle event to it, and adds its codes to A1's `src/lib/apiErrors.js` mapper.
- **No personal data at INFO or above**: log user and project IDs, never emails, names or search text.
- Tests assert that the audit events are emitted.

## 8. Non-goals

- Visibility levels (private/internal/public) and invitations for users who don't have an account yet.
- A switch that isolates exact-email lookup to listed domains only. Add it if a deployment needs it.
- Read/write tiers on per-document shares (shares stay read-only).

## 9. API surface — PENDING

## 10. UI screens, journey verification, testing — PENDING
