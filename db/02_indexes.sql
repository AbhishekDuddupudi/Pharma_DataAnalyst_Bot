-- ================================================================
-- Pharma Data Analyst Bot – Indexes for Analytics Queries
-- ================================================================
-- Created after seed data is loaded for faster bulk insert.
-- ================================================================

-- ── fact_sales: single-column indexes ─────────────────────────

CREATE INDEX IF NOT EXISTS idx_fact_date
    ON fact_sales (date);

CREATE INDEX IF NOT EXISTS idx_fact_product
    ON fact_sales (product_id);

CREATE INDEX IF NOT EXISTS idx_fact_territory
    ON fact_sales (territory_id);

-- ── fact_sales: composite indexes for common query patterns ───

-- "Sales for a product in a region over time"
CREATE INDEX IF NOT EXISTS idx_fact_product_territory_date
    ON fact_sales (product_id, territory_id, date);

-- "All products in a territory over time" (regional dashboards)
CREATE INDEX IF NOT EXISTS idx_fact_territory_date
    ON fact_sales (territory_id, date);

-- "Product trends over time" (QoQ, YoY analysis)
CREATE INDEX IF NOT EXISTS idx_fact_product_date
    ON fact_sales (product_id, date);

-- ── dim_product: lookup by company / therapeutic area ─────────

CREATE INDEX IF NOT EXISTS idx_product_company
    ON dim_product (company_name);

CREATE INDEX IF NOT EXISTS idx_product_therapy
    ON dim_product (therapeutic_area);

-- ── dim_territory: lookup by region ───────────────────────────

CREATE INDEX IF NOT EXISTS idx_territory_region
    ON dim_territory (region);

-- ── app tables ────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages (conversation_id, created_at);

-- ── Analyze tables for query planner ──────────────────────────

ANALYZE dim_time;
ANALYZE dim_product;
ANALYZE dim_territory;
ANALYZE fact_sales;
