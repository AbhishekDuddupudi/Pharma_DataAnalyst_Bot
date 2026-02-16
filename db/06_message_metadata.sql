-- ================================================================
-- Add metadata JSONB column to chat_message
-- ================================================================
-- Stores artifacts (sql_tasks, tables, chart), assumptions,
-- follow-up questions per assistant message so that older chats
-- can render them identically to new ones.
-- ================================================================

ALTER TABLE chat_message
    ADD COLUMN IF NOT EXISTS metadata JSONB;

COMMENT ON COLUMN chat_message.metadata
    IS 'JSON blob: {sql_tasks, tables, chart, assumptions, follow_ups}';
