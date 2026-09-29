-- v2.3 Task F: exact-word search alongside meaning (C1 spec §5 "keyword +
-- vector search"). The 'simple' configuration has no stemming and no stop
-- words, so it works in any language and keeps labels and numbers ("4.2",
-- "mimic-iv") as tokens. Adding a stored generated column rewrites
-- doc_chunks once; on a large library, expect this migration to take a while.
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS text_search tsvector
    GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED;
CREATE INDEX IF NOT EXISTS doc_chunks_text_search_idx ON doc_chunks USING gin (text_search);
