"""Semantic search over one document's chunks (`search_chunks`) or a whole project's
(`search_scope`, C2.1 spec 5.5), shared by POST /v1/docs/{id}/search, the chat's
search_documents tool and stage-0 prefetch (spec §5.2, §5.3), so they can never
disagree about what "relevant" means."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..auth.authz import readable_docs_params, readable_docs_where
from . import doc_versions

TEXT_CAP = 4000   # characters per returned chunk


@dataclass(frozen=True)
class ReadableDoc:
    doc_id: str
    name: str      # the reader's own name for it (their library entry), else the file name
    state: str
    page_count: int | None = None   # the reader's pages (set by extraction); None if unknown
    embedding_profile: str | None = None   # how its chunks were made (v2.3 Task E); None = before v2.3
    embedding_model: str | None = None     # the model its vectors came from
    version_note: dict | None = None  # V1 §7: an old version and nowhere current (old_version_of)


async def readable_doc(conn, doc_id: str, user_id: str) -> ReadableDoc | None:
    """The document if `user_id` may read it (the one SQL definition in
    authz.readable_docs_where), else None. Never trusts the id alone. Named
    as the Library names it (doc_versions.display_name_sql), and carrying a
    version note when it is an old version (V1 spec §7)."""
    cur = await conn.execute(
        f"SELECT d.doc_id, {doc_versions.display_name_sql('d', '_me')}, d.state, d.page_count, "
        "d.embedding_profile, d.embedding_model FROM documents d "
        "LEFT JOIN library_entries _me ON _me.doc_id = d.doc_id AND _me.user_id = %s "
        f"WHERE d.doc_id = %s AND {readable_docs_where('d')}",
        [*doc_versions.display_name_params(user_id), user_id, doc_id,
         *readable_docs_params(user_id)])
    row = await cur.fetchone()
    if not row:
        return None
    note = doc_versions.old_version_of(await doc_versions.versions_in(conn, doc_id, user_id))
    return ReadableDoc(row[0], row[1] or "document", *row[2:], version_note=note)


RRF_K = 60           # reciprocal-rank fusion's constant (Cormack et al., 2009)
FUSION_DEPTH = 20    # candidates taken from each list before fusing


async def search_chunks(conn, doc_id: str, qvec: list[float], k: int,
                        text: str | None = None) -> list[dict[str, Any]]:
    """Top-k chunks of one document: `search_scope` over [doc_id], unbalanced, with
    the rows as before (no `doc_id` key: the /search response is these rows).

    `score` is always the cosine similarity (higher = closer); trap: it is NOT
    a calibrated probability, a "good" value depends on the model. With `text`
    (v2.3 Task F) each row adds `by_words` and `word_rank` (1 = best word
    match, None = not found by words); see `search_scope`."""
    rows = await search_scope(conn, [doc_id], qvec, k, text, per_doc=None)
    for r in rows:
        r.pop("doc_id", None)
    return rows


async def search_scope(conn, doc_ids: list[str], qvec: list[float], k: int, text: str | None = None,
                       *, per_doc: int | None = None, depth: int = FUSION_DEPTH) -> list[dict[str, Any]]:
    """Top-k chunks over ONE OR MANY documents, ranked once (C2.1 spec 5.5). Rows
    are `search_chunks`'s plus `doc_id`. `score` is the cosine similarity.

    With `text`, the meaning ranking and a word ranking are fused by
    reciprocal-rank fusion, and each row adds `by_words` and `word_rank`. The
    word ranking is the chunks containing every word of `text` (the 'simple'
    full-text index: any language, labels like "4.2" kept). When NO chunk in
    the scope has every word, it is instead the chunks with at least half of
    the query's distinct words (rounded up), most words first, then ts_rank_cd
    (Meilisearch's word dropping): "install podman rootless rhel" still finds
    "install podman rootless". A text with no words leaves the meaning ranking.
    `depth` is how many candidates each list gives to the fusion (at least k).

    `per_doc` balances the fused ranking in Python (`_balance`): a document
    gives at most that many chunks until k are chosen, then skipped chunks
    fill any shortfall in rank order. None = no cap.

    Meaning is ranked exactly, not approximately: the HNSW index covers every
    document, and a plan that walks it filters doc_id only AFTER keeping
    ef_search (40) candidates library-wide, losing the scope's passages
    whenever other documents are closer. The MATERIALIZED CTE filters to the
    scope's (id, embedding) first. The word lists read doc_chunks directly, so
    the planner can use the GIN or doc_id index."""
    if not doc_ids:
        return []
    if not text:
        return _balance(await _meaning_only(conn, list(doc_ids), qvec, k, per_doc, depth), k, per_doc)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            WITH scope AS MATERIALIZED (
                SELECT id, embedding
                FROM doc_chunks
                WHERE doc_id = ANY(%(docs)s::text[]) AND embedding IS NOT NULL
            ), meaning AS (
                SELECT id, row_number() OVER (ORDER BY embedding <=> %(q)s::vector, id) AS r
                FROM scope
                ORDER BY embedding <=> %(q)s::vector, id
                LIMIT %(depth)s
            ), query_words AS (
                -- the query's distinct lexemes, each quoted and cast to a one-word tsquery (a
                -- ::tsquery cast takes the lexeme literally; to_tsquery would parse it again)
                SELECT plainto_tsquery('simple', %(text)s) AS all_q,
                       count(*) AS n,
                       string_agg(quoted, ' | ')::tsquery AS any_q,
                       array_agg(quoted::tsquery) AS each_q
                FROM (SELECT chr(39) || replace(replace(lexeme, chr(92), chr(92) || chr(92)),
                                                chr(39), chr(39) || chr(39)) || chr(39) AS quoted
                      FROM unnest(to_tsvector('simple', %(text)s))) AS lexemes
            ), exact AS (
                SELECT c.id, 0 AS matched, ts_rank_cd(c.text_search, qw.all_q) AS rk
                FROM doc_chunks c, query_words qw
                WHERE c.doc_id = ANY(%(docs)s::text[]) AND c.embedding IS NOT NULL
                  AND c.text_search @@ qw.all_q
                ORDER BY rk DESC, c.id
                LIMIT %(depth)s
            ), partial AS (
                SELECT p.id, p.matched, p.rk
                FROM (
                    SELECT c.id, qw.n, ts_rank_cd(c.text_search, qw.any_q) AS rk,
                           (SELECT count(*) FROM unnest(qw.each_q) AS one(q)
                            WHERE c.text_search @@ one.q) AS matched
                    FROM doc_chunks c, query_words qw
                    WHERE NOT EXISTS (SELECT 1 FROM exact)
                      AND qw.n >= 2
                      AND c.doc_id = ANY(%(docs)s::text[]) AND c.embedding IS NOT NULL
                      AND c.text_search @@ qw.any_q
                ) AS p
                WHERE p.matched * 2 >= p.n          -- at least ceil(n / 2) of the n words
                ORDER BY p.matched DESC, p.rk DESC, p.id
                LIMIT %(depth)s
            ), words AS (
                SELECT id, row_number() OVER (ORDER BY matched DESC, rk DESC, id) AS r
                FROM (SELECT id, matched, rk FROM exact
                      UNION ALL SELECT id, matched, rk FROM partial) AS found
            ), fused AS (
                SELECT id, sum(1.0 / (%(rrf)s + r)) AS rrf,
                       min(r) FILTER (WHERE src = 'w') AS word_rank
                FROM (SELECT id, r, 'm' AS src FROM meaning
                      UNION ALL SELECT id, r, 'w' FROM words) AS both_lists
                GROUP BY id
            )
            SELECT c.id, c.doc_id, c.page, c.chunk_type, c.text,
                   1 - (c.embedding <=> %(q)s::vector) AS score,
                   f.word_rank IS NOT NULL AS by_words, f.word_rank
            FROM fused f JOIN doc_chunks c ON c.id = f.id
            ORDER BY f.rrf DESC, c.id
            LIMIT %(limit)s
            """,
            {"docs": list(doc_ids), "q": qvec, "depth": max(k, depth), "rrf": RRF_K, "text": text,
             "limit": k if per_doc is None else None},   # balancing needs the whole fused list
        )
        return _balance(_rows(cur, await cur.fetchall()), k, per_doc)


