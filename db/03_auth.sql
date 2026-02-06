-- ================================================================
-- Pharma Data Analyst Bot – Auth Tables
-- ================================================================
-- Adds app_user and user_session for server-side session auth.
-- Runs after 02_indexes.sql (alphabetical order).
-- ================================================================

-- ── App Users ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS app_user (
    id            SERIAL       PRIMARY KEY,
    email         VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    display_name  VARCHAR(120),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

COMMENT ON TABLE app_user IS 'Application users for authentication.';

-- ── Server-Side Sessions ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS user_session (
    id          VARCHAR(64)  PRIMARY KEY,   -- UUID token
    user_id     INTEGER      NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    expires_at  TIMESTAMPTZ  NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_session_user    ON user_session (user_id);
CREATE INDEX IF NOT EXISTS idx_session_expires ON user_session (expires_at);

COMMENT ON TABLE user_session IS 'Server-side sessions tied to httpOnly cookies.';

-- ── Seed: demo user ───────────────────────────────────────────
-- Password: demo123  (bcrypt, 12 rounds)
-- NOTE: credentials are documented in README only, never shown in UI.

INSERT INTO app_user (email, password_hash, display_name)
VALUES (
    'demo@example.com',
    '$2b$12$DkFzIWG2.Yhk7oqS3ZZ3Yu/pY6.EQh2fj1EpXxC3g7YaGZOD.GQL2',
    'Demo User'
) ON CONFLICT (email) DO NOTHING;
