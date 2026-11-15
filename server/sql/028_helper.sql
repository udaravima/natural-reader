-- The helper model, phase 1 (docs/superpowers/specs/2026-11-15-helper-model-design.md §4.3, §5.1).
-- Tags and a one-line description for each document, written by a small model the admin names.
-- * project_documents.gen_* / library_entries.gen_*: what the helper wrote for ONE holding (a project's version of
--   a document, a person's library entry), with that holding's own tags in use as its vocabulary. Nothing is stored
--   on the content (documents), which every library and project holding the same file shares: what the helper was
--   shown in one project, and so may have copied into its answer, never shows in another.
-- * project_docs.tags / description: a project's own words for one of its documents, set by its Contributors.
--   NULL = show the generated ones of the current version. They never reach another project holding the file.
-- * doc_describe_jobs: holdings waiting for the helper; the content to read, and the project document or the
--   person whose entry it is. Worked one at a time, only while no indexing or conversion job waits
--   (doc_index_jobs is empty) and once the content is indexed, so the helper never slows indexing. Claims work as
--   in doc_index_jobs (SKIP LOCKED, a 10-minute lapse); a job is dropped after 2 attempts.
-- * inference_usage.kind gains 'helper', so helper calls are counted apart from chat and reader calls.
-- Down (manual; the runner never runs it). Generated tags and descriptions, and projects' own, are lost:
--   BEGIN;
--   DROP TABLE IF EXISTS doc_describe_jobs;
--   ALTER TABLE project_docs DROP COLUMN IF EXISTS tags, DROP COLUMN IF EXISTS description;
--   ALTER TABLE project_documents DROP COLUMN IF EXISTS gen_tags, DROP COLUMN IF EXISTS gen_description,
--       DROP COLUMN IF EXISTS gen_model, DROP COLUMN IF EXISTS gen_at;
--   ALTER TABLE library_entries DROP COLUMN IF EXISTS gen_tags, DROP COLUMN IF EXISTS gen_description,
--       DROP COLUMN IF EXISTS gen_model, DROP COLUMN IF EXISTS gen_at;
--   DELETE FROM inference_usage WHERE kind = 'helper';
--   ALTER TABLE inference_usage DROP CONSTRAINT IF EXISTS inference_usage_kind_check;
--   ALTER TABLE inference_usage ADD CONSTRAINT inference_usage_kind_check CHECK (kind IN ('', 'chat', 'reader'));
--   DELETE FROM schema_migrations WHERE version = 28;
--   COMMIT;

ALTER TABLE project_documents
    ADD COLUMN IF NOT EXISTS gen_tags        TEXT[],
    ADD COLUMN IF NOT EXISTS gen_description TEXT CHECK (char_length(gen_description) <= 300),
    ADD COLUMN IF NOT EXISTS gen_model       TEXT,
    ADD COLUMN IF NOT EXISTS gen_at          TIMESTAMPTZ;

ALTER TABLE library_entries
    ADD COLUMN IF NOT EXISTS gen_tags        TEXT[],
    ADD COLUMN IF NOT EXISTS gen_description TEXT CHECK (char_length(gen_description) <= 300),
    ADD COLUMN IF NOT EXISTS gen_model       TEXT,
    ADD COLUMN IF NOT EXISTS gen_at          TIMESTAMPTZ;

ALTER TABLE project_docs
    ADD COLUMN IF NOT EXISTS tags        TEXT[],
    ADD COLUMN IF NOT EXISTS description TEXT CHECK (char_length(description) <= 300);

CREATE TABLE IF NOT EXISTS doc_describe_jobs (
    id              BIGSERIAL PRIMARY KEY,
    doc_id          TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,   -- the content read
    project_doc_id  UUID REFERENCES project_docs(id) ON DELETE CASCADE,  -- for its version holding doc_id, or
    user_id         UUID,                                                -- for this person's entry for doc_id
    requested_by    UUID REFERENCES users(id) ON DELETE SET NULL,        -- charged for the call
    attempts        INT NOT NULL DEFAULT 0,
    claimed_by      TEXT,
    claimed_at      TIMESTAMPTZ,
    queued_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (user_id, doc_id) REFERENCES library_entries(user_id, doc_id) ON DELETE CASCADE,
    CHECK ((project_doc_id IS NULL) <> (user_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS doc_describe_jobs_version_idx
    ON doc_describe_jobs (project_doc_id, doc_id) WHERE project_doc_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS doc_describe_jobs_entry_idx
    ON doc_describe_jobs (user_id, doc_id) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS doc_describe_jobs_order_idx ON doc_describe_jobs (queued_at, id);

ALTER TABLE inference_usage DROP CONSTRAINT IF EXISTS inference_usage_kind_check;
ALTER TABLE inference_usage ADD CONSTRAINT inference_usage_kind_check
    CHECK (kind IN ('', 'chat', 'reader', 'helper'));

INSERT INTO schema_migrations(version) VALUES (28) ON CONFLICT DO NOTHING;
