-- ================================================================
-- Pharma Data Analyst Bot – Seed Data
-- ================================================================
-- Deterministic, insight-capable pharma analytics dataset.
--
-- Companies:   4
-- Products:   12 (across 4 therapeutic areas)
-- Territories: 40 (8 regions × 5 states each)
-- Time span:   Jan 2023 – Dec 2024 (104 weeks)
-- Fact rows:  ~49,920  (104 weeks × 12 products × ~40 territories)
--
-- Engineered patterns:
--   1. Regional decline  – Cardivex (NovaCure) drops ~40% in Northeast
--      during 2024-Q3 due to a simulated supply disruption.
--   2. Steady grower     – Oncoshield (BioGenix) grows 3-4% QoQ.
--   3. Seasonality       – Respiratory products peak in Q4/Q1 (flu season).
--   4. Gaussian noise    – ±8-15% jitter on every row via a seeded hash.
-- ================================================================

-- ────────────────────────────────────────────────────────────────
-- 1. dim_time  (Jan 1 2023 → Dec 31 2024)
-- ────────────────────────────────────────────────────────────────

INSERT INTO dim_time (date, year, quarter, month, week, day_of_week,
                      year_quarter, year_month, is_month_end)
SELECT
    d::date,
    EXTRACT(YEAR   FROM d)::smallint,
    EXTRACT(QUARTER FROM d)::smallint,
    EXTRACT(MONTH  FROM d)::smallint,
    EXTRACT(WEEK   FROM d)::smallint,
    EXTRACT(DOW    FROM d)::smallint,
    TO_CHAR(d, 'YYYY') || '-Q' || EXTRACT(QUARTER FROM d)::text,
    TO_CHAR(d, 'YYYY-MM'),
    (d = (date_trunc('month', d) + INTERVAL '1 month - 1 day')::date)
FROM generate_series('2023-01-01'::date, '2024-12-31'::date, '1 day') AS d;


-- ────────────────────────────────────────────────────────────────
-- 2. dim_product  (4 companies, 12 products, 4 therapeutic areas)
-- ────────────────────────────────────────────────────────────────

INSERT INTO dim_product (brand_name, generic_name, company_name,
                         therapeutic_area, dosage_form, launch_date) VALUES
-- NovaCure Therapeutics – Cardiovascular
('Cardivex',    'amlodipine besylate',    'NovaCure Therapeutics', 'Cardiovascular',  'Tablet',     '2019-03-15'),
('Pressura',    'losartan potassium',     'NovaCure Therapeutics', 'Cardiovascular',  'Tablet',     '2020-06-01'),
('Lipovant',    'atorvastatin calcium',   'NovaCure Therapeutics', 'Cardiovascular',  'Tablet',     '2021-01-10'),

-- BioGenix Pharma – Oncology
('Oncoshield',  'pembrolizumab',          'BioGenix Pharma',      'Oncology',        'Injection',  '2022-04-20'),
('Tumorex',     'nivolumab',              'BioGenix Pharma',      'Oncology',        'Injection',  '2021-09-01'),
('Cellguard',   'trastuzumab',            'BioGenix Pharma',      'Oncology',        'Injection',  '2020-11-15'),

-- MedVista Labs – Respiratory
('Breatheasy',  'fluticasone propionate', 'MedVista Labs',        'Respiratory',     'Inhaler',    '2018-07-01'),
('Aeroclar',    'montelukast sodium',     'MedVista Labs',        'Respiratory',     'Tablet',     '2019-11-20'),
('Pulmovex',    'budesonide',             'MedVista Labs',        'Respiratory',     'Inhaler',    '2022-02-14'),

-- Zenith BioSciences – CNS / Neurology
('NeuroCalm',   'sertraline HCl',         'Zenith BioSciences',  'CNS',             'Capsule',    '2020-01-08'),
('Cognimax',    'donepezil HCl',          'Zenith BioSciences',  'CNS',             'Tablet',     '2021-05-25'),
('Anxiolyze',   'buspirone HCl',          'Zenith BioSciences',  'CNS',             'Tablet',     '2023-03-01');


-- ────────────────────────────────────────────────────────────────
-- 3. dim_territory  (8 regions × 5 states = 40 rows)
-- ────────────────────────────────────────────────────────────────

INSERT INTO dim_territory (region, district, state) VALUES
-- Northeast
('Northeast', 'NE-Metro',    'New York'),
('Northeast', 'NE-Metro',    'New Jersey'),
('Northeast', 'NE-Suburban', 'Connecticut'),
('Northeast', 'NE-Suburban', 'Massachusetts'),
('Northeast', 'NE-Rural',    'Pennsylvania'),

