"""
Memory service – 4-layer memory bundle for multi-turn conversations.

Layers:
    1. recent_messages   – last N messages (short-term recall)
    2. summary           – rolling session summary (long-term context)
    3. context_json      – structured analytical state (metric/dims/filters)
    4. last_sql_intent   – semantic intent of last SQL tasks (follow-up anchor)

All state is persisted on chat_session so it survives restarts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from app.core.config import settings
from app.core.logging import get_logger
from app.services.llm import call_llm_json

logger = get_logger(__name__)


# ── Types ────────────────────────────────────────────────────────


@dataclass
class MemoryBundle:
    """Read-only snapshot of the 4-layer memory for a session."""
    recent_messages: list[dict[str, str]] = field(default_factory=list)
    summary: str = ""
    context_json: dict[str, Any] = field(default_factory=dict)
    last_sql_intent: dict[str, Any] = field(default_factory=dict)

    def is_follow_up_available(self) -> bool:
        """True when we have a prior SQL intent to anchor follow-ups on."""
        return bool(self.last_sql_intent.get("metric"))

    def format_for_prompt(self) -> str:
        """
        Render the memory bundle as a text block for LLM system prompts.

        Order (most stable → most volatile):
            1. last_sql_intent  (SQL-first memory – source of truth for follow-ups)
            2. context_json     (structured assumptions)
            3. summary          (long-term conversation recap)
            4. recent_messages  (last few turns)
        """
        parts: list[str] = []

        if self.last_sql_intent:
            parts.append(
                "## Previous Analysis Intent (follow-up anchor)\n"
                f"```json\n{json.dumps(self.last_sql_intent, indent=2)}\n```\n"
                "If the user's new question is a follow-up (e.g. 'same for last quarter', "
                "'filter by region', 'break that down by product'), prefer REUSING or "
                "MODIFYING this intent rather than starting from scratch.\n"
                "If the new question changes topic entirely, IGNORE this intent."
            )

        if self.context_json:
            parts.append(
                "## Current Analytical Context\n"
                f"```json\n{json.dumps(self.context_json, indent=2)}\n```"
            )

        if self.summary:
            parts.append(
                "## Conversation Summary\n"
                f"{self.summary}"
            )

        if self.recent_messages:
            recent = "\n".join(
                f"  {m['role'].upper()}: {m['content'][:300]}"
                for m in self.recent_messages[-5:]
            )
            parts.append(f"## Recent Messages\n{recent}")

        return "\n\n".join(parts)


# ── DB helper ────────────────────────────────────────────────────


async def _get_conn() -> asyncpg.Connection:
    dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.connect(dsn)


def _parse_jsonb(val: Any) -> dict:
    """Safely parse a JSONB value that may be str, dict, or None."""
    if val is None:
        return {}
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


# ── A) get_memory_bundle ─────────────────────────────────────────


async def get_memory_bundle(
    user_id: int,
    session_id: int,
) -> MemoryBundle:
    """
    Load the full 4-layer memory for a session.

    Returns a MemoryBundle with:
        recent_messages  – last 5 messages
        summary          – rolling summary text
        context_json     – structured state
        last_sql_intent  – last SQL semantic intent
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
        if session_row is None:
            return MemoryBundle()

        # Recent messages (last 5, chronological)
        msg_rows = await conn.fetch(
            "SELECT role, content FROM ("
            "  SELECT role, content, created_at "
            "  FROM chat_message "
            "  WHERE session_id = $1 "
            "  ORDER BY created_at DESC LIMIT 5"
            ") sub ORDER BY created_at ASC",
            session_id,
        )
        recent = [{"role": r["role"], "content": r["content"]} for r in msg_rows]

        return MemoryBundle(
            recent_messages=recent,
            summary=session_row["summary"] or "",
            context_json=_parse_jsonb(session_row["context_json"]),
            last_sql_intent=_parse_jsonb(session_row["last_sql_intent"]),
        )
    finally:
        await conn.close()


# ── B) update_session_summary ────────────────────────────────────


