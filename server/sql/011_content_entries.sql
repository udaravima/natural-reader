-- A1: document content vs library entries
-- (docs/superpowers/specs/2026-09-25-document-content-entries-design.md §6).
-- `documents` becomes content owned by nobody; `library_entries` records who
-- holds it; `project_documents` records which projects hold it. One
-- transaction (the runner wraps each file), so there is never a moment with
-- two sources of truth. Back up before upgrading: this drops doc_grants and
-- documents.user_id/tags after copying them.

ALTER TABLE documents ADD COLUMN IF NOT EXISTS extracted_by TEXT NOT NULL DEFAULT 'client'
    CHECK (extracted_by IN ('client', 'server'));
ALTER TABLE documents RENAME COLUMN pdf_path TO bytes_path;
UPDATE documents SET state = 'extracted' WHERE state = 'chunks_uploaded';

CREATE TABLE IF NOT EXISTS library_entries (
    user_id    UUID NOT NULL REFERENCES users(id)          ON DELETE CASCADE,
    doc_id     TEXT NOT NULL REFERENCES documents(doc_id)  ON DELETE CASCADE,
    file_name  TEXT,                     -- NULL = the content's canonical name
    tags       TEXT[] NOT NULL DEFAULT '{}',
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_via  TEXT NOT NULL CHECK (added_via IN ('upload', 'shared')),
    shared_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    PRIMARY KEY (user_id, doc_id)
);
-- PK serves "my library"; this serves "who holds this content" (GC, sole-holder).
CREATE INDEX IF NOT EXISTS library_entries_doc_idx ON library_entries(doc_id);

-- Owners proved possession (they uploaded): upload entries carrying their tags.
INSERT INTO library_entries (user_id, doc_id, tags, added_at, added_via)
SELECT user_id, doc_id, tags, created_at, 'upload' FROM documents
ON CONFLICT DO NOTHING;

-- Grants become shared entries. Only owners could grant (no grantor column),
-- so shared_by = owner loses nothing. A self-grant hits the owner's upload
-- entry and is dropped by ON CONFLICT, never downgrading it.
INSERT INTO library_entries (user_id, doc_id, added_via, shared_by)
SELECT g.grantee_user_id, g.doc_id, 'shared', d.user_id
FROM doc_grants g JOIN documents d ON d.doc_id = g.doc_id
ON CONFLICT DO NOTHING;

-- Placements now carry who filed them: different holders can file the same content.
ALTER TABLE project_documents
    ADD COLUMN IF NOT EXISTS added_by UUID REFERENCES users(id) ON DELETE SET NULL;
UPDATE project_documents pd SET added_by = d.user_id
FROM documents d WHERE d.doc_id = pd.doc_id AND pd.added_by IS NULL;

DROP TABLE IF EXISTS doc_grants;
DROP INDEX IF EXISTS documents_user_idx;
ALTER TABLE documents DROP COLUMN IF EXISTS user_id;
ALTER TABLE documents DROP COLUMN IF EXISTS tags;  -- drops documents_tags_gin with it

INSERT INTO schema_migrations(version) VALUES (11) ON CONFLICT DO NOTHING;