-- Southeast
('Southeast', 'SE-Metro',    'Florida'),
('Southeast', 'SE-Metro',    'Georgia'),
('Southeast', 'SE-Suburban', 'North Carolina'),
('Southeast', 'SE-Suburban', 'Virginia'),
('Southeast', 'SE-Rural',    'Tennessee'),

-- Midwest
('Midwest',   'MW-Metro',    'Illinois'),
('Midwest',   'MW-Metro',    'Ohio'),
('Midwest',   'MW-Suburban', 'Michigan'),
('Midwest',   'MW-Suburban', 'Indiana'),
('Midwest',   'MW-Rural',    'Wisconsin'),

-- Southwest
('Southwest', 'SW-Metro',    'Texas'),
('Southwest', 'SW-Metro',    'Arizona'),
('Southwest', 'SW-Suburban', 'Nevada'),
('Southwest', 'SW-Suburban', 'New Mexico'),
('Southwest', 'SW-Rural',    'Oklahoma'),

-- West
('West',      'WE-Metro',    'California'),
('West',      'WE-Metro',    'Washington'),
('West',      'WE-Suburban', 'Oregon'),
('West',      'WE-Suburban', 'Colorado'),
('West',      'WE-Rural',    'Utah'),

-- Mid-Atlantic
('Mid-Atlantic', 'MA-Metro',    'Maryland'),
('Mid-Atlantic', 'MA-Metro',    'Delaware'),
('Mid-Atlantic', 'MA-Suburban', 'DC'),
('Mid-Atlantic', 'MA-Suburban', 'West Virginia'),
('Mid-Atlantic', 'MA-Rural',    'Rhode Island'),

-- Mountain
('Mountain',  'MT-Metro',    'Montana'),
('Mountain',  'MT-Metro',    'Idaho'),
('Mountain',  'MT-Suburban', 'Wyoming'),
('Mountain',  'MT-Suburban', 'South Dakota'),
('Mountain',  'MT-Rural',    'North Dakota'),

-- Pacific Northwest
('Pacific NW', 'PNW-Metro',    'Alaska'),
('Pacific NW', 'PNW-Metro',    'Hawaii'),
('Pacific NW', 'PNW-Suburban', 'Nebraska'),
('Pacific NW', 'PNW-Suburban', 'Kansas'),
('Pacific NW', 'PNW-Rural',    'Iowa');


-- ────────────────────────────────────────────────────────────────
-- 4. fact_sales  (weekly grain, ~50k rows)
-- ────────────────────────────────────────────────────────────────
-- Uses a deterministic hash for noise so results are reproducible.
-- Key patterns are driven by product, territory, and date logic.
-- ────────────────────────────────────────────────────────────────

DO $$
DECLARE
    w_date        DATE;
    p_rec         RECORD;
    t_rec         RECORD;
    -- base metrics (per product-territory-week before modifiers)
    base_sales    NUMERIC;
    base_units    INTEGER;
    base_trx      INTEGER;
    base_nrx      INTEGER;
    -- modifiers
    seasonal_mod  NUMERIC;
    trend_mod     NUMERIC;
    regional_mod  NUMERIC;
    noise_mod     NUMERIC;
    -- final
    final_sales   NUMERIC;
    final_units   INTEGER;
    final_trx     INTEGER;
    final_nrx     INTEGER;
    -- helpers
    m             SMALLINT;  -- month
    q             SMALLINT;  -- quarter
    yr            SMALLINT;  -- year
    week_num      INTEGER;   -- sequential week number (0-based)
    hash_input    TEXT;
    hash_val      BIGINT;
