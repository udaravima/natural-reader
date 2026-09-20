-- Capability-based access control. Realm roles (reader/chat/admin) are mirrored
-- here and rebuilt into the request Principal every call, so admin changes take
-- effect immediately. Backfill preserves existing access on upgrade.
ALTER TABLE users ADD COLUMN IF NOT EXISTS capabilities TEXT[] NOT NULL DEFAULT '{}';

UPDATE users SET capabilities='{admin,reader,chat}' WHERE role='admin';
UPDATE users SET capabilities='{reader,chat}'
  WHERE role='member' AND status='active';
UPDATE users SET capabilities='{}' WHERE status IN ('pending','disabled');

INSERT INTO schema_migrations(version) VALUES (8) ON CONFLICT DO NOTHING;
