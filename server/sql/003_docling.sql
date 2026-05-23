-- PR 7: Docling integration — backend PDF→Markdown conversion.
--
-- Adds a parallel state machine on `documents` for the conversion lifecycle and
-- a `doc_pages` table that stores per-page Markdown. After a successful
-- conversion the existing indexing pipeline is re-run against `doc_pages` so
-- the chunks/embeddings reflect the docling-extracted text.
--
-- Conversion state values:
--   NULL                 — no conversion attempted
--   'converting'         — background job running
--   'converted'          — markdown stored in doc_pages
--   'conversion_failed'  — error captured in conversion_error
--
-- `conversion_options` shape: {"preset":"standard","ocr":false,"tables":true,
--                              "images":"drop","page_range":[1,20]}

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS pdf_path            TEXT,
    ADD COLUMN IF NOT EXISTS conversion_state    TEXT,
    ADD COLUMN IF NOT EXISTS conversion_options  JSONB,
    ADD COLUMN IF NOT EXISTS conversion_error    TEXT,
    ADD COLUMN IF NOT EXISTS converted_at        TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS doc_pages (
    id          BIGSERIAL PRIMARY KEY,
    doc_id      TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    page        INT NOT NULL,
    markdown    TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (doc_id, page)
);

CREATE INDEX IF NOT EXISTS doc_pages_doc_idx ON doc_pages(doc_id);

INSERT INTO schema_migrations(version) VALUES (3) ON CONFLICT DO NOTHING;
