# Deployment Guide — AWS (Plan A)

Production deployment for the Pharma Data Analyst Bot: **EC2 + Docker Compose**
(Nginx + FastAPI backend), **AWS RDS PostgreSQL**, fronted by an
**Application Load Balancer (ALB)** with TLS from **AWS Certificate Manager
(ACM)** and DNS via **Route 53**.

## Topology

```
                    Route 53 (your-domain)
                            │  (A / alias record)
                            ▼
                  Application Load Balancer
                  - HTTPS :443  (ACM certificate)
                  - HTTP  :80   (optional → redirect to 443)
                            │  forwards HTTP to target
                            ▼
        EC2 instance  (docker compose -f docker-compose.prod.yml)
        ┌──────────────────────────────────────────────┐
        │  web (Nginx) :80  ── public entry on the host │
        │    /        → React static assets (dist/)     │
        │    /api/... → proxy to backend:8000           │
        │  backend (FastAPI/uvicorn) :8000  internal    │
        └──────────────────────────────────────────────┘
                            │  DATABASE_URL
                            ▼
              AWS RDS PostgreSQL :5432 (private)
```

**Single origin:** the browser only ever talks to one domain (the ALB). Nginx
serves the SPA and proxies `/api` to the backend, so there is no cross-site
cookie/CORS complexity.

## TLS & cookies (important)

- **TLS terminates at the ALB** (ACM cert). The ALB → EC2/Nginx leg is plain
  HTTP inside the VPC. This is the standard pattern.
- The ALB sets **`X-Forwarded-Proto: https`**; Nginx forwards it to the backend
  ([nginx/nginx.conf](nginx/nginx.conf)).
- The backend runs Uvicorn with trusted proxy headers, so request scheme/host
   stay aligned with the ALB-forwarded values for any future absolute-URL or
   redirect logic.
- The session cookie's **`Secure`** flag is enabled by `APP_ENV=production`
  ([backend/app/security/cookies.py](backend/app/security/cookies.py)). Because
  the **browser ↔ ALB** connection is HTTPS, the browser accepts and returns the
  Secure cookie. The backend does **not** need to see HTTPS directly for this to
  work, so terminating TLS at the ALB is fine.
- Cookies are `HttpOnly; SameSite=Lax; Secure` — correct for the single-origin
  model.

## Security groups

Create three security groups and chain them (each layer only trusts the one in
front of it):

| SG | Inbound rule | Source |
|----|--------------|--------|
| **ALB SG** | TCP 443 | `0.0.0.0/0` |
| **ALB SG** | TCP 80 (optional, for HTTP→HTTPS redirect) | `0.0.0.0/0` |
| **EC2 SG** | TCP 80 (app traffic) | **ALB SG** only |
| **EC2 SG** | TCP 22 (SSH) | **your IP /32** only |
| **RDS SG** | TCP 5432 | **EC2 SG** only |

**Never expose publicly:** backend `8000`, the Vite dev port `5173`, or Postgres
`5432`. The prod compose ([docker-compose.prod.yml](docker-compose.prod.yml))
publishes only Nginx on `80`; backend uses `expose` (compose-internal only); there
is no Postgres container in production.

## One-time deployment steps

> **Warning:** [db/migrate.sh](db/migrate.sh) is a first-time bootstrap script
> for a fresh database. It is not a routine production migration tool.
> [db/00_schema.sql](db/00_schema.sql) contains `DROP TABLE` statements that
> rebuild the seeded analytics model before reseeding.

1. **Provision RDS PostgreSQL** (private subnets, RDS SG as above). Note the
   endpoint, username, password, and DB name (`pharma_db`).
2. **Launch the EC2 instance** (EC2 SG as above), install Docker + the Docker
   Compose plugin, and clone this repo.
3. **Create the production `.env`** on the host (never commit it). Required:
   ```
   APP_ENV=production
   LOG_LEVEL=INFO
   DATABASE_URL=postgresql+asyncpg://USER:PASS@<rds-endpoint>:5432/pharma_db
   CORS_ORIGINS=https://your-domain
   OPENAI_API_KEY=sk-...
   ```
   (See [.env.example](.env.example) for the full list.)
4. **Install the PostgreSQL client** on the EC2 host if needed (`psql` must be
   available on `PATH`).
5. **Bootstrap the database once** against a **fresh RDS database**:
   ```bash
   MIGRATION_CONFIRM=BOOTSTRAP DATABASE_URL="postgresql://USER:PASS@<rds-endpoint>:5432/pharma_db" ./db/migrate.sh
   ```
   Do **not** use this script later for routine production schema changes.
6. **Build & start the app:**
   ```bash
   docker compose -f docker-compose.prod.yml up -d --build
   ```
7. **Create the owner user** (interactive, hidden password input):
   ```bash
   docker compose -f docker-compose.prod.yml exec backend python -m scripts.create_owner
   ```
8. **ACM:** request/validate a public certificate for `your-domain`.
9. **ALB:** create an internet-facing ALB in the public subnets, attached to the
   ALB SG. Add an **HTTPS :443 listener** using the ACM cert, forwarding to a
   target group of the EC2 instance on **port 80**. Optionally add an **HTTP :80
   listener** that redirects to HTTPS. Set the target group health check to
   **`GET /api/health`**.
10. **Route 53:** create an **A / alias** record for `your-domain` pointing at the
   ALB.
11. **Verify:** browse `https://your-domain`, log in as the owner, run a query,
    and confirm the chat **streams** incrementally (SSE through Nginx + ALB).

## Operational notes

- **Updates/redeploys:** `git pull && docker compose -f docker-compose.prod.yml up -d --build`.
- **Reset owner password:** rerun step 6 — the upsert updates the existing user.
- **Logs:** `docker compose -f docker-compose.prod.yml logs -f web backend`.
- The legacy [docker-compose.yml](docker-compose.yml) remains for **local
  development only** (Postgres container, hot-reload, ports exposed on
  localhost). Do not use it on EC2.
