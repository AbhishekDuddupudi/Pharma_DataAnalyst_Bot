-- 07_memory.sql  –  4-layer memory bundle columns on chat_session
-- Run against pharma_db AFTER 04_chat_history.sql.

ALTER TABLE chat_session
    ADD COLUMN IF NOT EXISTS summary         TEXT    NULL,
    ADD COLUMN IF NOT EXISTS context_json    JSONB   NULL,
    ADD COLUMN IF NOT EXISTS last_sql_intent JSONB   NULL;

COMMENT ON COLUMN chat_session.summary
    IS 'Rolling 1-2 paragraph conversation summary (Option 2).';
COMMENT ON COLUMN chat_session.context_json
    IS 'Structured state: metric, dimensions, filters, time_window, grain (Option 3).';
COMMENT ON COLUMN chat_session.last_sql_intent
    IS 'Semantic intent of the last primary SQL task – anchor for follow-ups (Option 4).';
