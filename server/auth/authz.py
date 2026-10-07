"""Access predicates and ownership guards: who can read a document (library
entry or project placement), who holds an upload entry, who owns a session,
who may manage a project's documents. Refusals are 404 for both "missing"
and "not yours", so a caller can't tell one from the other."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException

from ..http_errors import refusal


async def _owner(conn, table: str, key_col: str, key: str) -> str | None:
    cur = await conn.execute(
        f"SELECT user_id FROM {table} WHERE {key_col} = %s", (key,)
    )
    row = await cur.fetchone()
    return str(row[0]) if row else None


async def assert_owns_session(conn, session_id: str, user_id: str) -> None:
    if await _owner(conn, "chat_sessions", "id", session_id) != user_id:
        raise HTTPException(status_code=404, detail="Session not found")


def readable_docs_where(alias: str = "d") -> str:
    """The ONE definition of "can read this document" (A1 spec §3, A0 §3.1):
    the user has a library entry for it, or it is placed in a project the
    user is a member of (any role — owners are members) — and that entry or
    placement is PROVED (migration 012): `verified`, i.e. it traces back to
    someone who sent this server the bytes. `<alias>` is a `documents` row.
    Bind with `readable_docs_params(user_id)`. Subquery aliases are
    underscore-prefixed so they can't shadow the caller's. Resolved in SQL,
    never in Python."""
    return (
        f"(EXISTS (SELECT 1 FROM library_entries _re "
        f"WHERE _re.doc_id = {alias}.doc_id AND _re.user_id = %s "
        f"AND {effective_holding_sql('_re')}) "
        f"OR EXISTS (SELECT 1 FROM project_documents _rpd "
        f"JOIN project_members _rpm ON _rpm.project_id = _rpd.project_id "
        f"WHERE _rpd.doc_id = {alias}.doc_id AND {effective_holding_sql('_rpd')} "
        f"AND _rpm.user_id = %s))"
    )


def effective_holding_sql(holding: str) -> str:
    """An entry or placement row (`holding`) that grants read: a verified
    one. Takes no parameters. Used by `readable_docs_where` and by anything
    that shows which projects hold a doc, so the two can't disagree."""
    return f"{holding}.verified"


def readable_docs_params(user_id: str) -> list[str]:
    """Exactly the parameters `readable_docs_where` needs, in order."""
    return [user_id, user_id]


async def assert_holds_upload(conn, doc_id: str, user_id: str) -> None:
    """404 unless the user holds a VERIFIED upload entry — they uploaded the
    bytes to this server, which is what sharing and filing into a project
    require. A pre-A1 upload entry (migration 012: unverified) is only a claim."""
    cur = await conn.execute(
        "SELECT 1 FROM library_entries WHERE doc_id = %s AND user_id = %s "
        "AND added_via = 'upload' AND verified", (doc_id, user_id))
    if await cur.fetchone() is None:
        raise refusal(404, "not_found", "Document not found")


def visible_projects_where(alias: str = "p") -> str:
    """A `projects` row the user is a member of (any role). Bind with
    `visible_projects_params(user_id)`."""
    return (
        f"EXISTS (SELECT 1 FROM project_members _vpm "
        f"WHERE _vpm.project_id = {alias}.id AND _vpm.user_id = %s)"
    )


def visible_projects_params(user_id: str) -> list[str]:
    return [user_id]


ROLES = ("reader", "contributor", "maintainer", "owner")
_RANK = {r: i for i, r in enumerate(ROLES)}
PROJECT_NOT_FOUND = "This project doesn't exist or you don't have access."
_ROLE_MESSAGES = {
    "reader": "Only members can do that.",
    "contributor": "Only Contributors, Maintainers or Owners can do that.",
    "maintainer": "Only Maintainers or Owners can do that.",
    "owner": "Only Owners can do that.",
}


def role_at_least(role: str | None, minimum: str) -> bool:
    return role is not None and _RANK[role] >= _RANK[minimum]


def insufficient_role(required: str) -> HTTPException:
    return refusal(403, "insufficient_role", _ROLE_MESSAGES[required], required=required)


async def project_role(conn, user_id: str, project_id, *, lock: bool = False) -> str | None:
    """The user's role in the project; None if not a member (or no project).
    `lock=True` first takes the project's row lock (A0 §3.3): membership
    writes on one project then run one at a time, and the role read here is
    the one committed by whoever held the lock before."""
    if lock:
        await conn.execute("SELECT 1 FROM projects WHERE id = %s FOR UPDATE", (project_id,))
    cur = await conn.execute(
        "SELECT role FROM project_members WHERE project_id = %s AND user_id = %s",
        (project_id, user_id))
    row = await cur.fetchone()
    return row[0] if row else None


@dataclass(frozen=True)
class ProjectAccess:
    role: str | None      # the caller's member role (None: an admin who isn't a member)
    is_admin: bool


async def require_project_role(conn, principal, project_id, minimum: str | None, *,
                               lock: bool = False, admin_ok: bool = True) -> ProjectAccess:
    """Every project route's first check (A0 §5, §6). A missing project and a
    non-member are the same 404, so a project's existence never leaks. A
    member below `minimum` (None = any member) gets 403 insufficient_role.
    An admin passes when `admin_ok` (the matrix's admin column); document
    routes pass `admin_ok=False`, because admins file and remove documents
    only through a member role."""
    cur = await conn.execute(
        "SELECT 1 FROM projects WHERE id = %s" + (" FOR UPDATE" if lock else ""), (project_id,))
    if await cur.fetchone() is None:
        raise refusal(404, "not_found", PROJECT_NOT_FOUND)
    role = await project_role(conn, principal.user_id, project_id)
    is_admin = principal.role == "admin"
    if is_admin and admin_ok:
        return ProjectAccess(role=role, is_admin=True)
    if role is None:
        raise refusal(404, "not_found", PROJECT_NOT_FOUND)
    if minimum is not None and not role_at_least(role, minimum):
        raise insufficient_role(minimum)
    return ProjectAccess(role=role, is_admin=is_admin)


def can_for(role: str | None, is_admin: bool) -> dict[str, bool]:
    """What the caller may do in a project, from their role (A0 §5). The API
    returns it with every project so the UI never re-derives the rules."""
    def at(minimum: str) -> bool:
        return role_at_least(role, minimum)
    return {
        "edit": at("maintainer") or is_admin,
        "manage_members": at("maintainer") or is_admin,
        "manage_owners": at("owner") or is_admin,
        "file_docs": at("contributor"),
        "remove_docs": at("maintainer"),
        "delete": at("owner") or is_admin,
        "leave": role is not None,
    }


async def can_manage_project_docs(conn, user_id: str, project_id) -> bool:
    """The one seam for "may remove a document from this project" (A1 §5,
    A0 §5): a member whose role is Maintainer or above."""
    return role_at_least(await project_role(conn, user_id, project_id), "maintainer")


CAN_READ_DOCS_SQL = readable_docs_where()
CAN_READ_ONE_DOC_SQL = (
    f"SELECT 1 FROM documents d WHERE d.doc_id = %s AND {readable_docs_where('d')}"
)


async def assert_can_read_doc(conn, doc_id: str, user_id: str) -> None:
    """404 unless `user_id` can read `doc_id`. Missing and not-readable are
    indistinguishable (no existence leak)."""
    cur = await conn.execute(CAN_READ_ONE_DOC_SQL, [doc_id, *readable_docs_params(user_id)])
    if await cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Document not found")
