-- ================================================================
-- Pharma Data Analyst Bot – Schema (Star-ish Analytics Model)
-- ================================================================
-- Run order: 00_schema.sql → 01_seed.sql → 02_indexes.sql
-- Postgres 16+
-- ================================================================

-- ── Extensions ────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Drop existing objects (idempotent rebuild) ────────────────

DROP TABLE IF EXISTS fact_sales   CASCADE;
DROP TABLE IF EXISTS messages     CASCADE;
DROP TABLE IF EXISTS conversations CASCADE;
DROP TABLE IF EXISTS dim_time     CASCADE;
DROP TABLE IF EXISTS dim_product  CASCADE;
DROP TABLE IF EXISTS dim_territory CASCADE;

-- ================================================================
-- DIMENSION: dim_time
-- ================================================================
-- One row per calendar date.  Pre-computed rollup columns make
-- analytics queries simple without needing date_trunc / extract.
-- ================================================================

CREATE TABLE dim_time (
    date          DATE        PRIMARY KEY,
    year          SMALLINT    NOT NULL,
    quarter       SMALLINT    NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    month         SMALLINT    NOT NULL CHECK (month BETWEEN 1 AND 12),
    week          SMALLINT    NOT NULL,
    day_of_week   SMALLINT    NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    year_quarter  VARCHAR(7)  NOT NULL,   -- e.g. '2024-Q3'
    year_month    VARCHAR(7)  NOT NULL,   -- e.g. '2024-07'
    is_month_end  BOOLEAN     NOT NULL DEFAULT FALSE
);

COMMENT ON TABLE  dim_time IS 'Calendar dimension – one row per date, pre-computed rollups.';
COMMENT ON COLUMN dim_time.week IS 'ISO week number (1-53).';

-- ================================================================
-- DIMENSION: dim_product
-- ================================================================

CREATE TABLE dim_product (
    product_id       SERIAL      PRIMARY KEY,
    brand_name       VARCHAR(80) NOT NULL,
    generic_name     VARCHAR(120),
    company_name     VARCHAR(80) NOT NULL,
    therapeutic_area VARCHAR(60) NOT NULL,
    dosage_form      VARCHAR(40),
    launch_date      DATE,
    is_active        BOOLEAN     NOT NULL DEFAULT TRUE
);

COMMENT ON TABLE dim_product IS 'Product dimension – branded pharma products.';

-- ================================================================
-- DIMENSION: dim_territory
-- ================================================================

CREATE TABLE dim_territory (
    territory_id  SERIAL      PRIMARY KEY,
    region        VARCHAR(40) NOT NULL,
    district      VARCHAR(60) NOT NULL,
    state         VARCHAR(40) NOT NULL
);

COMMENT ON TABLE dim_territory IS 'Territory dimension – region / district / state hierarchy.';

-- ================================================================
-- FACT: fact_sales
-- ================================================================
-- Grain: one row per (week_start_date, product_id, territory_id).
-- Weekly aggregation keeps volume manageable while providing
-- enough granularity for trend analysis.
-- ================================================================

CREATE TABLE fact_sales (
    id             BIGSERIAL   PRIMARY KEY,
    date           DATE        NOT NULL REFERENCES dim_time(date),
    product_id     INTEGER     NOT NULL REFERENCES dim_product(product_id),
    territory_id   INTEGER     NOT NULL REFERENCES dim_territory(territory_id),
    net_sales_usd  NUMERIC(14,2) NOT NULL,
    units          INTEGER     NOT NULL,
    trx            INTEGER     NOT NULL,   -- total prescriptions
    nrx            INTEGER     NOT NULL,   -- new prescriptions
    CONSTRAINT uq_fact_grain UNIQUE (date, product_id, territory_id)
);

COMMENT ON TABLE  fact_sales IS 'Weekly pharma sales fact table.';
COMMENT ON COLUMN fact_sales.trx IS 'Total prescriptions dispensed.';
COMMENT ON COLUMN fact_sales.nrx IS 'New (first-time) prescriptions dispensed.';

-- ================================================================
-- APP TABLES: conversations & messages (chat history)
-- ================================================================

CREATE TABLE conversations (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE messages (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content         TEXT NOT NULL,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