def _balance(rows: list[dict[str, Any]], k: int, per_doc: int | None) -> list[dict[str, Any]]:
    """Walk the ranked rows and take one unless its document already gave `per_doc`;
    stop at k. If that ends short of k, the skipped rows fill in rank order. So a
    document that really holds every answer still fills the list when nothing else
    matches. `per_doc` None = no cap, just the first k."""
    if per_doc is None:
        return rows[:k]
    taken: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    given: dict[str, int] = {}
    for r in rows:
        if len(taken) == k:
            break
        if given.get(r["doc_id"], 0) < per_doc:
            given[r["doc_id"]] = given.get(r["doc_id"], 0) + 1
            taken.append(r)
        else:
            skipped.append(r)
    return taken + skipped[:k - len(taken)]


async def _meaning_only(conn, doc_ids: list[str], qvec: list[float], k: int,
                        per_doc: int | None, depth: int) -> list[dict[str, Any]]:
    # Balancing needs more candidates than k to choose among.
    want = k if per_doc is None else max(k, depth)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            WITH scope AS MATERIALIZED (
                SELECT id, embedding
                FROM doc_chunks
                WHERE doc_id = ANY(%s::text[]) AND embedding IS NOT NULL
            ), top AS (
                SELECT id, embedding <=> %s::vector AS distance
                FROM scope
                ORDER BY distance, id
                LIMIT %s
            )
            SELECT c.id, c.doc_id, c.page, c.chunk_type, c.text, 1 - top.distance AS score
            FROM top JOIN doc_chunks c ON c.id = top.id
            ORDER BY top.distance, c.id
            """,
            (doc_ids, qvec, want),
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
