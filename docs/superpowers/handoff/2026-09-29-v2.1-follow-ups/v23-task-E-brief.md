### Task E: Sub-page chunks and the embedding model's own prefixes

**Change:**
- **Sub-page chunks.** After extraction, any chunk longer than `CHUNK_MAX_CHARS` (default 1,200) is split on sentence boundaries with `CHUNK_OVERLAP_CHARS` (default 200). Each part keeps its page and type. The reader-matching extraction and its fixtures stay unchanged, because splitting is a separate step. The same applies to docling pages.
- **Prefixes.** Embeddings use `EMBEDDING_DOCUMENT_PREFIX` and `EMBEDDING_QUERY_PREFIX`. When unset, they default to `search_document: ` and `search_query: ` for `nomic-embed-text*`, and to empty for any other model.
- **The embedding profile.**
  - `documents.embedding_profile` (migration 014) records `model | prefixes | chunker version`.
  - An indexed document whose profile differs from the current one is rebuilt in the background on first use (pre-fetch or tool). The rebuild builds and embeds the new chunks, then swaps them in one transaction. State stays `indexed` and the old set stays searchable until the swap (Review focus 6). One rebuild runs at a time, under the existing per-document lock.
  - A document whose **embedding model** differs is not searched; the tool says it's being re-indexed. Vectors from two models can't be compared.
  - Legacy documents without stored bytes keep their old chunks.

**Tests:**
- the split (sizes, overlap, pages kept);
- the prefixes per model;
- a stale profile triggers one background rebuild while the old chunks answer;
- a model mismatch is refused;
- the fixtures are unchanged.


**Carried from the Task C review:** every split part after a page's first has chunk_type + extract.CONTINUATION_SUFFIX ("+cont") with an exact-character overlap; test join_chunks(split(page)) == page. **Plan deviation (deliberate):** migration 014 carries only embedding_profile; Task F's tsvector column gets its own migration (015) so the two tasks stay separable.
