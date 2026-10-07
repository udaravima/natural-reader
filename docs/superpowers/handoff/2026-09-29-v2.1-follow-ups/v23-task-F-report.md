# v2.3 Task F report: exact words alongside meaning (hybrid search)

## Change
- **Migration `015_chunk_text_search.sql`.**
  - `doc_chunks.text_search tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED`, plus a GIN index.
  - `'simple'` has no stemming and no stop words, so it works in any language. Checked: it keeps `4.2`, `3.1`, `mimic-iv` (plus `mimic` and `iv`) and `表4.2` as tokens.
  - An upgrade note warns that the column rewrites `doc_chunks` once.
- **`doc_search.search_chunks(conn, doc_id, qvec, k, text=None)`.**
  - Within the MATERIALIZED per-document CTE there are two rankings, each `FUSION_DEPTH` (20) deep:
    - meaning: cosine distance, ties broken by id;
    - words: `plainto_tsquery('simple', text)`, so AND of every word, ranked by `ts_rank_cd`.
  - They are fused by reciprocal-rank fusion: `Σ 1/(60 + rank)`, ordered by (rrf desc, id).
  - Each row keeps its cosine `score`, for the floor and the buckets, and gains `by_words`.
  - With no `text`, or a text with no words, it is the meaning ranking as before. Prefetch and `/search` don't pass `text`, so they stay meaning-only.
  - **AND semantics is deliberate.** The model's queries are short ("Table 4.2") and match precisely. A long natural question rarely has every word in one chunk, so it simply adds no word hits rather than noise from stop words.
- **`search_documents`.**
  - Passes `text=query`.
  - The floor keeps a `by_words` passage even below `CHAT_SEARCH_MIN_SCORE`.
  - A word-found passage is labelled `"match": "words"` below the floor and `"both"` at or above it. Meaning-only passages carry no `match`.
  - Order within one document comes from the fusion; a cosine sort only applies across documents (C2).
  - The description now says "by meaning … and by exact words … so put labels, names or numbers in it (Table 4.2, MIMIC-IV)". It is still under 700 characters and names no other tool.
- **Docs:** CHAT_WITH_PDF (the result shape and the floor), ARCHITECTURE, CHANGELOG (a Changed line and the migration 015 upgrade note).

## Tests
`server/tests/test_hybrid_search.py` (new, 7 tests). Written first: 6 failed before the implementation; the migration test passed once 015 existed.
- the generated column and its GIN index exist;
- "Table 4.2": a meaning-only top 3 misses page 3, while the fused search finds it with `by_words` and a cosine below 0.45;
- fusion puts the chunk both rankings found first;
- no `text` means meaning-only, as before;
- a text with no words means meaning-only;
- `search_documents` keeps a word match below the floor, labelled `words` (with relevance `weak`), labels `both` above it, and adds nothing for meaning-only;
- the description mentions exact words.

Test fakes of `search_chunks` accept the new `text` keyword.

## Suites
- backend: 749 passed.

## Fix round 1 — FIX_BASE eaa42c4 (Task E's fix round sits between; this round touches only Task F code)

- **I1: the description and guidance steered the model away from labels.**
  - The description now says the question "was already searched by meaning", and to "search with different words, or with a label, name or number from it or from earlier passages (Table 4.2, MIMIC-IV)".
  - "Unrelated passages are left out" became "passages far in meaning are left out unless they have the query's words".
  - The guidance now reads "by meaning and by exact words", says the prefetched passages were "found by meaning", and that a label, name or number "is matched exactly". It names no other tool, and the description stays under 700 characters.
  - Test: `test_the_description_and_guidance_say_it_matches_exact_words_too`, which failed before the fix.
- **M1: the GIN index was never used, and the CTE spilled.**
  - The words ranking now reads `doc_chunks` directly (`WHERE doc_id = … AND text_search @@ tsq`), so the planner can use the GIN or doc_id index. The MATERIALIZED CTE holds `(id, embedding)` again.
  - Calls without `text` go through `_meaning_only`, which is the pre-Task F query unchanged.
  - The docstring is corrected.
- **M2: the floor bypass was unbounded.** Only rows with `word_rank <= WORD_BYPASS_RANKS` (3) skip the floor. Test: a common word matching 6 weak chunks keeps exactly 3 word-only passages; it failed before the fix.
- **M3: the migration was untested against existing rows.** Test: drop the column and index, run 015's SQL twice; the existing rows are filled and the search finds the label.
- **M4.** The fusion-order test asserts `[8, 3, 5]`.
- **M5: `/search` gained a key.** Rows without `text` have no `by_words`, so `/search` returns the old shape. Test added.
- **M6.** The debug log reports `best_cosine` (the max), not the fused top row's score.
- **Docs.** CHAT_WITH_PDF describes the top-3 bypass.

Suites:
- backend: 754 passed.
