#!/usr/bin/env python3
"""
Eval harness – run golden questions against the workflow directly.

Usage (from host, with docker services up):
    docker compose exec backend python -m eval.run_eval

Or locally (if you have the backend venv):
    cd backend && python -m eval.run_eval

The script:
  1. Loads golden_questions.json
  2. For each test (single or chain), calls run_workflow() directly
  3. Validates outputs: plain text, no markdown/SQL leaks, correct
     blocking/clarifying behaviour, memory reuse for chains
  4. Prints a readable console report + writes reports/latest.json
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Make sure the backend app is importable ──────────────────────
# When running as `python -m eval.run_eval` from backend/, the app
# package is already on sys.path.  When running from the repo root
# we need to add backend/ explicitly.
_backend_dir = Path(__file__).resolve().parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from app.agent.workflow import run_workflow, WorkflowState  # noqa: E402
from app.services.chat_history import (                      # noqa: E402
    create_session,
    add_message,
    get_recent_messages,
)
from app.services.memory import (                             # noqa: E402
    get_memory_bundle,
    update_last_sql_intent,
    update_context_json,
    update_session_summary,
)
from app.core.config import settings                           # noqa: E402
from app.services.observability import get_tracer             # noqa: E402

# ── Constants ────────────────────────────────────────────────────

GOLDEN_PATH = Path(__file__).resolve().parent / "golden_questions.json"
REPORTS_DIR = Path(__file__).resolve().parent / "reports"
DEMO_USER_ID = 1  # matches the seeded demo@example.com user

# Fast mode: set EVAL_FAST=true env var before running.
# Caps insight tasks to 2, skips chart LLM call, skips session summary.
# Keeps SQL generation, validation, and execution checks intact.
EVAL_FAST: bool = settings.EVAL_FAST

# Tokens that MUST NOT appear in plain-text answers
_DEFAULT_BANNED = ["SELECT", "FROM", "JOIN", "###", "**", "```"]

# ── Data classes ─────────────────────────────────────────────────


@dataclass
class CheckResult:
    """Outcome of a single validation check."""
    name: str
    passed: bool
    detail: str = ""


@dataclass
class TurnResult:
    """Result for one turn (question) inside a test."""
    question: str
    answer_text: str = ""
    tasks_count: int = 0
    tables_used: list[str] = field(default_factory=list)
    rows_returned: int = 0
    total_ms: int = 0
    llm_ms: int = 0
    db_ms: int = 0
    blocked: bool = False
    rejected: bool = False
    needs_clarification: bool = False
    checks: list[CheckResult] = field(default_factory=list)


@dataclass
class TestResult:
    """Aggregated result for one test (single or chain)."""
    test_id: str
    test_name: str
    test_type: str
    passed: bool = True
    fail_reason: str = ""
    turns: list[TurnResult] = field(default_factory=list)
    total_ms: int = 0


# ── Workflow runner ──────────────────────────────────────────────


async def _run_one_turn(
    message: str,
    user_id: int,
    session_id: int,
    tracer: Any,
    trace: Any,
) -> tuple[WorkflowState, TurnResult]:
    """Execute a single workflow turn and capture metrics."""

    # Collect emitted events (we don't render them, just store)
    events: list[tuple[str, dict]] = []

    async def _emit(event: str, data: dict) -> None:
        events.append((event, data))

    # Load history + memory just like the real API route does
    history_rows = await get_recent_messages(user_id, session_id, limit=6)
    history = [{"role": h["role"], "content": h["content"]} for h in history_rows]

    memory_bundle: dict = {}
    try:
        memory_bundle = await get_memory_bundle(user_id, session_id)
    except Exception:
        pass

    t0 = time.perf_counter()
    state = await run_workflow(
        message, history, _emit,
        memory_bundle=memory_bundle,
        tracer=tracer,
        trace=trace,
    )
    elapsed_ms = round((time.perf_counter() - t0) * 1000)

    turn = TurnResult(
        question=message,
        answer_text=state.answer_text,
        tasks_count=len(state.tasks),
        tables_used=state.tables_used,
        rows_returned=state.rows_returned,
        total_ms=elapsed_ms,
        llm_ms=state.llm_ms,
        db_ms=state.db_ms,
        blocked=state.blocked or state.rejected,
        rejected=state.rejected,
        needs_clarification=state.needs_clarification,
    )
    return state, turn


async def _persist_turn(
    state: WorkflowState,
    user_id: int,
    session_id: int,
) -> None:
    """
    Persist messages + memory exactly like the real API does,
    so that subsequent turns in a chain see proper memory state.
    """
    # Store assistant message
    sql_queries = "; ".join(t.sql for t in state.tasks if t.sql) or None
    metrics_data = {
        "total_ms": state.llm_ms + state.db_ms,
        "llm_ms": state.llm_ms,
        "db_ms": state.db_ms,
        "rows_returned": state.rows_returned,
    }
    await add_message(
        session_id, "assistant", state.answer_text,
        sql_query=sql_queries,
        metrics_json=metrics_data,
    )

    # Update memory layers (same logic as chat_stream.py)
    if not state.blocked and not state.rejected and not state.needs_clarification:
        try:
            grounding = state.grounding_parsed or {}
            intent_payload: dict[str, Any] = {
                "metric": (grounding.get("metrics", [None])[0]
                           if grounding.get("metrics") else None),
                "dimensions": grounding.get("columns", []),
                "filters": grounding.get("filters", []),
                "time_window": grounding.get("time_range", ""),
                "tables_used": state.tables_used,
                "last_sql_tasks": [
                    {"title": t.title, "sql": t.sql[:500]}
                    for t in state.tasks if t.sql
                ],
                "result_stats": {"rows": state.rows_returned},
            }
            await update_last_sql_intent(user_id, session_id, intent_payload)

            ctx_patch: dict[str, Any] = {}
            if grounding.get("metrics"):
                ctx_patch["metric"] = grounding["metrics"][0]
            if grounding.get("columns"):
                ctx_patch["dimensions"] = grounding["columns"]
            if grounding.get("filters"):
                ctx_patch["filters"] = grounding["filters"]
            if grounding.get("time_range"):
                ctx_patch["time_window"] = grounding["time_range"]
            entities: dict[str, str] = {}
            if "dim_product" in state.tables_used:
                entities["product"] = "referenced"
            if "dim_territory" in state.tables_used:
                entities["region"] = "referenced"
            if entities:
                ctx_patch["last_entities"] = entities
            if ctx_patch:
                await update_context_json(user_id, session_id, ctx_patch)

            result_facts = {
                "tasks_count": len(state.tasks),
                "tables_used": state.tables_used,
                "rows_returned": state.rows_returned,
            }
            if not EVAL_FAST:
                await update_session_summary(user_id, session_id, result_facts)
        except Exception:
            pass  # eval should not crash on memory persistence


# ── Validation checks ────────────────────────────────────────────


def _check_no_banned_tokens(
    answer: str,
    banned: list[str],
) -> list[CheckResult]:
    """Verify answer_text has no markdown/SQL tokens."""
    results: list[CheckResult] = []
    for token in banned:
        found = token in answer
        results.append(CheckResult(
            name=f"no_banned:{token}",
            passed=not found,
            detail=f"Found '{token}' in answer" if found else "",
        ))
    return results


def _check_blocked(
    turn: TurnResult,
    should_block: bool,
) -> list[CheckResult]:
    """Verify blocking behaviour."""
    results: list[CheckResult] = []
    if should_block:
        results.append(CheckResult(
            name="blocked",
            passed=turn.blocked,
            detail="" if turn.blocked else "Expected blocked=true but got false",
        ))
        results.append(CheckResult(
            name="blocked_no_tasks",
            passed=turn.tasks_count == 0,
            detail="" if turn.tasks_count == 0 else f"Expected 0 tasks, got {turn.tasks_count}",
        ))
    else:
        results.append(CheckResult(
            name="not_blocked",
            passed=not turn.blocked,
            detail="" if not turn.blocked else "Unexpectedly blocked",
        ))
    return results


def _check_clarify(
    turn: TurnResult,
    should_clarify: bool,
) -> list[CheckResult]:
    """Verify clarification behaviour."""
    results: list[CheckResult] = []
    if should_clarify:
        results.append(CheckResult(
            name="clarification",
            passed=turn.needs_clarification,
            detail="" if turn.needs_clarification else "Expected clarification but got none",
        ))
        results.append(CheckResult(
            name="clarify_no_tasks",
            passed=turn.tasks_count == 0,
            detail="" if turn.tasks_count == 0 else f"Expected 0 tasks, got {turn.tasks_count}",
        ))
    return results


def _check_analytics(
    turn: TurnResult,
    expect: dict,
) -> list[CheckResult]:
    """Verify analytics-mode expectations (tasks, tables, rows)."""
    results: list[CheckResult] = []
    min_tasks = expect.get("min_tasks", 0)
    if min_tasks > 0:
        ok = turn.tasks_count >= min_tasks
        results.append(CheckResult(
            name="min_tasks",
            passed=ok,
            detail="" if ok else f"Expected >={min_tasks} tasks, got {turn.tasks_count}",
        ))
        # At least some rows returned for analytics
        results.append(CheckResult(
            name="rows_returned",
            passed=turn.rows_returned > 0,
            detail="" if turn.rows_returned > 0 else "No rows returned",
        ))

    must_use = expect.get("must_use_tables", [])
    for tbl in must_use:
        found = tbl in turn.tables_used
        results.append(CheckResult(
            name=f"uses_table:{tbl}",
            passed=found,
            detail="" if found else f"Expected table '{tbl}' not in tables_used={turn.tables_used}",
        ))

    must_not_use = expect.get("must_not_use_tables", [])
    for tbl in must_not_use:
        found = tbl in turn.tables_used
        results.append(CheckResult(
            name=f"not_uses_table:{tbl}",
            passed=not found,
            detail="" if not found else f"Table '{tbl}' should not be used",
        ))
    return results


def _check_chain_memory(
    turns: list[TurnResult],
    expect: dict,
) -> list[CheckResult]:
    """
    Verify follow-up chain behaviour:
      - tables_used remain consistent across turns
      - later turns aren't blocked
    """
    results: list[CheckResult] = []
    if len(turns) < 2:
        return results

    # After the first turn, subsequent turns should reuse similar tables
    base_tables = set(turns[0].tables_used)
    for i, t in enumerate(turns[1:], start=2):
        if t.blocked:
            results.append(CheckResult(
                name=f"chain_turn{i}_not_blocked",
                passed=False,
                detail=f"Turn {i} was unexpectedly blocked",
            ))
            continue

        overlap = base_tables & set(t.tables_used)
        ok = len(overlap) > 0
        results.append(CheckResult(
            name=f"chain_turn{i}_table_overlap",
            passed=ok,
            detail="" if ok else (
                f"Turn {i} tables={t.tables_used} have no overlap "
                f"with turn 1 tables={turns[0].tables_used}"
            ),
        ))

    # Check per-turn min_tasks
    min_each = expect.get("min_tasks_each_turn", [])
    if isinstance(min_each, int):
        min_each = [min_each] * len(turns)
    for i, (t, mt) in enumerate(zip(turns, min_each), start=1):
        ok = t.tasks_count >= mt
        results.append(CheckResult(
            name=f"chain_turn{i}_min_tasks",
            passed=ok,
            detail="" if ok else f"Turn {i}: expected >={mt} tasks, got {t.tasks_count}",
        ))

    return results


# ── Main eval loop ───────────────────────────────────────────────


async def run_eval() -> list[TestResult]:
    """Run all golden questions and return results."""
    # Load test suite
    with open(GOLDEN_PATH) as f:
        suite = json.load(f)
    tests = suite["tests"]

    # Set up tracer (NoOp if Langfuse not configured)
    tracer = get_tracer()

    all_results: list[TestResult] = []
    total = len(tests)

    print("\n" + "=" * 64)
    print("  PHARMA DATA ANALYST BOT – EVAL HARNESS")
    print("=" * 64)
    mode = "FAST (tasks≤2, no chart, no summary)" if EVAL_FAST else "full"
    print(f"  Tests: {total}   Mode: {mode}   User: {DEMO_USER_ID}")
    print(f"  Time:  {datetime.now(timezone.utc).isoformat()[:19]}Z")
    print("=" * 64 + "\n")

    for idx, test in enumerate(tests, 1):
        test_id = test["id"]
        test_name = test["name"]
        test_type = test["type"]
        expect = test["expect"]

        # Create a fresh session per test so chains work independently
        session = await create_session(DEMO_USER_ID)
        session_id = session["id"]

        # Optional Langfuse trace for the whole test
        trace = tracer.start_trace(
            name=f"eval.{test_id}",
            request_id=f"eval-{test_id}-{int(time.time())}",
            user_id=DEMO_USER_ID,
            session_id=session_id,
            metadata={"eval": True, "test_id": test_id, "test_name": test_name},
        )

        banned = expect.get("answer_must_not_contain", _DEFAULT_BANNED)
        tr = TestResult(test_id=test_id, test_name=test_name, test_type=test_type)

        print(f"[{idx}/{total}] {test_type.upper():6s} | {test_id:12s} | {test_name}")

        try:
            if test_type == "single":
                # ── Single-turn test ──────────────────────────
                message = test["input"]
                await add_message(session_id, "user", message)

                state, turn = await _run_one_turn(
                    message, DEMO_USER_ID, session_id, tracer, trace,
                )
                await _persist_turn(state, DEMO_USER_ID, session_id)

                # Run checks
                turn.checks.extend(_check_no_banned_tokens(turn.answer_text, banned))
                turn.checks.extend(_check_blocked(turn, expect.get("should_block", False)))
                turn.checks.extend(_check_clarify(turn, expect.get("should_clarify", False)))
                if not expect.get("should_block") and not expect.get("should_clarify"):
                    turn.checks.extend(_check_analytics(turn, expect))

                tr.turns.append(turn)
                tr.total_ms = turn.total_ms

            elif test_type == "chain":
                # ── Multi-turn chain ──────────────────────────
                turns_input = test["turns"]
                chain_ms = 0

                for turn_idx, message in enumerate(turns_input):
                    await add_message(session_id, "user", message)
                    state, turn = await _run_one_turn(
                        message, DEMO_USER_ID, session_id, tracer, trace,
                    )
                    await _persist_turn(state, DEMO_USER_ID, session_id)

                    # Per-turn checks
                    turn.checks.extend(_check_no_banned_tokens(turn.answer_text, banned))
                    turn.checks.append(CheckResult(
                        name=f"turn{turn_idx+1}_not_blocked",
                        passed=not turn.blocked,
                        detail="" if not turn.blocked else "Follow-up turn was blocked",
                    ))

                    tr.turns.append(turn)
                    chain_ms += turn.total_ms

                # Chain-level memory checks
                memory_checks = _check_chain_memory(tr.turns, expect)
                # Attach chain checks to the last turn for reporting
                if tr.turns:
                    tr.turns[-1].checks.extend(memory_checks)

                tr.total_ms = chain_ms

        except Exception as exc:
            tr.passed = False
            tr.fail_reason = f"EXCEPTION: {exc}"
            print(f"         EXCEPTION: {exc}")

        # Aggregate pass/fail
        all_checks = [c for t in tr.turns for c in t.checks]
        failed = [c for c in all_checks if not c.passed]
        if failed:
            tr.passed = False
            tr.fail_reason = "; ".join(f"{c.name}: {c.detail}" for c in failed[:3])

        status = "\033[92mPASS\033[0m" if tr.passed else "\033[91mFAIL\033[0m"
        ms_str = f"{tr.total_ms:>6d}ms"
        print(f"         {status}  {ms_str}", end="")
        if not tr.passed:
            print(f"  → {tr.fail_reason[:80]}")
        else:
            print()

        # Finalize Langfuse trace
        tracer.finalize_trace(trace, output={
            "passed": tr.passed,
            "total_ms": tr.total_ms,
        })
        tracer.flush()

        all_results.append(tr)

    return all_results


# ── Report rendering ─────────────────────────────────────────────


def _print_summary(results: list[TestResult], report_path: Path | None = None) -> None:
    """Print a clean summary table to stdout."""
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    total_ms_list = [r.total_ms for r in results]
    avg_ms = sum(total_ms_list) / len(total_ms_list) if total_ms_list else 0

    # Sort for slowest-5
    sorted_by_time = sorted(results, key=lambda r: r.total_ms, reverse=True)

    print("\n" + "=" * 64)
    print("  SUMMARY")
    print("=" * 64)
    print(f"  Total:  {len(results)}")
    print(f"  Passed: {passed}  ({100 * passed / len(results):.0f}%)")
    print(f"  Failed: {failed}")
    print(f"  Avg ms: {avg_ms:.0f}")

    if len(total_ms_list) >= 5:
        sorted_ms = sorted(total_ms_list)
        p95_idx = int(0.95 * len(sorted_ms))
        print(f"  P95 ms: {sorted_ms[p95_idx]}")

    print("\n  Slowest 5:")
    for r in sorted_by_time[:5]:
        tag = "PASS" if r.passed else "FAIL"
        print(f"    {r.total_ms:>6d}ms  [{tag}]  {r.test_id}: {r.test_name}")

    if failed > 0:
        print("\n  Failed tests:")
        for r in results:
            if not r.passed:
                print(f"    {r.test_id}: {r.fail_reason[:100]}")

    if report_path:
        print(f"\n  Report: {report_path}")
    print("=" * 64)


def _write_json_report(results: list[TestResult]) -> Path:
    """Write a JSON report to reports/latest.json."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / "latest.json"

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "tests": [],
    }

    for r in results:
        test_entry: dict[str, Any] = {
            "test_id": r.test_id,
            "test_name": r.test_name,
            "type": r.test_type,
            "passed": r.passed,
            "fail_reason": r.fail_reason,
            "total_ms": r.total_ms,
            "turns": [],
        }
        for t in r.turns:
            turn_entry = {
                "question": t.question,
                "answer_preview": t.answer_text[:200],
                "tasks_count": t.tasks_count,
                "tables_used": t.tables_used,
                "rows_returned": t.rows_returned,
                "total_ms": t.total_ms,
                "llm_ms": t.llm_ms,
                "db_ms": t.db_ms,
                "blocked": t.blocked,
                "needs_clarification": t.needs_clarification,
                "checks": [
                    {"name": c.name, "passed": c.passed, "detail": c.detail}
                    for c in t.checks
                ],
            }
            test_entry["turns"].append(turn_entry)

        report["tests"].append(test_entry)

    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    return out_path


# ── Entrypoint ───────────────────────────────────────────────────


async def main() -> None:
    results = await run_eval()
    report_path = _write_json_report(results)
    _print_summary(results, report_path)

    # Exit with non-zero if any test failed
    if sum(1 for r in results if not r.passed):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
