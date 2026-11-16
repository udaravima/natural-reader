-- Chat titles from the first exchange (docs/superpowers/specs/2026-11-15-helper-model-design.md §6).
-- * chat_sessions.title_source: who wrote the title. 'default' ("New chat"), 'cut' (the first message cut to 60
--  characters, written when the first turn creates the chat), 'model' (the helper model wrote it), 'person' (a
--  rename). The helper's title after the first reply is written only over 'default' or 'cut', so nothing a person
--  named is ever replaced. Chats already named at this upgrade count as named by a person.
-- * chat_sessions.title_pending_at: a title is being written, since then (server/chat/titles.py; the browser re-reads
--  the chat list while it is set, and gives up on it after 3 minutes, e.g. after a restart mid-call).
-- Down (manual; the runner never runs it). Who wrote each title is forgotten; the titles stay:
--   BEGIN;
--   ALTER TABLE chat_sessions DROP COLUMN IF EXISTS title_source, DROP COLUMN IF EXISTS title_pending_at;
--   DELETE FROM schema_migrations WHERE version = 29;
--   COMMIT;

ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS title_source TEXT NOT NULL DEFAULT 'default'
        CHECK (title_source IN ('default', 'cut', 'model', 'person')),
    ADD COLUMN IF NOT EXISTS title_pending_at TIMESTAMPTZ;

UPDATE chat_sessions SET title_source = 'person' WHERE title <> 'New chat' AND title_source = 'default';

INSERT INTO schema_migrations(version) VALUES (29) ON CONFLICT DO NOTHING;
