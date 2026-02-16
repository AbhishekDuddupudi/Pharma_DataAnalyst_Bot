"""
Chat-history service – session and message CRUD.

All database access for chat sessions / messages goes through this
module so routers stay thin and logic is testable in isolation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import asyncpg

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# ── DB helper ────────────────────────────────────────────────────


async def _get_conn() -> asyncpg.Connection:
    dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.connect(dsn)


# ── Sessions ─────────────────────────────────────────────────────


async def create_session(user_id: int) -> dict[str, Any]:
    """Create a new chat session and return it."""
    conn = await _get_conn()
    try:
        row = await conn.fetchrow(
            "INSERT INTO chat_session (user_id) "
            "VALUES ($1) "
            "RETURNING id, user_id, title, created_at, updated_at",
            user_id,
        )
        logger.info("Chat session %s created for user_id=%s", row["id"], user_id)
        return _session_to_dict(row)
    finally:
        await conn.close()


async def list_sessions(user_id: int) -> list[dict[str, Any]]:
    """Return all sessions for a user, most recent first."""
    conn = await _get_conn()
    try:
        rows = await conn.fetch(
            "SELECT id, user_id, title, created_at, updated_at "
            "FROM chat_session "
            "WHERE user_id = $1 "
            "ORDER BY updated_at DESC",
            user_id,
        )
        return [_session_to_dict(r) for r in rows]
    finally:
        await conn.close()


async def get_session(user_id: int, session_id: int) -> dict[str, Any] | None:
    """Fetch a single session, enforcing ownership."""
    conn = await _get_conn()
    try:
        row = await conn.fetchrow(
            "SELECT id, user_id, title, created_at, updated_at "
            "FROM chat_session "
            "WHERE id = $1 AND user_id = $2",
            session_id,
            user_id,
        )
        return _session_to_dict(row) if row else None
    finally:
        await conn.close()


# ── Messages ─────────────────────────────────────────────────────


async def list_messages(user_id: int, session_id: int) -> list[dict[str, Any]]:
    """Return all messages for a session (oldest first), enforcing ownership."""
    # Verify ownership
    session = await get_session(user_id, session_id)
    if session is None:
        return []

    conn = await _get_conn()
    try:
        rows = await conn.fetch(
            "SELECT id, session_id, role, content, sql_query, metadata, created_at "
            "FROM chat_message "
            "WHERE session_id = $1 "
            "ORDER BY created_at ASC",
            session_id,
        )
        return [_message_to_dict(r) for r in rows]
    finally:
        await conn.close()


async def add_message(
    session_id: int,
    role: str,
    content: str,
    sql_query: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert a message and touch the session's updated_at."""
    import json as _json
    conn = await _get_conn()
    try:
        meta_json = _json.dumps(metadata) if metadata else None
        row = await conn.fetchrow(
            "INSERT INTO chat_message (session_id, role, content, sql_query, metadata) "
            "VALUES ($1, $2, $3, $4, $5::jsonb) "
            "RETURNING id, session_id, role, content, sql_query, metadata, created_at",
            session_id,
            role,
            content,
            sql_query,
            meta_json,
        )
        # Bump session updated_at
        await conn.execute(
            "UPDATE chat_session SET updated_at = now() WHERE id = $1",
            session_id,
        )
        return _message_to_dict(row)
    finally:
        await conn.close()


async def get_recent_messages(
    user_id: int,
    session_id: int,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """
    Return the last *limit* messages for a session (for the memory window).
    Enforces ownership. Returned oldest-first within the window.
    """
    session = await get_session(user_id, session_id)
    if session is None:
        return []

    conn = await _get_conn()
    try:
        rows = await conn.fetch(
            "SELECT id, session_id, role, content, sql_query, metadata, created_at "
            "FROM chat_message "
            "WHERE session_id = $1 "
            "ORDER BY created_at DESC "
            "LIMIT $2",
            session_id,
            limit,
        )
        # Reverse so they come back in chronological order
        return [_message_to_dict(r) for r in reversed(rows)]
    finally:
        await conn.close()


# ── Auto-title ───────────────────────────────────────────────────


async def maybe_auto_title(session_id: int) -> str | None:
    """
    If the session has no title and has at least one user message,
    generate a short title from the first user message (truncated to
    60 chars).  Returns the new title or None if nothing changed.
    """
    conn = await _get_conn()
    try:
        session = await conn.fetchrow(
            "SELECT id, title FROM chat_session WHERE id = $1",
            session_id,
        )
        if session is None or session["title"] is not None:
            return session["title"] if session else None

        first_msg = await conn.fetchrow(
            "SELECT content FROM chat_message "
            "WHERE session_id = $1 AND role = 'user' "
            "ORDER BY created_at ASC LIMIT 1",
            session_id,
        )
        if first_msg is None:
            return None

        title = _make_title(first_msg["content"])
        await conn.execute(
            "UPDATE chat_session SET title = $1 WHERE id = $2",
            title,
            session_id,
        )
        return title
    finally:
        await conn.close()


# ── Private helpers ──────────────────────────────────────────────


def _make_title(text: str, max_len: int = 60) -> str:
    """Create a short title from a user message."""
    # Take first line, strip whitespace
    line = text.strip().split("\n")[0].strip()
    if len(line) <= max_len:
        return line
    # Truncate at word boundary
    truncated = line[:max_len].rsplit(" ", 1)[0]
    return truncated + "…"


def _session_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    for key in ("created_at", "updated_at"):
        if isinstance(d.get(key), datetime):
            d[key] = d[key].isoformat()
    return d


def _message_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    import json as _json
    d = dict(row)
    if isinstance(d.get("created_at"), datetime):
        d["created_at"] = d["created_at"].isoformat()
    # Parse metadata JSONB if present (asyncpg returns str or None)
    meta = d.get("metadata")
    if meta is not None and isinstance(meta, str):
        try:
            d["metadata"] = _json.loads(meta)
        except Exception:
            d["metadata"] = None
    return d
