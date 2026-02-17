"""
Sessions & chat router – protected by require_auth.

Endpoints:
    GET  /api/sessions               → list user sessions
    POST /api/sessions               → create empty session
    GET  /api/sessions/{id}/messages  → messages for a session
    POST /api/chat                   → send a chat message (non-streaming, uses workflow)
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.security.deps import require_auth
from app.services.chat_history import (
    add_message,
    create_session,
    get_recent_messages,
    get_session,
    list_messages,
    list_sessions,
    maybe_auto_title,
)
from app.services.audit import (
    create_audit_start,
    finalize_audit_success,
    finalize_audit_error,
)
from app.agent.workflow import run_workflow
from app.core.logging import get_logger

logger = get_logger(__name__)

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
    metadata: dict | None = None
    artifacts_json: dict | None = None
    assumptions: list | None = None
    followups: list | None = None
    metrics_json: dict | None = None
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
    Accept a user message, run the 10-node workflow (non-streaming),
    store results, and return the full message history.
    """
    user_id: int = user["id"]
    request_id = str(uuid.uuid4())

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
    await maybe_auto_title(session_id)

    # Load history for context
    history_rows = await get_recent_messages(user_id, session_id, limit=6)
    history = [{"role": h["role"], "content": h["content"]} for h in history_rows]

    # Create audit entry
    audit_id: int | None = None
    try:
        audit_id = await create_audit_start(
            request_id=request_id,
            user_id=user_id,
            session_id=session_id,
            mode="sync",
        )
    except Exception:
        logger.warning("Failed to create audit entry", exc_info=True)

    # No-op emitter for non-streaming mode
    async def _noop_emit(event: str, data: dict) -> None:
        pass

    # Run the full workflow
    t0 = time.perf_counter()
    try:
        state = await run_workflow(body.message, history, _noop_emit)
    except Exception as exc:
        if audit_id:
            try:
                await finalize_audit_error(audit_id, error_message=str(exc))
            except Exception:
                logger.warning("Audit finalize (error) failed", exc_info=True)
        raise

    total_ms = round((time.perf_counter() - t0) * 1000)

    # Persist assistant response
    sql_queries = "; ".join(t.sql for t in state.tasks if t.sql) or None

    # Build structured artifacts
    artifacts_data: dict[str, Any] = {}
    sql_tasks_list = [
        {"title": t.title, "sql": t.sql}
        for t in state.tasks if t.sql
    ]
    if sql_tasks_list:
        artifacts_data["sql_tasks"] = sql_tasks_list
    tables_list = []
    for t in state.tasks:
        if t.result and t.result.rows:
            tables_list.append({
                "title": t.title,
                "columns": t.result.columns,
                "rows": t.result.rows[:50],
            })
    if tables_list:
        artifacts_data["tables"] = tables_list
    if state.chart_spec:
        artifacts_data["chart"] = state.chart_spec

    metrics_data: dict[str, Any] = {
        "total_ms": total_ms,
        "llm_ms": state.llm_ms,
        "db_ms": state.db_ms,
        "rows_returned": state.rows_returned,
    }

    await add_message(
        session_id, "assistant", state.answer_text,
        sql_query=sql_queries,
        artifacts_json=artifacts_data or None,
        assumptions=state.assumptions or None,
        followups=state.follow_ups or None,
        metrics_json=metrics_data,
    )

    # Finalize audit
    if audit_id:
        try:
            await finalize_audit_success(
                audit_id,
                tasks_count=len(state.tasks),
                retries_used=state.retries_used,
                tables_used=state.tables_used,
                metrics_used=state.metrics_used,
                timings_ms={"total_ms": total_ms, "llm_ms": state.llm_ms, "db_ms": state.db_ms},
                rows_returned=state.rows_returned,
            )
        except Exception:
            logger.warning("Audit finalize (success) failed", exc_info=True)

    # Return full message history
    messages = await list_messages(user_id, session_id)

    return ChatResponse(
        session_id=session_id,
        answer=state.answer_text,
        messages=messages,
    )
