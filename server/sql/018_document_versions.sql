-- V1 (spec docs/superpowers/specs/2026-10-12-document-versions-design.md §3, §8):
-- every placement becomes version 1 of its own project document. A project
-- document (project_docs) is "a document in a project"; each of its versions
-- is a project_documents row. One transaction (the runner wraps each file).
-- No data is dropped, but the primary key of project_documents changes and
-- there is no down step: take a pg_dump before upgrading.

CREATE TABLE IF NOT EXISTS project_docs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id   UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by   UUID REFERENCES users(id) ON DELETE SET NULL,
    -- The highest version number ever issued (spec §3.2): never reused.
    last_version INT NOT NULL CHECK (last_version >= 1),
    UNIQUE (id, project_id)
);
CREATE INDEX IF NOT EXISTS project_docs_project_idx ON project_docs(project_id);

ALTER TABLE project_documents
    ADD COLUMN IF NOT EXISTS project_doc_id UUID,
    ADD COLUMN IF NOT EXISTS version        INT,
    ADD COLUMN IF NOT EXISTS file_name      TEXT,
    ADD COLUMN IF NOT EXISTS note           TEXT CHECK (char_length(note) <= 500),
    ADD COLUMN IF NOT EXISTS origin         TEXT CHECK (origin IN ('upload', 'restore', 'merge')),
    ADD COLUMN IF NOT EXISTS origin_version INT,
    ADD COLUMN IF NOT EXISTS is_current     BOOLEAN;

-- One document per existing placement: v1, current, named as the project
-- shows it today (projects.py _placement_name): the filer's entry name, else
-- the canonical name. Nothing visibly renames during the upgrade.
UPDATE project_documents
   SET project_doc_id = gen_random_uuid(), version = 1, origin = 'upload', is_current = true
 WHERE project_doc_id IS NULL;
UPDATE project_documents pd
   SET file_name = COALESCE(
       (SELECT e.file_name FROM library_entries e
         WHERE e.user_id = pd.added_by AND e.doc_id = pd.doc_id),
       (SELECT d.file_name FROM documents d WHERE d.doc_id = pd.doc_id))
 WHERE pd.file_name IS NULL;
INSERT INTO project_docs (id, project_id, created_at, created_by, last_version)
SELECT project_doc_id, project_id, added_at, added_by, 1 FROM project_documents;

ALTER TABLE project_documents
    ALTER COLUMN project_doc_id SET NOT NULL,
    ALTER COLUMN version        SET NOT NULL,
    ALTER COLUMN file_name      SET NOT NULL,
    ALTER COLUMN origin         SET NOT NULL,
    ALTER COLUMN is_current     SET NOT NULL;

-- The old key (one row per content per project) is stricter than "one
-- CURRENT row per content per project", so these can't fail on existing data.
ALTER TABLE project_documents DROP CONSTRAINT IF EXISTS project_documents_pkey;
ALTER TABLE project_documents ADD CONSTRAINT project_documents_pkey
    PRIMARY KEY (project_doc_id, version);
ALTER TABLE project_documents ADD CONSTRAINT project_documents_project_doc_fkey
    FOREIGN KEY (project_doc_id, project_id)
    REFERENCES project_docs(id, project_id) ON DELETE CASCADE;
CREATE UNIQUE INDEX IF NOT EXISTS project_documents_one_current
    ON project_documents (project_doc_id) WHERE is_current;
-- The duplicate guard (spec §3.2): content is current in ≤ 1 document per project.
CREATE UNIQUE INDEX IF NOT EXISTS project_documents_current_content
    ON project_documents (project_id, doc_id) WHERE is_current;
-- project_documents_doc_idx (doc_id) stays: it serves the read predicate.

INSERT INTO schema_migrations(version) VALUES (18) ON CONFLICT DO NOTHING;
