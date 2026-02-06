"""
Sessions & chat router – protected by require_auth.

Endpoints:
    GET  /api/sessions               → list user sessions
    POST /api/sessions               → create empty session
    GET  /api/sessions/{id}/messages  → messages for a session
    POST /api/chat                   → send a chat message (stub agent)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.security.deps import require_auth
from app.services.chat_history import (
    add_message,
    create_session,
    get_session,
    list_messages,
    list_sessions,
    maybe_auto_title,
)

router = APIRouter(tags=["chat"])


# ── Schemas ──────────────────────────────────────────────────────


class SessionOut(BaseModel):
    id: int
    user_id: int
    title: str | None = None
    created_at: str
    updated_at: str


class MessageOut(BaseModel):
    id: int
    session_id: int
    role: str
    content: str
    sql_query: str | None = None
    created_at: str


class ChatRequest(BaseModel):
    session_id: int | None = None
    message: str


class ChatResponse(BaseModel):
    session_id: int
    answer: str
    messages: list[MessageOut]


# ── Session endpoints ────────────────────────────────────────────


@router.get("/sessions", response_model=list[SessionOut])
async def sessions_list(user: dict[str, Any] = Depends(require_auth)):
    """List all sessions for the authenticated user."""
    return await list_sessions(user["id"])


@router.post("/sessions", response_model=SessionOut, status_code=201)
async def sessions_create(user: dict[str, Any] = Depends(require_auth)):
    """Create a new empty chat session."""
    return await create_session(user["id"])


@router.get("/sessions/{chat_session_id}/messages", response_model=list[MessageOut])
async def sessions_messages(
    chat_session_id: int,
    user: dict[str, Any] = Depends(require_auth),
):
    """Return all messages for a session (enforces ownership)."""
    session = await get_session(user["id"], chat_session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return await list_messages(user["id"], chat_session_id)


# ── Chat (stub) ─────────────────────────────────────────────────


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, user: dict[str, Any] = Depends(require_auth)):
    """
    Accept a user message, store it, return a stub assistant response.
    Creates a session automatically if ``session_id`` is not provided.
    """
    user_id: int = user["id"]

    # Resolve or create session
    if body.session_id is not None:
        session = await get_session(user_id, body.session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found",
            )
        session_id = session["id"]
    else:
        new_session = await create_session(user_id)
        session_id = new_session["id"]

    # Store user message
    await add_message(session_id, "user", body.message)

    # Auto-title from first user message
    await maybe_auto_title(session_id)

    # Stub assistant response (will be replaced by real agent later)
    stub_answer = "Got it. (Agent coming next.)"
    await add_message(session_id, "assistant", stub_answer)

    # Return full message history
    messages = await list_messages(user_id, session_id)

    return ChatResponse(
        session_id=session_id,
        answer=stub_answer,
        messages=messages,
    )
