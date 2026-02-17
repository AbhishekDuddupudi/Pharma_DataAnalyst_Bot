-- ================================================================
-- Pharma Data Analyst Bot – Session Memory Columns
-- ================================================================
-- Adds 3 columns to chat_session for the 4-layer memory bundle:
--   summary        – rolling plain-text session summary
--   context_json   – structured context (metric/dims/filters/time/grain)
--   last_sql_intent – last SQL intent payload (semantic intent + tables)
--
-- The index on (user_id, updated_at DESC) already exists from
-- 04_chat_history.sql so we skip it here.
-- ================================================================

ALTER TABLE chat_session
    ADD COLUMN IF NOT EXISTS summary        TEXT   NULL,
    ADD COLUMN IF NOT EXISTS context_json   JSONB  NULL,
    ADD COLUMN IF NOT EXISTS last_sql_intent JSONB NULL;

COMMENT ON COLUMN chat_session.summary
    IS 'Rolling plain-text session summary (user goal, scope, findings).';
COMMENT ON COLUMN chat_session.context_json
    IS 'Structured context: metric, dimensions, filters, time_window, grain, last_entities, user_preferences.';
COMMENT ON COLUMN chat_session.last_sql_intent
    IS 'Last SQL intent payload: metric, dimensions, filters, tables_used, last_sql_tasks, result_stats.';
