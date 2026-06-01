# Deployment Guide — AWS

This project supports two deployment variants:

- **Plan A — Production (ALB + RDS):** the professional/enterprise target,
  documented immediately below. Use this for the eventual production upgrade.
- **Plan B — EC2-only free-tier:** a single-box, cost-conscious variant for the
  first AWS launch (no RDS/ALB/ECS). Jump to
  [EC2-only free-tier deployment (Plan B)](#ec2-only-free-tier-deployment-plan-b).

---

## Plan A — Production (ALB + RDS)

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

---

# EC2-only free-tier deployment (Plan B)

A single EC2 instance runs the **entire stack** with Docker Compose —
Nginx (public reverse proxy), the built React app (static), the FastAPI
backend, and a **PostgreSQL container** with a persistent named volume.
HTTPS is handled by **Let's Encrypt / Certbot** on the box. **No RDS, no ALB,
no ECS, no NAT Gateway, no Secrets Manager.**

Compose file: [docker-compose.ec2-free.yml](docker-compose.ec2-free.yml).
This does **not** replace Plan A — [docker-compose.prod.yml](docker-compose.prod.yml)
and the ALB/RDS docs above are kept for the future professional upgrade.

## Cost notes

- Intended for **AWS Free Tier / credit-conscious** use. Avoids the paid /
  always-on pieces: **RDS, ALB, ECS, NAT Gateway, Secrets Manager**, and paid
  monitoring.
- Runs on a single `t2.micro`/`t3.micro` (free-tier eligible) with an
  **Elastic IP** (free while attached to a running instance).
- Postgres data lives in a Docker named volume (`pgdata_ec2`) on the instance's
  EBS volume — back it up yourself (e.g. `pg_dump` to S3).
- This is **not** the final enterprise architecture — no managed DB backups, no
  multi-AZ, no load balancer. Upgrade to Plan A when ready.

## Topology

```
                 Route 53  (app.pharma-da-copilot.com)
                         │  A record → EC2 Elastic IP
                         ▼
        EC2 instance  (docker compose -f docker-compose.ec2-free.yml)
        ┌────────────────────────────────────────────────┐
        │  web (Nginx)  :80 + :443   ── ONLY public ports │
        │    /        → React static assets (dist/)       │
        │    /api/... → proxy to backend:8000  (SSE-safe) │
        │    TLS via Let's Encrypt (Certbot, webroot)     │
        │  backend (FastAPI/uvicorn) :8000   internal     │
        │  db (postgres:16-alpine)   :5432   internal     │
        │       volume: pgdata_ec2 (persistent)           │
        └────────────────────────────────────────────────┘
```

Single origin (same as Plan A): `/` serves the SPA, `/api` proxies to the
backend. Backend `8000` and Postgres `5432` are never published — only Nginx
`80`/`443` are.

## Security group (one box)

| Port | Source | Purpose |
|------|--------|---------|
| 443  | `0.0.0.0/0` | HTTPS app traffic |
| 80   | `0.0.0.0/0` | ACME challenge + HTTP→HTTPS redirect |
| 22   | **your IP /32** | SSH admin |

Do **not** open `8000` or `5432`.

## Step-by-step

### 1. Create the EC2 instance
- Amazon Linux 2023 or Ubuntu, `t3.micro` (free-tier). Attach the SG above.
- Allocate an **Elastic IP** and associate it with the instance.

### 2. Install Docker + Compose plugin, then clone
```bash
# Amazon Linux 2023
sudo dnf -y install docker git && sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user            # re-login after this
# Compose plugin:
sudo mkdir -p /usr/libexec/docker/cli-plugins
sudo curl -sSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m) \
  -o /usr/libexec/docker/cli-plugins/docker-compose && sudo chmod +x /usr/libexec/docker/cli-plugins/docker-compose

git clone <your-repo-url> pharma && cd pharma
```

### 3. Create `.env` on the EC2 host (never commit it)
Start in **HTTP smoke-test** mode (Secure cookies need HTTPS, so use
`development` until TLS is on):
```
APP_ENV=development
LOG_LEVEL=INFO
POSTGRES_USER=pharma
POSTGRES_PASSWORD=<strong-password>
POSTGRES_DB=pharma_db
CORS_ORIGINS=http://app.pharma-da-copilot.com
OPENAI_API_KEY=sk-...
```
> `DATABASE_URL` is set automatically by the compose file to point at the `db`
> container — you don't need it in `.env` for this variant.

### 4. Build & start (DB auto-initializes on first boot)
```bash
docker compose -f docker-compose.ec2-free.yml up -d --build
```
The Postgres container auto-applies `db/*.sql` (alphabetical) on its **first**
start because the `db/` folder is mounted into `docker-entrypoint-initdb.d`.
Confirm:
```bash
docker compose -f docker-compose.ec2-free.yml exec db \
  psql -U pharma -d pharma_db -c "\dt"
```
> Re-seeding an existing volume from scratch instead? Use the bootstrap script
> (it DROPs + rebuilds the analytics tables):
> ```bash
> docker compose -f docker-compose.ec2-free.yml exec -e MIGRATION_CONFIRM=BOOTSTRAP backend \
>   bash -lc 'DATABASE_URL="$DATABASE_URL" ./db/migrate.sh'   # requires psql in the image
> ```
> (Simplest is to just recreate the volume: `down -v` then `up`.)

### 5. Create the owner user (interactive, hidden input)
```bash
docker compose -f docker-compose.ec2-free.yml exec backend python -m scripts.create_owner
```

### 6. HTTP smoke test
```bash
curl -i http://app.pharma-da-copilot.com/api/health     # → 200 {"status":"ok"}
```
Browse `http://app.pharma-da-copilot.com`, log in, run a query, confirm the
chat **streams** incrementally. (Login works here because `APP_ENV=development`
→ no Secure flag.)

### 7. Route 53 DNS
Create an **A record**:
```
Name:  app.pharma-da-copilot.com
Type:  A
Value: <your EC2 Elastic IP>
TTL:   300
```
Wait for it to resolve (`dig +short app.pharma-da-copilot.com`) before issuing a
cert — Let's Encrypt validates over the public DNS name.

### 8. Issue the TLS certificate (Certbot, webroot)
The default Nginx config (Phase 1) already serves `/.well-known/acme-challenge/`
from the shared `certbot_www` volume, so issue with:
```bash
docker compose -f docker-compose.ec2-free.yml run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  -d app.pharma-da-copilot.com \
  --email you@example.com --agree-tos --no-eff-email
```

### 9. Switch Nginx to HTTPS and turn on production cookies
```bash
# Flip the app to production (enables Secure cookies):
sed -i 's/^APP_ENV=.*/APP_ENV=production/' .env
sed -i 's#^CORS_ORIGINS=.*#CORS_ORIGINS=https://app.pharma-da-copilot.com#' .env

# Re-up with the HTTPS Nginx config + restart backend to pick up APP_ENV:
NGINX_CONF=./nginx/nginx.ec2-free-ssl.conf \
  docker compose -f docker-compose.ec2-free.yml up -d --force-recreate web backend
```
Verify:
```bash
curl -I https://app.pharma-da-copilot.com/api/health     # → 200 over TLS
curl -I http://app.pharma-da-copilot.com                 # → 301 redirect to https
```
Then browse `https://app.pharma-da-copilot.com`, log in (Secure cookie now), and
confirm streaming.

### 10. Auto-renewal (cron on the host)
Let's Encrypt certs last 90 days. Add a host cron:
```bash
# /etc/cron.d/certbot-renew  (runs twice daily; reloads Nginx after renewal)
0 0,12 * * * root cd /home/ec2-user/pharma && \
  docker compose -f docker-compose.ec2-free.yml run --rm certbot renew --webroot -w /var/www/certbot && \
  docker compose -f docker-compose.ec2-free.yml exec web nginx -s reload
```

## Operational notes (Plan B)

- **Updates/redeploys:** `git pull && NGINX_CONF=./nginx/nginx.ec2-free-ssl.conf docker compose -f docker-compose.ec2-free.yml up -d --build`.
- **Reset owner password:** rerun step 5 (idempotent upsert).
- **Logs:** `docker compose -f docker-compose.ec2-free.yml logs -f web backend db`.
- **DB backup:** `docker compose -f docker-compose.ec2-free.yml exec db pg_dump -U pharma pharma_db > backup.sql`.
