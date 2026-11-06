-- Notifications for each person (spec docs/superpowers/specs/2026-11-06-notifications-design.md): an inbox, not a
-- popup. One row per notice per person. `level` is the kind's: 'action' (something stays stuck until a person does
-- something) or 'info'. Names are copied into `title`, as project events copy them, because the document may be
-- renamed or gone by the time the notice is read. `key` names an occurrence (index:<doc>, convert:<doc>,
-- rebuild:<doc>, reembed): a repeat while the person's notice is still unread updates that row (`repeats` + 1)
-- instead of adding one, which is what the partial unique index enforces. `resolved_at` is set when the problem
-- behind a needs-action notice went away by itself; the notice then sorts as information.
-- Cleanup (server/services/notifications.py): read ones 30 days after read_at, unread ones 90 days after created_at.
-- Down (manual; the runner never runs it). Safe at any time: the previous image has no notifications:
--   BEGIN;
--   DROP TABLE IF EXISTS notifications;
--   DELETE FROM schema_migrations WHERE version = 26;
--   COMMIT;

CREATE TABLE IF NOT EXISTS notifications (
    id           BIGSERIAL PRIMARY KEY,
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,
    level        TEXT NOT NULL CHECK (level IN ('action', 'info')),
    title        TEXT NOT NULL,
    body         TEXT,
    doc_id       TEXT REFERENCES documents(doc_id) ON DELETE SET NULL,
    target       JSONB,
    key          TEXT,
    repeats      INT NOT NULL DEFAULT 1,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    read_at      TIMESTAMPTZ,
    resolved_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS notifications_user_idx ON notifications (user_id, read_at, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS notifications_open_key_idx ON notifications (user_id, key)
    WHERE key IS NOT NULL AND read_at IS NULL;
CREATE INDEX IF NOT EXISTS notifications_key_idx ON notifications (key) WHERE key IS NOT NULL AND resolved_at IS NULL;

INSERT INTO schema_migrations(version) VALUES (26) ON CONFLICT DO NOTHING;