async def update_session_summary(
    user_id: int,
    session_id: int,
) -> str:
    """
    Generate and persist a 1-2 paragraph rolling summary for the session.

    Uses a cheap LLM call with the previous summary + last ~10 messages +
    result facts (from metadata).  Keeps it concise and stable.
    """
    conn = await _get_conn()
    try:
        # Current summary
        sess = await conn.fetchrow(
            "SELECT summary FROM chat_session WHERE id = $1 AND user_id = $2",
            session_id, user_id,
        )
        if sess is None:
            return ""
        prev_summary = sess["summary"] or "(no prior summary)"

        # Last ~10 messages with condensed metadata
        rows = await conn.fetch(
            "SELECT role, content, metadata FROM ("
            "  SELECT role, content, metadata, created_at "
            "  FROM chat_message WHERE session_id = $1 "
            "  ORDER BY created_at DESC LIMIT 10"
            ") sub ORDER BY created_at ASC",
            session_id,
        )
        msg_lines: list[str] = []
        for r in rows:
            prefix = r["role"].upper()
            content = r["content"][:400]
            line = f"{prefix}: {content}"
            # Append a short "result facts" line from metadata
            meta = _parse_jsonb(r["metadata"])
            if meta.get("tables"):
                for tbl in meta["tables"][:2]:
                    line += f"\n  [Data: {tbl.get('title','')} – {len(tbl.get('rows',[]))} rows]"
            msg_lines.append(line)

        messages_text = "\n".join(msg_lines)

        system = (
            "You are a conversation summarizer for a pharmaceutical data analyst bot.\n"
            "Given the previous summary and the latest messages, produce an UPDATED summary.\n\n"
            "The summary MUST have these small headings:\n"
            "- **User Goal**: What the user is trying to learn.\n"
            "- **Current Scope**: Product/region/time/metric being analyzed.\n"
            "- **Key Findings**: Important results so far (use numbers).\n"
            "- **Open Assumptions / Follow-ups**: What is still unclear or suggested.\n\n"
            "Rules:\n"
            "- Keep it to 1-2 short paragraphs total (under 200 words).\n"
            "- Be stable: don't lose important earlier context.\n"
            "- Do NOT include SQL or raw data.\n"
            "- Return JSON: {\"summary\": \"...\"}"
        )
        user_prompt = (
            f"Previous summary:\n{prev_summary}\n\n"
            f"Latest messages:\n{messages_text}"
        )

        resp = await call_llm_json(system, user_prompt, max_tokens=512)
        new_summary = resp["result"].get("summary", prev_summary)

        await conn.execute(
            "UPDATE chat_session SET summary = $1 WHERE id = $2",
            new_summary, session_id,
        )
        logger.info("Session %s summary updated (%d chars)", session_id, len(new_summary))
        return new_summary
    finally:
        await conn.close()


# ── C) update_context_json ───────────────────────────────────────


async def update_context_json(
    user_id: int,
    session_id: int,
    patch: dict[str, Any],
) -> dict[str, Any]:
    """
    Merge *patch* into the session's context_json and persist.

    The context tracks structured analytical state:
        metric, dimensions, filters, time_window, grain, entities
    """
    conn = await _get_conn()
    try:
        row = await conn.fetchrow(
            "SELECT context_json FROM chat_session WHERE id = $1 AND user_id = $2",
            session_id, user_id,
        )
        if row is None:
            return {}

        current = _parse_jsonb(row["context_json"])
        # Merge: patch overwrites keys, but keep existing keys not in patch
        merged = {**current, **patch}
        # Remove None values to keep it clean
        merged = {k: v for k, v in merged.items() if v is not None}

        await conn.execute(
            "UPDATE chat_session SET context_json = $1::jsonb WHERE id = $2",
            json.dumps(merged), session_id,
        )
        logger.info("Session %s context_json updated: %s", session_id, list(merged.keys()))
        return merged
    finally:
        await conn.close()


# ── D) update_last_sql_intent ────────────────────────────────────


async def update_last_sql_intent(
    user_id: int,
    session_id: int,
    intent_payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Store the semantic intent of the latest SQL analysis.

    Expected shape:
    {
        "metric": "net_sales_usd",
        "dimensions": ["product", "region", "week"],
        "filters": [{"field":"region","op":"=","value":"Northeast"}],
        "time_window": "2024-Q3",
        "grain": "weekly",
        "tables_used": ["fact_sales","dim_product"],
        "last_sql_tasks": [{"task_id":"t1","purpose":"...","sql":"SELECT ..."}],
        "result_stats": {"rows":120, "min_date":"...", "max_date":"..."}
    }
    """
    conn = await _get_conn()
    try:
        # Ensure we don't store enormous result sets
        payload = _trim_intent(intent_payload)

        await conn.execute(
            "UPDATE chat_session SET last_sql_intent = $1::jsonb WHERE id = $2",
            json.dumps(payload), session_id,
        )
        logger.info(
            "Session %s last_sql_intent updated: metric=%s tables=%s",
            session_id,
            payload.get("metric"),
            payload.get("tables_used"),
        )
        return payload
    finally:
        await conn.close()


def _trim_intent(payload: dict[str, Any]) -> dict[str, Any]:
    """Ensure the intent payload stays small and safe."""
    trimmed = dict(payload)

    # Cap SQL strings in last_sql_tasks
    tasks = trimmed.get("last_sql_tasks", [])
    if isinstance(tasks, list):
        trimmed["last_sql_tasks"] = [
            {
                "task_id": t.get("task_id", f"t{i}"),
                "purpose": str(t.get("purpose", ""))[:200],
                "sql": str(t.get("sql", ""))[:1000],
            }
            for i, t in enumerate(tasks[:4])  # max 4 tasks
        ]

    # Cap result_stats
    stats = trimmed.get("result_stats")
    if isinstance(stats, dict):
        trimmed["result_stats"] = {
            k: v for k, v in stats.items()
            if k in ("rows", "min_date", "max_date", "total_value", "avg_value")
        }

    return trimmed
