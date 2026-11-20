-- Phone lines (docs/superpowers/specs/2026-11-20-phone-lines-design.md, 5.5).
-- * users.kind: 'person' (everyone so far) or 'line' (the service user a phone line acts as; never signs in).
-- * phone_lines: one entry point (profile + project + model + voice + limits) and its service user.
-- * phone_line_clips: recorded lines in the line's voice, 8 kHz.
-- * phone_calls: one row per connection; the transcript is the chat in session_id.
-- Down (manual; the runner never runs it). Every line, clip and call row is lost, and line users are deleted:
--   BEGIN;
--   DROP TABLE IF EXISTS phone_calls;
--   DROP TABLE IF EXISTS phone_line_clips;
--   DROP TABLE IF EXISTS phone_lines;
--   DELETE FROM users WHERE kind = 'line';
--   ALTER TABLE users DROP COLUMN IF EXISTS kind;
--   DELETE FROM schema_migrations WHERE version = 33;
--   COMMIT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'person'
    CHECK (kind IN ('person', 'line'));

CREATE TABLE IF NOT EXISTS phone_lines (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),   -- also the AudioSocket call UUID
    name            TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60),
    enabled         BOOLEAN NOT NULL DEFAULT FALSE,
    profile_id      UUID REFERENCES assistant_profiles(id) ON DELETE SET NULL,  -- NULL: the default profile
    project_id      UUID REFERENCES projects(id) ON DELETE SET NULL,            -- NULL: can't be switched on
    model_id        TEXT NOT NULL,
    voice           JSONB NOT NULL,              -- {"engine": "kokoro"|"chirp"|"google", "voice": "...", "accent": "..."}
    language        TEXT NOT NULL DEFAULT 'en' CHECK (language = 'en'),   -- phase 2 widens this
    greeting        TEXT NOT NULL CHECK (char_length(greeting) BETWEEN 1 AND 400),
    max_call_s      INT NOT NULL DEFAULT 600 CHECK (max_call_s BETWEEN 60 AND 3600),
    max_calls       INT NOT NULL DEFAULT 2 CHECK (max_calls BETWEEN 1 AND 20),
    service_user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE RESTRICT,
    created_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS phone_line_clips (
    line_id     UUID NOT NULL REFERENCES phone_lines(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('greeting','busy','closed','goodbye','still','sorry','filler','checking')),
    ordinal     INT NOT NULL DEFAULT 0,
    text        TEXT NOT NULL,
    voice_key   TEXT NOT NULL,          -- engine:voice:accent the audio was made with; stale if the line's differs
    pcm         BYTEA NOT NULL,         -- 16-bit LE mono, 8 kHz
    rendered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (line_id, kind, ordinal)
);

CREATE TABLE IF NOT EXISTS phone_calls (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    line_id     UUID NOT NULL REFERENCES phone_lines(id) ON DELETE CASCADE,
    session_id  TEXT REFERENCES chat_sessions(id) ON DELETE SET NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at    TIMESTAMPTZ,
    end_reason  TEXT CHECK (end_reason IN ('caller','limit','idle','budget','busy','closed','error','shutdown')),
    turns       INT NOT NULL DEFAULT 0,
    stats       JSONB NOT NULL DEFAULT '[]'   -- one object per turn: the timings in 5.3
);
CREATE INDEX IF NOT EXISTS phone_calls_line_idx ON phone_calls (line_id, started_at DESC);
INSERT INTO schema_migrations(version) VALUES (33) ON CONFLICT DO NOTHING;
