-- Inference gateway (sub-project E): per-user daily token accounting.
-- `day` carries no DEFAULT on purpose: callers compute the UTC date in
-- Python so the budget boundary never depends on the DB server timezone.
CREATE TABLE IF NOT EXISTS inference_usage (
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day           DATE NOT NULL,
    prompt_tokens BIGINT NOT NULL DEFAULT 0,
    eval_tokens   BIGINT NOT NULL DEFAULT 0,
    requests      BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);

-- NULL = deployment default (INFERENCE_DAILY_TOKEN_BUDGET); 0 = unlimited.
ALTER TABLE users ADD COLUMN IF NOT EXISTS inference_daily_token_budget BIGINT;

INSERT INTO schema_migrations(version) VALUES (7) ON CONFLICT DO NOTHING;
