-- Document library: projects (grouping + primary sharing unit), per-doc grants
-- (exception path), and tags. Read access = owner OR project member OR grantee.
CREATE TABLE IF NOT EXISTS projects (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    description   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS project_members (
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id    UUID NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
    PRIMARY KEY (project_id, user_id)
);

CREATE TABLE IF NOT EXISTS doc_grants (
    doc_id          TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    grantee_user_id UUID NOT NULL REFERENCES users(id)         ON DELETE CASCADE,
    PRIMARY KEY (doc_id, grantee_user_id)
);

ALTER TABLE documents ADD COLUMN IF NOT EXISTS project_id UUID
    REFERENCES projects(id) ON DELETE SET NULL;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS documents_project_idx   ON documents(project_id);
CREATE INDEX IF NOT EXISTS documents_tags_gin      ON documents USING GIN (tags);
CREATE INDEX IF NOT EXISTS doc_grants_grantee_idx  ON doc_grants(grantee_user_id);
CREATE INDEX IF NOT EXISTS project_members_user_idx ON project_members(user_id);

INSERT INTO schema_migrations(version) VALUES (9) ON CONFLICT DO NOTHING;
