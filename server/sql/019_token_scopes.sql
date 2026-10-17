-- Scoped personal access tokens. A token's scopes narrow what its owner's
-- account may do (server/auth/scopes.py); they never add to it. NULL means
-- full access: every token made before this migration keeps working as it did.
-- Down: ALTER TABLE personal_access_tokens DROP COLUMN scopes;
ALTER TABLE personal_access_tokens
    ADD COLUMN IF NOT EXISTS scopes TEXT[]
    CHECK (scopes IS NULL OR (
        cardinality(scopes) > 0
        AND scopes <@ ARRAY['read_aloud', 'documents', 'chat', 'admin']::TEXT[]
    ));

INSERT INTO schema_migrations(version) VALUES (19) ON CONFLICT DO NOTHING;
