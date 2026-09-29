"""Semantic search over ONE document's chunks, shared by POST /v1/docs/{id}/search,
the chat's search_documents tool and stage-0 prefetch (spec §5.2, §5.3), so the
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
    score is NOT a calibrated probability; a "good" value depends on the model.

    Exact, not approximate: the HNSW index covers every document, and a plan
    that walks it filters doc_id only AFTER keeping ef_search (40) candidates
    library-wide, losing this document's passages whenever other documents
    are closer. The MATERIALIZED CTE filters to the one document first, then
    ranks exactly. Only (id, embedding) is materialized; the text is joined
    back for the top k alone (review: 5,000 chunks, ~32 ms, nothing spilled)."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            WITH doc AS MATERIALIZED (
                SELECT id, embedding
                FROM doc_chunks
                WHERE doc_id = %s AND embedding IS NOT NULL
            ), top AS (
                SELECT id, embedding <=> %s::vector AS distance
                FROM doc
                ORDER BY distance
                LIMIT %s
            )
            SELECT c.id, c.page, c.chunk_type, c.text, 1 - top.distance AS score
            FROM top JOIN doc_chunks c ON c.id = top.id
            ORDER BY top.distance, c.id
            """,
            (doc_id, qvec, k),
        )
        rows = await cur.fetchall()
        cols = [d.name for d in cur.description]
    results = [dict(zip(cols, r)) for r in rows]
    for r in results:
        r["score"] = float(r["score"])
        if r.get("text") and len(r["text"]) > TEXT_CAP:
            r["text"] = r["text"][:TEXT_CAP] + " [truncated]"
    return results
