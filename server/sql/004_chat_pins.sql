-- Persist chat "pins": explicit document excerpts kept attached to a conversation.

ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS pins JSONB NOT NULL DEFAULT '[]'::jsonb;

INSERT INTO schema_migrations(version) VALUES (4) ON CONFLICT DO NOTHING;
