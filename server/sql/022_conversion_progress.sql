-- How far a Docling conversion has got, for the Convert button: the latest of
-- docling's progress reports while documents.conversion_state = 'converting',
-- e.g. {"phase": "build", "done": 3, "total": 40}, or {"phase": "queued"}
-- before docling starts; NULL otherwise
-- (server/services/docling_convert.py progress_from_event).
-- Down: ALTER TABLE documents DROP COLUMN conversion_progress;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS conversion_progress JSONB;
INSERT INTO schema_migrations(version) VALUES (22) ON CONFLICT DO NOTHING;
