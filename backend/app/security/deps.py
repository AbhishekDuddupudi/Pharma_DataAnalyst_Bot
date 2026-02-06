"""
FastAPI dependencies for authentication.

- ``get_current_user``  – returns user dict or None (soft check).
- ``require_auth``      – raises 401 if not authenticated (hard check).
"""

from __future__ import annotations

from typing import Any

from fastapi import Cookie, Depends, HTTPException, status

from app.security.cookies import SESSION_COOKIE
from app.services.auth_service import get_session, get_user_by_id


async def get_current_user(
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict[str, Any] | None:
    """
    Read the session cookie, look up the session, and return the user.
    Returns ``None`` when unauthenticated (no cookie / expired / invalid).
    """
    if not session_id:
        return None

    session = await get_session(session_id)
    if not session:
        return None

    user = await get_user_by_id(session["user_id"])
    return user


async def require_auth(
    user: dict[str, Any] | None = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Dependency that *requires* an authenticated user.
    Raises HTTP 401 if the user is not logged in.
    """
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user
