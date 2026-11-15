-- The helper model, phase 1 (docs/superpowers/specs/2026-11-15-helper-model-design.md §4.3, §5.1).
-- Tags and a one-line description for each document, written by a small model the admin names.
-- * documents.gen_*: what the helper wrote for this content. Content is shared by every library and project
--   that holds the same file, so only the helper writes here (its text comes from the file alone).
-- * project_docs.tags / description: a project's own words for one of its documents, set by its Contributors.
--   NULL = show the generated ones of the current version. They never reach another project holding the file.
-- * doc_describe_jobs: documents waiting for the helper. Worked one at a time, only while no indexing or
--   conversion job waits (doc_index_jobs is empty), so the helper never slows indexing. Claims work as in
--   doc_index_jobs (SKIP LOCKED, a 10-minute lapse); a job is dropped after 2 attempts.
-- * inference_usage.kind gains 'helper', so helper calls are counted apart from chat and reader calls.
-- Down (manual; the runner never runs it). Generated tags and descriptions, and projects' own, are lost:
--   BEGIN;
--   DROP TABLE IF EXISTS doc_describe_jobs;
--   ALTER TABLE project_docs DROP COLUMN IF EXISTS tags, DROP COLUMN IF EXISTS description;
--   ALTER TABLE documents DROP COLUMN IF EXISTS gen_tags, DROP COLUMN IF EXISTS gen_description,
--       DROP COLUMN IF EXISTS gen_model, DROP COLUMN IF EXISTS gen_at;
--   DELETE FROM inference_usage WHERE kind = 'helper';
--   ALTER TABLE inference_usage DROP CONSTRAINT IF EXISTS inference_usage_kind_check;
--   ALTER TABLE inference_usage ADD CONSTRAINT inference_usage_kind_check CHECK (kind IN ('', 'chat', 'reader'));
--   DELETE FROM schema_migrations WHERE version = 28;
--   COMMIT;

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS gen_tags        TEXT[],
    ADD COLUMN IF NOT EXISTS gen_description TEXT CHECK (char_length(gen_description) <= 300),
    ADD COLUMN IF NOT EXISTS gen_model       TEXT,
    ADD COLUMN IF NOT EXISTS gen_at          TIMESTAMPTZ;

ALTER TABLE project_docs
    ADD COLUMN IF NOT EXISTS tags        TEXT[],
    ADD COLUMN IF NOT EXISTS description TEXT CHECK (char_length(description) <= 300);

CREATE TABLE IF NOT EXISTS doc_describe_jobs (
    doc_id        TEXT PRIMARY KEY REFERENCES documents(doc_id) ON DELETE CASCADE,
    requested_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    project_id    UUID REFERENCES projects(id) ON DELETE SET NULL,   -- whose tags the model is shown to reuse
    attempts      INT NOT NULL DEFAULT 0,
    claimed_by    TEXT,
    claimed_at    TIMESTAMPTZ,
    queued_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS doc_describe_jobs_order_idx ON doc_describe_jobs (queued_at, doc_id);

ALTER TABLE inference_usage DROP CONSTRAINT IF EXISTS inference_usage_kind_check;
ALTER TABLE inference_usage ADD CONSTRAINT inference_usage_kind_check
    CHECK (kind IN ('', 'chat', 'reader', 'helper'));

INSERT INTO schema_migrations(version) VALUES (28) ON CONFLICT DO NOTHING;
