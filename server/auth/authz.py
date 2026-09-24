"""Row-ownership guards. Missing OR not-owned both raise 404 so a caller can't
distinguish 'exists but not yours' from 'does not exist'."""
from __future__ import annotations

from fastapi import HTTPException


async def _owner(conn, table: str, key_col: str, key: str) -> str | None:
    cur = await conn.execute(
        f"SELECT user_id FROM {table} WHERE {key_col} = %s", (key,)
    )
    row = await cur.fetchone()
    return str(row[0]) if row else None


async def assert_owns_doc(conn, doc_id: str, user_id: str) -> None:
    if await _owner(conn, "documents", "doc_id", doc_id) != user_id:
        raise HTTPException(status_code=404, detail="Document not found")


async def assert_owns_session(conn, session_id: str, user_id: str) -> None:
    if await _owner(conn, "chat_sessions", "id", session_id) != user_id:
        raise HTTPException(status_code=404, detail="Session not found")


def readable_docs_where(alias: str = "d") -> str:
    """The ONE definition of "can read this document": owner, grantee, owner of
    a linked project, or member of a linked project. `<alias>` is a `documents`
    row. Bind it with `readable_docs_params(user_id)` — never a hand-counted
    list. Subquery aliases are underscore-prefixed so they can't shadow a table
    alias in the caller's query. Access is resolved in SQL, never in Python."""
    return (
        f"({alias}.user_id = %s "
        f"OR EXISTS (SELECT 1 FROM doc_grants _rg "
        f"WHERE _rg.doc_id = {alias}.doc_id AND _rg.grantee_user_id = %s) "
        f"OR EXISTS (SELECT 1 FROM project_documents _rpd "
        f"JOIN projects _rp ON _rp.id = _rpd.project_id "
        f"WHERE _rpd.doc_id = {alias}.doc_id AND (_rp.owner_user_id = %s "
        f"OR EXISTS (SELECT 1 FROM project_members _rpm "
        f"WHERE _rpm.project_id = _rp.id AND _rpm.user_id = %s))))"
    )


def readable_docs_params(user_id: str) -> list[str]:
    """Exactly the parameters `readable_docs_where` needs, in order."""
    return [user_id, user_id, user_id, user_id]


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
