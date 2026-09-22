# Document library (RAG Phase 0)

The library turns the single-owner document store into a **shared, organizable
library**: documents can be grouped into projects, shared with other users, and
tagged, while every read stays access-controlled. This is Phase 0 of the RAG
roadmap — it builds the ownership/sharing/organization substrate that later
phases (cross-document search, descriptions + routing, multi-round retrieval)
sit on top of. It adds no search intelligence itself.

Branch: `feat/document-library-rag` (off `feat/admin-console`). Migration
**009**. Nothing here is on `master`.

## The one idea: `can_read`

Every read decision reduces to one predicate, resolved entirely in SQL:

> **A user can read a document iff they own it, OR they are a member of the
> project it belongs to, OR they hold a per-document grant on it.**

Three ways in, checked in that order:

| Path | Table | Meaning |
|------|-------|---------|
| Owner | `documents.owner_user_id` | You uploaded/registered it. Full read + write. |
| Project member | `project_members` | You were added to the project the doc is filed under. Read-only. |
| Grantee | `doc_grants` | You were handed this one specific document. Read-only. |

Projects are the **primary sharing unit** (share a folder of docs by adding a
member); per-doc grants are the **exception path** (hand someone a single file
without giving them the whole project).

**Writes never widen.** Membership and grants grant *read* only. Editing tags,
reassigning the project, deleting, uploading PDF bytes, indexing, converting —
all stay **owner-only**. Admin reassignment of ownership is the sole exception
(below).

### 404, not 403 — the indistinguishability posture

A user who can't read a document gets **404**, identical to a document that
doesn't exist. A 403 would confirm the doc exists; 404 leaks nothing. This is
enforced structurally: `assert_can_read_doc()` and the list query's
`readable_docs_where()` filter build the `can_read` predicate into the SQL
`WHERE`, so a stranger's document **can never appear in a result set** — not
even one that happens to match the caller's `q`/`tag`/`project_id` filters.
There is no Python-side post-filter that could be bypassed.

## Schema (migration 009)

```
projects(id, owner_user_id→users, name, description, created_at)
project_members(project_id→projects, user_id→users)          PK(project_id, user_id)
doc_grants(doc_id→documents, grantee_user_id→users)           PK(doc_id, grantee_user_id)
documents.project_id  UUID → projects(id) ON DELETE SET NULL  -- deleting a project un-files, never deletes, its docs
documents.tags        TEXT[] NOT NULL DEFAULT '{}'
```

Indexes back the three access paths + the two filters: `documents(project_id)`,
GIN on `documents(tags)`, `doc_grants(grantee_user_id)`,
`project_members(user_id)`. The migration self-registers as version 9
(`INSERT INTO schema_migrations(version) VALUES (9) ON CONFLICT DO NOTHING`),
so the test harness and startup apply it exactly once. **009 is independent of
auth's 008** (`users.capabilities`); the two migration numbers were
deconflicted so the two tracks can land in either order.

## API surface

### Documents (`server/routers/docs.py`)

| Route | Guard | Notes |
|-------|-------|-------|
| `GET /v1/docs?q=&project_id=&tag=` | any signed-in user | Lists **readable** docs only. `q` matches file name (ILIKE) or a tag; `project_id`/`tag` filter. Returns `{doc_id, file_name, state, tags, project_id, project_name, owner_user_id, is_owner}`. |
| `GET /v1/docs/{id}` · `POST /v1/docs/{id}/search` · `GET /v1/docs/{id}/markdown` | **reader** (`can_read`) | The read routes — widened from owner-only to `can_read`. |
| `PATCH /v1/docs/{id}` | owner (tags/project) · admin (owner) | Owner sets `tags`/`project_id`; admin-only sets `owner_user_id` (reassignment). Owner assigning `project_id` must own or be a member of the target project (else 404) — blocks cross-tenant injection (IDOR). |
| `DELETE /v1/docs/{id}` + all write/index/convert/pdf routes | owner | Unchanged owner-only surface. |
| `PUT` · `DELETE /v1/docs/{id}/grants/{user_id}` | doc owner | Idempotent (204). Grant/revoke per-doc read. Unknown `user_id` → 404 (not 500). |

### Projects (`server/routers/projects.py`)

| Route | Guard | Notes |
|-------|-------|-------|
| `POST /v1/projects` | any signed-in user | Creates a project you own. |
| `GET /v1/projects` | any signed-in user | Lists projects you **own or are a member of**; each row carries `is_owner`. |
| `GET` · `PATCH` · `DELETE /v1/projects/{id}` | owner (404 otherwise) | Read/rename/delete your own project. Delete un-files its docs (`ON DELETE SET NULL`), never deletes them. |
| `PUT` · `DELETE /v1/projects/{id}/members/{user_id}` | owner | Idempotent (204). Add/remove a read-member. Unknown `user_id` → 404. |

## Frontend

A **Library** view (`viewMode: 'library'`, `src/components/library/LibraryPage.jsx`)
in the reader window's view switcher, available to any active user. It lists
readable docs with search-as-you-type (`?q=`), a project filter, and — on rows
you **own** — inline tag editing, project reassignment, and delete. Rows shared
with you (`is_owner: false`) show a "shared" badge and are read-only.

The **upload/register surface** (`src/components/PdfViewer.jsx` toolbar +
`src/lib/docMeta.js`) gained an optional project select + tags input. When a
document registers (via index or convert) and a project/tag was chosen, one
follow-up `PATCH /v1/docs/{id}` attaches it; choosing neither leaves the plain
register path byte-for-byte unchanged. The picker is per-document
(`useDocMetaPicker`) — it resets when the loaded document changes, so a
selection can't leak onto the next document.

## Backfilled documents and the admin reassignment path

Documents that predate multi-user auth were assigned to the bootstrap/seed
admin (migration 005/006). To hand one to its real owner, an **admin** uses
`PATCH /v1/docs/{id}` with `owner_user_id` — the one path that transfers
ownership. A non-admin owner cannot change `owner_user_id` (403); they can only
organize (tags/project) or share (grants/members) within what they own.

## Capability coordination with the auth track

The read routes here are guarded by `can_read` **only**. When this branch is
integrated with `feat/keycloak-identity-permissions` (auth, migration 008), the
read routes and the Library view additionally gain
`require_capability("reader")` — a user needs both the `reader` capability *and*
`can_read` on the specific document. That gate is **deliberately not in this
branch**, to avoid a cross-branch dependency; it's applied at integration.

## What's next (out of scope here)

Phase 1 = `docScope` cross-document search; Phase 2 = document descriptions +
`find_documents` routing; Phase 3 = multi-round retrieval loop. Each gets its
own spec → plan → build. The "Ask about these" affordance in the Library is a
Phase 1 feature and is intentionally absent.
