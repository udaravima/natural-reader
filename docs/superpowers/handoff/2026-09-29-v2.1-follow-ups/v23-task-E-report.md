# v2.3 Task E report: sub-page chunks and the embedding model's own prefixes

## Change
- **Splitting (`extract.split_chunks`).** This is a separate step after the reader-matching extraction, which is unchanged, and so are its fixtures.
  - A chunk longer than `CHUNK_MAX_CHARS` (1,200) is cut:
    - after the last sentence end in the second half of the window;
    - else before the last whitespace;
    - else hard, for text with no spaces.
  - The next part starts at the earliest word start within the last `CHUNK_OVERLAP_CHARS` (200). If there is none, it starts exactly 200 characters back. So the overlap is 16-200 characters, and exact.
  - Parts keep their page and type; later parts are `type + "+cont"`. Ords are renumbered.
  - `chunk_settings(env)` validates: max 400-8,000; overlap from 16 to max/4.
  - `CHUNKER_VERSION = "split1:<max>:<overlap>"`.
- **`join_chunks`** caps its overlap search at `CHUNK_OVERLAP_CHARS`. In periodic text ("word word", "文字文字") the longest match would otherwise swallow real characters. A continuation with no overlap found joins with a space.
- **Prefixes.**
  - `InferenceConfig.embed_document_prefix` and `embed_query_prefix` come from `EMBEDDING_DOCUMENT_PREFIX` and `EMBEDDING_QUERY_PREFIX`. When unset, `nomic-embed-text*` gets its own prefixes and any other model gets none. Set but empty turns them off.
  - `embeddings.embed_query` and `embed_documents` add them.
  - Callers: prefetch, `search_documents` and `POST /search` use `embed_query`; `run_embed`, the legacy swap and the rebuild use `embed_documents`.
- **Profile.**
  - `embeddings.current_profile()` is `model|repr(doc prefix)|repr(query prefix)|CHUNKER_VERSION`.
  - Migration `014_embedding_profile.sql` adds `documents.embedding_profile`. It is additive, and NULL means before v2.3.
  - `run_embed` and the legacy swap record it.
  - `ReadableDoc` gains `embedding_profile` and `embedding_model`.
- **Rebuild (`doc_pipeline`).**
  - `ensure_current(doc)`: for an indexed document with another profile, it schedules one background `run_rebuild`. `_rebuilding` dedupes, and the tasks are held by strong references.
  - If the document's model differs from the current one, it returns the document with `state="reindexing"`. Document tools aren't offered, the prefetch skips it, and the rules say "being re-indexed… ask again shortly". Otherwise it returns the document as is, so the old chunks keep answering.
  - Called in `orchestrator._open_doc` and in `POST /v1/docs/{id}/search`, which answers 409 `{"error": "reindexing"}` on a model mismatch.
  - `run_rebuild` runs under a global `Semaphore(1)` plus the per-document lock. It re-reads the row and returns if the document is no longer indexed or is already current. The source is:
    - the converted pages;
    - else the stored file, when `extracted_by='server'` and its SHA matches (legacy `'client'` content is not made server-derived here);
    - else the chunks it has.

    It splits them and embeds everything first, then `replace_chunks` and the profile update in **one transaction**. Any failure, including a skipped embedding, keeps the old index.
- **All chunk writes split.** Native extraction (`run_pipeline`), converted pages (`chunks_from_pages`, now through `_page_chunks` and `replace_chunks`), and the legacy swap.
- **Docs:**
  - `.env.example`: prefixes, chunk sizes, and the profile behaviour;
  - CHAT_WITH_PDF: sub-page chunks and the profile;
  - ARCHITECTURE;
  - CHANGELOG: a Changed line and Unreleased upgrade notes (a one-time rebuild on first use, and a same-dimension model swap needs no manual re-index).
- **Deviation.** Migration 014 carries only the profile; Task F adds its own migration.

## Tests
- **`test_chunk_split.py` (new, 11 tests):**
  - a round trip `join_chunks(split(page)) == page` for prose, text with no sentence ends, CJK with no spaces, a Markdown table, and one very long word;
  - sizes;
  - the page kept;
  - `+cont` types;
  - overlap between 16 and 200 characters, with no whitespace at the edges;
  - prose parts end on a sentence;
  - short chunks unchanged and ords in order;
  - splitting is idempotent;
  - settings validation.

  Red first: the import failed, then the periodic cases failed until `join_chunks` was capped.
- **`test_embeddings_routing.py` (+7):** prefixes per model, set to empty, and explicit; query and document prompts carry their prefixes (respx); the profile changes with the prefix and the model. Red first.
- **`test_doc_rebuild.py` (new, 11 tests):**
  - a stale document is rebuilt with split, prefixed chunks; the old chunk is still present during every embedding call; the profile and model are recorded;
  - `ensure_current` schedules one rebuild for two triggers and keeps a same-model document searchable;
  - a model mismatch reads as "reindexing";
  - a current, non-indexed or missing document is left alone;
  - legacy content with no bytes is rebuilt from its own chunks;
  - a converted document is rebuilt from `doc_pages`;
  - a failed embedding keeps the old index;
  - a new document is indexed split, with the current profile;
  - a reindexing document gets no document tools and the rules say why, with no prefetch;
  - a turn on a mismatched document reads it as reindexing (mutation-checked: without `ensure_current` in `_open_doc` it fails);
  - the search route answers 409.
- **Test patches retargeted:** `embed_one` to `embed_query`, and `embed_batch` to `embed_documents` in `docs_harness`.

## Suites
- backend: 742 passed;
- vitest: exit 0;
- eslint: clean.

## Not verified here
There was no live Ollama run. That prefixes improve scores on nomic is its model card's claim; Task G's harness measures it. The relevance buckets and floor (Task B) were calibrated without prefixes and should be re-measured.
