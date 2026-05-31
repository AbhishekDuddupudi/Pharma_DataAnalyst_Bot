#!/usr/bin/env bash
# ── Apply DB schema + seed to a target Postgres ───────────────────
# RDS has no docker-entrypoint-initdb.d, so the SQL files that the local
# Postgres container auto-runs must be applied manually. This script runs
# every db/*.sql file in alphabetical order against DATABASE_URL.
#
# The overall sequence is safe to rerun against the app database. Individual
# files are written to tolerate re-application where needed, and the analytics
# reset/seed files intentionally rebuild their data each run.
#
# Usage:
#   DATABASE_URL="postgresql://USER:PASS@<rds-endpoint>:5432/pharma_db" ./db/migrate.sh
#
# A SQLAlchemy-style "postgresql+asyncpg://" URL is accepted too — the
# "+asyncpg" driver suffix is stripped for psql.

set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "ERROR: DATABASE_URL is not set." >&2
  exit 1
fi

if ! command -v psql >/dev/null 2>&1; then
  echo "ERROR: psql is not installed or not on PATH. Install the PostgreSQL client before running db/migrate.sh." >&2
  exit 1
fi

# psql speaks libpq URLs; strip the SQLAlchemy async driver suffix if present.
PSQL_URL="${DATABASE_URL/+asyncpg/}"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

shopt -s nullglob
files=("$DIR"/*.sql)
if [[ ${#files[@]} -eq 0 ]]; then
  echo "ERROR: no .sql files found in $DIR" >&2
  exit 1
fi

for f in "${files[@]}"; do
  echo ">>> Applying $(basename "$f")"
  psql "$PSQL_URL" -v ON_ERROR_STOP=1 -f "$f"
done

echo "All migrations applied successfully."
