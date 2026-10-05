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
    embedding_profile: str | None = None   # how its chunks were made (v2.3 Task E); None = before v2.3
    embedding_model: str | None = None     # the model its vectors came from


async def readable_doc(conn, doc_id: str, user_id: str) -> ReadableDoc | None:
    """The document if `user_id` may read it (the one SQL definition in
    authz.readable_docs_where), else None. Never trusts the id alone."""
    cur = await conn.execute(
        "SELECT d.doc_id, COALESCE((SELECT e.file_name FROM library_entries e "
        "WHERE e.doc_id = d.doc_id AND e.user_id = %s), d.file_name), d.state, d.page_count, "
        "d.embedding_profile, d.embedding_model "
        f"FROM documents d WHERE d.doc_id = %s AND {readable_docs_where('d')}",
        [user_id, doc_id, *readable_docs_params(user_id)])
    row = await cur.fetchone()
    return ReadableDoc(row[0], row[1] or "document", *row[2:]) if row else None


RRF_K = 60           # reciprocal-rank fusion's constant (Cormack et al., 2009)
FUSION_DEPTH = 20    # candidates taken from each list before fusing


async def search_chunks(conn, doc_id: str, qvec: list[float], k: int,
                        text: str | None = None) -> list[dict[str, Any]]:
    """Top-k chunks of one document. `score` is always the cosine similarity
    (higher = closer); trap: it is NOT a calibrated probability, a "good"
    value depends on the model.

    With `text` (v2.3 Task F), chunks containing every word of it (the
    'simple' full-text index: any language, labels like "4.2" kept) are
    fused with the meaning ranking by reciprocal-rank fusion, and each row
    adds `by_words` and `word_rank` (1 = best word match, None = not found by
    words). A text with no words, or with words no chunk has all of, leaves
    the meaning ranking. Without `text` the rows are as before.

    Meaning is ranked exactly, not approximately: the HNSW index covers every
    document, and a plan that walks it filters doc_id only AFTER keeping
    ef_search (40) candidates library-wide, losing this document's passages
    whenever other documents are closer. The MATERIALIZED CTE filters to the
    one document's (id, embedding) first. The word ranking reads doc_chunks
    directly, so the planner can use the GIN or doc_id index."""
    if not text:
        return await _meaning_only(conn, doc_id, qvec, k)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            WITH doc AS MATERIALIZED (
                SELECT id, embedding
                FROM doc_chunks
                WHERE doc_id = %(doc)s AND embedding IS NOT NULL
            ), meaning AS (
                SELECT id, row_number() OVER (ORDER BY embedding <=> %(q)s::vector, id) AS r
                FROM doc
                ORDER BY embedding <=> %(q)s::vector, id
                LIMIT %(depth)s
            ), words AS (
                SELECT id, row_number() OVER (ORDER BY ts_rank_cd(text_search, tsq) DESC, id) AS r
                FROM doc_chunks, plainto_tsquery('simple', %(text)s) AS tsq
                WHERE doc_id = %(doc)s AND embedding IS NOT NULL AND text_search @@ tsq
                ORDER BY r
                LIMIT %(depth)s
            ), fused AS (
                SELECT id, sum(1.0 / (%(rrf)s + r)) AS rrf,
                       min(r) FILTER (WHERE src = 'w') AS word_rank
                FROM (SELECT id, r, 'm' AS src FROM meaning
                      UNION ALL SELECT id, r, 'w' FROM words) AS both_lists
                GROUP BY id
            )
            SELECT c.id, c.page, c.chunk_type, c.text,
                   1 - (c.embedding <=> %(q)s::vector) AS score,
                   f.word_rank IS NOT NULL AS by_words, f.word_rank
            FROM fused f JOIN doc_chunks c ON c.id = f.id
            ORDER BY f.rrf DESC, c.id
            LIMIT %(k)s
            """,
            {"doc": doc_id, "q": qvec, "depth": max(k, FUSION_DEPTH), "k": k, "rrf": RRF_K, "text": text},
        )
        return _rows(cur, await cur.fetchall())


async def _meaning_only(conn, doc_id: str, qvec: list[float], k: int) -> list[dict[str, Any]]:
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
                ORDER BY distance, id
                LIMIT %s
            )
            SELECT c.id, c.page, c.chunk_type, c.text, 1 - top.distance AS score
            FROM top JOIN doc_chunks c ON c.id = top.id
            ORDER BY top.distance, c.id
            """,
            (doc_id, qvec, k),
        )
        return _rows(cur, await cur.fetchall())


def _rows(cur, rows) -> list[dict[str, Any]]:
    cols = [d.name for d in cur.description]
    results = [dict(zip(cols, r)) for r in rows]
    for r in results:
        r["score"] = float(r["score"])
        if r.get("text") and len(r["text"]) > TEXT_CAP:
            r["text"] = r["text"][:TEXT_CAP] + " [truncated]"
    return results
