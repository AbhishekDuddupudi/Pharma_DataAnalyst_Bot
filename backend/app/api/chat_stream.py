"""
SSE streaming chat endpoint.

POST /api/chat/stream
  → text/event-stream with progress statuses, tokens, and artifacts.

Uses the same session / message persistence as the non-streaming endpoint.
"""

from __future__ import annotations

import asyncio
import json
import traceback
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.security.cookies import SESSION_COOKIE
from app.security.deps import require_auth
from app.services.auth_service import get_session as get_auth_session, get_user_by_id
from app.services.chat_history import (
    add_message,
    create_session,
    get_session,
    maybe_auto_title,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["chat"])

# ── Request schema ───────────────────────────────────────────────


class StreamChatRequest(BaseModel):
    session_id: int | None = None
    message: str


# ── Pipeline steps ───────────────────────────────────────────────

PIPELINE_STEPS = [
    ("preprocess_input", "Preprocessing your question…"),
    ("analysis_planner", "Planning the analysis…"),
    ("sql_generator", "Generating SQL query…"),
    ("sql_validator", "Validating SQL…"),
    ("sql_executor", "Running query…"),
    ("response_synthesizer", "Writing answer…"),
]

# Simulated delay between steps (seconds) — keeps UI feeling real
_STEP_DELAY = 0.15
_TOKEN_DELAY = 0.03


# ── SSE helpers ──────────────────────────────────────────────────


def _sse(event: str, data: dict) -> str:
    """Format a single SSE frame."""
    payload = json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


# ── Stream generator ────────────────────────────────────────────


async def _generate_stream(
    user_id: int,
    body: StreamChatRequest,
) -> AsyncGenerator[str, None]:
    """
    Async generator that yields SSE frames.
    Handles session creation, message persistence, and the stub pipeline.
    """
    session_id: int | None = None

    try:
        # ── Resolve / create session ──────────────────────────
        if body.session_id is not None:
            session = await get_session(user_id, body.session_id)
            if session is None:
                yield _sse("error", {"message": "Session not found"})
                return
            session_id = session["id"]
        else:
            new_session = await create_session(user_id)
            session_id = new_session["id"]

        # Emit session event first
        yield _sse("session", {"session_id": session_id})

        # ── Store user message ────────────────────────────────
        await add_message(session_id, "user", body.message)
        await maybe_auto_title(session_id)

        # ── Pipeline status steps ─────────────────────────────
        for step_name, step_msg in PIPELINE_STEPS:
            yield _sse("status", {"step": step_name, "message": step_msg})
            await asyncio.sleep(_STEP_DELAY)

        # ── Emit SQL artifact (stub) ──────────────────────────
        stub_sql = "SELECT 'stub — real agent coming next' AS info;"
        yield _sse("artifact_sql", {"sql": stub_sql})

        # ── Emit table artifact (stub) ────────────────────────
        yield _sse("artifact_table", {
            "columns": ["info"],
            "rows": [["Stub result — real query coming in P6"]],
        })

        # ── Emit chart artifact (stub) ────────────────────────
        yield _sse("artifact_chart", {
            "chartSpec": {"type": "placeholder", "note": "Chart available in P6"},
        })

        # ── Stream assistant answer tokens ────────────────────
        stub_answer = (
            "I've analyzed your question. "
            "The data pipeline is being set up — "
            "once the agent is connected, "
            "I'll generate real SQL, run it against your database, "
            "and provide charts and tables. "
            "Stay tuned!"
        )

        # Build full text in memory while streaming tokens
        full_text_parts: list[str] = []

        for token in _tokenize(stub_answer):
            yield _sse("token", {"text": token})
            full_text_parts.append(token)
            await asyncio.sleep(_TOKEN_DELAY)

        # ── Persist assistant message ─────────────────────────
        full_text = "".join(full_text_parts)
        await add_message(session_id, "assistant", full_text, sql_query=stub_sql)

        # ── Complete ──────────────────────────────────────────
        yield _sse("complete", {"ok": True})

    except Exception as exc:
        logger.exception("Streaming error for user %s", user_id)
        yield _sse("error", {"message": str(exc)})


def _tokenize(text: str) -> list[str]:
    """
    Split text into small chunks that feel like token-by-token streaming.
    Splits on word boundaries, keeping spaces attached.
    """
    tokens: list[str] = []
    current = ""
    for ch in text:
        current += ch
        if ch in (" ", ",", ".", "!", "?", ";", "—", "\n"):
            tokens.append(current)
            current = ""
    if current:
        tokens.append(current)
    return tokens


# ── Endpoint ─────────────────────────────────────────────────────


@router.post("/chat/stream")
async def chat_stream(
    body: StreamChatRequest,
    user: dict[str, Any] = Depends(require_auth),
):
    """
    SSE streaming chat endpoint.
    Returns ``text/event-stream`` with progress, tokens, and artifacts.
    """
    return StreamingResponse(
        _generate_stream(user["id"], body),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering if present
        },
    )
