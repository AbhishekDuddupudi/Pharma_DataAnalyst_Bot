"""
Agentic text-to-SQL workflow – 10-node orchestration.

Nodes:
    1. preprocess_input      – normalise, extract entities
    2. scope_policy_check    – reject off-topic / dangerous requests
    3. semantic_grounding     – map question to schema concepts
    4. analysis_planner       – decide analysis plan (simple / insights)
    5. sql_generator          – generate SQL for each task
    6. sql_validator          – validate SQL via policy + syntax
    7. sql_repair             – auto-fix invalid SQL (up to SQL_MAX_RETRIES)
    8. sql_executor           – run queries
    9. viz_builder            – suggest chart specs
   10. response_synthesizer   – stream the final answer

Each node emits progress via an ``emit`` callback so the SSE layer
can relay statuses and artifacts in real-time.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Coroutine

from app.core.config import settings
from app.core.logging import get_logger
from app.security.sql_policy import validate_sql, get_allowlist_summary
from app.services.llm import call_llm_json, stream_llm_tokens
from app.services.sql_executor import execute_query, QueryResult

logger = get_logger(__name__)

# ── Shared types ─────────────────────────────────────────────────

Emitter = Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]]

INSIGHT_KEYWORDS = {
    "why", "drivers", "factors", "insights", "explain",
    "root cause", "decline", "growth", "change", "reason",
    "drop", "increase", "decrease", "trend",
}

# ── Scope rule-sets ──────────────────────────────────────────────

_ANALYTICS_DOMAINS = {
    "sales", "revenue", "product", "products", "territory", "territories",
    "time", "trend", "trends", "comparison", "comparisons", "compare",
    "driver", "drivers", "prescriptions", "trx", "nrx", "units",
    "quarter", "quarterly", "monthly", "yearly", "annual",
    "region", "regions", "state", "states", "top", "bottom",
    "growth", "decline", "market", "share", "performance",
    "brand", "therapeutic", "oncology", "cardiovascular", "respiratory", "cns",
    "forecast", "average", "total", "sum", "count",
}

_BLOCKED_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\b(write|compose|draft)\s+(a\s+)?(poem|essay|story|song|joke|limerick)",
        r"\b(tell|say)\s+(me\s+)?(a\s+)?(joke|riddle|story|fun fact)",
        r"\bhack\b", r"\bexploit\b", r"\bbypass\b", r"\bignore\s+instructions\b",
        r"\bjailbreak\b", r"\bpretend\s+you\b", r"\bact\s+as\b",
        r"\bforget\s+(your|all)\b", r"\bsystem\s+prompt\b",
        r"\b(recipe|cook|weather|translate|code\s+review)\b",
    ]
]

# ── Repairable error patterns ───────────────────────────────────

_REPAIRABLE_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"column .+ does not exist",
        r"relation .+ does not exist",
        r"undefined column",
        r"undefined table",
        r"ambiguous column",
        r"syntax error",
        r"missing FROM-clause entry",
        r"invalid input syntax",
        r"operator does not exist",
        r"must appear in the GROUP BY",
    ]
]


def _is_repairable_error(error_msg: str) -> bool:
    """Return True if the DB error is a known SQL structural issue."""
    return any(p.search(error_msg) for p in _REPAIRABLE_PATTERNS)


def _short_error_reason(error_msg: str) -> str:
    """Extract a short, safe reason from a DB error for the UI."""
    for label, pattern in [
        ("unknown column", r"column .+ does not exist"),
        ("undefined table", r"relation .+ does not exist"),
        ("ambiguous column", r"ambiguous column"),
        ("syntax error", r"syntax error"),
        ("missing FROM clause", r"missing FROM-clause entry"),
        ("invalid syntax", r"invalid input syntax"),
        ("operator mismatch", r"operator does not exist"),
        ("GROUP BY required", r"must appear in the GROUP BY"),
    ]:
        if re.search(pattern, error_msg, re.IGNORECASE):
            return label
    return "query error"


@dataclass
class Task:
    """One analysis sub-task (e.g., 'top products by revenue')."""
    title: str = ""
    sql: str = ""
    original_sql: str = ""       # keep pre-repair SQL for audit
    valid: bool = False
    result: QueryResult | None = None
    error: str | None = None


@dataclass
class WorkflowState:
    """Accumulates state across nodes."""
    user_message: str = ""
    history: list[dict] = field(default_factory=list)
    mode: str = "simple"          # "simple" or "insights"
    preprocessed: str = ""
    grounding: str = ""
    grounding_parsed: dict = field(default_factory=dict)
    tasks: list[Task] = field(default_factory=list)
    chart_spec: dict | None = None
    answer_text: str = ""
    # Metrics / audit
    total_t0: float = 0.0
    llm_ms: int = 0
    db_ms: int = 0
    rows_returned: int = 0
    tokens_streamed: int = 0
    retries_used: int = 0
    rejected: bool = False
    reject_reason: str = ""
    blocked: bool = False
    needs_clarification: bool = False
    clarification_questions: list[str] = field(default_factory=list)
    tables_used: list[str] = field(default_factory=list)
    metrics_used: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    follow_ups: list[str] = field(default_factory=list)


# ── Schema loader ────────────────────────────────────────────────

_schema_cache: dict | None = None


def _load_schema() -> dict:
    global _schema_cache
    if _schema_cache is None:
        p = Path(__file__).resolve().parent.parent / "catalog" / "semantic_schema.json"
        _schema_cache = json.loads(p.read_text())
    return _schema_cache


def _schema_summary() -> str:
    """Build a compact textual summary for LLM system prompts."""
    schema = _load_schema()
    parts: list[str] = []
    for tname, tinfo in schema["tables"].items():
        cols = ", ".join(
            f"{c} ({ci['type']})" for c, ci in tinfo["columns"].items()
        )
        parts.append(f"• {tname}: {tinfo['description']}\n  Columns: {cols}")
    parts.append("\nJoins: " + "; ".join(
        f"{j['from']} → {j['to']}" for j in schema["joins"]
    ))
    parts.append("\nMetrics: " + "; ".join(
        f"{k}: {v['aggregation']}" for k, v in schema["metrics"].items()
    ))
    entities = schema.get("known_entities", {})
    if entities.get("products"):
        parts.append(f"\nKnown products: {', '.join(entities['products'])}")
    if entities.get("therapeutic_areas"):
        parts.append(f"Therapeutic areas: {', '.join(entities['therapeutic_areas'])}")
    if entities.get("regions"):
        parts.append(f"Regions: {', '.join(entities['regions'])}")
    notes = schema.get("data_notes", [])
    if notes:
        parts.append("\nData notes:\n" + "\n".join(f"  - {n}" for n in notes))
    return "\n".join(parts)


# ── Node implementations ────────────────────────────────────────


async def _preprocess_input(state: WorkflowState, emit: Emitter) -> None:
    """Node 1: normalise, strip noise, detect mode."""
    await emit("status", {"step": "preprocess_input", "message": "Preprocessing your question…"})

    msg = state.user_message.strip()
    lower = msg.lower()

    # Detect insights mode
    if any(kw in lower for kw in INSIGHT_KEYWORDS):
        state.mode = "insights"
    else:
        state.mode = "simple"

    state.preprocessed = msg
    logger.info("Preprocess: mode=%s", state.mode)


async def _scope_policy_check(state: WorkflowState, emit: Emitter) -> None:
    """
    Node 2: rules-first scope gate.

    1. Blocked patterns → instant reject (no LLM).
    2. Analytics domain keyword match → instant allow.
    3. Greetings / small-talk about the bot → allow.
    4. Ambiguous → fall back to LLM scope check.
    """
    lower = state.preprocessed.lower()
    words = set(re.findall(r"[a-z]+", lower))

    # ── Step A: blocked patterns ──────────────────────────────
    for pat in _BLOCKED_PATTERNS:
        if pat.search(lower):
            state.blocked = True
            state.rejected = True
            state.reject_reason = "This doesn't look like a pharmaceutical data question."
            await emit("status", {
                "step": "scope_policy_check",
                "message": "Blocked (rules) — off-topic request",
            })
            logger.info("Scope BLOCKED by rules: %s", state.preprocessed[:80])
            return

    # ── Step B: greetings / small-talk about the bot → allow ─
    greeting_words = {"hi", "hello", "hey", "help", "what", "who", "how"}
    bot_words = {"bot", "analyst", "you", "pharma"}
    if words & greeting_words and (len(words) < 6 or words & bot_words):
        await emit("status", {
            "step": "scope_policy_check",
            "message": "Allowed (rules) — greeting / help",
        })
        logger.info("Scope ALLOWED by rules: greeting")
        return

    # ── Step C: analytics domain match → allow ───────────────
    domain_overlap = words & _ANALYTICS_DOMAINS
    if len(domain_overlap) >= 2:
        # ── Step C2: vague insight detection ──────────────────
        # If it's an insight-mode question, check for specificity
        if state.mode == "insights":
            schema = _load_schema()
            known = schema.get("known_entities", {})
            known_products = {p.lower() for p in (known.get("products") or [])}
            known_areas = {a.lower() for a in (known.get("therapeutic_areas") or [])}
            known_regions = {r.lower() for r in (known.get("regions") or [])}
            time_words = {"2023", "2024", "2025", "q1", "q2", "q3", "q4",
                          "january", "february", "march", "april", "may", "june",
                          "july", "august", "september", "october", "november", "december",
                          "last year", "this year", "ytd", "year"}

            has_product = bool(words & known_products) or bool(words & known_areas)
            has_region = bool(words & known_regions)
            has_time = bool(words & time_words)

            missing: list[str] = []
            if not has_product:
                missing.append("Which product or therapeutic area?")
            if not has_region:
                missing.append("Which region or territory?")
            if not has_time:
                missing.append("What time period (e.g. Q1 2024, last year)?")

            if len(missing) >= 2:
                state.needs_clarification = True
                state.clarification_questions = missing
                await emit("status", {
                    "step": "scope_policy_check",
                    "message": "Need clarification — question is too vague",
                })
                logger.info("Scope CLARIFICATION needed: missing=%s", missing)
                return

        await emit("status", {
            "step": "scope_policy_check",
            "message": "Allowed (rules) — analytics question detected",
        })
        logger.info("Scope ALLOWED by rules: domains=%s", domain_overlap)
        return

    # ── Step D: ambiguous → LLM fallback ─────────────────────
    await emit("status", {
        "step": "scope_policy_check",
        "message": "Ambiguous → using LLM scope check",
    })
    logger.info("Scope ambiguous, calling LLM")

    system = (
        "You are a scope-checking agent for a pharmaceutical data analyst bot. "
        "The bot can ONLY answer questions about pharmaceutical sales data "
        "(sales, revenue, prescriptions, products, territories, trends, comparisons). "
        "Return JSON: {\"in_scope\": true/false, \"reason\": \"...\"}\n"
        "If the question is a greeting or chitchat about the bot itself, mark in_scope=true."
    )

    resp = await call_llm_json(system, state.preprocessed)
    state.llm_ms += resp["llm_ms"]
    result = resp["result"]

    if not result.get("in_scope", True):
        state.rejected = True
        state.reject_reason = result.get("reason", "Question is out of scope.")
        await emit("status", {
            "step": "scope_policy_check",
            "message": f"Blocked (LLM) — {state.reject_reason[:60]}",
        })
        logger.info("Scope REJECTED by LLM: %s", state.reject_reason)
    else:
        await emit("status", {
            "step": "scope_policy_check",
            "message": "Allowed (LLM)",
        })


async def _semantic_grounding(state: WorkflowState, emit: Emitter) -> None:
    """Node 3: map question to schema concepts."""
    await emit("status", {"step": "semantic_grounding", "message": "Mapping to schema…"})

    schema_text = _schema_summary()
    system = (
        "You are a semantic grounding agent. Given a user question and the database schema below, "
        "identify the relevant tables, columns, filters, time ranges, and metrics.\n\n"
        f"SCHEMA:\n{schema_text}\n\n"
        "Return JSON: {\"tables\": [...], \"columns\": [...], \"filters\": [...], "
        "\"time_range\": \"...\", \"metrics\": [...], \"notes\": \"...\"}"
    )

    resp = await call_llm_json(system, state.preprocessed)
    state.llm_ms += resp["llm_ms"]
    state.grounding_parsed = resp["result"]
    state.grounding = json.dumps(resp["result"], indent=2)
    # Track tables & metrics for audit
    state.tables_used = resp["result"].get("tables", [])
    state.metrics_used = resp["result"].get("metrics", [])
    logger.info("Grounding done: %d tokens", resp["tokens_out"])


async def _analysis_planner(state: WorkflowState, emit: Emitter) -> None:
    """Node 4: create analysis plan with 1-4 sub-tasks."""
    await emit("status", {"step": "analysis_planner", "message": "Planning analysis…"})

    schema_text = _schema_summary()
    n_tasks = "3-4" if state.mode == "insights" else "1"

    system = (
        "You are an analysis planner for a pharmaceutical data analyst bot.\n"
        f"Create {n_tasks} analysis tasks to answer the user's question.\n\n"
        f"SCHEMA:\n{schema_text}\n\n"
        f"GROUNDING:\n{state.grounding}\n\n"
        "Return JSON: {\"tasks\": [{\"title\": \"...\", \"description\": \"...\"}]}\n"
        "Each task should be a self-contained analytical query. "
        "Keep titles concise (under 10 words)."
    )

    resp = await call_llm_json(system, state.preprocessed)
    state.llm_ms += resp["llm_ms"]
    tasks_data = resp["result"].get("tasks", [{"title": "Main query", "description": state.preprocessed}])

    state.tasks = [Task(title=t.get("title", f"Task {i+1}")) for i, t in enumerate(tasks_data)]
    logger.info("Planner: %d tasks", len(state.tasks))


async def _sql_generator(state: WorkflowState, emit: Emitter) -> None:
    """Node 5: generate SQL for each task."""
    await emit("status", {"step": "sql_generator", "message": "Generating SQL…"})

    schema_text = _schema_summary()
    policy = get_allowlist_summary()

    # Build history context
    history_text = ""
    if state.history:
        hist_lines = []
        for h in state.history[-5:]:
            hist_lines.append(f"{h['role'].upper()}: {h['content'][:200]}")
        history_text = "\nRecent conversation:\n" + "\n".join(hist_lines) + "\n"

    for task in state.tasks:
        system = (
            "You are a PostgreSQL SQL generator for pharmaceutical sales data.\n"
            f"SCHEMA:\n{schema_text}\n\n"
            f"GROUNDING:\n{state.grounding}\n\n"
            f"POLICY: {policy}\n"
            f"{history_text}"
            "Generate ONLY a valid PostgreSQL SELECT query. No explanation.\n"
            "Return JSON: {\"sql\": \"SELECT ...\"}\n"
            "Rules:\n"
            "- Use these aliases: fact_sales AS fs, dim_product AS dp, dim_territory AS dtr, dim_time AS dt.\n"
            "- The date column is on fact_sales (fs.date) and dim_time (dt.date). dim_territory does NOT have a date column.\n"
            "- JOIN fact_sales to dim_time via fs.date = dt.date to get year, quarter, month etc.\n"
            "- JOIN fact_sales to dim_territory via fs.territory_id = dtr.territory_id for region, state etc.\n"
            "- JOIN fact_sales to dim_product via fs.product_id = dp.product_id for brand_name etc.\n"
            "- Use aggregation functions (SUM, AVG, COUNT) as appropriate.\n"
            "- Include ORDER BY and reasonable LIMIT.\n"
            "- Alias columns with readable names.\n"
            "- Do NOT use CTEs unless necessary."
        )

        user_prompt = f"Task: {task.title}\nUser question: {state.preprocessed}"

        resp = await call_llm_json(system, user_prompt)
        state.llm_ms += resp["llm_ms"]
        task.sql = resp["result"].get("sql", "").strip()
        task.original_sql = task.sql
        logger.info("SQL generated for '%s': %s", task.title, task.sql[:100])


async def _sql_validator(state: WorkflowState, emit: Emitter) -> None:
    """Node 6: validate generated SQL."""
    await emit("status", {"step": "sql_validator", "message": "Validating SQL…"})

    for task in state.tasks:
        result = validate_sql(task.sql)
        task.valid = result.valid
        if not result.valid:
            task.error = "; ".join(result.errors)
            logger.warning("Validation failed for '%s': %s", task.title, task.error)
        else:
            task.error = None


async def _sql_repair(state: WorkflowState, emit: Emitter) -> None:
    """Node 7: attempt to fix invalid SQL (up to SQL_MAX_RETRIES)."""
    needs_repair = [t for t in state.tasks if not t.valid]
    if not needs_repair:
        return

    schema_text = _schema_summary()
    policy = get_allowlist_summary()

    for task in needs_repair:
        for attempt in range(settings.SQL_MAX_RETRIES):
            reason = _short_error_reason(task.error or "validation error")
            state.retries_used += 1

            await emit("status", {
                "step": "sql_repair",
                "message": f"SQL failed validation → repair ({attempt + 1}/{settings.SQL_MAX_RETRIES})",
            })
            await emit("retry", {
                "type": "validator",
                "attempt": attempt + 1,
                "max": settings.SQL_MAX_RETRIES,
                "reason": reason,
            })
            system = (
                "You are a SQL repair agent. Fix the invalid SQL query.\n"
                f"SCHEMA:\n{schema_text}\n\n"
                f"POLICY: {policy}\n\n"
                "IMPORTANT: Use aliases: fact_sales AS fs, dim_product AS dp, dim_territory AS dtr, dim_time AS dt.\n"
                "The date column belongs to fact_sales (fs.date) and dim_time (dt.date). dim_territory does NOT have a date column.\n\n"
                f"Error: {task.error}\n"
                f"Original SQL:\n{task.sql}\n\n"
                "Return JSON: {\"sql\": \"SELECT ...\"}\n"
                "Fix ONLY the errors. Keep the query intent the same."
            )

            resp = await call_llm_json(system, f"Fix this SQL for: {task.title}")
            state.llm_ms += resp["llm_ms"]
            task.sql = resp["result"].get("sql", task.sql).strip()

            result = validate_sql(task.sql)
            task.valid = result.valid
            if result.valid:
                task.error = None
                logger.info("SQL repaired for '%s' on attempt %d", task.title, attempt + 1)
                break
            else:
                task.error = "; ".join(result.errors)
                logger.warning("Repair attempt %d failed for '%s': %s", attempt + 1, task.title, task.error)


async def _sql_executor_node(state: WorkflowState, emit: Emitter) -> None:
    """
    Node 8: execute validated SQL queries.

    Only auto-repairs on known SQL structural errors (unknown column, syntax, etc.).
    Data/logic issues (empty results) are NOT auto-repaired.
    Emits artifact_sql (with final SQL) then artifact_table per successful task.
    """
    await emit("status", {"step": "sql_executor", "message": "Running queries…"})

    schema_text = _schema_summary()
    policy = get_allowlist_summary()

    for task in state.tasks:
        if not task.valid:
            task.error = task.error or "SQL validation failed"
            continue

        # Try execution with auto-repair on known SQL errors only
        max_attempts = settings.SQL_MAX_RETRIES + 1
        for attempt in range(max_attempts):
            try:
                result = await execute_query(task.sql)
                task.result = result
                task.error = None
                state.db_ms += result.db_ms
                state.rows_returned += result.row_count
                break  # Success

            except (ValueError, RuntimeError) as exc:
                error_str = str(exc)
                task.error = error_str
                logger.warning(
                    "Execution attempt %d failed for '%s': %s",
                    attempt + 1, task.title, exc,
                )

                # Only attempt repair on known SQL structural errors
                if attempt < max_attempts - 1 and _is_repairable_error(error_str):
                    reason = _short_error_reason(error_str)
                    state.retries_used += 1

                    await emit("status", {
                        "step": "sql_repair",
                        "message": f"Query error → repair ({attempt + 1}/{settings.SQL_MAX_RETRIES})",
                    })
                    await emit("retry", {
                        "type": "db",
                        "attempt": attempt + 1,
                        "max": settings.SQL_MAX_RETRIES,
                        "reason": reason,
                    })

                    repair_system = (
                        "You are a SQL repair agent. The query below produced a database error.\n"
                        f"SCHEMA:\n{schema_text}\n\n"
                        f"POLICY: {policy}\n\n"
                        "IMPORTANT: Use aliases: fact_sales AS fs, dim_product AS dp, dim_territory AS dtr, dim_time AS dt.\n"
                        "The date column belongs to fact_sales (fs.date) and dim_time (dt.date). "
                        "dim_territory does NOT have a date column.\n\n"
                        f"Database error: {task.error}\n"
                        f"Broken SQL:\n{task.sql}\n\n"
                        "Return JSON: {{\"sql\": \"SELECT ...\"}}\n"
                        "Fix the error. Keep the query intent the same."
                    )
                    resp = await call_llm_json(repair_system, f"Fix SQL for: {task.title}")
                    state.llm_ms += resp["llm_ms"]
                    task.sql = resp["result"].get("sql", task.sql).strip()

                    # Re-validate policy
                    vr = validate_sql(task.sql)
                    if not vr.valid:
                        task.error = "; ".join(vr.errors)
                        break
                else:
                    # Not repairable or out of retries
                    break

    # ── Emit artifact_sql (all tasks, with final SQL) ─────────
    sql_artifacts: list[dict] = []
    for task in state.tasks:
        entry: dict = {"title": task.title, "sql": task.sql}
        if task.error:
            entry["error"] = task.error
        sql_artifacts.append(entry)
    await emit("artifact_sql", {"tasks": sql_artifacts})

    # ── Emit artifact_table per successful task ───────────────
    for task in state.tasks:
        if task.result and task.result.row_count >= 0:
            await emit("artifact_table", {
                "task_title": task.title,
                "columns": task.result.columns,
                "rows": task.result.rows,
                "row_count": task.result.row_count,
                "truncated": task.result.truncated,
            })


async def _viz_builder(state: WorkflowState, emit: Emitter) -> None:
    """Node 9: suggest a chart specification."""
    await emit("status", {"step": "viz_builder", "message": "Building visualisation…"})

    # Find the first task with results
    task_with_data = next((t for t in state.tasks if t.result and t.result.row_count > 0), None)
    if not task_with_data or not task_with_data.result:
        state.chart_spec = None
        await emit("artifact_chart", {"available": False})
        return

    result = task_with_data.result
    # Sample a few rows for the LLM
    sample_rows = result.rows[:5]
    data_preview = json.dumps({"columns": result.columns, "sample_rows": sample_rows})

    system = (
        "You are a data visualisation advisor. Given a query result preview, "
        "suggest the best chart type and axes.\n"
        "Return JSON: {\"chart_type\": \"bar|line|pie|table\", "
        "\"x_column\": \"...\", \"y_column\": \"...\", "
        "\"title\": \"...\", \"available\": true}\n"
        "If the data is not suitable for a chart, set available=false."
    )

    resp = await call_llm_json(system, f"Question: {state.preprocessed}\nData:\n{data_preview}")
    state.llm_ms += resp["llm_ms"]
    spec = resp["result"]
    state.chart_spec = spec

    await emit("artifact_chart", spec)


async def _response_synthesizer(state: WorkflowState, emit: Emitter) -> None:
    """Node 10: produce a structured answer with assumptions + follow-ups."""
    await emit("status", {"step": "response_synthesizer", "message": "Writing answer…"})

    # Build context from all task results
    results_text_parts: list[str] = []
    for task in state.tasks:
        if task.result and task.result.row_count > 0:
            # Include column headers + first rows
            cols = ", ".join(task.result.columns)
            rows_sample = task.result.rows[:10]
            rows_text = "\n".join(str(r) for r in rows_sample)
            results_text_parts.append(
                f"Task: {task.title}\nColumns: {cols}\nRows ({task.result.row_count} total):\n{rows_text}"
            )
        elif task.error:
            results_text_parts.append(f"Task: {task.title}\nError: {task.error}")

    results_text = "\n\n".join(results_text_parts) if results_text_parts else "No data was returned."

    system = (
        "You are a pharmaceutical data analyst presenting query results.\n"
        "Return a JSON object with exactly these keys:\n"
        "{\n"
        '  "answer": "...",\n'
        '  "assumptions": ["...", "..."],\n'
        '  "follow_ups": ["...", "..."]\n'
        "}\n\n"
        "Rules for the 'answer' field:\n"
        "- Write a clear, professional summary of the findings.\n"
        "- You may use markdown headings (## or ###), bold (**text**), and bullet lists.\n"
        "- Mention specific values, percentages, and trends from the data.\n"
        "- NEVER include SQL code, query text, or table names in the answer.\n"
        "- Keep it under 250 words.\n"
        "- If there were errors, explain what happened.\n\n"
        "Rules for 'assumptions':\n"
        "- List 1-3 key assumptions made (e.g. time range, metric used, filters applied).\n"
        "- Each assumption should be a short sentence.\n\n"
        "Rules for 'follow_ups':\n"
        "- Suggest 2-3 natural follow-up questions the user might ask next.\n"
        "- Keep each under 12 words.\n"
        "- Make them specific and actionable."
    )

    user_prompt = f"User question: {state.preprocessed}\n\nQuery results:\n{results_text}"

    resp = await call_llm_json(system, user_prompt)
    state.llm_ms += resp["llm_ms"]
    result = resp["result"]

    answer = result.get("answer", "I was unable to generate a summary.")
    state.assumptions = result.get("assumptions", [])
    state.follow_ups = result.get("follow_ups", [])

    # Emit the structured metadata before streaming the answer
    await emit("answer_meta", {
        "assumptions": state.assumptions,
        "follow_ups": state.follow_ups,
    })

    # Stream the answer text token-by-token for the live typing effect
    words = answer.split(" ")
    for i, word in enumerate(words):
        token = word if i == len(words) - 1 else word + " "
        await emit("token", {"text": token})
        state.tokens_streamed += 1

    state.answer_text = answer


# ── Main orchestrator ────────────────────────────────────────────


async def run_workflow(
    user_message: str,
    history: list[dict],
    emit: Emitter,
) -> WorkflowState:
    """
    Execute the full 10-node workflow.

    Parameters:
        user_message – the user's question.
        history – recent conversation history [{role, content}, …].
        emit – async callback ``(event_type, data_dict) -> None``.

    Returns the final WorkflowState with all results.
    """
    state = WorkflowState(
        user_message=user_message,
        history=history,
        total_t0=time.perf_counter(),
    )

    # ── Node 1: preprocess ────────────────────────────────────
    await _preprocess_input(state, emit)

    # ── Node 2: scope check (rules-first) ─────────────────────
    await _scope_policy_check(state, emit)

    if state.blocked:
        # Hard block — stream rejection and end
        await emit("status", {"step": "response_synthesizer", "message": "Responding…"})
        rejection = f"I can only help with pharmaceutical sales data questions. {state.reject_reason}"
        for word in rejection.split(" "):
            await emit("token", {"text": word + " "})
            state.tokens_streamed += 1
        state.answer_text = rejection
        return state

    if state.needs_clarification:
        # Need more info — stream clarification questions
        await emit("status", {"step": "response_synthesizer", "message": "Asking for clarification…"})
        clarification = "I'd like to help! Could you provide more details?\n" + "\n".join(
            f"• {q}" for q in state.clarification_questions
        )
        for word in clarification.split(" "):
            await emit("token", {"text": word + " "})
            state.tokens_streamed += 1
        state.answer_text = clarification
        return state

    if state.rejected:
        # LLM-based rejection
        await emit("status", {"step": "response_synthesizer", "message": "Responding…"})
        rejection = f"I can only help with pharmaceutical sales data questions. {state.reject_reason}"
        for word in rejection.split(" "):
            await emit("token", {"text": word + " "})
            state.tokens_streamed += 1
        state.answer_text = rejection
        return state

    # ── Node 3: semantic grounding ────────────────────────────
    await _semantic_grounding(state, emit)

    # ── Node 4: analysis planner ──────────────────────────────
    await _analysis_planner(state, emit)

    # ── Node 5: sql generator ─────────────────────────────────
    await _sql_generator(state, emit)

    # ── Node 6: sql validator ─────────────────────────────────
    await _sql_validator(state, emit)

    # ── Node 7: sql repair (if needed) ────────────────────────
    await _sql_repair(state, emit)

    # ── Node 8: sql executor ──────────────────────────────────
    await _sql_executor_node(state, emit)

    # ── Node 9: viz builder ───────────────────────────────────
    await _viz_builder(state, emit)

    # ── Node 10: response synthesizer ─────────────────────────
    await _response_synthesizer(state, emit)

    return state
