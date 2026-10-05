-- v2.4 Task A2: deployment settings an admin edits in the admin console
-- (first: the assistant profile, key 'assistant_profile', value {"text": ...}).
-- A deployer's file or env var is the default; a row here overrides it, and
-- deleting the row restores the default.
CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    updated_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
