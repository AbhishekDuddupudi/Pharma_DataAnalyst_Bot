"""
Langfuse tracing wrapper – no-op friendly.

If ``LANGFUSE_ENABLED`` is false or keys are missing, every method
silently does nothing (``NoOpTracer``).  When enabled the real
``LangfuseTracer`` creates traces + spans on the Langfuse backend.

Safety: We never send cookies, passwords, API keys, or full DB result
rows.  Only SQL text, row counts, timings, selected tables/columns,
node names, retry counts, and sanitised error messages.
"""

from __future__ import annotations

import time
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# ── Sensitive-key blocklist (never include in span metadata) ─────

_REDACT_KEYS = {
    "cookie", "cookies", "session_id_cookie", "password",
    "openai_api_key", "api_key", "secret", "token",
    "langfuse_secret_key", "langfuse_public_key",
}


def _sanitise(data: dict | None) -> dict | None:
    """Strip sensitive keys and truncate large values."""
    if data is None:
        return None
    clean: dict[str, Any] = {}
    for k, v in data.items():
        if k.lower() in _REDACT_KEYS:
            continue
        # Truncate long strings (e.g. full SQL result dumps)
        if isinstance(v, str) and len(v) > 4_000:
            clean[k] = v[:4_000] + "…[truncated]"
        elif isinstance(v, list) and len(v) > 50:
            clean[k] = v[:50]  # cap list length (e.g. rows)
        else:
            clean[k] = v
    return clean


# ═══════════════════════════════════════════════════════════════════
# No-Op implementations — used when Langfuse is disabled
# ═══════════════════════════════════════════════════════════════════


class _NoOpSpan:
    """Placeholder that silently absorbs all method calls."""

    def end(self, **_kw: Any) -> None:  # noqa: D401
        pass

    def event(self, **_kw: Any) -> None:
        pass

    def generation(self, **_kw: Any) -> "_NoOpSpan":
        return self

    def span(self, **_kw: Any) -> "_NoOpSpan":
        return self


class _NoOpTrace(_NoOpSpan):
    """Placeholder trace."""

    @property
    def trace_id(self) -> str:
        return ""

    def get_trace_url(self) -> str:
        return ""


