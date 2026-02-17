-- ================================================================
-- Add dedicated artifact / assumption / follow-up columns
-- ================================================================
-- Split structured data out of the catch-all "metadata" blob so
-- the API can return them as top-level fields and the frontend
-- can render per-message tabs for historical conversations.
-- ================================================================

ALTER TABLE chat_message
    ADD COLUMN IF NOT EXISTS artifacts_json JSONB,
    ADD COLUMN IF NOT EXISTS assumptions   JSONB,
    ADD COLUMN IF NOT EXISTS followups     JSONB,
    ADD COLUMN IF NOT EXISTS metrics_json  JSONB;

COMMENT ON COLUMN chat_message.artifacts_json
    IS 'JSON: {sql_tasks: [...], tables: [...], chart: {...}}';
COMMENT ON COLUMN chat_message.assumptions
    IS 'JSON array of assumption strings';
COMMENT ON COLUMN chat_message.followups
    IS 'JSON array of follow-up question strings';
COMMENT ON COLUMN chat_message.metrics_json
    IS 'JSON: {total_ms, llm_ms, db_ms, rows_returned, ...}';
