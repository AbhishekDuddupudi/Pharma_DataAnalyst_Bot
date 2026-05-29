"""
Langfuse tracing wrapper – no-op friendly (Langfuse SDK v4).

If ``LANGFUSE_ENABLED`` is false or keys are missing, every method
silently does nothing (``NoOpTracer``).  When enabled the real
``LangfuseTracer`` uses the Langfuse v4 SDK (``Langfuse()`` client with
``start_observation()`` / ``update()`` / ``end()``).

Trace hierarchy (one per chat request)::

    root span  ← start_trace()          → _SpanHandle (wraps LangfuseSpan)
      child span ← start_span()         → _SpanHandle
        generation ← log_generation()  → LangfuseGeneration (ended inline)
        event      ← log_event()       → LangfuseEvent (ended inline)

Safety: We never send cookies, passwords, API keys, or full DB result
rows.  Only SQL text, row counts, timings, selected tables/columns,
node names, retry counts, and sanitised error messages.
"""

from __future__ import annotations

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
# _SpanHandle – thin wrapper that adds get_trace_url() to a v4 span
# ═══════════════════════════════════════════════════════════════════


class _SpanHandle:
    """
    Wraps a ``LangfuseSpan`` / ``LangfuseGeneration`` from the v4 SDK and
    surfaces the helpers that the rest of the code-base expects:
    ``trace_id`` and ``get_trace_url()``.
    """

    __slots__ = ("_span", "_client")

    def __init__(self, span: Any, client: Any) -> None:
        self._span = span
        self._client = client

    @property
    def trace_id(self) -> str:
        return str(getattr(self._span, "trace_id", "") or "")

    def get_trace_url(self) -> str:
        tid = self.trace_id
        return self._client.get_trace_url(trace_id=tid) or "" if tid else ""


# ═══════════════════════════════════════════════════════════════════
# Real Langfuse tracer (SDK v4)
# ═══════════════════════════════════════════════════════════════════


class LangfuseTracer:
    """
    Thin wrapper around the Langfuse Python SDK v4.

    Uses ``Langfuse()`` directly (explicit credentials) so the existing
    ``LANGFUSE_HOST`` config key is honoured without renaming to the v4
    default env-var ``LANGFUSE_BASE_URL``.

    Each ``start_trace()`` creates a root ``LangfuseSpan`` (the v4 concept
    of a trace *is* the root span).  Child spans and generations are
    created on their parent via ``parent.start_observation()``.
    """

    def __init__(self) -> None:
        from langfuse import Langfuse  # lazy import

        self._lf = Langfuse(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            host=settings.LANGFUSE_HOST,
        )
        logger.info(
            "Langfuse v4 tracer initialised (host=%s)", settings.LANGFUSE_HOST
        )

    # ── helpers ─────────────────────────────────────────────────

    def _unwrap(self, handle: Any) -> Any:
        """Return the underlying v4 span from a _SpanHandle (or pass-through)."""
        return handle._span if isinstance(handle, _SpanHandle) else handle

    # ── trace lifecycle ──────────────────────────────────────────

    def start_trace(
        self,
        *,
        name: str,
        request_id: str,
        user_id: int | str,
        session_id: int | str | None = None,
        metadata: dict | None = None,
    ) -> _SpanHandle:
        """
        Create a root span that represents the entire request trace.

        A deterministic trace ID is derived from ``request_id`` so that
        external logs and the Langfuse UI can be correlated.
        """
        # Seed-based ID so request_id <-> Langfuse trace are always aligned
        trace_id = self._lf.create_trace_id(seed=request_id)

        root = self._lf.start_observation(
            as_type="span",
            name=name,
            trace_context={"trace_id": trace_id},
            input=_sanitise(metadata),
        )
        # Propagate user/session to all child observations via trace attributes
        root.update(
            user_id=str(user_id),
            session_id=str(session_id) if session_id else None,
            trace_name=name,
        )
        return _SpanHandle(root, client=self._lf)

    # ── child spans ──────────────────────────────────────────────

    def start_span(
        self,
        trace: Any,
        *,
        name: str,
        input: dict | str | None = None,
        metadata: dict | None = None,
    ) -> _SpanHandle:
        """Open a child span on a trace or another span."""
        parent = self._unwrap(trace)
        child = parent.start_observation(
            as_type="span",
            name=name,
            input=_sanitise(input) if isinstance(input, dict) else input,
            metadata=_sanitise(metadata),
        )
        return _SpanHandle(child, client=self._lf)

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
        raw = self._unwrap(span)
        kw: dict[str, Any] = {}
        if output is not None:
            kw["output"] = _sanitise(output) if isinstance(output, dict) else output
        if metadata is not None:
            kw["metadata"] = _sanitise(metadata)
        if level:
            kw["level"] = level
        if status_message:
            kw["status_message"] = status_message
        if kw:
            raw.update(**kw)
        raw.end()

    # ── LLM generation spans ─────────────────────────────────────

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
        parent = self._unwrap(trace_or_span)

        gen = parent.start_observation(
            as_type="generation",
            name=name,
            model=model,
            input=input if isinstance(input, str) else _sanitise(input),
            output=output if isinstance(output, str) else _sanitise(output),
            metadata=_sanitise(metadata),
        )
        if usage:
            gen.update(
                usage_details={
                    "input": usage.get("input", 0),
                    "output": usage.get("output", 0),
                }
            )
        if level:
            gen.update(level=level)
        gen.end()
        return gen

    # ── one-shot events ───────────────────────────────────────────

    def log_event(
        self,
        trace_or_span: Any,
        *,
        name: str,
        metadata: dict | None = None,
        level: str | None = None,
    ) -> None:
        """Log a discrete point-in-time event on a trace or span."""
        parent = self._unwrap(trace_or_span)
        ev = parent.start_observation(as_type="event", name=name)
        kw: dict[str, Any] = {}
        if metadata:
            kw["metadata"] = _sanitise(metadata)
        if level:
            kw["level"] = level
        if kw:
            ev.update(**kw)
        ev.end()

    # ── finalize root trace ───────────────────────────────────────

    def finalize_trace(
        self,
        trace: Any,
        *,
        output: dict | str | None = None,
        metadata: dict | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        """Update the root span with final output / metrics and end it."""
        raw = self._unwrap(trace)
        kw: dict[str, Any] = {}
        if output is not None:
            kw["output"] = _sanitise(output) if isinstance(output, dict) else output
        if metadata is not None:
            kw["metadata"] = _sanitise(metadata)
        if level:
            kw["level"] = level
        if status_message:
            kw["status_message"] = status_message
        if kw:
            raw.update(**kw)
        raw.end()

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


def shutdown_tracer() -> None:
    """
    Gracefully shut down the Langfuse client.

    Flushes all buffered spans and terminates background threads.
    Call this from the FastAPI ``lifespan`` shutdown handler to avoid
    losing the last traces when the process exits.
    """
    global _tracer_instance
    if isinstance(_tracer_instance, LangfuseTracer):
        try:
            _tracer_instance._lf.shutdown()
            logger.info("Langfuse client shut down cleanly")
        except Exception:
            logger.warning("Langfuse shutdown failed", exc_info=True)


def flush_langfuse() -> None:
    """Convenience wrapper — flush the singleton tracer's pending events."""
    get_tracer().flush()
