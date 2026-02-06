# Pharma Data Analyst Bot

> GenAI-powered Text-to-SQL chatbot for pharmaceutical data analysis.  
> Monorepo: **React (Vite)** frontend + **FastAPI** backend + **Postgres**.

---

## Prerequisites

| Tool            | Version  |
|-----------------|----------|
| Docker          | 24+      |
| Docker Compose  | v2+      |
| Node.js (local) | 20 LTS (only needed for local frontend dev outside Docker) |
| Python (local)  | 3.11+ (only needed for local backend dev outside Docker)   |

---

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/AbhishekDuddupudi/Pharma_DataAnalyst_Bot.git
cd Pharma_DataAnalyst_Bot

# 2. Create your env file
cp .env.example .env
# Edit .env and add your real OPENAI_API_KEY

# 3. Build and run everything
docker-compose up --build
```

Once running:

| Service  | URL                        |
|----------|----------------------------|
| Frontend | http://localhost:5173      |
| Backend  | http://localhost:8000      |
| Postgres | localhost:5432             |

---

## API Endpoints

| Method | Path            | Description                |
|--------|-----------------|----------------------------|
| GET    | `/api/health`   | Health check               |
| GET    | `/api/version`  | App version and info       |

---

## Project Structure

```
.
├── backend/                  # FastAPI application
│   └── app/
│       ├── api/              # Route handlers
│       ├── agent/            # LangGraph workflow + nodes
│       ├── services/         # DB access, LLM client, streaming
│       ├── security/         # Auth dependencies (future)
│       ├── catalog/          # Semantic schema + metrics YAML (future)
│       └── core/             # Config, logging, middleware
├── frontend/                 # React + Vite + TypeScript + Tailwind
│   └── src/
│       ├── api/              # API client
│       ├── pages/            # Page components
│       ├── layouts/          # Layout wrappers
│       └── components/       # Shared components
├── db/                       # Postgres init scripts
│   ├── 00_schema.sql         # Star-schema DDL
│   ├── 01_seed.sql           # Deterministic data generation
│   └── 02_indexes.sql        # Analytics indexes
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Dataset Overview

The Postgres container auto-seeds a **star-schema analytics dataset** designed for pharma sales analysis and Text-to-SQL demos.

### Schema (star model)

| Table | Type | Description |
|-------|------|-------------|
| `dim_time` | Dimension | Calendar dates (2023-01-01 – 2024-12-31) with year, quarter, month, week, `year_quarter`, `year_month` |
| `dim_product` | Dimension | 12 branded drugs across 4 companies and 4 therapeutic areas |
| `dim_territory` | Dimension | 40 territories (8 regions × 5 states), with region/district/state hierarchy |
| `fact_sales` | Fact | ~50k rows — weekly grain `(date, product_id, territory_id)` with `net_sales_usd`, `units`, `trx`, `nrx` |

### Companies and Products

| Company | Therapeutic Area | Products |
|---------|-----------------|----------|
| NovaCure Therapeutics | Cardiovascular | Cardivex, Pressura, Lipovant |
| BioGenix Pharma | Oncology | Oncoshield, Tumorex, Cellguard |
| MedVista Labs | Respiratory | Breatheasy, Aeroclar, Pulmovex |
| Zenith BioSciences | CNS | NeuroCalm, Cognimax, Anxiolyze |

### Engineered Insight Patterns

| # | Pattern | Detail |
|---|---------|--------|
| 1 | **Regional decline** | Cardivex (NovaCure) drops ~58% in Northeast during 2024-Q3 (supply disruption), partial Q4 recovery. Pressura gains share in same region. |
| 2 | **Steady grower** | Oncoshield (BioGenix) grows ~30% over 2 years with consistent QoQ trajectory. |
| 3 | **Seasonality** | Respiratory products (Breatheasy, Aeroclar, Pulmovex) peak in Q4/Q1 and dip in summer. CNS products have mild winter uplift. |
| 4 | **New product launch** | Anxiolyze launches Mar 2023 with an S-curve ramp-up. |
| 5 | **Mature product decline** | Lipovant slowly loses ~15% share over 2 years. |
| 6 | **Gaussian noise** | ±12% deterministic jitter on every row (seeded hash) for realism. |

