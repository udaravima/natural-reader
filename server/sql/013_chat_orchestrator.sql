-- C1: the server runs the chat turn (spec §5.5, §7.3, §7.4). Additive only.
--
-- chat_messages.status: 'streaming' while a turn writes it; 'complete',
-- 'aborted' (stop button, closed tab, or a worker that died mid-turn) or
-- 'error' when it ends. Every row that exists now is a finished message.
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'complete';
ALTER TABLE chat_messages DROP CONSTRAINT IF EXISTS chat_messages_status_check;
ALTER TABLE chat_messages ADD CONSTRAINT chat_messages_status_check
    CHECK (status IN ('streaming', 'complete', 'aborted', 'error'));
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS finish_reason TEXT;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS model TEXT;

-- One turn per session. active_turn_id is the streaming assistant message's
-- id; the running turn refreshes the heartbeat every 15 s. A claim whose
-- heartbeat is older than 60 s belongs to a dead worker and may be taken over.
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS active_turn_id TEXT;
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS active_turn_heartbeat_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS chat_sessions_active_turn_idx
    ON chat_sessions (active_turn_heartbeat_at) WHERE active_turn_id IS NOT NULL;

-- Attachment bytes, so a follow-up turn (and a reload) can send an earlier
-- image to the model again. Metadata stays in chat_messages.attachments.
-- Deleting the chat deletes them.
CREATE TABLE IF NOT EXISTS chat_attachments (
    id          BIGSERIAL PRIMARY KEY,
    message_id  TEXT NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    ordinal     INT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('image', 'audio')),
    mime        TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    size        INT NOT NULL,
    data        BYTEA NOT NULL,
    UNIQUE (message_id, ordinal)
);

INSERT INTO schema_migrations(version) VALUES (13) ON CONFLICT DO NOTHING;
