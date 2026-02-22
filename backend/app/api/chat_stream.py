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
from app.services.memory import (
    get_memory_bundle,
    update_context_json,
    update_last_sql_intent,
    update_session_summary,
)
from app.agent.workflow import run_workflow
from app.core.logging import get_logger
from app.services.observability import get_tracer

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

        # ── Start Langfuse trace ──────────────────────────────
        tracer = get_tracer()
        trace = tracer.start_trace(
            name="chat.stream",
            request_id=request_id,
            user_id=user_id,
            session_id=session_id,
            metadata={"mode": "stream", "client": "web", "streaming": True},
        )

        # ── Store user message ────────────────────────────────
        await add_message(session_id, "user", body.message)
        await maybe_auto_title(session_id)

        # ── Load conversation history for context ─────────────
        history_rows = await get_recent_messages(user_id, session_id, limit=6)
        history = [{"role": h["role"], "content": h["content"]} for h in history_rows]

        # ── Load memory bundle ────────────────────────────────
        memory_bundle: dict = {}
        try:
            memory_bundle = await get_memory_bundle(user_id, session_id)
        except Exception:
            logger.warning("Failed to load memory bundle", exc_info=True)

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
                workflow_state = await run_workflow(body.message, history, emit, memory_bundle=memory_bundle, tracer=tracer, trace=trace)
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
            tracer.finalize_trace(trace, level="ERROR", status_message=workflow_error[:200])
            tracer.flush()
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
            tracer.log_event(trace, name="stream.cancelled", level="WARNING")
            tracer.finalize_trace(trace, level="WARNING", status_message="Client disconnected")
            tracer.flush()
            return

        # ── Emit metrics ──────────────────────────────────────
        if workflow_state:
            total_ms = round((time.perf_counter() - workflow_state.total_t0) * 1000)
            timings_ms = {
                "total_ms": total_ms,
                "llm_ms": workflow_state.llm_ms,
                "db_ms": workflow_state.db_ms,
            }

            # Build trace link if available
            trace_id = getattr(trace, 'trace_id', '') or getattr(trace, 'id', '') or ''
            trace_url = ''
            if hasattr(trace, 'get_trace_url'):
                try:
                    trace_url = trace.get_trace_url()
                except Exception:
                    pass

            yield _sse("metrics", {
                "total_ms": total_ms,
                "llm_ms": workflow_state.llm_ms,
                "db_ms": workflow_state.db_ms,
                "rows_returned": workflow_state.rows_returned,
                "tokens_streamed": workflow_state.tokens_streamed,
                "retries_used": workflow_state.retries_used,
                **({
                    "langfuse_trace_id": trace_id,
                    "langfuse_url": trace_url,
                } if trace_id else {}),
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

            # Build structured artifacts for dedicated columns
            artifacts_data: dict[str, Any] = {}
            sql_tasks_list = [
                {"title": t.title, "sql": t.sql}
                for t in workflow_state.tasks if t.sql
            ]
            if sql_tasks_list:
                artifacts_data["sql_tasks"] = sql_tasks_list
            tables_list = []
            for t in workflow_state.tasks:
                if t.result and t.result.rows:
                    tables_list.append({
                        "title": t.title,
                        "columns": t.result.columns,
                        "rows": t.result.rows[:50],
                    })
            if tables_list:
                artifacts_data["tables"] = tables_list
            if workflow_state.chart_spec:
                artifacts_data["chart"] = workflow_state.chart_spec

            metrics_data: dict[str, Any] = {
                "total_ms": total_ms,
                "llm_ms": workflow_state.llm_ms,
                "db_ms": workflow_state.db_ms,
                "rows_returned": workflow_state.rows_returned,
            }
            # Include Langfuse trace link if available
            if trace_id:
                metrics_data["langfuse_trace_id"] = trace_id
            if trace_url:
                metrics_data["langfuse_url"] = trace_url

            await add_message(
                session_id,
                "assistant",
                workflow_state.answer_text,
                sql_query=sql_queries or None,
                artifacts_json=artifacts_data or None,
                assumptions=workflow_state.assumptions or None,
                followups=workflow_state.follow_ups or None,
                metrics_json=metrics_data,
            )

            # ── Persist memory (only on successful, non-blocked runs) ──
            if not workflow_state.blocked and not workflow_state.rejected:
                try:
                    # 1. SQL intent
                    grounding = workflow_state.grounding_parsed or {}
                    intent_payload: dict[str, Any] = {
                        "metric": grounding.get("metrics", [None])[0] if grounding.get("metrics") else None,
                        "dimensions": grounding.get("columns", []),
                        "filters": grounding.get("filters", []),
                        "time_window": grounding.get("time_range", ""),
                        "tables_used": workflow_state.tables_used,
                        "last_sql_tasks": [
                            {"title": t.title, "sql": t.sql[:500]}
                            for t in workflow_state.tasks if t.sql
                        ],
                        "result_stats": {
                            "rows": workflow_state.rows_returned,
                        },
                    }
                    await update_last_sql_intent(user_id, session_id, intent_payload)

                    # 2. Context patch
                    ctx_patch: dict[str, Any] = {}
                    if grounding.get("metrics"):
                        ctx_patch["metric"] = grounding["metrics"][0]
                    if grounding.get("columns"):
                        ctx_patch["dimensions"] = grounding["columns"]
                    if grounding.get("filters"):
                        ctx_patch["filters"] = grounding["filters"]
                    if grounding.get("time_range"):
                        ctx_patch["time_window"] = grounding["time_range"]
                    # Extract entities
                    entities: dict[str, str] = {}
                    schema_ents = grounding.get("tables", [])
                    if "dim_product" in schema_ents or "dim_product" in workflow_state.tables_used:
                        entities["product"] = "referenced"
                    if "dim_territory" in schema_ents or "dim_territory" in workflow_state.tables_used:
                        entities["region"] = "referenced"
                    if entities:
                        ctx_patch["last_entities"] = entities
                    if ctx_patch:
                        await update_context_json(user_id, session_id, ctx_patch)

                    # 3. Session summary (async, non-blocking)
                    result_facts = {
                        "tasks_count": len(workflow_state.tasks),
                        "tables_used": workflow_state.tables_used,
                        "rows_returned": workflow_state.rows_returned,
                    }
                    await update_session_summary(user_id, session_id, result_facts)

                except Exception:
                    logger.warning("Memory persistence failed", exc_info=True)

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

        # ── Finalize Langfuse trace ───────────────────────────
        tracer.finalize_trace(trace, output={
            "ok": complete_data.get("ok", False),
            "total_ms": timings_ms.get("total_ms") if workflow_state else None,
        })
        tracer.flush()

    except Exception as exc:
        logger.exception("Streaming error for user %s", user_id)
        yield _sse("error", {"message": str(exc)})
        try:
            tracer.finalize_trace(trace, level="ERROR", status_message=str(exc)[:200])
            tracer.flush()
        except Exception:
            pass


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
