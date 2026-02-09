"""
SSE streaming chat endpoint.

POST /api/chat/stream
  → text/event-stream with progress statuses, tokens, and artifacts.

Uses the 10-node agentic workflow and persists results to chat_history.

P6 add-ons:
  • Emits request_id event (from X-Request-Id middleware).
  • Emits metrics event before complete.
  • Emits retry / audit SSE events.
  • Checks request.is_disconnected() for cancel correctness.
  • Complete event carries ok, blocked, needs_clarification flags.
  • Audit lifecycle: create on start, finalize on success/error.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.security.deps import require_auth
from app.services.chat_history import (
    add_message,
    create_session,
    get_recent_messages,
    get_session,
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


# ── Request schema ───────────────────────────────────────────────


class StreamChatRequest(BaseModel):
    session_id: int | None = None
    message: str


# ── SSE helpers ──────────────────────────────────────────────────


def _sse(event: str, data: dict) -> str:
    """Format a single SSE frame."""
    payload = json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


# ── Stream generator ────────────────────────────────────────────


async def _generate_stream(
    request: Request,
    user_id: int,
    body: StreamChatRequest,
) -> AsyncGenerator[str, None]:
    """
    Async generator that yields SSE frames.
    Runs the real 10-node workflow via emit callback.
    """
    session_id: int | None = None
    cancelled = False

    # Queue for workflow → SSE bridge
    queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()

    async def emit(event: str, data: dict) -> None:
        """Callback passed to the workflow. Enqueues SSE events."""
        await queue.put((event, data))

    try:
        # ── Emit request_id ───────────────────────────────────
        request_id = getattr(request.state, "request_id", None) or "unknown"
        if request_id != "unknown":
            yield _sse("request_id", {"request_id": request_id})

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

        yield _sse("session", {"session_id": session_id})

        # ── Store user message ────────────────────────────────
        await add_message(session_id, "user", body.message)
        await maybe_auto_title(session_id)

        # ── Load conversation history for context ─────────────
        history_rows = await get_recent_messages(user_id, session_id, limit=6)
        history = [{"role": h["role"], "content": h["content"]} for h in history_rows]

        # ── Create audit log entry ────────────────────────────
        audit_id: int | None = None
        try:
            audit_id = await create_audit_start(
                request_id=request_id,
                user_id=user_id,
                session_id=session_id,
                mode="stream",
            )
        except Exception:
            logger.warning("Failed to create audit entry", exc_info=True)

        # ── Run workflow in a background task ─────────────────
        # The workflow calls emit() which enqueues events.
        # We drain the queue and yield SSE frames.

        workflow_state = None
        workflow_error: str | None = None

        async def _run_workflow():
            nonlocal workflow_state, workflow_error
            try:
                workflow_state = await run_workflow(body.message, history, emit)
            except Exception as exc:
                workflow_error = str(exc)
                logger.exception("Workflow error for user %s", user_id)
            finally:
                await queue.put(None)  # Sentinel

        task = asyncio.create_task(_run_workflow())

        # ── Drain queue → SSE ─────────────────────────────────
        while True:
            # Check for client disconnect
            if await request.is_disconnected():
                cancelled = True
                task.cancel()
                logger.info("Client disconnected, cancelling workflow")
                break

            try:
                item = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            if item is None:
                break  # Workflow done

            event, data = item
            yield _sse(event, data)

        # Wait for task to finish
        try:
            await task
        except asyncio.CancelledError:
            pass

        # ── Handle error from workflow ────────────────────────
        if workflow_error:
            yield _sse("error", {"message": workflow_error})
            if audit_id:
                try:
                    await finalize_audit_error(
                        audit_id,
                        error_message=workflow_error,
                    )
                except Exception:
                    logger.warning("Audit finalize (error) failed", exc_info=True)
            return

        if cancelled:
            return

        # ── Emit metrics ──────────────────────────────────────
        if workflow_state:
            total_ms = round((time.perf_counter() - workflow_state.total_t0) * 1000)
            timings_ms = {
                "total_ms": total_ms,
                "llm_ms": workflow_state.llm_ms,
                "db_ms": workflow_state.db_ms,
            }

            yield _sse("metrics", {
                "total_ms": total_ms,
                "llm_ms": workflow_state.llm_ms,
                "db_ms": workflow_state.db_ms,
                "rows_returned": workflow_state.rows_returned,
                "tokens_streamed": workflow_state.tokens_streamed,
                "retries_used": workflow_state.retries_used,
            })

            # ── Emit audit event ──────────────────────────────
            yield _sse("audit", {
                "request_id": request_id,
                "mode": "stream",
                "tasks_count": len(workflow_state.tasks),
                "retries_used": workflow_state.retries_used,
                "tables_used": workflow_state.tables_used,
                "safety_checks_passed": not workflow_state.blocked and not workflow_state.rejected,
            })

            # ── Persist assistant message ─────────────────────
            sql_queries = "; ".join(t.sql for t in workflow_state.tasks if t.sql)
            await add_message(
                session_id,
                "assistant",
                workflow_state.answer_text,
                sql_query=sql_queries or None,
            )

            # ── Finalize audit ────────────────────────────────
            if audit_id:
                try:
                    await finalize_audit_success(
                        audit_id,
                        tasks_count=len(workflow_state.tasks),
                        retries_used=workflow_state.retries_used,
                        tables_used=workflow_state.tables_used,
                        metrics_used=workflow_state.metrics_used,
                        timings_ms=timings_ms,
                        rows_returned=workflow_state.rows_returned,
                    )
                except Exception:
                    logger.warning("Audit finalize (success) failed", exc_info=True)

        # ── Complete ──────────────────────────────────────────
        complete_data: dict[str, Any] = {"ok": True}
        if workflow_state:
            if workflow_state.blocked:
                complete_data["ok"] = False
                complete_data["blocked"] = True
                complete_data["reason"] = workflow_state.reject_reason
            elif workflow_state.needs_clarification:
                complete_data["ok"] = False
                complete_data["needs_clarification"] = True
                complete_data["questions"] = workflow_state.clarification_questions
            elif workflow_state.rejected:
                complete_data["ok"] = False
                complete_data["blocked"] = True
                complete_data["reason"] = workflow_state.reject_reason
        yield _sse("complete", complete_data)

    except Exception as exc:
        logger.exception("Streaming error for user %s", user_id)
        yield _sse("error", {"message": str(exc)})


# ── Endpoint ─────────────────────────────────────────────────────


@router.post("/chat/stream")
async def chat_stream(
    request: Request,
    body: StreamChatRequest,
    user: dict[str, Any] = Depends(require_auth),
):
    """
    SSE streaming chat endpoint.
    Returns ``text/event-stream`` with progress, tokens, and artifacts.
    """
    return StreamingResponse(
        _generate_stream(request, user["id"], body),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
