-- Auth credentials: server-side sessions (SPA cookie) and personal access
-- tokens (extension + scripts). Both are high-entropy bearer secrets stored as
-- sha256 hashes — the raw value is shown once and lives only in the client.

CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,        -- sha256(cookie token); never the raw token
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    user_agent   TEXT
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id);

CREATE TABLE IF NOT EXISTS personal_access_tokens (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,    -- sha256(shown-once token)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    expires_at   TIMESTAMPTZ              -- NULL = no expiry
);
CREATE INDEX IF NOT EXISTS pat_user_idx ON personal_access_tokens(user_id);

INSERT INTO schema_migrations(version) VALUES (6) ON CONFLICT DO NOTHING;
