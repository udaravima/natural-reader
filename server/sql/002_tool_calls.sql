-- PR 5: persist autonomous tool calls (search_document and future tools).
--
-- The JSONB shape mirrors what the frontend captures on the assistant message:
--   [{ "name": "search_document",
--      "arguments": { "query": "...", "k": 5 },
--      "result_summary": { "ok": true, "chunk_count": 4 } }, ...]
--
-- Result summaries are intentionally compact — the full retrieved chunk text
-- is already in the assistant's final answer, so we just keep enough metadata
-- for the disclosure UI (which tool, what args, how many hits).

ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS tool_calls JSONB;

INSERT INTO schema_migrations(version) VALUES (2) ON CONFLICT DO NOTHING;
