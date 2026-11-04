-- Admin console v2 phase 2b: the per-document rebuild queue lives in the database. Before this, each worker
-- kept its own in-memory set of documents being rebuilt and of failed ones, so with WORKERS > 1 two workers
-- could rebuild one document, the Overview saw one worker's queue, and a restart lost the queue.
-- A row is a document waiting to be rebuilt under `profile` (model | prefixes | chunker). Workers claim rows
-- with FOR UPDATE SKIP LOCKED; `claimed_by` names the worker, `claimed_at` is its heartbeat (a claim older than
-- 10 minutes is taken over), and a failed row waits until `retry_after`.
-- Down (manual; the runner never runs it). Safe at any time: the previous image keeps its queue in memory and
-- rebuilds a stale document on its next use, so nothing is lost but the queue itself:
--   BEGIN;
--   DROP TABLE IF EXISTS doc_rebuilds;
--   DELETE FROM schema_migrations WHERE version = 25;
--   COMMIT;

CREATE TABLE IF NOT EXISTS doc_rebuilds (
    doc_id       TEXT PRIMARY KEY REFERENCES documents(doc_id) ON DELETE CASCADE,
    profile      TEXT NOT NULL,
    state        TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'running', 'failed')),
    attempts     INT NOT NULL DEFAULT 0,
    error        TEXT,
    claimed_by   TEXT,
    claimed_at   TIMESTAMPTZ,
    retry_after  TIMESTAMPTZ,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS doc_rebuilds_state_idx ON doc_rebuilds (state, updated_at);

INSERT INTO schema_migrations(version) VALUES (25) ON CONFLICT DO NOTHING;
