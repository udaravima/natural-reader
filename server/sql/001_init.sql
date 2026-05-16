-- Initial schema for Natural Reader's chat + doc-RAG backend.
--
-- Embedding dimension is hard-coded at 768 (nomic-embed-text). Swapping models
-- requires dropping the embedding column and re-indexing all docs — keep this
-- in mind before changing EMBEDDING_MODEL in the server config.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =============================================================================
-- Documents + chunks (used from PR 3+ onward; defined here so the schema is one
-- atomic migration and we don't need a v2 for the same feature.)
-- =============================================================================

CREATE TABLE IF NOT EXISTS documents (
    doc_id          TEXT PRIMARY KEY,
    file_name       TEXT NOT NULL,
    file_type       TEXT NOT NULL,
    size_bytes      BIGINT NOT NULL,
    page_count      INT,
    state           TEXT NOT NULL DEFAULT 'registered',
    embedding_model TEXT,
    embedding_dim   INT,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS doc_chunks (
    id              BIGSERIAL PRIMARY KEY,
    doc_id          TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    ord             INT NOT NULL,
    page            INT,
    chunk_type      TEXT,
    text            TEXT NOT NULL,
    text_hash       TEXT NOT NULL,
    embedding       vector(768),
    embedding_model TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (doc_id, text_hash)
);

CREATE INDEX IF NOT EXISTS doc_chunks_doc_idx ON doc_chunks(doc_id);
CREATE INDEX IF NOT EXISTS doc_chunks_embedding_idx
    ON doc_chunks USING hnsw (embedding vector_cosine_ops);

-- =============================================================================
-- Chat sessions, messages, and event log.
-- =============================================================================

CREATE TABLE IF NOT EXISTS chat_sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT 'New chat',
    model       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    thinking    TEXT,
    attachments JSONB NOT NULL DEFAULT '[]',
    doc_context JSONB,
    stats       JSONB,
    timestamp   BIGINT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chat_messages_session_idx
    ON chat_messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS chat_events (
    id          BIGSERIAL PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    message     TEXT NOT NULL DEFAULT '',
    ts          BIGINT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chat_events_session_idx
    ON chat_events(session_id, created_at);

INSERT INTO schema_migrations(version) VALUES (1) ON CONFLICT DO NOTHING;
