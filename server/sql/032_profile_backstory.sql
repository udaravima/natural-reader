-- A profile's backstory (docs/superpowers/specs/2026-11-17-character-profiles-design.md, "Backstory and rules").
-- * assistant_profiles.backstory: who the character is, sent after the house rules and before the profile's rules
--   under the label chat/profile_text.BACKSTORY_LABEL. Up to 6,000 characters; empty means none. The Librarian
--   (builtin) never has one.
-- Down (manual; the runner never runs it). Every backstory is lost:
--   BEGIN;
--   ALTER TABLE assistant_profiles DROP CONSTRAINT IF EXISTS assistant_profiles_builtin_plain;
--   ALTER TABLE assistant_profiles DROP COLUMN IF EXISTS backstory;
--   DELETE FROM schema_migrations WHERE version = 32;
--   COMMIT;
ALTER TABLE assistant_profiles ADD COLUMN IF NOT EXISTS backstory TEXT NOT NULL DEFAULT ''
    CHECK (char_length(backstory) <= 6000);
ALTER TABLE assistant_profiles DROP CONSTRAINT IF EXISTS assistant_profiles_builtin_plain;
ALTER TABLE assistant_profiles ADD CONSTRAINT assistant_profiles_builtin_plain CHECK (NOT builtin OR backstory = '');
INSERT INTO schema_migrations(version) VALUES (32) ON CONFLICT DO NOTHING;
