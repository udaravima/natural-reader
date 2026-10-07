-- v2.3 Task E: how a document's chunks were made and embedded:
-- "model|document prefix|query prefix|chunker". An indexed document whose
-- profile differs from the server's current one is rebuilt in the background
-- on first use; until then its old chunks stay searchable (same model) or
-- the document isn't searched (another model: vectors can't be compared).
-- NULL = indexed before v2.3 (whole-page chunks, no prefixes).
ALTER TABLE documents ADD COLUMN IF NOT EXISTS embedding_profile TEXT;

INSERT INTO schema_migrations(version) VALUES (14) ON CONFLICT DO NOTHING;