class NoOpTracer:
    """Tracer that does nothing — returned when Langfuse is disabled."""

    def start_trace(
        self,
        *,
        name: str,
        request_id: str,
        user_id: int | str,
        session_id: int | str | None = None,
        metadata: dict | None = None,
    ) -> _NoOpTrace:
        return _NoOpTrace()

    def start_span(
        self,
        trace: Any,
        *,
        name: str,
        input: dict | str | None = None,
        metadata: dict | None = None,
    ) -> _NoOpSpan:
        return _NoOpSpan()

    def end_span(
        self,
        span: Any,
        *,
        output: dict | str | None = None,
        metadata: dict | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        pass

    def log_generation(
        self,
        trace_or_span: Any,
        *,
        name: str,
        model: str | None = None,
        input: str | dict | None = None,
        output: str | dict | None = None,
        usage: dict | None = None,
        metadata: dict | None = None,
        level: str | None = None,
    ) -> Any:
        return _NoOpSpan()

    def log_event(
        self,
        trace_or_span: Any,
        *,
        name: str,
        metadata: dict | None = None,
        level: str | None = None,
    ) -> None:
        pass

    def finalize_trace(
        self,
        trace: Any,
        *,
        output: dict | str | None = None,
        metadata: dict | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        pass

    def flush(self) -> None:
        pass


# ═══════════════════════════════════════════════════════════════════
# Real Langfuse tracer
# ═══════════════════════════════════════════════════════════════════


class LangfuseTracer:
    """
    Thin wrapper around the Langfuse Python SDK.

    Each ``start_trace`` call creates a Langfuse trace.  Spans and
    generations are children of that trace (or of other spans).
    """

    def __init__(self) -> None:
        from langfuse import Langfuse  # lazy import

        self._lf = Langfuse(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            host=settings.LANGFUSE_HOST,
        )
        logger.info("Langfuse tracer initialised (host=%s)", settings.LANGFUSE_HOST)

    # ── trace lifecycle ──────────────────────────────────────

    def start_trace(
        self,
        *,
        name: str,
        request_id: str,
        user_id: int | str,
        session_id: int | str | None = None,
        metadata: dict | None = None,
    ) -> Any:
        """Create a new Langfuse trace for this request."""
        return self._lf.trace(
            id=request_id,                        # use our request_id as trace id
            name=name,
            user_id=str(user_id),
            session_id=str(session_id) if session_id else None,
            metadata=_sanitise(metadata),
        )

    # ── spans (workflow nodes / DB calls) ────────────────────

    def start_span(
        self,
        trace: Any,
        *,
        name: str,
        input: dict | str | None = None,
        metadata: dict | None = None,
    ) -> Any:
        """Open a child span on a trace or another span."""
        return trace.span(
            name=name,
            input=_sanitise(input) if isinstance(input, dict) else input,
            metadata=_sanitise(metadata),
        )

    def end_span(
        self,
        span: Any,
        *,
        output: dict | str | None = None,
        metadata: dict | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        """Close a span with optional output and metadata."""
        kw: dict[str, Any] = {}
        if output is not None:
            kw["output"] = _sanitise(output) if isinstance(output, dict) else output
        if metadata is not None:
            kw["metadata"] = _sanitise(metadata)
        if level:
            kw["level"] = level
        if status_message:
            kw["status_message"] = status_message
        span.end(**kw)

    # ── LLM generation spans ─────────────────────────────────

    def log_generation(
        self,
        trace_or_span: Any,
        *,
        name: str,
        model: str | None = None,
        input: str | dict | None = None,
        output: str | dict | None = None,
        usage: dict | None = None,
        metadata: dict | None = None,
        level: str | None = None,
    ) -> Any:
        """Record an LLM generation as a child of the given trace/span."""
        kw: dict[str, Any] = {"name": name}
        if model:
            kw["model"] = model
        if input is not None:
            kw["input"] = input if isinstance(input, str) else _sanitise(input)
        if output is not None:
            kw["output"] = output if isinstance(output, str) else _sanitise(output)
        if usage:
            kw["usage"] = usage
        if metadata:
            kw["metadata"] = _sanitise(metadata)
        if level:
            kw["level"] = level
        return trace_or_span.generation(**kw)

    # ── one-shot events (retries, cancellations, etc.) ───────

    def log_event(
        self,
        trace_or_span: Any,
        *,
        name: str,
        metadata: dict | None = None,
        level: str | None = None,
    ) -> None:
        """Log a discrete event on a trace or span."""
        kw: dict[str, Any] = {"name": name}
        if metadata:
            kw["metadata"] = _sanitise(metadata)
        if level:
            kw["level"] = level
        trace_or_span.event(**kw)

    # ── finalize trace ───────────────────────────────────────

    def finalize_trace(
        self,
        trace: Any,
        *,
        output: dict | str | None = None,
        metadata: dict | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        """Update the trace with final output / metrics and flush."""
        kw: dict[str, Any] = {}
        if output is not None:
            kw["output"] = _sanitise(output) if isinstance(output, dict) else output
        if metadata is not None:
            kw["metadata"] = _sanitise(metadata)
        if level:
            kw["level"] = level
        if status_message:
            kw["status_message"] = status_message
        trace.update(**kw)

    def flush(self) -> None:
        """Flush pending events to Langfuse (call before process exit)."""
        try:
            self._lf.flush()
        except Exception:
            logger.warning("Langfuse flush failed", exc_info=True)


# ═══════════════════════════════════════════════════════════════════
# Factory – returns the correct tracer based on config
# ═══════════════════════════════════════════════════════════════════

_tracer_instance: NoOpTracer | LangfuseTracer | None = None


def get_tracer() -> NoOpTracer | LangfuseTracer:
    """
    Return a singleton tracer.

    • Langfuse enabled + keys present → ``LangfuseTracer``
    • Otherwise → ``NoOpTracer`` (zero overhead, no errors)
    """
    global _tracer_instance
    if _tracer_instance is not None:
        return _tracer_instance

    if (
        settings.LANGFUSE_ENABLED
        and settings.LANGFUSE_PUBLIC_KEY
        and settings.LANGFUSE_SECRET_KEY
    ):
        try:
            _tracer_instance = LangfuseTracer()
        except Exception:
            logger.warning("Failed to init Langfuse, falling back to NoOp", exc_info=True)
            _tracer_instance = NoOpTracer()
    else:
        logger.info("Langfuse disabled — using NoOp tracer")
        _tracer_instance = NoOpTracer()

    return _tracer_instance
