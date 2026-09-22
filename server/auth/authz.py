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


async def assert_can_read_doc(conn, doc_id: str, user_id: str) -> None:
    """Read access = owner OR project member OR explicit grantee. Missing and
    not-readable are both 404 (no existence leak)."""
    cur = await conn.execute(
        """
        SELECT 1 FROM documents d
        WHERE d.doc_id = %s AND (
            d.user_id = %s
            OR EXISTS (SELECT 1 FROM project_members pm
                       WHERE pm.project_id = d.project_id AND pm.user_id = %s)
            OR EXISTS (SELECT 1 FROM doc_grants g
                       WHERE g.doc_id = d.doc_id AND g.grantee_user_id = %s)
        )
        """,
        (doc_id, user_id, user_id, user_id),
    )
    if await cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Document not found")


def readable_docs_where(alias: str = "d") -> str:
    """Reusable predicate for list/search: `<alias>` is a `documents` row and
    the caller passes the user id THREE times positionally after any earlier
    params. Keeps access resolution in SQL, never post-filtered in Python."""
    return (
        f"({alias}.user_id = %s "
        f"OR EXISTS (SELECT 1 FROM project_members pm "
        f"WHERE pm.project_id = {alias}.project_id AND pm.user_id = %s) "
        f"OR EXISTS (SELECT 1 FROM doc_grants g "
        f"WHERE g.doc_id = {alias}.doc_id AND g.grantee_user_id = %s))"
    )


CAN_READ_DOCS_SQL = readable_docs_where()
