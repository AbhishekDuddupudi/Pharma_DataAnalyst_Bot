"""
Auth router – login, logout, and session introspection.

All business logic lives in ``app.services.auth_service``;
this module only handles HTTP concerns (request parsing, cookies,
response shaping).
"""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr

from app.security.cookies import (
    COOKIE_SETTINGS,
    DELETE_COOKIE_SETTINGS,
    SESSION_COOKIE,
)
from app.security.deps import get_current_user
from app.services.auth_service import (
    create_session,
    delete_session,
    get_user_by_email,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Request / Response Schemas ───────────────────────────────────


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str | None = None


class LoginResponse(BaseModel):
    user: UserOut


class MeResponse(BaseModel):
    user: UserOut | None = None


class LogoutResponse(BaseModel):
    ok: bool = True


# ── Endpoints ────────────────────────────────────────────────────


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, response: Response):
    """Authenticate with email + password, set session cookie."""

    user = await get_user_by_email(body.email)

    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    session_id = await create_session(user["id"])
    response.set_cookie(value=session_id, **COOKIE_SETTINGS)

    return LoginResponse(
        user=UserOut(
            id=user["id"],
            email=user["email"],
            display_name=user.get("display_name"),
        )
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    response: Response,
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE),
):
    """Clear the session cookie and delete the server-side session."""
    if session_id:
        await delete_session(session_id)

    response.delete_cookie(**DELETE_COOKIE_SETTINGS)
    return LogoutResponse()


@router.get("/me", response_model=MeResponse)
async def me(user: dict | None = Depends(get_current_user)):
    """Return the currently authenticated user, or null."""
    if user is None:
        return MeResponse(user=None)

    return MeResponse(
        user=UserOut(
            id=user["id"],
            email=user["email"],
            display_name=user.get("display_name"),
        )
    )
