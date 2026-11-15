-- Indexing and conversion jobs live in the database (bulk upload spec 2026-11-14, phase 1). Before this, each
-- upload, Index, re-extract and Convert started a background task in the worker that took the request: nothing
-- limited how many ran together, a restart forgot the ones waiting, and with WORKERS > 1 the per-process lock
-- let two workers work on one document.
-- A row is one document waiting for (or doing) one job: 'pipeline' (extract and embed), 'legacy_swap', or
-- 'convert' (Docling, with its `options`). One row per document, so an indexing job and a conversion of the
-- same document can't both exist. Workers claim rows with FOR UPDATE SKIP LOCKED; `claimed_by` names the worker,
-- `claimed_at` is its heartbeat (a claim older than 10 minutes is taken over), and `attempts` counts claims: a
-- job that keeps killing its worker is given up after 3. A finished or failed job's row is deleted.
-- Down (manual; the runner never runs it). Jobs still queued are lost: the previous image doesn't read this
-- table, and their documents stay 'stored' until someone presses Index, as before this migration:
--   BEGIN;
--   DROP TABLE IF EXISTS doc_index_jobs;
--   ALTER TABLE doc_rebuilds DROP COLUMN IF EXISTS stops;
--   DELETE FROM schema_migrations WHERE version = 27;
--   COMMIT;

CREATE TABLE IF NOT EXISTS doc_index_jobs (
    doc_id        TEXT PRIMARY KEY REFERENCES documents(doc_id) ON DELETE CASCADE,
    job           TEXT NOT NULL DEFAULT 'pipeline' CHECK (job IN ('pipeline', 'legacy_swap', 'convert')),
    options       JSONB,
    requested_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    state         TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'running')),
    attempts      INT NOT NULL DEFAULT 0,
    claimed_by    TEXT,
    claimed_at    TIMESTAMPTZ,
    queued_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS doc_index_jobs_order_idx ON doc_index_jobs (queued_at, doc_id);

-- The rebuild queue counts the times a rebuild's worker died while running it (its claim lapsed and was taken over),
-- apart from ordinary failures, which retry by themselves: a rebuild that stops the server is given up after 3.
ALTER TABLE doc_rebuilds ADD COLUMN IF NOT EXISTS stops INT NOT NULL DEFAULT 0;

INSERT INTO schema_migrations(version) VALUES (27) ON CONFLICT DO NOTHING;
