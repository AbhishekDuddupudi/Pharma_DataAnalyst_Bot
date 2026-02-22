"""
Safe SQL executor – runs validated SELECT queries against Postgres.

Features:
    • SELECT-only enforcement (double-checks via sql_policy).
    • Row cap (settings.SQL_MAX_ROWS).
    • Timing (db_ms).
    • Returns columns + rows as plain lists.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from app.core.config import settings
from app.core.logging import get_logger
from app.security.sql_policy import validate_sql
from app.services.observability import get_tracer

logger = get_logger(__name__)


@dataclass
class QueryResult:
    """Holds the result of a successful SQL query."""

    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    db_ms: int = 0


async def _get_conn() -> asyncpg.Connection:
    dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.connect(dsn)


async def execute_query(sql: str, *, parent_span: Any = None) -> QueryResult:
    """
    Execute a read-only SQL query and return structured results.

    Raises:
        ValueError – if the SQL fails policy validation.
        RuntimeError – if the DB query itself errors.

    If *parent_span* is provided, a Langfuse span is logged for this query.
    """
    # Double-check policy before execution
    validation = validate_sql(sql)
    if not validation.valid:
        raise ValueError(f"SQL policy violation: {'; '.join(validation.errors)}")

    # Normalise — remove trailing semicolons
    clean_sql = sql.strip().rstrip(";").strip()

    # Wrap in a LIMIT if none is present, to enforce row cap
    upper = clean_sql.upper()
    max_rows = settings.SQL_MAX_ROWS

    # We always add our own LIMIT to be safe; if user already has one,
    # we wrap in a subquery.
    limited_sql = f"SELECT * FROM ({clean_sql}) _q LIMIT {max_rows + 1}"

    conn = await _get_conn()
    try:
        t0 = time.perf_counter()

        # Use a read-only transaction
        async with conn.transaction(readonly=True):
            rows = await conn.fetch(limited_sql)

        db_ms = round((time.perf_counter() - t0) * 1000)

        if not rows:
            return QueryResult(columns=[], rows=[], row_count=0, db_ms=db_ms)

        columns = list(rows[0].keys())
        truncated = len(rows) > max_rows
        result_rows = rows[:max_rows]

        # Convert asyncpg Records to plain lists (JSON-safe)
        plain_rows: list[list[Any]] = []
        for r in result_rows:
            plain_rows.append([_serialise(r[col]) for col in columns])

        qr = QueryResult(
            columns=columns,
            rows=plain_rows,
            row_count=len(plain_rows),
            truncated=truncated,
            db_ms=db_ms,
        )

        # ── Langfuse db.query span ────────────────────────
        if parent_span is not None:
            tracer = get_tracer()
            span = tracer.start_span(parent_span, name="db.query", input={"sql": clean_sql[:2000]})
            tracer.end_span(span, output={
                "row_count": qr.row_count,
                "columns": qr.columns,
                "truncated": qr.truncated,
            }, metadata={"db_ms": db_ms})

        return qr

    except asyncpg.PostgresError as exc:
        logger.error("SQL execution error: %s | SQL: %s", exc, clean_sql[:200])
        raise RuntimeError(f"Database error: {exc}") from exc
    finally:
        await conn.close()


def _serialise(value: Any) -> Any:
    """Convert DB types to JSON-safe Python types."""
    if value is None:
        return None
    if isinstance(value, (int, float, str, bool)):
        return value
    # Decimal, date, datetime → string
    return str(value)