BEGIN
    -- Iterate over every Monday in [2023-01-02 .. 2024-12-30]
    -- (first Monday of 2023 is Jan 2)
    FOR w_date IN
        SELECT d::date
        FROM generate_series('2023-01-02'::date, '2024-12-30'::date, '7 days') d
    LOOP
        yr := EXTRACT(YEAR FROM w_date)::smallint;
        m  := EXTRACT(MONTH FROM w_date)::smallint;
        q  := EXTRACT(QUARTER FROM w_date)::smallint;
        -- sequential week index (0 = first week)
        week_num := ((w_date - '2023-01-02'::date) / 7)::integer;

        FOR p_rec IN SELECT product_id, brand_name, company_name,
                            therapeutic_area FROM dim_product
        LOOP
            FOR t_rec IN SELECT territory_id, region FROM dim_territory
            LOOP
                -- ──────────────────────────────────────────────
                -- A. BASE VALUES (vary by therapeutic area size)
                -- ──────────────────────────────────────────────
                CASE p_rec.therapeutic_area
                    WHEN 'Cardiovascular' THEN
                        base_sales := 42000; base_units := 1200;
                        base_trx := 950;     base_nrx := 180;
                    WHEN 'Oncology' THEN
                        base_sales := 87000; base_units := 320;
                        base_trx := 280;     base_nrx := 65;
                    WHEN 'Respiratory' THEN
                        base_sales := 28000; base_units := 1600;
                        base_trx := 1300;    base_nrx := 310;
                    WHEN 'CNS' THEN
                        base_sales := 35000; base_units := 900;
                        base_trx := 750;     base_nrx := 140;
                    ELSE
                        base_sales := 30000; base_units := 800;
                        base_trx := 600;     base_nrx := 120;
                END CASE;

                -- Scale down per-product variation (lower ids = market leaders)
                -- Product 1,4,7,10 get 1.0x; 2,5,8,11 get 0.75x; 3,6,9,12 get 0.55x
                CASE ((p_rec.product_id - 1) % 3)
                    WHEN 0 THEN base_sales := base_sales * 1.00;
                    WHEN 1 THEN base_sales := base_sales * 0.75;
                    WHEN 2 THEN base_sales := base_sales * 0.55;
                END CASE;
                base_units := (base_units * (base_sales / GREATEST(
                    CASE p_rec.therapeutic_area
                        WHEN 'Cardiovascular' THEN 42000
                        WHEN 'Oncology'       THEN 87000
                        WHEN 'Respiratory'    THEN 28000
                        WHEN 'CNS'            THEN 35000
                        ELSE 30000
                    END, 1)))::integer;
                base_trx  := (base_trx * (base_sales / GREATEST(
                    CASE p_rec.therapeutic_area
                        WHEN 'Cardiovascular' THEN 42000
                        WHEN 'Oncology'       THEN 87000
                        WHEN 'Respiratory'    THEN 28000
                        WHEN 'CNS'            THEN 35000
                        ELSE 30000
                    END, 1)))::integer;
                base_nrx  := (base_nrx * (base_sales / GREATEST(
                    CASE p_rec.therapeutic_area
                        WHEN 'Cardiovascular' THEN 42000
                        WHEN 'Oncology'       THEN 87000
                        WHEN 'Respiratory'    THEN 28000
                        WHEN 'CNS'            THEN 35000
                        ELSE 30000
                    END, 1)))::integer;

                -- ──────────────────────────────────────────────
                -- B. SEASONAL MODIFIER
                -- ──────────────────────────────────────────────
                -- Respiratory peaks in winter (Nov-Feb), dips in summer
                IF p_rec.therapeutic_area = 'Respiratory' THEN
                    CASE
                        WHEN m IN (12, 1, 2)  THEN seasonal_mod := 1.35;
                        WHEN m IN (11, 3)     THEN seasonal_mod := 1.15;
                        WHEN m IN (6, 7, 8)   THEN seasonal_mod := 0.72;
                        ELSE                       seasonal_mod := 0.95;
                    END CASE;
                -- CNS slightly higher in winter (seasonal depression)
                ELSIF p_rec.therapeutic_area = 'CNS' THEN
                    CASE
                        WHEN m IN (11, 12, 1, 2) THEN seasonal_mod := 1.12;
                        WHEN m IN (6, 7)         THEN seasonal_mod := 0.90;
                        ELSE                          seasonal_mod := 1.0;
                    END CASE;
                ELSE
                    seasonal_mod := 1.0;
                END IF;

                -- ──────────────────────────────────────────────
                -- C. TREND MODIFIER (growth/decline over time)
                -- ──────────────────────────────────────────────
                -- Default: slight market growth ~1% per quarter
                trend_mod := 1.0 + (week_num::numeric / 104.0) * 0.08;

                -- PATTERN 1: Oncoshield (BioGenix) – strong steady growth
                -- ~3.5% per quarter → ~30% over 2 years
                IF p_rec.brand_name = 'Oncoshield' THEN
                    trend_mod := 1.0 + (week_num::numeric / 104.0) * 0.30;
                END IF;

                -- Cellguard – moderate growth (rising competitor)
                IF p_rec.brand_name = 'Cellguard' THEN
                    trend_mod := 1.0 + (week_num::numeric / 104.0) * 0.18;
                END IF;

                -- Anxiolyze – new launch Mar 2023, ramp-up curve
                IF p_rec.brand_name = 'Anxiolyze' THEN
                    IF w_date < '2023-03-01'::date THEN
                        trend_mod := 0.0;  -- not launched yet
                    ELSE
                        -- S-curve ramp from 0.15 → 1.0 over ~40 weeks
                        trend_mod := LEAST(1.0,
                            0.15 + 0.85 * (1.0 - EXP(
                                -0.07 * ((w_date - '2023-03-01'::date)::numeric / 7.0)
                            ))
                        );
                    END IF;
                END IF;

                -- Lipovant – slow decline (older statin losing share)
                IF p_rec.brand_name = 'Lipovant' THEN
                    trend_mod := 1.0 - (week_num::numeric / 104.0) * 0.15;
                END IF;

                -- ──────────────────────────────────────────────
                -- D. REGIONAL MODIFIER
                -- ──────────────────────────────────────────────
                regional_mod := 1.0;

                -- Larger regions get a population-weighted boost
                CASE t_rec.region
                    WHEN 'West'         THEN regional_mod := 1.25;
                    WHEN 'Northeast'    THEN regional_mod := 1.15;
                    WHEN 'Southeast'    THEN regional_mod := 1.10;
                    WHEN 'Southwest'    THEN regional_mod := 1.05;
                    WHEN 'Midwest'      THEN regional_mod := 1.00;
                    WHEN 'Mid-Atlantic' THEN regional_mod := 0.90;
                    WHEN 'Mountain'     THEN regional_mod := 0.65;
                    WHEN 'Pacific NW'   THEN regional_mod := 0.70;
                    ELSE regional_mod := 1.0;
                END CASE;

                -- PATTERN 2: Cardivex DECLINE in Northeast during 2024-Q3
                -- Simulates supply disruption / competitor entry
                IF p_rec.brand_name = 'Cardivex'
                   AND t_rec.region = 'Northeast'
                   AND yr = 2024 AND q = 3 THEN
                    regional_mod := regional_mod * 0.42;  -- ~58% drop
                END IF;

                -- Partial recovery in Q4 2024 (still below normal)
                IF p_rec.brand_name = 'Cardivex'
                   AND t_rec.region = 'Northeast'
                   AND yr = 2024 AND q = 4 THEN
                    regional_mod := regional_mod * 0.70;  -- ~30% below normal
                END IF;

                -- Pressura (same company, same area) picks up some share
                -- in Northeast during the same period
                IF p_rec.brand_name = 'Pressura'
                   AND t_rec.region = 'Northeast'
                   AND yr = 2024 AND q >= 3 THEN
                    regional_mod := regional_mod * 1.25;
                END IF;

                -- ──────────────────────────────────────────────
                -- E. DETERMINISTIC NOISE  (±12% jitter)
                -- ──────────────────────────────────────────────
                hash_input := w_date::text || '-' || p_rec.product_id::text
                              || '-' || t_rec.territory_id::text;
                -- Use MD5 to get a stable hash, take first 8 hex chars → int
                hash_val := ('x' || LEFT(MD5(hash_input), 8))::bit(32)::bigint;
                -- Map to range [-0.12 .. +0.12]
                noise_mod := 1.0 + ((hash_val % 1000)::numeric / 1000.0) * 0.24 - 0.12;

                -- ──────────────────────────────────────────────
                -- F. COMPUTE FINAL VALUES
                -- ──────────────────────────────────────────────
                final_sales := GREATEST(0,
                    ROUND(base_sales * seasonal_mod * trend_mod
                          * regional_mod * noise_mod, 2));
                final_units := GREATEST(0,
                    ROUND(base_units * seasonal_mod * trend_mod
                          * regional_mod * noise_mod)::integer);
                final_trx   := GREATEST(0,
                    ROUND(base_trx * seasonal_mod * trend_mod
                          * regional_mod * noise_mod)::integer);
                final_nrx   := GREATEST(0,
                    ROUND(base_nrx * seasonal_mod * trend_mod
                          * regional_mod * noise_mod)::integer);

                -- Skip rows where trend_mod = 0 (product not yet launched)
                IF trend_mod > 0 THEN
                    INSERT INTO fact_sales
                        (date, product_id, territory_id,
                         net_sales_usd, units, trx, nrx)
                    VALUES
                        (w_date, p_rec.product_id, t_rec.territory_id,
                         final_sales, final_units, final_trx, final_nrx);
                END IF;

            END LOOP; -- territory
        END LOOP; -- product
    END LOOP; -- week
END $$;


-- ────────────────────────────────────────────────────────────────
-- 5. Quick sanity checks (logged to Postgres console)
-- ────────────────────────────────────────────────────────────────

DO $$
DECLARE
    row_count BIGINT;
    min_date  DATE;
    max_date  DATE;
BEGIN
    SELECT COUNT(*), MIN(date), MAX(date)
    INTO row_count, min_date, max_date
    FROM fact_sales;

    RAISE NOTICE '── Seed complete ──────────────────────────────────';
    RAISE NOTICE 'fact_sales rows : %', row_count;
    RAISE NOTICE 'date range      : % → %', min_date, max_date;
    RAISE NOTICE 'products        : %', (SELECT COUNT(*) FROM dim_product);
    RAISE NOTICE 'territories     : %', (SELECT COUNT(*) FROM dim_territory);
    RAISE NOTICE 'time dates      : %', (SELECT COUNT(*) FROM dim_time);
    RAISE NOTICE '───────────────────────────────────────────────────';
END $$;
