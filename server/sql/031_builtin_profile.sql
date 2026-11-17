-- The Librarian (docs/superpowers/specs/2026-11-17-character-profiles-design.md, "The Librarian").
-- * assistant_profiles.builtin: the app's own document assistant, always in the list. It has no text (a turn that
--   uses it gets the app's rules with its name in the first line), is always on, and there is at most one.
-- * This file changes the schema only. The row itself is added at start-up by profile_library.ensure_librarian,
--   so it is there on a fresh database, after this upgrade, and after a hand edit removes it.
-- Down (manual; the runner never runs it). The Librarian's row goes; chats that used it keep its name and use the
-- default from their next turn (if it was the default, make another profile the default first):
--   BEGIN;
--   DELETE FROM assistant_profiles WHERE builtin;
--   DROP INDEX IF EXISTS assistant_profiles_one_builtin;
--   ALTER TABLE assistant_profiles DROP CONSTRAINT IF EXISTS assistant_profiles_builtin_on,
--       DROP CONSTRAINT IF EXISTS assistant_profiles_text_check;
--   ALTER TABLE assistant_profiles ADD CONSTRAINT assistant_profiles_text_check
--       CHECK (char_length(text) BETWEEN 1 AND 8000);
--   ALTER TABLE assistant_profiles DROP COLUMN IF EXISTS builtin;
--   DELETE FROM schema_migrations WHERE version = 31;
--   COMMIT;
ALTER TABLE assistant_profiles ADD COLUMN IF NOT EXISTS builtin BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE assistant_profiles DROP CONSTRAINT IF EXISTS assistant_profiles_text_check;
ALTER TABLE assistant_profiles ADD CONSTRAINT assistant_profiles_text_check
    CHECK (char_length(text) <= 8000 AND (builtin OR char_length(text) >= 1));
ALTER TABLE assistant_profiles DROP CONSTRAINT IF EXISTS assistant_profiles_builtin_on;
ALTER TABLE assistant_profiles ADD CONSTRAINT assistant_profiles_builtin_on CHECK (enabled OR NOT builtin);
CREATE UNIQUE INDEX IF NOT EXISTS assistant_profiles_one_builtin ON assistant_profiles ((TRUE)) WHERE builtin;
INSERT INTO schema_migrations(version) VALUES (31) ON CONFLICT DO NOTHING;
