-- Many-to-many projects <-> documents. Replaces documents.project_id (009).
-- No added_by: only a doc's owner can link it, so it would always equal
-- documents.user_id. Add it if project owners ever get to add others' docs.
CREATE TABLE IF NOT EXISTS project_documents (
    project_id UUID NOT NULL REFERENCES projects(id)      ON DELETE CASCADE,
    doc_id     TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, doc_id)
);
-- PK serves project -> docs; this serves doc -> projects (the read predicate).
CREATE INDEX IF NOT EXISTS project_documents_doc_idx ON project_documents(doc_id);

-- Carry every existing single-project assignment across. ON CONFLICT can't
-- fire (one project per doc before this migration); kept as a statement of intent.
INSERT INTO project_documents (project_id, doc_id)
SELECT project_id, doc_id FROM documents WHERE project_id IS NOT NULL
ON CONFLICT DO NOTHING;

-- Same migration, so there is never a moment with two sources of truth.
DROP INDEX IF EXISTS documents_project_idx;
ALTER TABLE documents DROP COLUMN IF EXISTS project_id;

INSERT INTO schema_migrations(version) VALUES (10) ON CONFLICT DO NOTHING;
