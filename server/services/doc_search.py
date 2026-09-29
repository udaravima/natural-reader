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
    value depends on the model. `by_words` says the word search found it.

    With `text` (v2.3 Task F), chunks containing every word of it (the
    'simple' full-text index: any language, labels like "4.2" kept) are
    fused with the meaning ranking by reciprocal-rank fusion. A text with no
    words, or with words no chunk has all of, leaves the meaning ranking.

    Exact, not approximate: the HNSW index covers every document, and a plan
    that walks it filters doc_id only AFTER keeping ef_search (40) candidates
    library-wide, losing this document's passages whenever other documents
    are closer. The MATERIALIZED CTE filters to the one document first, then
    ranks exactly; the text is joined back for the top k alone (review: 5,000
    chunks, ~32 ms, nothing spilled)."""
    depth = max(k, FUSION_DEPTH) if text else k
    async with conn.cursor() as cur:
        await cur.execute(
            """
            WITH doc AS MATERIALIZED (
                SELECT id, embedding, text_search
                FROM doc_chunks
                WHERE doc_id = %(doc)s AND embedding IS NOT NULL
            ), meaning AS (
                SELECT id, row_number() OVER (ORDER BY embedding <=> %(q)s::vector, id) AS r
                FROM doc
                ORDER BY embedding <=> %(q)s::vector, id
                LIMIT %(depth)s
            ), words AS (
                SELECT id, row_number() OVER (ORDER BY ts_rank_cd(text_search, tsq) DESC, id) AS r
                FROM doc, plainto_tsquery('simple', %(text)s) AS tsq
                WHERE %(text)s IS NOT NULL AND text_search @@ tsq
                ORDER BY r
                LIMIT %(depth)s
            ), fused AS (
                SELECT id, sum(1.0 / (%(rrf)s + r)) AS rrf, bool_or(src = 'w') AS by_words
                FROM (SELECT id, r, 'm' AS src FROM meaning
                      UNION ALL SELECT id, r, 'w' FROM words) AS both_lists
                GROUP BY id
            )
            SELECT c.id, c.page, c.chunk_type, c.text,
                   1 - (c.embedding <=> %(q)s::vector) AS score, f.by_words
            FROM fused f JOIN doc_chunks c ON c.id = f.id
            ORDER BY f.rrf DESC, c.id
            LIMIT %(k)s
            """,
            {"doc": doc_id, "q": qvec, "depth": depth, "k": k, "rrf": RRF_K, "text": text},
        )
        rows = await cur.fetchall()
        cols = [d.name for d in cur.description]
    results = [dict(zip(cols, r)) for r in rows]
    for r in results:
        r["score"] = float(r["score"])
        if r.get("text") and len(r["text"]) > TEXT_CAP:
            r["text"] = r["text"][:TEXT_CAP] + " [truncated]"
    return results
