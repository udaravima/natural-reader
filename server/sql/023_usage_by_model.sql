-- Admin console v2 phase 3: usage per model and kind. inference_usage had one row
-- per (user_id, day); it now has one per (user_id, day, model, kind), so the console
-- can group spend by model. `model` is the canonical provider:name; `kind` is 'chat'
-- or 'reader'. Rows written before this migration cannot be split, so they keep
-- model '' and kind '' ("Unknown" in the console) and still count toward each
-- user's budget for the day. `estimated_tokens` is the part of prompt+eval tokens
-- that was counted by the chars/4 estimate rather than reported by the provider.
-- The '' defaults exist only to backfill old rows; they are dropped afterwards so a
-- writer that forgets model or kind fails loudly instead of writing ''.
-- Down: DROP INDEX inference_usage_day_idx; then merge the rows of each (user_id, day)
--   into one, ALTER TABLE inference_usage DROP CONSTRAINT inference_usage_pkey,
--   DROP COLUMN model, kind, estimated_tokens (and the kind check), then
--   ADD PRIMARY KEY (user_id, day).
ALTER TABLE inference_usage ADD COLUMN IF NOT EXISTS model TEXT NOT NULL DEFAULT '';
ALTER TABLE inference_usage ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT '';
ALTER TABLE inference_usage ADD COLUMN IF NOT EXISTS estimated_tokens BIGINT NOT NULL DEFAULT 0;

ALTER TABLE inference_usage DROP CONSTRAINT IF EXISTS inference_usage_kind_check;
ALTER TABLE inference_usage ADD CONSTRAINT inference_usage_kind_check
    CHECK (kind IN ('', 'chat', 'reader'));

ALTER TABLE inference_usage DROP CONSTRAINT IF EXISTS inference_usage_pkey;
ALTER TABLE inference_usage ADD PRIMARY KEY (user_id, day, model, kind);

ALTER TABLE inference_usage ALTER COLUMN model DROP DEFAULT;
ALTER TABLE inference_usage ALTER COLUMN kind DROP DEFAULT;

-- The admin queries filter by day across all users.
CREATE INDEX IF NOT EXISTS inference_usage_day_idx ON inference_usage (day);

INSERT INTO schema_migrations(version) VALUES (23) ON CONFLICT DO NOTHING;
