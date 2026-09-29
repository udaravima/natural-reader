"""Semantic search over ONE document's chunks, shared by POST /v1/docs/{id}/search,
the chat's search_document tool and stage-0 prefetch (spec §5.2, §5.3), so the
three can never disagree about what "relevant" means."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..auth.authz import readable_docs_params, readable_docs_where

TEXT_CAP = 4000   # characters per returned chunk


@dataclass(frozen=True)
class ReadableDoc:
    doc_id: str
    name: str      # the reader's own name for it (their library entry), else the file name
    state: str
    page_count: int | None = None   # the reader's pages (set by extraction); None if unknown


async def readable_doc(conn, doc_id: str, user_id: str) -> ReadableDoc | None:
    """The document if `user_id` may read it (the one SQL definition in
    authz.readable_docs_where), else None. Never trusts the id alone."""
    cur = await conn.execute(
        "SELECT d.doc_id, COALESCE((SELECT e.file_name FROM library_entries e "
        "WHERE e.doc_id = d.doc_id AND e.user_id = %s), d.file_name), d.state, d.page_count "
        f"FROM documents d WHERE d.doc_id = %s AND {readable_docs_where('d')}",
        [user_id, doc_id, *readable_docs_params(user_id)])
    row = await cur.fetchone()
    return ReadableDoc(row[0], row[1] or "document", row[2], row[3]) if row else None


async def search_chunks(conn, doc_id: str, qvec: list[float], k: int) -> list[dict[str, Any]]:
    """Top-k chunks by cosine similarity (higher `score` = closer). Trap: the
    score is NOT a calibrated probability; a "good" value depends on the model."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, page, chunk_type, text,
                   1 - (embedding <=> %s::vector) AS score
            FROM doc_chunks
            WHERE doc_id = %s AND embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (qvec, doc_id, qvec, k),
        )
        rows = await cur.fetchall()
        cols = [d.name for d in cur.description]
    results = [dict(zip(cols, r)) for r in rows]
    for r in results:
        r["score"] = float(r["score"])
        if r.get("text") and len(r["text"]) > TEXT_CAP:
            r["text"] = r["text"][:TEXT_CAP] + " [truncated]"
    return results
