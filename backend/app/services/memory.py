"""
Memory service – 4-layer conversation memory bundle.

Layers:
    1. recent_messages  – last N messages (from chat_message table)
    2. summary          – rolling plain-text session summary (chat_session.summary)
    3. context_json     – structured context dict (chat_session.context_json)
    4. last_sql_intent  – last SQL intent payload (chat_session.last_sql_intent)

All functions use the same DB access pattern as chat_history.py.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from app.core.config import settings
from app.core.logging import get_logger
from app.services.llm import call_llm_text

logger = get_logger(__name__)


# ── DB helper ────────────────────────────────────────────────────


async def _get_conn() -> asyncpg.Connection:
    dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.connect(dsn)


# ── 1. Memory bundle (read) ─────────────────────────────────────


async def get_memory_bundle(
    user_id: int,
    session_id: int,
) -> dict[str, Any]:
    """
    Return the full 4-layer memory bundle for a session.

    Keys:
        recent_messages  – last 5 messages (role + content only)
        summary          – str (may be empty)
        context_json     – dict (may be empty)
        last_sql_intent  – dict (may be empty)
    """
    conn = await _get_conn()
    try:
        # Session-level memory columns
        session_row = await conn.fetchrow(
            "SELECT summary, context_json, last_sql_intent "
            "FROM chat_session "
            "WHERE id = $1 AND user_id = $2",
            session_id,
            user_id,
        )

        summary = ""
        context_json: dict = {}
        last_sql_intent: dict = {}

        if session_row:
            summary = session_row["summary"] or ""
            raw_ctx = session_row["context_json"]
            raw_intent = session_row["last_sql_intent"]
            if raw_ctx:
                context_json = json.loads(raw_ctx) if isinstance(raw_ctx, str) else raw_ctx
            if raw_intent:
                last_sql_intent = json.loads(raw_intent) if isinstance(raw_intent, str) else raw_intent

        # Recent messages (role + content only, lightweight)
        rows = await conn.fetch(
            "SELECT role, content FROM chat_message "
            "WHERE session_id = $1 "
            "ORDER BY created_at DESC LIMIT 5",
            session_id,
        )
        recent_messages = [
            {"role": r["role"], "content": r["content"][:500]}
            for r in reversed(rows)
        ]

        return {
            "recent_messages": recent_messages,
            "summary": summary,
            "context_json": context_json,
            "last_sql_intent": last_sql_intent,
        }
    finally:
        await conn.close()


# ── 2. Rolling summary (write) ──────────────────────────────────


async def update_session_summary(
    user_id: int,
    session_id: int,
    result_facts: dict[str, Any] | None = None,
) -> str:
    """
    Generate and persist a rolling session summary.

    Inputs:  previous summary + last 10 messages + result_facts.
    Output:  plain-text summary (no markdown), stored to chat_session.summary.
    """
    conn = await _get_conn()
    try:
        # Fetch current summary
        session_row = await conn.fetchrow(
            "SELECT summary FROM chat_session WHERE id = $1 AND user_id = $2",
            session_id,
            user_id,
        )
        prev_summary = (session_row["summary"] or "") if session_row else ""

        # Fetch last 10 messages
        rows = await conn.fetch(
            "SELECT role, content FROM chat_message "
            "WHERE session_id = $1 "
            "ORDER BY created_at DESC LIMIT 10",
            session_id,
        )
        messages_text = "\n".join(
            f"{r['role'].upper()}: {r['content'][:300]}"
            for r in reversed(rows)
        )

        facts_text = ""
        if result_facts:
            facts_text = f"\nResult facts: {json.dumps(result_facts, default=str)}"

        system = (
            "You are a concise session summariser for a pharmaceutical data analyst bot.\n"
            "Produce a PLAIN TEXT summary (absolutely no markdown: no #, **, *, `, ```).\n"
            "Use these section labels on their own line, followed by a short sentence:\n"
            "  User goal: ...\n"
            "  Current scope: ...\n"
            "  Key findings so far: ...\n"
            "  Open assumptions / follow-ups: ...\n\n"
            "Keep it to 1-2 short paragraphs total. Be factual and specific.\n"
            "If there is a previous summary, UPDATE it (don't repeat everything)."
        )

        user_prompt = (
            f"Previous summary:\n{prev_summary or '(none)'}\n\n"
            f"Recent messages:\n{messages_text}\n"
            f"{facts_text}"
        )

        resp = await call_llm_text(system, user_prompt, max_tokens=512)
        new_summary = resp["text"].strip()

        # Persist
        await conn.execute(
            "UPDATE chat_session SET summary = $1 WHERE id = $2 AND user_id = $3",
            new_summary,
            session_id,
            user_id,
        )
        logger.info("Session %d summary updated (%d chars)", session_id, len(new_summary))
        return new_summary
    finally:
        await conn.close()


# ── 3. Structured context (write) ───────────────────────────────


async def update_context_json(
    user_id: int,
    session_id: int,
    patch: dict[str, Any],
) -> dict[str, Any]:
    """
    Shallow-merge *patch* into the session's context_json and persist.

    Context schema (compact):
        metric, dimensions, filters, time_window, grain,
        last_entities: {product, region, territory},
        user_preferences: {...}
    """
    conn = await _get_conn()
    try:
        row = await conn.fetchrow(
            "SELECT context_json FROM chat_session WHERE id = $1 AND user_id = $2",
            session_id,
            user_id,
        )
        existing: dict = {}
        if row and row["context_json"]:
            raw = row["context_json"]
            existing = json.loads(raw) if isinstance(raw, str) else raw

        # Shallow merge
        merged = {**existing, **patch}

        await conn.execute(
            "UPDATE chat_session SET context_json = $1::jsonb WHERE id = $2 AND user_id = $3",
            json.dumps(merged, default=str),
            session_id,
            user_id,
        )
        logger.info("Session %d context_json updated: keys=%s", session_id, list(merged.keys()))
        return merged
    finally:
        await conn.close()


# ── 4. SQL intent (write) ───────────────────────────────────────


async def update_last_sql_intent(
    user_id: int,
    session_id: int,
    intent_payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Store a compact SQL intent payload to chat_session.last_sql_intent.

    Expected shape:
        metric, dimensions, filters, time_window, grain,
        tables_used, last_sql_tasks [{task_id, purpose, sql}],
        result_stats {rows, min_date, max_date}
    """
    conn = await _get_conn()
    try:
        await conn.execute(
            "UPDATE chat_session SET last_sql_intent = $1::jsonb WHERE id = $2 AND user_id = $3",
            json.dumps(intent_payload, default=str),
            session_id,
            user_id,
        )
        logger.info("Session %d last_sql_intent updated", session_id)
        return intent_payload
    finally:
        await conn.close()
