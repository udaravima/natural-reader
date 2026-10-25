"""Semantic search over one document's chunks (`search_chunks`) or a whole project's
(`search_scope`, C2.1 spec 5.5), shared by POST /v1/docs/{id}/search, the chat's
search_documents tool and stage-0 prefetch (spec §5.2, §5.3), so they can never
disagree about what "relevant" means."""
from __future__ import annotations

from collections.abc import Collection
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
PARTIAL_WORDS = 12   # at most this many (the rarest) of a query's words count in partial word matching
QUERY_LEXEMES = 64   # a query's first this-many distinct words are looked at: each costs a frequency lookup
PARTIAL_WEIGHT = 0.1   # a partial word match counts this much in the fusion (spec 5.5 point 3); meaning and all-words count 1
PARTIAL_POOL = 4     # ts_rank_cd breaks ties among this many x depth candidates (it is costly)
COMMON_SAMPLE = 1000   # chunks sampled to spot words in over 60% of the scope without a lookup each


async def search_chunks(conn, doc_id: str, qvec: list[float], k: int,
                        text: str | None = None) -> list[dict[str, Any]]:
    """Top-k chunks of one document: `search_scope` over [doc_id], unbalanced, with
    the rows as before (no `doc_id` key: the /search response is these rows).

    `score` is always the cosine similarity (higher = closer); trap: it is NOT
    a calibrated probability, a "good" value depends on the model. With `text`
    (v2.3 Task F) each row adds `by_words`, `word_rank` (1 = best word match,
    None = not found by words) and `word_match` ('all' words, 'partial', or
    None); see `search_scope`."""
    rows = await search_scope(conn, [doc_id], qvec, k, text, per_doc=None)
    for r in rows:
        r.pop("doc_id", None)
    return rows


