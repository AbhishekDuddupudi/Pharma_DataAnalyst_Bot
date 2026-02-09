"""
Audit service – persist workflow audit logs to Postgres.

Provides three lifecycle helpers:
    • create_audit_start(...)   → insert a row at workflow start.
    • finalize_audit_success(…) → mark success, fill timings/metrics.
    • finalize_audit_error(…)   → mark failure with error message.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def _get_conn() -> asyncpg.Connection:
    dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.connect(dsn)


async def create_audit_start(
    *,
    request_id: str,
    user_id: int,
    session_id: int | None,
    mode: str,
) -> int:
    """Insert an audit row at the beginning of a workflow run. Returns the id."""
    conn = await _get_conn()
    try:
        row = await conn.fetchrow(
            "INSERT INTO audit_log (request_id, user_id, session_id, mode) "
            "VALUES ($1, $2, $3, $4) "
            "RETURNING id",
            request_id,
            user_id,
            session_id,
            mode,
        )
        audit_id: int = row["id"]  # type: ignore[index]
        logger.info("Audit start: id=%s request_id=%s", audit_id, request_id)
        return audit_id
    finally:
        await conn.close()


async def finalize_audit_success(
    audit_id: int,
    *,
    tasks_count: int = 0,
    retries_used: int = 0,
    tables_used: list[str] | None = None,
    metrics_used: list[str] | None = None,
    timings_ms: dict[str, int] | None = None,
    rows_returned: int = 0,
) -> None:
    """Mark an audit row as successfully completed."""
    import json

    conn = await _get_conn()
    try:
        await conn.execute(
            "UPDATE audit_log "
            "SET finished_at   = now(), "
            "    success       = true, "
            "    tasks_count   = $2, "
            "    retries_used  = $3, "
            "    tables_used   = $4::jsonb, "
            "    metrics_used  = $5::jsonb, "
            "    timings_ms    = $6::jsonb, "
            "    rows_returned = $7 "
            "WHERE id = $1",
            audit_id,
            tasks_count,
            retries_used,
            json.dumps(tables_used or []),
            json.dumps(metrics_used or []),
            json.dumps(timings_ms or {}),
            rows_returned,
        )
        logger.info("Audit success: id=%s", audit_id)
    finally:
        await conn.close()


async def finalize_audit_error(
    audit_id: int,
    *,
    error_message: str,
    tasks_count: int = 0,
    retries_used: int = 0,
    timings_ms: dict[str, int] | None = None,
) -> None:
    """Mark an audit row as failed."""
    import json

    conn = await _get_conn()
    try:
        await conn.execute(
            "UPDATE audit_log "
            "SET finished_at    = now(), "
            "    success        = false, "
            "    error_message  = $2, "
            "    tasks_count    = $3, "
            "    retries_used   = $4, "
            "    timings_ms     = $5::jsonb "
            "WHERE id = $1",
            audit_id,
            error_message,
            tasks_count,
            retries_used,
            json.dumps(timings_ms or {}),
        )
        logger.info("Audit error: id=%s error=%s", audit_id, error_message[:100])
    finally:
        await conn.close()
