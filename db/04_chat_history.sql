-- ================================================================
-- Pharma Data Analyst Bot – Chat History Tables
-- ================================================================
-- New chat_session / chat_message tables for user-based chat.
-- The old conversations / messages tables are left in place but
-- are no longer used by the application.
-- ================================================================

-- ── Chat Sessions ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS chat_session (
    id          SERIAL       PRIMARY KEY,
    user_id     INTEGER      NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    title       VARCHAR(120),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

COMMENT ON TABLE chat_session IS 'Per-user chat sessions (replaces old conversations table).';

-- ── Chat Messages ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS chat_message (
    id          SERIAL       PRIMARY KEY,
    session_id  INTEGER      NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
    role        VARCHAR(10)  NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT         NOT NULL,
    sql_query   TEXT,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

COMMENT ON TABLE chat_message IS 'Messages within a chat session.';

-- ── Indexes ──────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_chat_session_user
    ON chat_session (user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_chat_message_session
    ON chat_message (session_id, created_at ASC);
