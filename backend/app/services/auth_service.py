"""
Authentication service – password hashing, user lookup, session CRUD.

All database access for auth goes through this module so routers
stay thin and logic is testable in isolation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import bcrypt

from app.core.config import settings
from app.core.logging import get_logger
from app.security.cookies import SESSION_MAX_AGE_SECONDS

logger = get_logger(__name__)

# ── Helpers ──────────────────────────────────────────────────────


async def _get_conn() -> asyncpg.Connection:
    """Return a raw asyncpg connection from DATABASE_URL."""
    # Strip the SQLAlchemy driver prefix so asyncpg can connect
    dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.connect(dsn)


# ── Password Hashing ────────────────────────────────────────────


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain*."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Check *plain* against a bcrypt *hashed* value."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ── User Lookup ─────────────────────────────────────────────────


async def get_user_by_email(email: str) -> dict[str, Any] | None:
    """Fetch a user row by email.  Returns None if not found."""
    conn = await _get_conn()
    try:
        row = await conn.fetchrow(
            "SELECT id, email, password_hash, display_name "
            "FROM app_user WHERE email = $1",
            email,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    """Fetch a user row by primary key."""
    conn = await _get_conn()
    try:
        row = await conn.fetchrow(
            "SELECT id, email, display_name "
            "FROM app_user WHERE id = $1",
            user_id,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


# ── Session CRUD ────────────────────────────────────────────────


async def create_session(user_id: int) -> str:
    """Create a new session row and return the session token (UUID)."""
    session_id = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=SESSION_MAX_AGE_SECONDS)

    conn = await _get_conn()
    try:
        await conn.execute(
            "INSERT INTO user_session (id, user_id, expires_at) "
            "VALUES ($1, $2, $3)",
            session_id,
            user_id,
            expires_at,
        )
    finally:
        await conn.close()

    logger.info("Session created for user_id=%s", user_id)
    return session_id


async def get_session(session_id: str) -> dict[str, Any] | None:
    """
    Look up a session by token.  Returns None if expired or missing.
    """
    conn = await _get_conn()
    try:
        row = await conn.fetchrow(
            "SELECT id, user_id, expires_at "
            "FROM user_session "
            "WHERE id = $1 AND expires_at > now()",
            session_id,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def delete_session(session_id: str) -> None:
    """Delete a session row (logout)."""
    conn = await _get_conn()
    try:
        await conn.execute("DELETE FROM user_session WHERE id = $1", session_id)
    finally:
        await conn.close()

    logger.info("Session deleted")
