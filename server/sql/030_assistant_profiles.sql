-- The profile library (docs/superpowers/specs/2026-11-16-profile-library-design.md).
-- * assistant_profiles: the admin's named profiles. One is the default while any exists (a partial unique index);
--   the default is always enabled. `id` is a profile's lasting identity: chats, and later agents, point at it.
-- * chat_sessions.profile_id: the profile the chat's last turn used. profile_name: its name then. A chat with a
--   name and no id used a profile that was since deleted; its next turn uses the default.
-- * The single profile an admin saved before this (app_settings 'assistant_profile') becomes one profile named
--   "Assistant", enabled and the default, with the same text, so every chat reads what it read before. House rules
--   (app_settings 'assistant_house_rules') start empty.
-- Down (manual; the runner never runs it). The default profile's text becomes the single profile again; the other
-- profiles, the house rules and each chat's choice are lost:
--   BEGIN;
--   INSERT INTO app_settings (key, value) SELECT 'assistant_profile', jsonb_build_object('text', text)
--       FROM assistant_profiles WHERE is_default ON CONFLICT (key) DO NOTHING;
--   DELETE FROM app_settings WHERE key = 'assistant_house_rules';
--   ALTER TABLE chat_sessions DROP COLUMN IF EXISTS profile_id, DROP COLUMN IF EXISTS profile_name;
--   DROP TABLE IF EXISTS assistant_profiles;
--   DELETE FROM schema_migrations WHERE version = 30;
--   COMMIT;

CREATE TABLE IF NOT EXISTS assistant_profiles (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60),
    description TEXT NOT NULL DEFAULT '' CHECK (char_length(description) <= 200),
    text        TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND 8000),
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    is_default  BOOLEAN NOT NULL DEFAULT FALSE,
    position    INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    CHECK (enabled OR NOT is_default)
);
CREATE UNIQUE INDEX IF NOT EXISTS assistant_profiles_name_key ON assistant_profiles (lower(name));
CREATE UNIQUE INDEX IF NOT EXISTS assistant_profiles_one_default ON assistant_profiles ((TRUE)) WHERE is_default;

ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS profile_id UUID REFERENCES assistant_profiles(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS profile_name TEXT;

INSERT INTO assistant_profiles (name, text, is_default, updated_by)
SELECT 'Assistant', left(value->>'text', 8000), TRUE, updated_by
FROM app_settings
WHERE key = 'assistant_profile' AND btrim(coalesce(value->>'text', '')) <> ''
  AND NOT EXISTS (SELECT 1 FROM assistant_profiles);
DELETE FROM app_settings WHERE key = 'assistant_profile';

INSERT INTO schema_migrations(version) VALUES (30) ON CONFLICT DO NOTHING;
