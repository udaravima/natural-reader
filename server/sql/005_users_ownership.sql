-- Multi-user: local user records (mirroring OIDC identities) + per-user
-- ownership of documents and chat sessions.
--
-- The seed admin (fixed UUID) owns all pre-existing rows so nothing is orphaned
-- when a single-user install becomes multi-user. Its sentinel email may be
-- rewritten at startup from BOOTSTRAP_ADMIN_EMAIL so the real admin's first
-- OIDC login links to (and inherits) this row.

CREATE TABLE IF NOT EXISTS users (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    oidc_iss     TEXT,                    -- issuer; NULL for the seed row
    oidc_sub     TEXT,                    -- subject; NULL until first login links it
    email        TEXT UNIQUE NOT NULL,
    display_name TEXT,
    role         TEXT NOT NULL DEFAULT 'member'
                     CHECK (role IN ('admin','member')),
    status       TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('active','pending','disabled')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (oidc_iss, oidc_sub)           -- sub is unique per issuer
);

INSERT INTO users (id, email, role, status)
VALUES ('00000000-0000-0000-0000-000000000001', 'admin@localhost', 'admin', 'active')
ON CONFLICT (id) DO NOTHING;

-- Ownership columns (nullable during backfill, then NOT NULL).
ALTER TABLE documents     ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE CASCADE;

UPDATE documents     SET user_id = '00000000-0000-0000-0000-000000000001' WHERE user_id IS NULL;
UPDATE chat_sessions SET user_id = '00000000-0000-0000-0000-000000000001' WHERE user_id IS NULL;

ALTER TABLE documents     ALTER COLUMN user_id SET NOT NULL;
ALTER TABLE chat_sessions ALTER COLUMN user_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS documents_user_idx     ON documents(user_id);
CREATE INDEX IF NOT EXISTS chat_sessions_user_idx ON chat_sessions(user_id);

INSERT INTO schema_migrations(version) VALUES (5) ON CONFLICT DO NOTHING;
