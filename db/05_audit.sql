-- ================================================================
-- Pharma Data Analyst Bot – Audit Log
-- ================================================================
-- Tracks every workflow run for governance & observability.
-- Runs after 04_chat_history.sql.
-- ================================================================

CREATE TABLE IF NOT EXISTS audit_log (
    id              SERIAL        PRIMARY KEY,
    request_id      TEXT          NOT NULL,
    user_id         INTEGER       NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    session_id      INTEGER       REFERENCES chat_session(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    mode            VARCHAR(20)   NOT NULL DEFAULT 'simple',
    tasks_count     INTEGER       NOT NULL DEFAULT 0,
    retries_used    INTEGER       NOT NULL DEFAULT 0,
    tables_used     JSONB         NOT NULL DEFAULT '[]'::jsonb,
    metrics_used    JSONB         NOT NULL DEFAULT '[]'::jsonb,
    timings_ms      JSONB         NOT NULL DEFAULT '{}'::jsonb,
    rows_returned   INTEGER       NOT NULL DEFAULT 0,
    success         BOOLEAN       NOT NULL DEFAULT false,
    error_message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_request_id ON audit_log (request_id);
CREATE INDEX IF NOT EXISTS idx_audit_user_id    ON audit_log (user_id);
CREATE INDEX IF NOT EXISTS idx_audit_session_id ON audit_log (session_id);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_log (created_at DESC);

COMMENT ON TABLE audit_log IS 'One row per workflow run – governance and observability.';
