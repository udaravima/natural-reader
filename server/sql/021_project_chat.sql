-- Project chat (C2.1; spec docs/superpowers/specs/2026-10-18-project-chat-design.md).
--   chat_sessions.scope_project_id: the project a chat is scoped to, or NULL
--     for a chat that is not (every existing chat). Deleting the project
--     keeps the chat and just unscopes it.
--   doc_outlines: one cached outline per document (its headings or PDF
--     bookmarks, plus an opening excerpt), built once and shared by every
--     project the document sits in. Deleting the document deletes its outline.
-- Down: DROP TABLE doc_outlines; ALTER TABLE chat_sessions DROP COLUMN scope_project_id;
ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS scope_project_id UUID REFERENCES projects(id) ON DELETE SET NULL;
-- Deleting a project (the FK's SET NULL) looks up its chats by this column;
-- most chats are unscoped, so index only the scoped ones.
CREATE INDEX IF NOT EXISTS chat_sessions_scope_project_idx
    ON chat_sessions (scope_project_id) WHERE scope_project_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS doc_outlines (
    doc_id     TEXT PRIMARY KEY REFERENCES documents(doc_id) ON DELETE CASCADE,
    entries    JSONB NOT NULL DEFAULT '[]'::jsonb,
    opening    TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL CHECK (source IN ('headings', 'bookmarks', 'none')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations(version) VALUES (21) ON CONFLICT DO NOTHING;