### Example Queries

```sql
-- Total sales by quarter for Cardivex
SELECT t.year_quarter, SUM(f.net_sales_usd) AS total_sales
FROM fact_sales f
JOIN dim_time t    ON f.date = t.date
JOIN dim_product p ON f.product_id = p.product_id
WHERE p.brand_name = 'Cardivex'
GROUP BY t.year_quarter ORDER BY t.year_quarter;

-- Sales by region for Cardivex in 2024
SELECT r.region, SUM(f.net_sales_usd) AS total_sales
FROM fact_sales f
JOIN dim_territory r ON f.territory_id = r.territory_id
JOIN dim_product p   ON f.product_id = p.product_id
JOIN dim_time t      ON f.date = t.date
WHERE p.brand_name = 'Cardivex' AND t.year = 2024
GROUP BY r.region ORDER BY total_sales DESC;

-- QoQ growth by product
WITH quarterly AS (
    SELECT p.brand_name, t.year_quarter,
           SUM(f.net_sales_usd) AS q_sales
    FROM fact_sales f
    JOIN dim_time t    ON f.date = t.date
    JOIN dim_product p ON f.product_id = p.product_id
    GROUP BY p.brand_name, t.year_quarter
)
SELECT brand_name, year_quarter, q_sales,
       ROUND((q_sales / LAG(q_sales) OVER (
           PARTITION BY brand_name ORDER BY year_quarter
       ) - 1) * 100, 1) AS qoq_growth_pct
FROM quarterly ORDER BY brand_name, year_quarter;

-- Top regions by decline between 2024-Q2 and 2024-Q3
WITH by_region_q AS (
    SELECT r.region, t.year_quarter,
           SUM(f.net_sales_usd) AS q_sales
    FROM fact_sales f
    JOIN dim_time t      ON f.date = t.date
    JOIN dim_territory r ON f.territory_id = r.territory_id
    WHERE t.year_quarter IN ('2024-Q2','2024-Q3')
    GROUP BY r.region, t.year_quarter
)
SELECT region,
       MAX(CASE WHEN year_quarter='2024-Q2' THEN q_sales END) AS q2,
       MAX(CASE WHEN year_quarter='2024-Q3' THEN q_sales END) AS q3,
       ROUND((MAX(CASE WHEN year_quarter='2024-Q3' THEN q_sales END) /
              MAX(CASE WHEN year_quarter='2024-Q2' THEN q_sales END) - 1) * 100, 1)
           AS change_pct
FROM by_region_q GROUP BY region ORDER BY change_pct;

-- Compare Oncology products
SELECT p.brand_name, t.year_quarter, SUM(f.net_sales_usd) AS total_sales
FROM fact_sales f
JOIN dim_time t    ON f.date = t.date
JOIN dim_product p ON f.product_id = p.product_id
WHERE p.therapeutic_area = 'Oncology'
GROUP BY p.brand_name, t.year_quarter
ORDER BY p.brand_name, t.year_quarter;
```

### Init Script Execution Order

Scripts in `db/` run alphabetically when the container is first created:

1. `00_schema.sql` — tables, constraints, extensions
2. `01_seed.sql` — dimension + fact data generation (PL/pgSQL)
3. `02_indexes.sql` — analytics indexes + `ANALYZE`

> To re-seed: `docker-compose down -v && docker-compose up --build`

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Port already in use | Stop other services on 5173 / 8000 / 5432 or change ports in `docker-compose.yml` |
| DB not ready | Backend waits for the Postgres health check — give it a few seconds |
| Frontend can't reach backend | Ensure CORS_ORIGINS in `.env` includes `http://localhost:5173` |
| Containers won't start | Run `docker-compose down -v && docker-compose up --build` for a clean rebuild |
| Python deps changed | Rebuild backend: `docker-compose build backend` |
| npm deps changed | Rebuild frontend: `docker-compose build frontend` |

---

## License

Private — all rights reserved.