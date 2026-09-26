"""Access predicates and ownership guards: who can read a document (library
entry or project placement), who holds an upload entry, who owns a session,
who may manage a project's documents. Refusals are 404 for both "missing"
and "not yours", so a caller can't tell one from the other."""
from __future__ import annotations

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
    """The ONE definition of "can read this document" (A1 spec §3): the user
    has a library entry for it, or it is placed in a project the user owns or
    belongs to — and that entry or placement is PROVED (migration 012):
    `verified`, i.e. it traces back to someone who sent this server the
    bytes. A pre-A1 row (the browser sent only a hash) is a claim, and never
    grants read until its holder uploads the file. `<alias>` is a
    `documents` row. Bind with `readable_docs_params(user_id)`. Subquery
    aliases are underscore-prefixed so they can't shadow the caller's.
    Resolved in SQL, never in Python."""
    return (
        f"(EXISTS (SELECT 1 FROM library_entries _re "
        f"WHERE _re.doc_id = {alias}.doc_id AND _re.user_id = %s "
        f"AND {effective_holding_sql('_re')}) "
        f"OR EXISTS (SELECT 1 FROM project_documents _rpd "
        f"JOIN projects _rp ON _rp.id = _rpd.project_id "
        f"WHERE _rpd.doc_id = {alias}.doc_id AND {effective_holding_sql('_rpd')} "
        f"AND (_rp.owner_user_id = %s "
        f"OR EXISTS (SELECT 1 FROM project_members _rpm "
        f"WHERE _rpm.project_id = _rp.id AND _rpm.user_id = %s))))"
    )


def effective_holding_sql(holding: str) -> str:
    """An entry or placement row (`holding`) that grants read: a verified
    one. Takes no parameters. Used by `readable_docs_where` and by anything
    that shows which projects hold a doc, so the two can't disagree."""
    return f"{holding}.verified"


def readable_docs_params(user_id: str) -> list[str]:
    """Exactly the parameters `readable_docs_where` needs, in order."""
    return [user_id, user_id, user_id]


async def assert_holds_upload(conn, doc_id: str, user_id: str) -> None:
    """404 unless the user holds a VERIFIED upload entry — they uploaded the
    bytes to this server, which is what sharing and filing into a project
    require. A pre-A1 upload entry (migration 012: unverified) is only a claim."""
    cur = await conn.execute(
        "SELECT 1 FROM library_entries WHERE doc_id = %s AND user_id = %s "
        "AND added_via = 'upload' AND verified", (doc_id, user_id))
    if await cur.fetchone() is None:
        raise refusal(404, "not_found", "Document not found")


async def can_manage_project_docs(conn, user_id: str, project_id) -> bool:
    """The one seam for "may remove a document from this project" (A1 §5).
    A1: the project owner. A0 swaps the body to "role >= maintainer"."""
    cur = await conn.execute(
        "SELECT 1 FROM projects WHERE id = %s AND owner_user_id = %s", (project_id, user_id))
    return await cur.fetchone() is not None


def visible_projects_where(alias: str = "p") -> str:
    """A `projects` row the user owns or is a member of. Bind with
    `visible_projects_params(user_id)`."""
    return (
        f"({alias}.owner_user_id = %s OR EXISTS (SELECT 1 FROM project_members _vpm "
        f"WHERE _vpm.project_id = {alias}.id AND _vpm.user_id = %s))"
    )


def visible_projects_params(user_id: str) -> list[str]:
    return [user_id, user_id]


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
