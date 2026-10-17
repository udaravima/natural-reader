-- Profile pictures (Settings → Profile). One small raster image per person,
-- resized in the browser before upload; the server checks the bytes are a
-- PNG, JPEG or WebP and caps the size (server/services/avatars.py).
-- Down: DROP TABLE user_avatars;
CREATE TABLE IF NOT EXISTS user_avatars (
    user_id      UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    content_type TEXT NOT NULL CHECK (content_type IN ('image/png', 'image/jpeg', 'image/webp')),
    data         BYTEA NOT NULL CHECK (octet_length(data) BETWEEN 1 AND 262144),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO schema_migrations(version) VALUES (20) ON CONFLICT DO NOTHING;