async def search_scope(conn, doc_ids: list[str], qvec: list[float], k: int, text: str | None = None,
                       *, per_doc: int | None = None, depth: int = FUSION_DEPTH,
                       exclude: Collection[int] = ()) -> list[dict[str, Any]]:
    """Top-k chunks over ONE OR MANY documents, ranked once (C2.1 spec 5.5). Rows
    are `search_chunks`'s plus `doc_id`. `score` is the cosine similarity.

    With `text`, the meaning ranking and a word ranking are fused by
    reciprocal-rank fusion, and each row adds `by_words`, `word_rank` and
    `word_match`. The word ranking is the chunks containing every word of `text`
    (the 'simple' full-text index: any language, labels like "4.2" kept):
    `word_match` 'all'. When NO chunk in the scope has every word, it is
    instead the chunks with at least half (rounded up) of the query's
    INFORMATIVE words, most words first, then ts_rank_cd (Meilisearch's word
    dropping): `word_match` 'partial', and "install podman rootless rhel" still
    finds "install podman rootless". A word is informative when some chunk of
    the scope has it but at most half of them do, so "what is the clause 4.2"
    is about "clause" and "4.2", not about the words every sentence has (nor
    about a word nobody has); at most the PARTIAL_WORDS rarest count. No
    informative word, no partial list. A row found by meaning only has
    `word_match` None; `by_words` is true for both kinds. A partial match is
    weaker evidence than meaning for a sentence-like query (a few common-ish
    words together in unrelated chunks), so its words-list term counts
    PARTIAL_WEIGHT (a tenth) in the fusion. Measured on the project eval: at
    a half, partial rows that were also in the meaning list still took the
    first places from the best chunk by meaning (the term, about 0.008, was
    worth more than the gap between meaning ranks 1 and 20); at a tenth a
    partial row must be within about the 8 best by meaning to lead, and one
    found by both lists near the top of meaning still does.
    A text with no words leaves the meaning ranking.
    `depth` is how many candidates each list gives to the fusion (at least k).

    `per_doc` balances the fused ranking in Python (`_balance`): a document
    gives at most that many chunks until k are chosen, then skipped chunks
    fill any shortfall in rank order. None = no cap.

    `exclude` is the chunk ids the caller already has (the chat tool passes
    what it showed this turn). They are set aside BEFORE balancing: they use
    up neither k nor a document's `per_doc` allowance, so a follow-up search
    still reaches a document's deeper passages. k counts rows NOT excluded; the
    excluded rows that rank among them come back in their rank place (so the
    caller can still say "you already have page 3"), and the search looks
    k + len(exclude) deep so they cannot crowd the new rows out.

    Meaning is ranked exactly, not approximately: the HNSW index covers every
    document, and a plan that walks it filters doc_id only AFTER keeping
    ef_search (40) candidates library-wide, losing the scope's passages
    whenever other documents are closer. The MATERIALIZED CTE filters to the
    scope's (id, embedding) first. The word lists read doc_chunks directly, so
    the planner can use the GIN or doc_id index.

    Cost of the partial list (measured on 10,000 chunks against spec 5.5's
    300 ms; see scripts/bench_project_search.py). It runs only when no chunk
    has every word. Telling which words are informative takes one GIN lookup
    per query word (about 3 ms each at 10,000 chunks), except words clearly in
    over three fifths of a COMMON_SAMPLE-chunk sample, which are out without
    one; a 32-word query of common words costs about 120 ms in all. `matched`
    is worked out once per candidate chunk. ts_rank_cd is NOT cheap: 462 ms for
    31 terms over 10,000 chunks, against 15 ms for matching them. So it only
    breaks ties among the first PARTIAL_POOL x depth candidates by matched
    count (ties beyond that pool fall to a hash of the id: id order would give every place to the document
    indexed first). A query's first QUERY_LEXEMES (64) distinct words are looked at, because each word is
    one such lookup (measured on 10,000 chunks, vacuumed, 50 documents x 200 chunks: a 200-word query took
    130 ms p50 uncapped and 66 ms capped; EVAL.md)."""
    # prepare=False below: psycopg would prepare this text after 5 runs, and PostgreSQL would then switch to a
    # generic plan that cannot see how many documents are in scope or how common the query words are.
    # Measured on 10,000 chunks: the generic plan was 40-100% slower; planning afresh costs about 1 ms.
    if not doc_ids:
        return []
    exclude = set(exclude)
    fetch = k + len(exclude)   # candidates to look at: the excluded ones are walked past
    if not text:
        return _balance(await _meaning_only(conn, list(doc_ids), qvec, fetch, per_doc, depth), k, per_doc, exclude)
    depth = max(fetch, depth)
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
            ), exact AS (
                SELECT c.id, ts_rank_cd(c.text_search, allq.q) AS rk
                FROM doc_chunks c, (SELECT plainto_tsquery('simple', %(text)s) AS q) AS allq
                WHERE c.doc_id = ANY(%(docs)s::text[]) AND c.embedding IS NOT NULL
                  AND c.text_search @@ allq.q
                ORDER BY rk DESC, c.id
                LIMIT %(depth)s
            ), lex AS (
                -- the query's distinct lexemes, quoted so a ::tsquery cast takes each literally
                -- (to_tsquery would parse "mimic-iv" again into a phrase)
                SELECT lexeme, chr(39) || replace(replace(lexeme, chr(92), chr(92) || chr(92)),
                                                  chr(39), chr(39) || chr(39)) || chr(39) AS quoted
                FROM unnest(to_tsvector('simple', %(text)s))
                ORDER BY positions[1], lexeme    -- the words as the query has them, the first QUERY_LEXEMES
                LIMIT %(lexemes)s
            ), total AS (
                SELECT count(*) AS n FROM scope
            ), sample AS MATERIALIZED (
                -- about COMMON_SAMPLE of the scope's chunks, spread by a hash of the id (the whole
                -- scope when it is small): only to spot words that are clearly in over half of them
                SELECT c.text_search
                FROM scope s JOIN doc_chunks c ON c.id = s.id, total
                WHERE NOT EXISTS (SELECT 1 FROM exact)
                  AND mod(hashint8(s.id), greatest(1, total.n / %(sample)s)) = 0
            ), sample_size AS (
                SELECT count(*) AS size FROM sample
            ), freq AS MATERIALIZED (
                -- how many of the scope's chunks hold each word (GIN index, then the doc filter),
                -- except a word in over three fifths of the sample, which is out whatever its exact count
                -- (more than half) and would cost a lookup touching most of the scope. Only when no
                -- chunk has every word. MATERIALIZED: df is read twice below; a plain CTE would count twice
                SELECT lex.lexeme, lex.quoted,
                       CASE WHEN seen.n * 10 > sample_size.size * 6 THEN NULL
                            ELSE (SELECT count(*) FROM doc_chunks c
                                  WHERE c.doc_id = ANY(%(docs)s::text[]) AND c.embedding IS NOT NULL
                                    AND c.text_search @@ lex.quoted::tsquery) END AS df
                FROM lex, sample_size
                CROSS JOIN LATERAL (SELECT count(*) AS n FROM sample
                                    WHERE sample.text_search @@ lex.quoted::tsquery) AS seen
                WHERE NOT EXISTS (SELECT 1 FROM exact)
            ), rare AS (
                SELECT freq.quoted
                FROM freq, total
                WHERE freq.df >= 1                        -- a word no chunk has says nothing about which chunk fits
                  AND freq.df * 2 <= total.n              -- and one in at most half of the scope's chunks
                ORDER BY freq.df, freq.lexeme
                LIMIT %(rare)s
            ), informative AS (
                SELECT count(*) AS m,
                       string_agg(quoted, ' | ')::tsquery AS any_q,
                       array_agg(quoted::tsquery) AS each_q
                FROM rare
            ), pool AS (
                -- matched = how many informative words the chunk has, worked out once per candidate
                SELECT c.id, hit.matched
                FROM informative i, doc_chunks c
                CROSS JOIN LATERAL (SELECT count(*) AS matched
                                    FROM unnest(i.each_q) AS one(q) WHERE c.text_search @@ one.q) AS hit
                WHERE NOT EXISTS (SELECT 1 FROM exact)
                  AND c.doc_id = ANY(%(docs)s::text[]) AND c.embedding IS NOT NULL
                  AND c.text_search @@ i.any_q
                  AND hit.matched * 2 >= i.m            -- at least ceil(m / 2) of the m words
                ORDER BY hit.matched DESC, hashint8(c.id)   -- not id: it would favour the oldest documents
                LIMIT %(pool)s
            ), partial AS (
                SELECT pool.id, pool.matched, ts_rank_cd(c.text_search, i.any_q) AS rk,
                       hashint8(pool.id)::bigint AS tie
                FROM pool JOIN doc_chunks c ON c.id = pool.id, informative i
                ORDER BY pool.matched DESC, rk DESC, tie
                LIMIT %(depth)s
            ), words AS (
                SELECT id, kind, row_number() OVER (ORDER BY matched DESC, rk DESC, tie, id) AS r
                FROM (SELECT id, 0 AS matched, rk, id AS tie, 'all' AS kind FROM exact
                      UNION ALL SELECT id, matched, rk, tie, 'partial' FROM partial) AS found
            ), fused AS (
                SELECT id, sum(CASE WHEN kind = 'partial' THEN %(pweight)s::numeric ELSE 1.0 END
                               / (%(rrf)s + r)) AS rrf,
                       min(r) FILTER (WHERE src = 'w') AS word_rank,
                       min(kind) FILTER (WHERE src = 'w') AS word_match
                FROM (SELECT id, r, 'm' AS src, NULL::text AS kind FROM meaning
                      UNION ALL SELECT id, r, 'w', kind FROM words) AS both_lists
                GROUP BY id
            )
            SELECT c.id, c.doc_id, c.page, c.chunk_type, c.text,
                   1 - (c.embedding <=> %(q)s::vector) AS score,
                   f.word_rank IS NOT NULL AS by_words, f.word_rank, f.word_match
            FROM fused f JOIN doc_chunks c ON c.id = f.id
            ORDER BY f.rrf DESC, c.id
            LIMIT %(limit)s
            """,
            {"docs": list(doc_ids), "q": qvec, "depth": depth, "rrf": RRF_K, "text": text,
             "rare": PARTIAL_WORDS, "lexemes": QUERY_LEXEMES, "pweight": PARTIAL_WEIGHT, "pool": PARTIAL_POOL * depth, "sample": COMMON_SAMPLE,
             "limit": fetch if per_doc is None else None},   # balancing needs the whole fused list
            prepare=False,   # see the comment above
        )
        return _balance(_rows(cur, await cur.fetchall()), k, per_doc, exclude)


def _balance(rows: list[dict[str, Any]], k: int, per_doc: int | None,
             exclude: Collection[int] = ()) -> list[dict[str, Any]]:
    """Walk the ranked rows and take one unless its document already gave `per_doc`;
    stop at k. If that ends short of k, the skipped rows fill in rank order. So a
    document that really holds every answer still fills the list when nothing else
    matches. `per_doc` None = no cap, just the first k.

    Rows whose id is in `exclude` are walked past: they are neither counted toward k
    nor toward a document's allowance. The ones met before the k-th row stay in the
    result in their rank place (the caller tells them apart by id); with no
    `exclude` this is exactly the walk above."""
    taken: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    given: dict[str, int] = {}
    new = 0
    for r in rows:
        if new == k:
            break
        if r["id"] in exclude:
            taken.append(r)
        elif per_doc is None or given.get(r["doc_id"], 0) < per_doc:
            if per_doc is not None:
                given[r["doc_id"]] = given.get(r["doc_id"], 0) + 1
            taken.append(r)
            new += 1
        else:
            skipped.append(r)
    return taken + skipped[:k - new]


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
            (doc_ids, qvec, want), prepare=False,   # as in search_scope
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
