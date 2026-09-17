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
