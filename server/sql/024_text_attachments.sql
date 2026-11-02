-- Message box inputs: a chat message can carry a text file (kind 'text') beside
-- images and audio. The only schema change is the kind CHECK on chat_attachments.
-- Postgres named the unnamed constraint of 013 chat_attachments_kind_check.
-- Down (manual; the runner never runs it). It deletes the text attachments (the
-- previous image's CHECK would refuse them), restores the CHECK and forgets the
-- migration so the up can run again. A message that had a text attachment keeps
-- its metadata entry; the file's bytes are gone:
--   BEGIN;
--   DELETE FROM chat_attachments WHERE kind = 'text';
--   ALTER TABLE chat_attachments DROP CONSTRAINT IF EXISTS chat_attachments_kind_check;
--   ALTER TABLE chat_attachments ADD CONSTRAINT chat_attachments_kind_check
--     CHECK (kind IN ('image', 'audio'));
--   DELETE FROM schema_migrations WHERE version = 24;
--   COMMIT;
ALTER TABLE chat_attachments DROP CONSTRAINT IF EXISTS chat_attachments_kind_check;
ALTER TABLE chat_attachments ADD CONSTRAINT chat_attachments_kind_check
    CHECK (kind IN ('image', 'audio', 'text'));

INSERT INTO schema_migrations(version) VALUES (24) ON CONFLICT DO NOTHING;
